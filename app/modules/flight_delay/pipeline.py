from __future__ import annotations

import copy
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from app.logging_utils import LOGGER, log_extra
from app.engine.workflow import StageRunner
from app.engine.stage_fallbacks import build_stage_error_return
from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
from app.vision_preprocessor import prepare_attachments_for_claim

from app.modules.flight_delay.stages.utils import (
    _policy_excerpt_or_default,
    _is_unknown,
    _merge_vision_into_parsed,
    _merge_aviation_into_parsed,
    _truthy,
    _has_timezone,
    _parse_threshold_minutes,
    _extract_delay_minutes_from_text,
)
from app.modules.flight_delay.stages.hardcheck import (
    _check_foreseeability_fraud,
    _run_hardcheck,
)
from app.modules.flight_delay.stages.payout import _run_payout_calc
from app.modules.flight_delay.stages.delay_calc import (
    _compute_delay_minutes,
    _augment_with_computed_delay,
)
from app.modules.flight_delay.stages.postprocess import _postprocess_audit_result
from app.modules.flight_delay.stages.duplicate import (
    _is_concluded_status,
    _is_same_event,
    _check_duplicate_claim,
)
from app.modules.flight_delay.stages.validators import (
    _check_inheritance_scenario,
    _check_legal_capacity,
    _check_name_match,
    _check_same_day_policy,
    _check_coverage_area_text,
    _check_hardcheck_exclusion,
)


async def review_flight_delay_async(
    *,
    reviewer: Any,
    claim_folder: Path,
    claim_info: Dict[str, Any],
    policy_terms: str,
    index: int,
    total: int,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """
    航班延误审核主流程（编排层）：
    - stage0: 重复理赔检测
    - stage0_vision: 视觉/OCR 材料抽取
    - stage1: AI 数据解析与时区标准化
    - stage1.2: 合并 Vision 抽取结果
    - stage1.3: 飞常准航班权威数据查询
    - stage1.4: 接驳/替代航班飞常准查询
    - stage_hardcheck: 代码侧硬校验集合
    - stage10: 赔付金额预计算
    - stage2_precheck: 硬免责前置拦截
    - stage2: AI 理赔判定
    - postprocess: 规则兜底后处理
    """
    forceid = str(claim_info.get("forceid") or "unknown")
    ctx: Dict[str, Any] = {"debug": [], "flight_delay": None}

    runner = StageRunner(ctx=ctx, forceid=forceid)
    free_text = (claim_info.get("Description_of_Accident") or "").strip()

    # ========== stage0_duplicate: 重复理赔检测 ==========
    duplicate_check = _check_duplicate_claim(claim_info=claim_info, forceid=forceid)
    if duplicate_check:
        LOGGER.info(
            f"[{index}/{total}] 重复理赔检测命中: {duplicate_check.get('reason', '')}",
            extra=log_extra(forceid=forceid, stage="fd_duplicate_check", attempt=0),
        )
        return duplicate_check

    # ========== stage0_vision: 视觉/OCR 材料抽取 ==========
    LOGGER.info(
        f"[{index}/{total}] 航班延误-视觉抽取: 启动材料提取...",
        extra=log_extra(forceid=forceid, stage="fd_vision_extract", attempt=0),
    )
    vision_extract: Dict[str, Any] = {}
    try:
        extractor = MaterialExtractor(reviewer=reviewer, forceid=forceid)
        extraction = await extractor.extract(
            claim_folder=claim_folder,
            claim_info=claim_info,
            strategy=ExtractionStrategy.VISION_DIRECT,
            prompt_name="00_vision_extract",
            session=session,
        )
        raw_vision = extraction.vision_data
        if isinstance(raw_vision, dict):
            vision_extract = raw_vision
        elif isinstance(raw_vision, list) and raw_vision and isinstance(raw_vision[0], dict):
            vision_extract = raw_vision[0]
        else:
            LOGGER.warning(
                f"[{forceid}] 视觉抽取结果类型非 dict：{type(raw_vision).__name__}",
                extra=log_extra(forceid=forceid, stage="fd_vision_extract", attempt=0),
            )
    except Exception as _ve:
        LOGGER.warning(
            f"[{forceid}] 视觉抽取阶段异常（降级跳过）: {_ve}",
            extra=log_extra(forceid=forceid, stage="fd_vision_extract", attempt=0),
        )
    ctx["flight_delay_vision_extract"] = vision_extract

    # ========== stage1: 数据解析与时区标准化 ==========
    LOGGER.info(f"[{index}/{total}] 航班延误-阶段1: 数据解析与时区标准化...", extra=log_extra(forceid=forceid, stage="fd_parse", attempt=0))
    parsed, err = await runner.run(
        "fd_parse",
        reviewer._ai_flight_delay_parse_async,
        claim_info,
        free_text,
        session=session,
        max_retries=2,
        retry_sleep=2.0,
    )
    if err:
        return build_stage_error_return(forceid=forceid, checkpoint="航班信息解析/时区标准化", err=err, ctx=ctx)
    ctx["flight_delay_parse"] = parsed

    # ========== stage1.2: 合并 Vision 抽取结果 ==========
    if vision_extract:
        # 0) 理赔焦点航段识别
        v_claim_focus = vision_extract.get("claim_focus") or {}
        if isinstance(v_claim_focus, dict):
            cf_node = parsed.setdefault("claim_focus", {})
            for k, v in v_claim_focus.items():
                v_str = str(v).strip() if v is not None else ""
                if not _is_unknown(v_str) and _is_unknown(cf_node.get(k)):
                    cf_node[k] = v_str

        # 0.5) schedule_revision_chain
        v_chain = vision_extract.get("schedule_revision_chain") or []
        if isinstance(v_chain, list) and v_chain:
            parsed["schedule_revision_chain"] = v_chain
            first_rev = v_chain[0] if v_chain else {}
            if isinstance(first_rev, dict):
                sched_node = parsed.setdefault("schedule_local", {})
                rev_planned_dep = str(first_rev.get("planned_dep") or "").strip()
                rev_planned_arr = str(first_rev.get("planned_arr") or "").strip()
                rev_dep_tz = str(first_rev.get("dep_timezone_hint") or "").strip()
                rev_arr_tz = str(first_rev.get("arr_timezone_hint") or "").strip()
                if "/" in rev_planned_dep:
                    rev_planned_dep = rev_planned_dep.split("/")[0].strip()
                if "/" in rev_planned_arr:
                    rev_planned_arr = rev_planned_arr.split("/")[0].strip()
                if not _is_unknown(rev_planned_dep) and _is_unknown(sched_node.get("planned_dep")):
                    sched_node["planned_dep"] = rev_planned_dep
                if not _is_unknown(rev_planned_arr) and _is_unknown(sched_node.get("planned_arr")):
                    sched_node["planned_arr"] = rev_planned_arr
                if not _is_unknown(rev_dep_tz):
                    sched_node["dep_timezone_hint"] = rev_dep_tz
                if not _is_unknown(rev_arr_tz):
                    sched_node["arr_timezone_hint"] = rev_arr_tz
            last_rev = v_chain[-1] if len(v_chain) > 1 else first_rev
            if isinstance(last_rev, dict):
                alt_node = parsed.setdefault("alternate_local", {})
                last_dep = str(last_rev.get("planned_dep") or "").strip()
                last_arr = str(last_rev.get("planned_arr") or "").strip()
                if not _is_unknown(last_dep) and _is_unknown(alt_node.get("alt_dep")):
                    alt_node["alt_dep"] = last_dep
                if not _is_unknown(last_arr) and _is_unknown(alt_node.get("alt_arr")):
                    alt_node["alt_arr"] = last_arr

        # 0.6) aviation_scheduled
        v_avi_sched = vision_extract.get("aviation_scheduled") or {}
        if isinstance(v_avi_sched, dict):
            avi_node = parsed.setdefault("aviation_scheduled", {})
            for k, v in v_avi_sched.items():
                v_str = str(v).strip() if v is not None else ""
                if not _is_unknown(v_str) and _is_unknown(avi_node.get(k)):
                    avi_node[k] = v_str

        # 1) 航班号
        cf_flight = str((parsed.get("claim_focus") or {}).get("flight_no") or "").strip()
        if not _is_unknown(cf_flight):
            flight_node = parsed.setdefault("flight", {})
            flight_node["ticket_flight_no"] = cf_flight
        else:
            v_flight_no = str(vision_extract.get("flight_no") or "").strip()
            if not _is_unknown(v_flight_no):
                flight_node = parsed.setdefault("flight", {})
                if _is_unknown(flight_node.get("ticket_flight_no")):
                    flight_node["ticket_flight_no"] = v_flight_no

        # 1.5) claim_focus dep/arr_iata
        cf_dep = str((parsed.get("claim_focus") or {}).get("dep_iata") or "").strip().upper()
        cf_arr = str((parsed.get("claim_focus") or {}).get("arr_iata") or "").strip().upper()
        route_node = parsed.setdefault("route", {})
        if not _is_unknown(cf_dep) and _is_unknown(route_node.get("dep_iata")):
            route_node["dep_iata"] = cf_dep
        if not _is_unknown(cf_arr) and _is_unknown(route_node.get("arr_iata")):
            route_node["arr_iata"] = cf_arr

        # 2) 计划起飞时间
        v_flight_date = str(vision_extract.get("flight_date") or "").strip()
        if not _is_unknown(v_flight_date):
            sched_node = parsed.setdefault("schedule_local", {})
            existing_dep = str(sched_node.get("planned_dep") or "").strip()
            if _is_unknown(existing_dep):
                sched_node["planned_dep"] = v_flight_date

        # 2.5) 机场三字码
        v_dep_iata = str(vision_extract.get("dep_iata") or "").strip().upper()
        v_arr_iata = str(vision_extract.get("arr_iata") or "").strip().upper()
        if not _is_unknown(v_dep_iata) and _is_unknown(route_node.get("dep_iata")):
            route_node["dep_iata"] = v_dep_iata
        if not _is_unknown(v_arr_iata) and _is_unknown(route_node.get("arr_iata")):
            route_node["arr_iata"] = v_arr_iata

        # 3) 替代航班时间
        v_alt = vision_extract.get("alternate") or {}
        if isinstance(v_alt, dict):
            alt_node = parsed.setdefault("alternate_local", {})
            for src_key, dst_key in [("alt_dep", "alt_dep"), ("alt_arr", "alt_arr"),
                                      ("alt_flight_no", "alt_flight_no"), ("alt_source", "alt_source")]:
                v_val = str(v_alt.get(src_key) or "").strip()
                if not _is_unknown(v_val) and _is_unknown(alt_node.get(dst_key)):
                    alt_node[dst_key] = v_val
            is_conn_booking = _truthy(v_alt.get("is_connecting_rebooking")) is True
            if is_conn_booking:
                v_alt_dep = str(v_alt.get("alt_dep") or "").strip()
                if not _is_unknown(v_alt_dep) and not _is_unknown(alt_node.get("alt_dep")):
                    alt_node["alt_dep"] = v_alt_dep
            if _truthy(v_alt.get("is_connecting_missed")) is True:
                itin_node = parsed.setdefault("itinerary", {})
                itin_node["is_connecting_or_transit"] = "true"
                itin_node["mentions_missed_connection"] = "true"
            if is_conn_booking:
                # 校验：itinerary_segments 只有1段且替代航班与原航班同路线时，
                # 不标记联程改签（Vision 可能误判，如携程APP变动截图含后续行程段）
                v_segments = vision_extract.get("itinerary_segments") or []
                orig_dep = str(vision_extract.get("dep_iata") or "").strip().upper()
                orig_arr = str(vision_extract.get("arr_iata") or "").strip().upper()
                v_alt_dep_iata = str(v_alt.get("dep_iata") or "").strip().upper()
                v_alt_arr_iata = str(v_alt.get("arr_iata") or "").strip().upper()
                # 同路线判定：替代航班 dep/arr 与原航班一致（说明只是改期，非联程改签）
                same_route = (
                    not _is_unknown(v_alt_dep_iata) and not _is_unknown(v_alt_arr_iata)
                    and not _is_unknown(orig_dep) and not _is_unknown(orig_arr)
                    and v_alt_dep_iata == orig_dep and v_alt_arr_iata == orig_arr
                )
                # 单段判定：itinerary_segments 只有1个原始航段
                single_segment = isinstance(v_segments, list) and len(v_segments) <= 1
                is_conn_booking_validated = is_conn_booking and not (same_route or single_segment)
                if is_conn_booking_validated:
                    itin_node = parsed.setdefault("itinerary", {})
                    itin_node["is_connecting_or_transit"] = "true"
                    itin_node["is_connecting_rebooking"] = "true"

        # 4) evidence
        v_evidence = vision_extract.get("evidence") or {}
        if isinstance(v_evidence, dict):
            ev_node = parsed.setdefault("evidence", {})
            for k, v in v_evidence.items():
                v_str = str(v).strip() if v is not None else ""
                if not _is_unknown(v_str) and _is_unknown(ev_node.get(k)):
                    ev_node[k] = v

        # 5) delay_proof_reason_text
        reason_text = str(v_evidence.get("delay_proof_reason_text") or "").strip()
        if not _is_unknown(reason_text):
            if _is_unknown(parsed.get("delay_reason")):
                parsed["delay_reason"] = reason_text
            if _is_unknown(parsed.get("delay_reason_is_external")):
                _INTERNAL_KEYWORDS = ["公司原因", "商业原因", "运力调整", "计划取消", "company reason"]
                is_internal = any(kw in reason_text.lower() for kw in _INTERNAL_KEYWORDS)
                parsed["delay_reason_is_external"] = "false" if is_internal else "true"

        # 6) delay_proof_planned_dep / delay_proof_actual_dep
        proof_planned = str(v_evidence.get("delay_proof_planned_dep") or "").strip()
        proof_actual = str(v_evidence.get("delay_proof_actual_dep") or "").strip()
        if not _is_unknown(proof_planned):
            sched_node = parsed.setdefault("schedule_local", {})
            if _is_unknown(sched_node.get("planned_dep")):
                sched_node["planned_dep"] = proof_planned
        if not _is_unknown(proof_actual):
            actual_node = parsed.setdefault("actual_local", {})
            if _is_unknown(actual_node.get("actual_dep")):
                actual_node["actual_dep"] = proof_actual

        # 6.5) delay_proof_planned_arr / delay_proof_actual_arr
        proof_planned_arr = str(v_evidence.get("delay_proof_planned_arr") or "").strip()
        proof_actual_arr = str(v_evidence.get("delay_proof_actual_arr") or "").strip()
        if not _is_unknown(proof_planned_arr):
            sched_node = parsed.setdefault("schedule_local", {})
            if _is_unknown(sched_node.get("planned_arr")):
                sched_node["planned_arr"] = proof_planned_arr
        if not _is_unknown(proof_actual_arr):
            actual_node = parsed.setdefault("actual_local", {})
            if _is_unknown(actual_node.get("actual_arr")):
                actual_node["actual_arr"] = proof_actual_arr

        # 7) boarding_pass_actual_dep
        bp_actual = str(v_evidence.get("boarding_pass_actual_dep") or "").strip()
        if not _is_unknown(bp_actual):
            v_alt_fn = str(v_alt.get("alt_flight_no") or "").strip()
            has_alt_flight = not _is_unknown(v_alt_fn)
            has_chain = isinstance(v_chain, list) and len(v_chain) > 0
            avi_status = str(parsed.get("aviation_status") or "").strip()
            is_cancelled = avi_status in ("取消", "cancelled", "CANCELLED")
            is_rebooking = has_alt_flight or has_chain or is_cancelled
            if is_rebooking:
                alt_node = parsed.setdefault("alternate_local", {})
                if _is_unknown(alt_node.get("alt_dep")):
                    alt_node["alt_dep"] = bp_actual
            else:
                actual_node = parsed.setdefault("actual_local", {})
                if _is_unknown(actual_node.get("actual_dep")):
                    actual_node["actual_dep"] = bp_actual

        ctx["flight_delay_parse"] = parsed

    # ========== stage1.3: 飞常准航班权威数据查询 ==========
    from app.skills.flight_lookup import get_flight_lookup_skill

    aviation_result: Dict[str, Any] = {}
    aviation_results_all: List[Dict[str, Any]] = []

    all_flights = (vision_extract.get("all_flights_found") or [])
    claim_focus = (parsed.get("claim_focus") or {})
    cf_fn = str(claim_focus.get("flight_no") or "").strip()
    cf_dep = str(claim_focus.get("dep_iata") or "").strip().upper()
    cf_arr = str(claim_focus.get("arr_iata") or "").strip().upper()

    chain = (parsed.get("schedule_revision_chain") or [])
    chain_date = ""
    if chain and isinstance(chain[0], dict):
        chain_dep_raw = str(chain[0].get("planned_dep") or "").strip()
        if chain_dep_raw and chain_dep_raw.lower() not in ("unknown", ""):
            chain_date = chain_dep_raw[:10]

    v_flight_date = str(vision_extract.get("flight_date") or "").strip()
    v_flight_date = v_flight_date[:10] if v_flight_date and v_flight_date.lower() not in ("unknown", "") else ""

    planned_dep_raw = str((parsed.get("schedule_local") or {}).get("planned_dep") or "").strip()
    planned_dep_date = planned_dep_raw[:10] if planned_dep_raw and planned_dep_raw.lower() not in ("unknown", "") else ""

    flight_date = chain_date or v_flight_date or planned_dep_date

    cf_candidates = []
    if cf_fn and cf_fn.lower() not in ("unknown", ""):
        cf_candidates.append((cf_fn, cf_dep, cf_arr, flight_date))

    ticket_fn = str((parsed.get("flight") or {}).get("ticket_flight_no") or "").strip()
    if ticket_fn and ticket_fn.lower() not in ("unknown", "") and ticket_fn.upper() not in [c[0].upper() for c in cf_candidates]:
        cf_candidates.append((ticket_fn, "", "", flight_date))

    if len(cf_candidates) < 2 and all_flights and isinstance(all_flights, list):
        for fl in all_flights:
            if isinstance(fl, dict):
                fn = str(fl.get("flight_no") or "").strip()
                dep = str(fl.get("dep_iata") or "").strip().upper()
                arr = str(fl.get("arr_iata") or "").strip().upper()
                dt_raw = str(fl.get("date") or "").strip()
                dt = dt_raw[:10] if dt_raw and dt_raw.lower() not in ("unknown", "") else ""
                if fn and fn.lower() not in ("unknown", "") and fn.upper() not in [c[0].upper() for c in cf_candidates]:
                    cf_candidates.append((fn, dep, arr, dt or flight_date))
                    if len(cf_candidates) >= 2:
                        break

    route_dep_iata = str((parsed.get("route") or {}).get("dep_iata") or "").strip().upper()
    route_arr_iata = str((parsed.get("route") or {}).get("arr_iata") or "").strip().upper()

    for candidate_fn, candidate_dep, candidate_arr, candidate_date in cf_candidates:
        if not candidate_fn or not candidate_date:
            continue
        try:
            skill = get_flight_lookup_skill()
            one_result = await skill.lookup_status(
                flight_no=candidate_fn,
                date=candidate_date,
                dep_iata=candidate_dep if candidate_dep and candidate_dep.lower() != "unknown" else None,
                arr_iata=candidate_arr if candidate_arr and candidate_arr.lower() != "unknown" else None,
                session=session,
            )
            aviation_results_all.append({"candidate": (candidate_fn, candidate_date, candidate_dep, candidate_arr), "result": one_result})
            if one_result.get("success"):
                avi_dep = str(one_result.get("dep_iata") or "").strip().upper()
                avi_arr = str(one_result.get("arr_iata") or "").strip().upper()
                # 判断飞常准返回的路线是否与理赔路线一致
                route_match = (
                    not route_dep_iata or not route_arr_iata
                    or not avi_dep or not avi_arr
                    or (avi_dep == route_dep_iata and avi_arr == route_arr_iata)
                )
                LOGGER.info(
                    f"[{forceid}] 飞常准查询成功（候选={candidate_fn} {candidate_date}）: -> {one_result.get('status')} [{avi_dep}->{avi_arr}] route_match={route_match}",
                    extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
                )
                parsed = _merge_aviation_into_parsed(parsed, one_result)
                parsed.setdefault("evidence", {})
                if isinstance(parsed["evidence"], dict):
                    parsed["evidence"]["aviation_delay_proof"] = True
                    parsed["evidence"]["aviation_delay_proof_source"] = f"飞常准: {one_result.get('status','')} {one_result.get('source','')}"

                # 联程场景：飞常准查到了末段航班（arr_iata 与终点一致，但 dep 是中转机场）
                # 把末段的 planned_arr 和 dep/arr iata 存入 schedule_local，供延误计算使用
                arr_match = route_arr_iata and avi_arr and avi_arr == route_arr_iata
                dep_mismatch = route_dep_iata and avi_dep and avi_dep != route_dep_iata
                if arr_match and dep_mismatch:
                    sched_node = parsed.setdefault("schedule_local", {})
                    # 末段计划到达时间（终点到达，非中转出发）
                    last_planned_arr = one_result.get("planned_arr")
                    if last_planned_arr and not _is_unknown(str(last_planned_arr)):
                        sched_node["planned_arr"] = str(last_planned_arr)
                        LOGGER.info(
                            f"[{forceid}] 联程末段 planned_arr 更新为飞常准数据: {last_planned_arr}",
                            extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
                        )
                    # 记录末段机场供 delay_calc 机场匹配
                    sched_node["last_seg_dep_iata"] = avi_dep
                    sched_node["last_seg_arr_iata"] = avi_arr

                # 联程场景：飞常准查到了前程（dep 与出发一致，arr 是中转机场）
                # 把前程飞常准数据存入 parsed，供 hardcheck 判断前程是否正常到达
                dep_matches_route = route_dep_iata and avi_dep and avi_dep == route_dep_iata
                arr_is_transit = route_arr_iata and avi_arr and avi_arr != route_arr_iata
                if dep_matches_route and arr_is_transit:
                    seg_entry = {
                        "flight_no": one_result.get("flight_no"),
                        "dep_iata": avi_dep,
                        "arr_iata": avi_arr,
                        "planned_dep": one_result.get("planned_dep"),
                        "planned_arr": one_result.get("planned_arr"),
                        "actual_dep": one_result.get("actual_dep"),
                        "actual_arr": one_result.get("actual_arr"),
                        "status": one_result.get("status"),
                    }
                    parsed.setdefault("connecting_segments_data", []).append(seg_entry)

                ctx["flight_delay_parse"] = parsed
                aviation_result = one_result
                # 路线匹配时才终止：找到了正确航段，无需再查其他候选
                # 路线不匹配时继续，让后续候选有机会查到正确航段
                if route_match:
                    break
            else:
                LOGGER.info(
                    f"[{forceid}] 飞常准候选未返回数据: {candidate_fn} {candidate_date}, error={one_result.get('error', '')}",
                    extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
                )
        except Exception as _ae:
            LOGGER.warning(
                f"[{forceid}] 飞常准查询异常（降级跳过）: {_ae}",
                extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
            )

    ctx["flight_delay_aviation_lookup"] = aviation_result
    ctx["flight_delay_aviation_all_candidates"] = aviation_results_all

    # ========== stage1.4: 接驳/替代航班飞常准查询 ==========
    is_conn_rebooking = _truthy((parsed.get("itinerary") or {}).get("is_connecting_rebooking")) is True
    chain = (parsed or {}).get("schedule_revision_chain") or []
    first_alt_flight_no = None
    first_alt_date = None
    if is_conn_rebooking and isinstance(chain, list) and len(chain) >= 2:
        first_alt = chain[1]
        first_alt_flight_no = str(first_alt.get("original_flight_no") or "").strip()
        first_alt_date = str(first_alt.get("original_date") or "").strip()
        if first_alt_date and first_alt_date.lower() not in ("unknown", ""):
            first_alt_date = first_alt_date[:10]

    alt_local = parsed.get("alternate_local") or {}
    alt_fn = str(alt_local.get("alt_flight_no") or "").strip()
    alt_dep_raw = str(alt_local.get("alt_dep") or "").strip()
    alt_dep_date = alt_dep_raw[:10] if alt_dep_raw and alt_dep_raw.lower() not in ("unknown", "") else ""

    _already_queried = [c[0].upper() for c in cf_candidates] if cf_candidates else []

    if (
        is_conn_rebooking
        and first_alt_flight_no
        and first_alt_flight_no.lower() not in ("unknown", "null", "")
        and first_alt_date
        and first_alt_flight_no.upper() not in _already_queried
    ):
        try:
            skill = get_flight_lookup_skill()
            first_alt_aviation = await skill.lookup_status(
                flight_no=first_alt_flight_no,
                date=first_alt_date,
                dep_iata=None,
                arr_iata=None,
                session=session,
            )
            ctx["flight_delay_first_alt_aviation_lookup"] = first_alt_aviation
            if first_alt_aviation.get("success"):
                LOGGER.info(
                    f"[{forceid}] 联程首班替代航班飞常准查询成功: {first_alt_flight_no} {first_alt_date} -> {first_alt_aviation.get('status')}",
                    extra=log_extra(forceid=forceid, stage="fd_first_alt_aviation_lookup", attempt=0),
                )
                first_actual_dep = first_alt_aviation.get("actual_dep")
                if first_actual_dep:
                    parsed.setdefault("alternate_local", {})["alt_dep"] = first_actual_dep
                    parsed.setdefault("actual_local", {})["actual_dep"] = first_actual_dep
                    LOGGER.info(
                        f"[{forceid}] 联程首班 alt_dep/actual_dep 已覆盖为: {first_actual_dep}",
                        extra=log_extra(forceid=forceid, stage="fd_first_alt_aviation_lookup", attempt=0),
                    )
        except Exception as _first_ae:
            LOGGER.warning(
                f"[{forceid}] 联程首班替代航班查询失败（降级）: {_first_ae}",
                extra=log_extra(forceid=forceid, stage="fd_first_alt_aviation_lookup", attempt=0),
            )
            # 飞常准查询失败，用 Vision 提取的首段计划起飞时间兜底
            first_alt_planned_dep = str(first_alt.get("planned_dep") or "").strip()
            if first_alt_planned_dep and first_alt_planned_dep.lower() not in ("unknown", ""):
                parsed.setdefault("alternate_local", {})["alt_dep"] = first_alt_planned_dep
                parsed.setdefault("actual_local", {})["actual_dep"] = first_alt_planned_dep
                LOGGER.info(
                    f"[{forceid}] 联程首班 alt_dep/actual_dep 已用 Vision 提取时间兜底: {first_alt_planned_dep}",
                    extra=log_extra(forceid=forceid, stage="fd_first_alt_aviation_lookup", attempt=0),
                )

    if (
        alt_fn and alt_fn.lower() not in ("unknown", "null", "")
        and alt_dep_date
        and alt_fn.upper() not in _already_queried
    ):
        try:
            skill = get_flight_lookup_skill()
            alt_aviation = await skill.lookup_status(
                flight_no=alt_fn,
                date=alt_dep_date,
                dep_iata=None,
                arr_iata=None,
                session=session,
            )
            ctx["flight_delay_alt_aviation_lookup"] = alt_aviation
            if alt_aviation.get("success"):
                LOGGER.info(
                    f"[{forceid}] 接驳航班飞常准查询成功: {alt_fn} {alt_dep_date} -> {alt_aviation.get('status')}",
                    extra=log_extra(forceid=forceid, stage="fd_alt_aviation_lookup", attempt=0),
                )
                # 把替代航班路线信息存入 alternate_local，供 delay_calc 机场匹配使用
                avi_dep_iata = str(alt_aviation.get("dep_iata") or "").strip().upper()
                avi_arr_iata = str(alt_aviation.get("arr_iata") or "").strip().upper()
                if not _is_unknown(avi_dep_iata):
                    parsed.setdefault("alternate_local", {})["alt_dep_iata"] = avi_dep_iata
                if not _is_unknown(avi_arr_iata):
                    parsed.setdefault("alternate_local", {})["alt_arr_iata"] = avi_arr_iata

                is_conn_rebooking = _truthy((parsed.get("itinerary") or {}).get("is_connecting_rebooking")) is True
                actual_arr = alt_aviation.get("actual_arr")
                alt_arr_current = str(alt_local.get("alt_arr") or "")
                alt_arr_needs_fill = (
                    _is_unknown(alt_local.get("alt_arr"))
                    or "unknown" in alt_arr_current.lower()
                    or not _has_timezone(alt_arr_current)
                )
                if actual_arr and alt_arr_needs_fill:
                    parsed.setdefault("alternate_local", {})["alt_arr"] = actual_arr
                    # 联程改签时，替代末段航班的实际到达才是旅客的终到时间
                    # 非联程改签时，actual_local.actual_arr 应保留原航班的飞常准数据，不覆盖
                    if is_conn_rebooking:
                        parsed.setdefault("actual_local", {})["actual_arr"] = actual_arr

                actual_dep = alt_aviation.get("actual_dep")
                alt_dep_current = str(alt_local.get("alt_dep") or "")
                alt_dep_is_text_extracted = bool(re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?![:+\-])", alt_dep_current))
                alt_dep_needs_fill = (
                    _is_unknown(alt_local.get("alt_dep"))
                    or "unknown" in alt_dep_current.lower()
                    or (not _has_timezone(alt_dep_current) and not alt_dep_is_text_extracted)
                )
                try:
                    alt_dep_dt = datetime.fromisoformat(alt_dep_current.replace(" ", "T"))
                    alt_arr_dt_str = str(alt_local.get("alt_arr") or "")
                    if alt_arr_dt_str and alt_arr_dt_str.lower() not in ("unknown", ""):
                        alt_arr_dt = datetime.fromisoformat(alt_arr_dt_str.replace(" ", "T"))
                        if alt_dep_dt > alt_arr_dt:
                            alt_dep_needs_fill = False
                except Exception:
                    pass
                alt_dep_to_fill = actual_dep or alt_aviation.get("planned_dep")
                if alt_dep_to_fill:
                    is_conn_rebooking = _truthy((parsed.get("itinerary") or {}).get("is_connecting_rebooking")) is True
                    # 联程改签场景：alt_dep/actual_dep 已由首段航班覆盖，末段只覆盖 alt_arr/actual_arr
                    # 不再用末段起飞时间覆盖 alt_dep/actual_dep（否则延误时长虚高）
                    if is_conn_rebooking:
                        pass
                    elif alt_dep_needs_fill:
                        parsed.setdefault("alternate_local", {})["alt_dep"] = alt_dep_to_fill
                        parsed.setdefault("actual_local", {})["actual_dep"] = alt_dep_to_fill
                ctx["flight_delay_parse"] = parsed
        except Exception as _alt_ae:
            LOGGER.warning(
                f"[{forceid}] 接驳航班查询异常（降级跳过）: {_alt_ae}",
                extra=log_extra(forceid=forceid, stage="fd_alt_aviation_lookup", attempt=0),
            )

    policy_excerpt = _policy_excerpt_or_default(claim_info, policy_terms)
    parsed = _augment_with_computed_delay(parsed=parsed, policy_terms_excerpt=policy_excerpt, free_text=free_text)
    ctx["flight_delay_parse_enriched"] = parsed

    # ========== stage_hardcheck: 代码侧硬校验集合 ==========
    LOGGER.info(f"[{index}/{total}] 航班延误-硬校验: Skills B/C/E/H/I...", extra=log_extra(forceid=forceid, stage="fd_hardcheck", attempt=0))
    hardcheck = _run_hardcheck(parsed=parsed, claim_info=claim_info, policy_excerpt=policy_excerpt, free_text=free_text, vision_extract=ctx.get("flight_delay_vision_extract") or {})
    ctx["flight_delay_hardcheck"] = hardcheck

    # ========== 阶段10: 赔付金额预计算（代码侧） ==========
    payout_result = _run_payout_calc(parsed=parsed, claim_info=claim_info, policy_excerpt=policy_excerpt)
    ctx["flight_delay_payout"] = payout_result

    # ========== stage2_precheck: 硬免责前置拦截 ==========
    exclusion_result = _check_hardcheck_exclusion(hardcheck=hardcheck)
    if exclusion_result:
        LOGGER.info(
            f"[{index}/{total}] 硬免责命中，跳过AI判定: {exclusion_result['explanation'][:80]}",
            extra=log_extra(forceid=forceid, stage="fd_exclusion_precheck", attempt=0),
        )
        audit = exclusion_result
        ctx["flight_delay_audit"] = audit
        ctx["flight_delay_audit_post"] = audit
        audit_result = audit["audit_result"]
        is_additional = "Y" if audit_result == "需补齐资料" else "N"
        remark = "航班延误: " + audit["explanation"]
        return {
            "forceid": forceid,
            "ClaimId": claim_info.get("ClaimId", ""),
            "claim_type": "flight_delay",
            "Remark": remark,
            "IsAdditional": is_additional,
            "KeyConclusions": [{"checkpoint": "航班延误审核", "Eligible": "N", "Remark": remark}],
            "flight_delay_audit": audit,
            "DebugInfo": ctx,
        }

    # ========== stage2: AI 理赔判定 ==========
    LOGGER.info(f"[{index}/{total}] 航班延误-阶段2: 理赔判定...", extra=log_extra(forceid=forceid, stage="fd_audit", attempt=0))
    audit, err = await runner.run(
        "fd_audit",
        reviewer._ai_flight_delay_audit_async,
        claim_info,
        parsed,
        policy_excerpt,
        session=session,
        max_retries=2,
        retry_sleep=2.0,
        payout_json=payout_result,
    )
    if err:
        return build_stage_error_return(forceid=forceid, checkpoint="航班延误理赔判定", err=err, ctx=ctx)
    ctx["flight_delay_audit"] = audit

    # ========== 规则兜底后处理 ==========
    audit = _postprocess_audit_result(
        parsed=parsed,
        audit=audit,
        policy_terms_excerpt=policy_excerpt,
        hardcheck=hardcheck,
        payout_result=payout_result,
        free_text=free_text,
    )
    ctx["flight_delay_audit_post"] = audit

    # ========== 组装标准输出 ==========
    audit_result = str(audit.get("audit_result") or "").strip()
    is_additional = "Y" if audit_result == "需补齐资料" else "N"
    remark_prefix = "航班延误: "
    remark = remark_prefix + str(audit.get("explanation") or audit_result or "完成判定")

    hardcheck_notes: List[str] = []
    if hardcheck.get("war_risk", {}).get("is_war_risk"):
        affected = hardcheck.get("war_risk", {}).get("affected_locations", [])
        location_str = f"（受影响地区：{', '.join(affected)}）" if affected else ""
        hardcheck_notes.append(f"[战争风险] {hardcheck['war_risk'].get('note', '')}{location_str}")
    if hardcheck.get("transit_check", {}).get("is_domestic_cn"):
        hardcheck_notes.append(f"[境内中转免责] 中转地={hardcheck['transit_check'].get('iata')}")
    if hardcheck.get("coverage_area", {}).get("in_coverage") is False:
        hardcheck_notes.append("[超出承保区域]")
    if hardcheck.get("coverage_area_text_check", {}).get("in_coverage") is False:
        hardcheck_notes.append(f"[文本兜底-超出承保区域] {hardcheck['coverage_area_text_check'].get('note','')}")
    if hardcheck.get("same_day_policy_check", {}).get("is_denied") is True:
        hardcheck_notes.append(f"[同天投保免责] {hardcheck['same_day_policy_check'].get('note','')}")
    if hardcheck.get("name_match_check", {}).get("match_result") == "mismatch":
        hardcheck_notes.append(f"[姓名不符] {hardcheck['name_match_check'].get('note','')}")
    if hardcheck.get("inheritance_check", {}).get("is_inheritance_suspected"):
        hardcheck_notes.append(f"[疑似遗产继承] {hardcheck['inheritance_check'].get('note','')}")
    if hardcheck.get("capacity_check", {}).get("needs_guardian"):
        hardcheck_notes.append(f"[未成年/限制行为能力人] {hardcheck['capacity_check'].get('note','')}")
    if hardcheck.get("missed_connection_check", {}).get("is_missed_connection"):
        hardcheck_notes.append("[中转接驳免责]")
    if hardcheck.get("passenger_civil_check", {}).get("is_passenger_civil") is False:
        hardcheck_notes.append(f"[非客运航班] {hardcheck['passenger_civil_check'].get('flight_no', '')}")
    if hardcheck.get("fraud_foreseeability_check", {}).get("fraud_suspected"):
        hardcheck_notes.append("[欺诈嫌疑-需人工复核]")
    missing_req = hardcheck.get("required_materials_check", {}).get("missing_required") or []
    if missing_req:
        hardcheck_notes.append(f"[缺必备材料] {'、'.join(missing_req)}")
    if hardcheck_notes:
        remark += "；硬校验标记：" + "；".join(hardcheck_notes)

    key_conclusions = [
        {
            "checkpoint": "航班延误审核",
            "Eligible": "Y" if audit_result == "通过" else "N",
            "Remark": remark,
        }
    ]

    return {
        "forceid": forceid,
        "ClaimId": claim_info.get("ClaimId", ""),
        "claim_type": "flight_delay",
        "Remark": remark,
        "IsAdditional": is_additional,
        "KeyConclusions": key_conclusions,
        "flight_delay_audit": audit,
        "DebugInfo": ctx,
    }
