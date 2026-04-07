from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from app.logging_utils import LOGGER, log_extra
from app.engine.workflow import StageRunner
from app.engine.stage_fallbacks import build_stage_error_return
from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
from app.skills.airport import resolve_country, check_transit_domestic
from app.skills.war_risk import check_war_table
from app.skills.flight_lookup import get_flight_lookup_skill
from app.skills.weather import lookup_alerts_table, check_foreseeability
from app.skills.policy_booking import (
    lookup_effective_window,
    check_delay_in_coverage,
    lookup_coverage_area,
    check_delay_in_coverage_area,
    verify_evidence_basic,
)
from app.skills.compensation import calculate_payout, parse_tier_config_from_terms


def _policy_excerpt_or_default(claim_info: Dict[str, Any], policy_terms: str) -> str:
    if policy_terms:
        return policy_terms[:4000]
    insured_amount = str(claim_info.get("Insured_Amount") or claim_info.get("insured_amount") or "")
    return (
        "【缺少条款全文，按默认兜底】\n"
        "- 起赔标准：每满5小时赔付300元\n"
        "- 最高保额：1200元\n"
        f"- 赔付限额：{insured_amount or '以保单为准'}\n"
    )


def _is_unknown(v: Any) -> bool:
    """判断字段值是否为"未知"（null/unknown/空字符串）。"""
    if v is None:
        return True
    if isinstance(v, str) and v.strip().lower() in ("", "unknown", "null", "none"):
        return True
    return False


def _merge_vision_into_parsed(parsed: Dict[str, Any], vision: Dict[str, Any]) -> Dict[str, Any]:
    """
    将视觉（Vision）抽取结果合并到文本解析结果中。

    规则：
    - Vision 只**填补** parsed 中的 unknown/null 字段，不覆盖已有非 unknown 值。
    - 递归处理嵌套 dict（route/schedule_local/actual_local/alternate_local/evidence/flight 等）。
    - 非 dict 的 leaf 字段：parsed 为 unknown 且 vision 不为 unknown，则取 vision 值。
    """
    def _merge_dict(base: Any, override: Any) -> Any:
        if not isinstance(base, dict) or not isinstance(override, dict):
            return base
        merged = dict(base)
        for k, v_over in override.items():
            v_base = merged.get(k)
            if isinstance(v_base, dict) and isinstance(v_over, dict):
                merged[k] = _merge_dict(v_base, v_over)
            elif _is_unknown(v_base) and not _is_unknown(v_over):
                merged[k] = v_over
        return merged

    return _merge_dict(parsed, vision)


def _merge_aviation_into_parsed(parsed: Dict[str, Any], aviation: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 AviationStack 查询结果合并到 parsed 中。
    只补全 unknown/null 字段，官方数据优先级高于 Vision 抽取，但不覆盖已有非 unknown 值
    （以材料中的显式证明为准，官方数据仅作补全）。
    """
    if not aviation.get("success"):
        return parsed

    import copy
    p = copy.deepcopy(parsed)

    def _fill(keys: list, value: Any):
        """按路径填入，只填 unknown/null"""
        node = p
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        last = keys[-1]
        if _is_unknown(node.get(last)) and not _is_unknown(value):
            node[last] = value

    # 路线
    _fill(["route", "dep_iata"], aviation.get("dep_iata"))
    _fill(["route", "arr_iata"], aviation.get("arr_iata"))

    # 计划时间（飞常准返回 ISO8601 含时区）——强制覆盖 AI 解析值（官方数据更权威）
    def _force_fill(keys: list, value: Any):
        """强制填入，只要飞常准有值就覆盖"""
        if _is_unknown(value):
            return
        node = p
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    _force_fill(["schedule_local", "planned_dep"], aviation.get("planned_dep"))
    _force_fill(["schedule_local", "planned_arr"], aviation.get("planned_arr"))

    # 实际时间：飞常准的实际起降时间强制覆盖（比材料/vision抽取更权威，含时区信息）
    # 注意：材料上的时间往往缺时区或日期不完整（如"00:53"无法判断是哪天哪个时区）
    _force_fill(["actual_local", "actual_dep"], aviation.get("actual_dep"))
    _force_fill(["actual_local", "actual_arr"], aviation.get("actual_arr"))

    # 承运人
    _fill(["flight", "operating_carrier"], aviation.get("operating_carrier"))

    # 延误原因
    reason = aviation.get("delay_reason")
    if reason and _is_unknown(p.get("delay_reason")):
        p["delay_reason"] = reason

    # 飞常准返回延误原因时，推断 delay_reason_is_external
    # 规则：飞常准的官方延误原因（飞机晚到/天气/机械故障等）均视为已确认外部原因
    # 仅排除明确可预见/主观因素（罢工/商业决策等暂不处理，交 AI 判定）
    if reason and not _is_unknown(reason) and _is_unknown(p.get("delay_reason_is_external")):
        _INTERNAL_REASON_KEYWORDS = ["计划取消", "航空公司取消", "商业原因", "运力调整"]
        reason_lower = str(reason).lower()
        if any(kw in reason_lower for kw in _INTERNAL_REASON_KEYWORDS):
            pass  # 留给 AI 判定
        else:
            # 飞常准报告的延误原因（飞机晚到/天气/机械等）均视为外部原因已确认
            p["delay_reason_is_external"] = "true"

    # 多航段（中途停靠）：将所有段信息写入，供 AI 辨认乘客实际所在的航段
    segments = aviation.get("segments")
    if segments:
        p["aviation_segments"] = segments

    # 飞常准状态（取消/延误等）写入 parsed，供后处理判断
    avi_status = aviation.get("status")
    if avi_status and not _is_unknown(avi_status):
        p["aviation_status"] = avi_status

    # 标注数据来源
    p.setdefault("aviation_lookup_note", f"来自AviationStack: status={aviation.get('status')}, source={aviation.get('source')}")

    return p


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
    航班延误审核主流程（完整版）：
    - stage1: 数据解析与时区标准化（基于 claim_info + 描述文本）
    - stage1.5: 代码侧硬校验（延误时长取长原则 + 阈值）
    - stage_hardcheck: 代码侧硬校验集合（Skills B/C/E/H/I/阶段10）
    - stage2: AI 判定（阈值/免责/一致性）
    - postprocess: 规则兜底后处理
    输出兼容既有 review_results schema（Remark/IsAdditional/KeyConclusions/DebugInfo）。
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

    # ========== stage0_vision: 视觉/OCR 材料抽取（行程单/延误证明/改签短信等图片）==========
    # 使用共享 MaterialExtractor（VISION_DIRECT 策略）代替硬编码的 Vision 调用
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
        # vision result 期望为 dict；但实际可能因模型输出格式变体而返回 list/其它结构
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
    # Vision prompt 现在提取: flight_no / flight_date / dep_iata / arr_iata / alternate / evidence
    # 将这些字段填入 parsed 对应的路径（只填 unknown 槽，不覆盖已有值）
    if vision_extract:
        # 1) 航班号：填到 parsed.flight.ticket_flight_no
        v_flight_no = str(vision_extract.get("flight_no") or "").strip()
        if not _is_unknown(v_flight_no):
            flight_node = parsed.setdefault("flight", {})
            if _is_unknown(flight_node.get("ticket_flight_no")):
                flight_node["ticket_flight_no"] = v_flight_no

        # 2) 计划起飞时间：填到 parsed.schedule_local.planned_dep
        # Vision 现在会尽量提取完整的 YYYY-MM-DD HH:MM；若飞常准有官方数据则会强制覆盖
        v_flight_date = str(vision_extract.get("flight_date") or "").strip()
        if not _is_unknown(v_flight_date):
            sched_node = parsed.setdefault("schedule_local", {})
            existing_dep = str(sched_node.get("planned_dep") or "").strip()
            if _is_unknown(existing_dep):
                sched_node["planned_dep"] = v_flight_date

        # 2.5) 机场三字码：填到 parsed.route.dep_iata / arr_iata
        # 用于飞常准查询和时区换算（优先级：飞常准 > Vision > AI解析）
        v_dep_iata = str(vision_extract.get("dep_iata") or "").strip().upper()
        v_arr_iata = str(vision_extract.get("arr_iata") or "").strip().upper()
        if not _is_unknown(v_dep_iata):
            route_node = parsed.setdefault("route", {})
            if _is_unknown(route_node.get("dep_iata")):
                route_node["dep_iata"] = v_dep_iata
        if not _is_unknown(v_arr_iata):
            route_node = parsed.setdefault("route", {})
            if _is_unknown(route_node.get("arr_iata")):
                route_node["arr_iata"] = v_arr_iata

        # 3) 替代航班时间：填到 parsed.alternate_local
        v_alt = vision_extract.get("alternate") or {}
        if isinstance(v_alt, dict):
            alt_node = parsed.setdefault("alternate_local", {})
            for src_key, dst_key in [("alt_dep", "alt_dep"), ("alt_arr", "alt_arr"),
                                      ("alt_flight_no", "alt_flight_no"), ("alt_source", "alt_source")]:
                v_val = str(v_alt.get(src_key) or "").strip()
                if not _is_unknown(v_val) and _is_unknown(alt_node.get(dst_key)):
                    alt_node[dst_key] = v_val
            # 联程误机标志：写入 itinerary
            if _truthy(v_alt.get("is_connecting_missed")) is True:
                itin_node = parsed.setdefault("itinerary", {})
                itin_node["is_connecting_or_transit"] = "true"
                itin_node["mentions_missed_connection"] = "true"

        # 4) evidence：填到 parsed.evidence（逐字段填补 unknown 槽）
        v_evidence = vision_extract.get("evidence") or {}
        if isinstance(v_evidence, dict):
            ev_node = parsed.setdefault("evidence", {})
            for k, v in v_evidence.items():
                v_str = str(v).strip() if v is not None else ""
                if not _is_unknown(v_str) and _is_unknown(ev_node.get(k)):
                    ev_node[k] = v

        # 5) delay_proof_reason_text：Vision 从证明文件中抽取的延误原因原文
        #    填入 parsed.delay_reason（只填 unknown 槽），并推断 delay_reason_is_external
        v_evidence = vision_extract.get("evidence") or {}
        reason_text = str(v_evidence.get("delay_proof_reason_text") or "").strip()
        if not _is_unknown(reason_text):
            if _is_unknown(parsed.get("delay_reason")):
                parsed["delay_reason"] = reason_text
            # 推断外部原因：公司原因/商业决策视为内部，其余视为外部
            if _is_unknown(parsed.get("delay_reason_is_external")):
                _INTERNAL_KEYWORDS = ["公司原因", "商业原因", "运力调整", "计划取消", "company reason"]
                is_internal = any(kw in reason_text.lower() for kw in _INTERNAL_KEYWORDS)
                parsed["delay_reason_is_external"] = "false" if is_internal else "true"

        # 6) delay_proof_planned_dep / delay_proof_actual_dep：Vision 从延误证明中抽取的计划/实际起飞时间
        #    填入 parsed.schedule_local.planned_dep 和 parsed.actual_local.actual_dep（只填 unknown 槽）
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

        # 6.5) delay_proof_planned_arr / delay_proof_actual_arr：延误证明上的到达时间（用于"取长原则"）
        #      填入 parsed.schedule_local.planned_arr 和 parsed.actual_local.actual_arr（只填 unknown 槽）
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

        # 7) boarding_pass_actual_dep：登机牌上的实际起飞时间，作为 actual_dep 的补充来源
        bp_actual = str(v_evidence.get("boarding_pass_actual_dep") or "").strip()
        if not _is_unknown(bp_actual):
            actual_node = parsed.setdefault("actual_local", {})
            if _is_unknown(actual_node.get("actual_dep")):
                actual_node["actual_dep"] = bp_actual

        ctx["flight_delay_parse"] = parsed

    # ========== stage1.3: 飞常准航班权威数据查询 ==========
    # 入参：航班号 + 飞行日期，均优先从 vision 提取的材料中获取
    aviation_result: Dict[str, Any] = {}
    flight_no = str((parsed.get("flight") or {}).get("ticket_flight_no") or "").strip()
    # 飞行日期：优先 vision 直接提取的 flight_date（来自票面/截图），其次 schedule_local.planned_dep 日期部分
    v_flight_date = str(vision_extract.get("flight_date") or "").strip()
    v_flight_date = v_flight_date[:10] if v_flight_date and v_flight_date.lower() not in ("unknown", "") else ""
    planned_dep_raw = str((parsed.get("schedule_local") or {}).get("planned_dep") or "").strip()
    planned_dep_date = planned_dep_raw[:10] if planned_dep_raw and planned_dep_raw.lower() not in ("unknown", "") else ""
    flight_date = v_flight_date or planned_dep_date
    if flight_no and flight_no.lower() not in ("unknown", "") and flight_date:
        dep_iata = (parsed.get("route") or {}).get("dep_iata")
        arr_iata = (parsed.get("route") or {}).get("arr_iata")
        try:
            skill = get_flight_lookup_skill()
            aviation_result = await skill.lookup_status(
                flight_no=flight_no,
                date=flight_date,
                dep_iata=dep_iata if dep_iata and dep_iata.lower() != "unknown" else None,
                arr_iata=arr_iata if arr_iata and arr_iata.lower() != "unknown" else None,
                session=session,
            )
            if aviation_result.get("success"):
                LOGGER.info(
                    f"[{forceid}] 飞常准查询成功: {flight_no} {flight_date} -> {aviation_result.get('status')}",
                    extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
                )
                # 用官方数据补全 parsed 中的 unknown 字段
                parsed = _merge_aviation_into_parsed(parsed, aviation_result)
                # 飞常准查到官方数据 → 视为具备可信延误凭证，不再要求客户补交书面证明
                parsed.setdefault("evidence", {})
                if isinstance(parsed["evidence"], dict):
                    parsed["evidence"]["aviation_delay_proof"] = True
                    parsed["evidence"]["aviation_delay_proof_source"] = f"飞常准: {aviation_result.get('status','')} {aviation_result.get('source','')}"
                ctx["flight_delay_parse"] = parsed
            else:
                LOGGER.info(
                    f"[{forceid}] AviationStack未返回数据: {aviation_result.get('error', '')}",
                    extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
                )
        except Exception as _ae:
            LOGGER.warning(
                f"[{forceid}] AviationStack查询异常（降级跳过）: {_ae}",
                extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
            )
    ctx["flight_delay_aviation_lookup"] = aviation_result

    # ========== stage1.4: 接驳/替代航班飞常准查询 ==========
    # 若 alternate_local 有 alt_flight_no + alt_dep 日期，自动查接驳航班实际到达时间
    alt_local = parsed.get("alternate_local") or {}
    alt_fn = str(alt_local.get("alt_flight_no") or "").strip()
    alt_dep_raw = str(alt_local.get("alt_dep") or "").strip()
    alt_dep_date = alt_dep_raw[:10] if alt_dep_raw and alt_dep_raw.lower() not in ("unknown", "") else ""
    if (
        alt_fn and alt_fn.lower() not in ("unknown", "null", "")
        and alt_dep_date
        and alt_fn.upper() != flight_no.upper()  # 避免重复查同一航班
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
                # 将接驳航班实际到达时间回填 alternate_local.alt_arr：
                # 与 alt_dep 一致：当前为 unknown，或当前值无时区（材料常为本地时刻串，误当 UTC 会拉长「到达口径」延误）
                actual_arr = alt_aviation.get("actual_arr")
                alt_arr_current = str(alt_local.get("alt_arr") or "")
                alt_arr_needs_fill = (
                    _is_unknown(alt_local.get("alt_arr"))
                    or "unknown" in alt_arr_current.lower()
                    or not _has_timezone(alt_arr_current)
                )
                if actual_arr and alt_arr_needs_fill:
                    parsed.setdefault("alternate_local", {})["alt_arr"] = actual_arr
                    parsed.setdefault("actual_local", {})["actual_arr"] = actual_arr
                # 将接驳航班实际起飞时间回填 alternate_local.alt_dep（优先用 actual_dep）
                # 条件：当前为 unknown，或当前值不含时区信息（飞常准数据含时区更准确）
                actual_dep = alt_aviation.get("actual_dep")
                alt_dep_current = str(alt_local.get("alt_dep") or "")
                alt_dep_needs_fill = (
                    _is_unknown(alt_local.get("alt_dep"))
                    or "unknown" in alt_dep_current.lower()
                    or not _has_timezone(alt_dep_current)  # 无时区信息时也回填
                )
                # 优先用实际起飞时间，若无则用计划起飞时间
                alt_dep_to_fill = actual_dep or alt_aviation.get("planned_dep")
                if alt_dep_to_fill and alt_dep_needs_fill:
                    parsed.setdefault("alternate_local", {})["alt_dep"] = alt_dep_to_fill
                    # 同时回填 actual_local.actual_dep（用于兜底计算）
                    parsed.setdefault("actual_local", {})["actual_dep"] = alt_dep_to_fill
                ctx["flight_delay_parse"] = parsed
        except Exception as _alt_ae:
            LOGGER.warning(
                f"[{forceid}] 接驳航班查询异常（降级跳过）: {_alt_ae}",
                extra=log_extra(forceid=forceid, stage="fd_alt_aviation_lookup", attempt=0),
            )

    policy_excerpt = _policy_excerpt_or_default(claim_info, policy_terms)
    parsed = _augment_with_computed_delay(parsed=parsed, policy_terms_excerpt=policy_excerpt)
    ctx["flight_delay_parse_enriched"] = parsed

    # ========== stage_hardcheck: 代码侧硬校验集合 ==========
    LOGGER.info(f"[{index}/{total}] 航班延误-硬校验: Skills B/C/E/H/I...", extra=log_extra(forceid=forceid, stage="fd_hardcheck", attempt=0))
    hardcheck = _run_hardcheck(parsed=parsed, claim_info=claim_info, policy_excerpt=policy_excerpt, free_text=free_text, vision_extract=ctx.get("flight_delay_vision_extract") or {})
    ctx["flight_delay_hardcheck"] = hardcheck

    # ========== 阶段10: 赔付金额预计算（代码侧） ==========
    payout_result = _run_payout_calc(parsed=parsed, claim_info=claim_info, policy_excerpt=policy_excerpt)
    ctx["flight_delay_payout"] = payout_result

    # ========== stage2_precheck: 硬免责前置拦截（命中则跳过AI，直接拒赔）==========
    exclusion_result = _check_hardcheck_exclusion(hardcheck=hardcheck)
    if exclusion_result:
        LOGGER.info(
            f"[{index}/{total}] 硬免责命中，跳过AI判定: {exclusion_result['explanation'][:80]}",
            extra=log_extra(forceid=forceid, stage="fd_exclusion_precheck", attempt=0),
        )
        audit = exclusion_result
        ctx["flight_delay_audit"] = audit
        ctx["flight_delay_audit_post"] = audit
        # 直接组装输出，不再走 AI 和后处理
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

    # ========== 规则兜底后处理（防止模型误用"拒赔"口径） ==========
    audit = _postprocess_audit_result(
        parsed=parsed,
        audit=audit,
        policy_terms_excerpt=policy_excerpt,
        hardcheck=hardcheck,
        payout_result=payout_result,
    )
    ctx["flight_delay_audit_post"] = audit

    # ========== 组装标准输出 ==========
    audit_result = str(audit.get("audit_result") or "").strip()
    is_additional = "Y" if audit_result == "需补齐资料" else "N"
    remark_prefix = "航班延误: "
    remark = remark_prefix + str(audit.get("explanation") or audit_result or "完成判定")

    # 附加硬校验关键结论
    hardcheck_notes: List[str] = []
    if hardcheck.get("war_risk", {}).get("is_war_risk"):
        hardcheck_notes.append(f"[战争风险] {hardcheck['war_risk'].get('note', '')}")
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


def _check_foreseeability_fraud(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
) -> Dict[str, Any]:
    """
    情形6：可预见因素/欺诈检测。
    规则：投保时/购票时，延误因素已可预见，则拒赔。
    典型场景：台风预警已发布后才投保/改签；罢工已宣布后才购票。

    逻辑：
    1. 提取 invest_date（投保日）、ticket_booking_date（购票/改签日）
    2. 提取 delay_reason（延误原因）和 date_of_accident（事故日）
    3. 若延误原因为"天气/台风/罢工"等可预见因素，且投保日或购票日在事故日前<7天，
       标记为"需人工复核欺诈嫌疑"
    4. 若 AI 解析结果中明确标注 foreseeability_fraud=true，直接标记
    """
    result: Dict[str, Any] = {
        "fraud_suspected": False,
        "fraud_level": "none",  # none / suspect / confirmed
        "reason": "",
        "note": "",
    }

    try:
        # 从 claim_info 提取关键日期
        # 兼容字段命名差异：有的用 Policy_Start_Date，有的用 Effective_Date/Insurance_Period_From
        invest_date_raw = str(
            claim_info.get("Policy_Start_Date")
            or claim_info.get("policy_start_date")
            or claim_info.get("Insurance_Period_From")
            or claim_info.get("insurance_period_from")
            or claim_info.get("effective_from")
            or claim_info.get("Effective_Date")
            or claim_info.get("effective_date")
            or ""
        ).strip()
        accident_date_raw = str(claim_info.get("Date_of_Accident") or claim_info.get("date_of_accident") or "").strip()
        delay_reason = str((parsed or {}).get("delay_reason") or "").lower()

        def _parse_date_any(s: str) -> Optional[datetime]:
            """
            解析日期：
            - YYYY-MM-DD
            - YYYY/MM/DD
            - YYYYMMDD
            - YYYYMMDDHHMMSS
            """
            ss = str(s or "").strip()
            if not ss or ss.lower() in ("unknown", "null", "none"):
                return None
            if re.fullmatch(r"\d{14}", ss):
                return datetime.strptime(ss, "%Y%m%d%H%M%S")
            if re.fullmatch(r"\d{8}", ss):
                return datetime.strptime(ss, "%Y%m%d")
            if "-" in ss or "/" in ss:
                # 优先截取到日期段
                s0 = ss[:10]
                for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
                    try:
                        return datetime.strptime(s0, fmt)
                    except Exception:
                        continue
            # 最后兜底：尝试 fromisoformat（可能是 YYYY-MM-DD）
            try:
                return datetime.fromisoformat(ss)
            except Exception:
                return None

        invest_dt = _parse_date_any(invest_date_raw)
        accident_dt = _parse_date_any(accident_date_raw)
        invest_date = invest_dt.date() if invest_dt else None
        accident_date = accident_dt.date() if accident_dt else None
        action_time_iso = invest_date.isoformat() if invest_date else ""

        # 从 parsed 中提取 AI 解析出的欺诈嫌疑标注
        fraud_flag = _truthy((parsed or {}).get("foreseeability_fraud"))
        if fraud_flag is True:
            result["fraud_suspected"] = True
            result["fraud_level"] = "suspect"
            result["note"] = "AI解析阶段已标注可预见因素欺诈嫌疑，需人工复核"
            return result

        # 可预见因素关键词（天气/罢工/政治等在事前已公开的因素）
        foreseeable_keywords = [
            "台风", "typhoon", "hurricane", "飓风",
            "罢工", "strike",
            "暴风", "blizzard", "snowstorm",
            "洪水", "flood",
        ]
        reason_is_foreseeable = any(kw in delay_reason for kw in foreseeable_keywords)

        if not reason_is_foreseeable:
            result["note"] = "延误原因非典型可预见因素（天气/罢工等），跳过欺诈检测"
            return result

        # 先尝试"可预见因素时间线"硬比对（维护表预警发布时间 vs 投保/订票时间）
        # 没有维护表数据时会降级为你们现有的时间阈值启发式。
        try:
            route = (parsed or {}).get("route") or {}

            def _iata(val: Any) -> str:
                s = str(val or "").strip().upper()
                return s if s and s not in ("UNKNOWN", "NULL", "NONE") else ""

            dep_iata = _iata(route.get("dep_iata"))
            arr_iata = _iata(route.get("arr_iata"))
            airport_iata = arr_iata or dep_iata
            if airport_iata and action_time_iso and accident_date:
                alerts = lookup_alerts_table(airport_iata=airport_iata, check_date=accident_date)
            else:
                alerts = []

            if alerts:
                for alert in alerts:
                    published_at = str(alert.get("published_at") or "").strip()
                    if not published_at:
                        continue
                    fore_res = check_foreseeability(
                        published_at=published_at,
                        action_time=action_time_iso,
                        action_type="投保/订票",
                    )
                    if fore_res.get("is_foreseeable") is True:
                        result["fraud_suspected"] = True
                        result["fraud_level"] = "confirmed"
                        result["reason"] = (
                            f"可预见因素时间线命中：预警发布时间({published_at})早于投保/订票时间({action_time_raw})"
                        )
                        result["note"] = "命中可预见因素时间线 => 拒赔"
                        return result
        except Exception:
            # 时间线比对失败不影响主流程，交给启发式/人工
            pass

        if invest_date and accident_date:
            days_before = (accident_date - invest_date).days
            if days_before <= 3:
                # 事故日前3天内投保，且延误原因为可预见因素
                result["fraud_suspected"] = True
                result["fraud_level"] = "suspect"
                result["reason"] = (
                    f"延误原因为可预见因素（{delay_reason[:30]}），"
                    f"投保日（{invest_date.isoformat()}）距事故日（{accident_date.isoformat()}）仅{days_before}天，"
                    "疑似在已知延误因素后投保，需人工核查"
                )
                result["note"] = "命中情形6：投保时延误因素已可预见，建议人工复核欺诈嫌疑"
            else:
                result["note"] = f"投保日距事故日{days_before}天，未达欺诈判定阈值（≤3天）"
        else:
            result["note"] = f"延误原因含可预见因素（{delay_reason[:30]}），但日期信息不足，无法自动判定，建议人工关注"

    except Exception as e:
        result["note"] = f"欺诈检测异常: {e}"

    return result


def _run_hardcheck(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    policy_excerpt: str,
    free_text: str = "",
    vision_extract: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    代码侧硬校验集合（不依赖AI，确定性判定）：
    - Skill B: 机场三字码 -> 国家/时区（境内中转免责 + 服务区域）
    - Skill C: 战争因素（维护表快速查询）
    - Skill E: 保单权益有效期（代码侧判定）
    - Skill H: 承保区域校验
    - Skill I: 材料真实性基础校验（无权威数据时跳过）
    降级策略：任何 Skill 异常不影响主流程，记录到 debug_notes
    """
    result: Dict[str, Any] = {
        "dep_airport": {},
        "arr_airport": {},
        "transit_check": {},
        "war_risk": {},
        "policy_window": {},
        "coverage_area": {},
        "evidence_check": {},
        "passenger_civil_check": {},
        "missed_connection_check": {},
        "required_materials_check": {},
        "fraud_foreseeability_check": {},
        "policy_coverage_check": {},
        "debug_notes": [],
    }

    try:
        route = (parsed or {}).get("route") or {}

        def _iata(val: Any) -> str:
            s = str(val or "").strip().upper()
            return s if s and s not in ("UNKNOWN", "NULL", "NONE") else ""

        dep_iata = _iata(route.get("dep_iata"))
        arr_iata = _iata(route.get("arr_iata"))
        transit_iata = _iata(route.get("transit_iata"))

        # 使用事故日期判断战争因素维护表时段（避免用今天日期偏移）
        accident_date_raw = str(claim_info.get("Date_of_Accident") or claim_info.get("date_of_accident") or "").strip()

        def _parse_date_str(s: str) -> Optional[Any]:
            if not s:
                return None
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
                try:
                    return datetime.strptime(s[:10], fmt).date()
                except Exception:
                    continue
            return None

        check_date = _parse_date_str(accident_date_raw)

        # Skill B: 机场解析
        if dep_iata:
            result["dep_airport"] = resolve_country(dep_iata)
        if arr_iata:
            result["arr_airport"] = resolve_country(arr_iata)

        # 阶段4/11: 境内中转免责 + 承保区域
        if transit_iata:
            result["transit_check"] = check_transit_domestic(transit_iata)
        elif dep_iata and arr_iata:
            # 非联程：仅检查起飞地是否境内（用于服务区域，非免责判定）
            result["transit_check"] = {"iata": dep_iata, "is_domestic_cn": None, "note": "非联程中转，无需境内中转免责判定"}

        # ===== 新增：纯国内航班检测（出发地和目的地均为中国大陆 → 不赔）=====
        dep_info = result.get("dep_airport") or {}
        arr_info = result.get("arr_airport") or {}
        dep_cc = dep_info.get("country_code", "")
        arr_cc = arr_info.get("country_code", "")
        dep_found = dep_info.get("found", False)
        arr_found = arr_info.get("found", False)

        if dep_iata and arr_iata and dep_found and arr_found:
            # 两端机场均已识别：判断是否纯中国大陆国内航班
            is_pure_domestic_cn = (dep_cc == "CN" and arr_cc == "CN")
            result["domestic_flight_check"] = {
                "is_pure_domestic_cn": is_pure_domestic_cn,
                "dep_iata": dep_iata,
                "arr_iata": arr_iata,
                "dep_country": dep_cc,
                "arr_country": arr_cc,
                "note": (
                    f"纯中国大陆国内航班（{dep_iata}→{arr_iata}），不在承保范围内"
                    if is_pure_domestic_cn
                    else f"含国际/境外段（{dep_iata}[{dep_cc}]→{arr_iata}[{arr_cc}]），在承保范围内"
                ),
            }
        else:
            result["domestic_flight_check"] = {
                "is_pure_domestic_cn": None,
                "dep_iata": dep_iata,
                "arr_iata": arr_iata,
                "note": "出发地或目的地机场未知，无法判定是否纯国内航班",
            }

        # 阶段7: 战争因素（用起降国家分别检查）
        war_checks = []
        for airport_info in [result.get("dep_airport"), result.get("arr_airport")]:
            cc = (airport_info or {}).get("country_code", "")
            if cc and cc != "unknown":
                war_result = check_war_table(cc, check_date=check_date)
                if war_result.get("is_war_risk"):
                    war_checks.append(war_result)
        if war_checks:
            # 任一命中则标记战争风险
            result["war_risk"] = {
                **war_checks[0],
                "note": "；".join(w.get("note", "") for w in war_checks),
            }
        else:
            result["war_risk"] = {"is_war_risk": False if (dep_iata or arr_iata) else None, "note": "未命中战争风险维护表"}

        # 阶段0-1 (Skill E): 保单权益有效期
        result["policy_window"] = lookup_effective_window(claim_info)

        # 代码侧有效期硬校验：以原出发航班计划起飞时间（planned_dep）为基准
        # 优先级：exit_datetime（出境时间）> planned_dep > all_flights_found > Date_of_Accident
        # 联程案件中若第一程起飞时间不在有效期内，尝试用延误发生段时间（alt_dep）再判断
        try:
            policy_window = result["policy_window"]
            effective_from = policy_window.get("effective_from")
            effective_to = policy_window.get("effective_to")
            is_allianz = bool(policy_window.get("is_allianz"))
            sched_local = (parsed or {}).get("schedule_local") or {}
            planned_dep_raw = str(sched_local.get("planned_dep") or "").strip()
            planned_dep_time_str: Optional[str] = None

            # 优先级0：exit_datetime（出境时间）— 从 vision_extract.evidence 或 OCR 提取
            _used_exit_datetime = False
            _exit_datetime_raw = str((vision_extract or {}).get("evidence", {}).get("exit_datetime") or "").strip()
            if _exit_datetime_raw and _exit_datetime_raw.lower() not in ("unknown", "null", "none", ""):
                planned_dep_time_str = _exit_datetime_raw
                _used_exit_datetime = True

            # 优先级1：planned_dep
            if not _used_exit_datetime and planned_dep_raw and planned_dep_raw.lower() not in ("unknown", "null", "none", ""):
                planned_dep_time_str = planned_dep_raw

            # 兜底1：planned_dep 未知时，尝试从 vision_extract.all_flights_found 中找"原航班"的日期
            # 这利用了视觉提取已成功识别出的航班日期（比 Date_of_Accident 更精确）
            _used_flights_fallback = False
            if planned_dep_time_str is None:
                _all_flights = (vision_extract or {}).get("all_flights_found") or []
                for _fl in _all_flights:
                    _role = str(_fl.get("role_hint") or "").strip()
                    if "原航班" in _role:
                        _fl_date = str(_fl.get("date") or "").strip()
                        if _fl_date and _fl_date.lower() not in ("unknown", "null", "none", ""):
                            planned_dep_time_str = _fl_date
                            _used_flights_fallback = True
                            break

            # 兜底2：all_flights_found 也没有"原航班"时，用 Date_of_Accident 作为最终兜底
            _used_accident_fallback = False
            if planned_dep_time_str is None and accident_date_raw:
                planned_dep_time_str = accident_date_raw
                _used_accident_fallback = True
            # 安联顺延规则所需字段
            first_exit_date = str(claim_info.get("First_Exit_Date") or claim_info.get("first_exit_date") or "").strip() or None
            cov_check = check_delay_in_coverage(
                delay_time=planned_dep_time_str,
                effective_from=effective_from,
                effective_to=effective_to,
                is_allianz=is_allianz,
                first_exit_date=first_exit_date,
            )
            if _used_exit_datetime:
                cov_check["basis"] = f"exit_datetime（出境时间，优先基准）: {_exit_datetime_raw}"
            elif planned_dep_raw and planned_dep_raw.lower() not in ("unknown", "null", "none", ""):
                cov_check["basis"] = "planned_dep（原出发航班计划起飞时间）"
            elif _used_flights_fallback:
                cov_check["basis"] = f"all_flights_found[原航班]（视觉提取兜底，planned_dep未知）: {planned_dep_time_str}"
            elif _used_accident_fallback:
                cov_check["basis"] = "Date_of_Accident（事故日期兜底，planned_dep及视觉提取均未知）"
            else:
                cov_check["basis"] = "unknown（无可用时间基准）"

            # 联程兜底：若第一程起飞时间不在有效期内，尝试用 alt_dep（延误发生段时间）再判断
            if cov_check.get("in_coverage") is False:
                alt_local = (parsed or {}).get("alternate_local") or {}
                alt_dep_raw = str(alt_local.get("alt_dep") or "").strip()
                itinerary = (parsed or {}).get("itinerary") or {}
                is_connecting = str(itinerary.get("is_connecting_or_transit") or "").lower() == "true"
                if is_connecting and alt_dep_raw and alt_dep_raw.lower() not in ("unknown", "null", "none", ""):
                    alt_cov_check = check_delay_in_coverage(
                        delay_time=alt_dep_raw,
                        effective_from=effective_from,
                        effective_to=effective_to,
                        is_allianz=is_allianz,
                        first_exit_date=first_exit_date,
                    )
                    if alt_cov_check.get("in_coverage") is True:
                        alt_cov_check["basis"] = "alt_dep（联程延误发生段计划起飞时间）"
                        alt_cov_check["note"] = (
                            alt_cov_check.get("note", "") +
                            f"；第一程起飞时间 {planned_dep_time_str} 不在有效期内，但联程延误发生段时间 {alt_dep_raw} 在有效期内，以延误发生段为准"
                        )
                        cov_check = alt_cov_check

            result["policy_coverage_check"] = cov_check
        except Exception as e:
            result["policy_coverage_check"] = {"in_coverage": None, "note": f"有效期校验异常: {e}"}
            result["debug_notes"].append(f"policy_coverage_check异常: {e}")

        # 阶段11 (Skill H): 承保区域
        coverage_info = lookup_coverage_area(claim_info)
        delay_iata = arr_iata or dep_iata
        area_check = check_delay_in_coverage_area(delay_iata, coverage_info)
        result["coverage_area"] = {**coverage_info, **area_check}

        # ===== 新增：航班属性硬校验（情形：货运/私人航班拒赔）=====
        flight_info = (parsed or {}).get("flight") or {}
        is_passenger_civil = _truthy(flight_info.get("is_passenger_civil"))
        result["passenger_civil_check"] = {
            "is_passenger_civil": is_passenger_civil,
            "flight_no": flight_info.get("ticket_flight_no", "unknown"),
            "note": (
                "航班属性未知，无法判断是否为民航客运班机" if is_passenger_civil is None
                else ("确认为民航客运班机" if is_passenger_civil else "非民航客运班机（货运/私人），不予赔付")
            ),
        }

        # ===== 新增：中转接驳延误检测（情形4：前序延误导致误机后续）=====
        itinerary = (parsed or {}).get("itinerary") or {}
        mention_missed_connection = _truthy(itinerary.get("mentions_missed_connection"))
        is_connecting_flight = _truthy(itinerary.get("is_connecting_or_transit"))

        # 飞常准已确认被保险航班自身延误/取消 → 理赔事由是航班自身问题，非前序延误导致误机
        # 此时不应触发中转接驳免责（即便行程是联程，被保险航班自身被取消仍应赔付）
        aviation_delay_proof = _truthy((parsed or {}).get("evidence", {}).get("aviation_delay_proof"))

        # 兜底：explanation/extraction_notes/free_text 中含接驳关键词（排除否定句）
        def _has_connecting_keyword(text: str) -> bool:
            """检查文本中是否有联程误机的肯定性表述，排除'未见/未发现/无'等否定前缀。
            只匹配明确表达"因前序航班延误导致误机后续航班"的关键词，避免"判定为联程"等中性描述误触发。
            """
            import re as _re
            t = text.lower()
            # 只保留明确"误机后续"语义的关键词，移除"联程"等宽泛词（会被"判定为联程"误触发）
            for kw in ["missed their connecting", "misconnection", "connecting flight", "接驳", "误机后续", "错过后续", "未能搭乘后续", "错过接驳"]:
                for m in _re.finditer(_re.escape(kw), t):
                    # 取关键词前10个字符，看有没有否定词
                    prefix = t[max(0, m.start()-10):m.start()]
                    if any(neg in prefix for neg in ["未见", "未发现", "未检测", "无", "not ", "no ", "未提及", "不涉及"]):
                        continue
                    return True
            return False

        explanation_text = str((parsed or {}).get("explanation") or "")
        extraction_notes = str((vision_extract or {}).get("extraction_notes") or "")
        free_text_lower = (free_text or "")
        if (
            _has_connecting_keyword(explanation_text)
            or _has_connecting_keyword(extraction_notes)
            or _has_connecting_keyword(free_text_lower)
        ):
            mention_missed_connection = True

        delay_reason = str((parsed or {}).get("delay_reason") or "").lower()
        missed_connection_keywords = ["前序", "接驳", "误机", "missed connection", "connecting", "transit delay"]
        reason_suggests_missed = any(kw in delay_reason for kw in missed_connection_keywords)

        # Vision 明确判定"非联程误机"时，作为否定证据
        vision_alt = (vision_extract or {}).get("alternate") or {}
        vision_is_connecting_missed = str(vision_alt.get("is_connecting_missed") or "").strip().lower()
        vision_denies_missed = (vision_is_connecting_missed == "false")

        is_missed_connection = (
            (mention_missed_connection is True and not vision_denies_missed)
            or (is_connecting_flight is True and reason_suggests_missed and not vision_denies_missed)
        )

        # 关键豁免：飞常准已确认被保险航班自身状态（延误/取消），理赔事由明确，不适用中转接驳免责
        aviation_delay_proof_override = False
        overbooking_override = False
        if is_missed_connection and aviation_delay_proof is True:
            is_missed_connection = False
            aviation_delay_proof_override = True

        # 关键豁免：超售（overbooking/denied boarding）导致无法登机，属于外部原因，不适用中转接驳免责
        _overbooking_keywords = ["超售", "overbooking", "overbooked", "denied boarding", "denied_boarding", "拒绝登机"]
        _all_texts = " ".join([
            str((parsed or {}).get("delay_reason") or ""),
            str((parsed or {}).get("explanation") or ""),
            str((vision_extract or {}).get("extraction_notes") or ""),
            str(free_text or ""),
        ]).lower()
        if is_missed_connection and any(kw in _all_texts for kw in _overbooking_keywords):
            is_missed_connection = False
            overbooking_override = True

        result["missed_connection_check"] = {
            "is_missed_connection": is_missed_connection,
            "mention_missed_connection": mention_missed_connection,
            "is_connecting_flight": is_connecting_flight,
            "reason_suggests_missed": reason_suggests_missed,
            "vision_denies_missed": vision_denies_missed,
            "aviation_delay_proof_override": aviation_delay_proof_override,
            "overbooking_override": overbooking_override,
            "note": (
                "前序航班延误导致无法搭乘后续接驳航班，属于免责情形4，不予赔付" if is_missed_connection
                else (
                    "飞常准已确认被保险航班自身延误/取消，理赔事由明确，豁免中转接驳免责判定"
                    if aviation_delay_proof_override
                    else (
                        "超售/拒绝登机属于外部原因，豁免中转接驳免责判定"
                        if overbooking_override
                        else "未检测到中转接驳延误特征"
                    )
                )
            ),
        }

        # ===== 新增：必备材料清单硬检查 =====
        evidence = (parsed or {}).get("evidence") or {}
        has_application_form = _truthy(evidence.get("has_application_form"))
        has_insurance_certificate = _truthy(evidence.get("has_insurance_certificate"))
        has_id_proof = _truthy(evidence.get("has_id_proof"))
        has_delay_proof = _truthy(evidence.get("has_delay_proof"))
        has_boarding_pass = _truthy(evidence.get("has_boarding_pass"))
        has_passport = _truthy(evidence.get("has_passport"))
        has_exit_entry_record = _truthy(evidence.get("has_exit_entry_record"))

        # 口径兜底：claim_info.json 往往已经由"申请表/投保信息 + 身份信息"结构化得到，
        # 若系统抽取仍误判为缺失，可按关键字段一致性将其视为"已具备"，避免误触发缺件门禁。
        try:
            if has_application_form is False:
                if (
                    str(claim_info.get("PolicyNo") or "").strip()
                    and str(claim_info.get("Applicant_Name") or "").strip()
                    and str(claim_info.get("Insurance_Company") or "").strip()
                    and str(claim_info.get("Description_of_Accident") or "").strip()
                ):
                    has_application_form = True

            if has_id_proof is False:
                if str(claim_info.get("ID_Type") or "").strip() and str(claim_info.get("ID_Number") or "").strip():
                    has_id_proof = True
        except Exception:
            # 兜底失败不影响主流程
            pass

        # 口径兜底：飞常准官方数据查到 → 直接视为已具备延误凭证，不要求补材
        try:
            if has_delay_proof is not True:
                if _truthy(evidence.get("aviation_delay_proof")) is True:
                    has_delay_proof = True
        except Exception:
            pass

        # 口径兜底：当 claim_info 的事故描述中明确包含"同一航班号 + 取消/罢工/延误"等关键信息，
        # 且 vision 未识别到承运人延误证明（has_delay_proof=false），则视为具备可用证明，避免误触发缺件门禁。
        try:
            if has_delay_proof is False:
                desc = str(claim_info.get("Description_of_Accident") or "").strip().lower()
                flight_no = str(
                    (parsed or {}).get("flight", {}).get("ticket_flight_no")
                    or (parsed or {}).get("flight", {}).get("operating_flight_no")
                    or ""
                ).strip().upper().replace(" ", "")

                # 关键词：避免误触发到"普通延误"的场景
                keywords = ["取消", "延误", "罢工", "cancel", "delay", "strike"]
                has_keyword = any(k in desc for k in keywords)

                # Vision 失败时，parsed.flight 可能为空：尝试从事故描述中提取航班号（如 LH2452）
                if not flight_no and desc:
                    m = re.search(r"\b([A-Z]{2}\d{1,5})\b", desc.upper())
                    if m:
                        flight_no = str(m.group(1)).strip().upper()

                if flight_no and has_keyword:
                    if flight_no in desc.upper() or flight_no in desc:
                        if has_boarding_pass is True or has_application_form is True or has_id_proof is True:
                            has_delay_proof = True
        except Exception:
            pass

        # ===== 口径兜底：允许"理赔通知书/申请表中填写的 Policy/Card No & ID No"作为保险凭证 =====
        # 你们定义的核心：凭证有效性主要看填写字段是否与 claim_info.json 一致（且无明显伪造/篡改痕迹）。
        # 模型偶发把"理赔通知书"误判为非凭证，这里做字段一致性兜底，避免错误缺件导致 IsAdditional=Y。
        try:
            claim_policy_no = str(claim_info.get("PolicyNo") or "").strip()
            claim_id_no = str(claim_info.get("ID_Number") or "").strip()
            parsed_policy_no = str((parsed or {}).get("policy_hint", {}).get("policy_no") or "").strip()
            parsed_id_no = str((parsed or {}).get("passenger", {}).get("id_number") or "").strip()

            if has_insurance_certificate is False:
                if (
                    claim_policy_no
                    and parsed_policy_no
                    and claim_policy_no == parsed_policy_no
                    and claim_id_no
                    and parsed_id_no
                    and claim_id_no == parsed_id_no
                    and has_id_proof is True
                ):
                    has_insurance_certificate = True
        except Exception:
            # 兜底失败不影响主流程
            pass

        missing_required = []
        if has_application_form is False:
            missing_required.append("权益补偿给付申请书")
        if has_insurance_certificate is False:
            missing_required.append("保险凭证/会员权益卡")
        if has_id_proof is False:
            missing_required.append("申请人身份证明（身份证/护照）")
        if has_delay_proof is False:
            missing_required.append("承运人延误书面证明")
        # 登机牌/机票：无登机牌（False）或 AI 无法判断（None/unknown）且飞常准未独立确认延误时，需要求补件
        # 若飞常准已通过官方数据确认航班延误，可豁免登机牌要求
        # 注意：has_boarding_pass=None 表示 AI 返回"unknown"，即材料中未见登机牌，同样需要补件
        if has_boarding_pass is not True and not (_truthy(evidence.get("aviation_delay_proof")) is True):
            missing_required.append("登机牌或电子客票行程单")
        # 护照照片页：false=明确缺失时要求补件；unknown/None=不强制（可能是境内航班）
        if has_passport is False:
            missing_required.append("被保险人护照照片页")
        # 出入境记录：false=明确缺失时要求补件；unknown/None=不强制
        if has_exit_entry_record is False:
            missing_required.append("中国海关出入境盖章页或电子出入境记录")
        result["required_materials_check"] = {
            "missing_required": missing_required,
            "has_application_form": has_application_form,
            "has_insurance_certificate": has_insurance_certificate,
            "has_id_proof": has_id_proof,
            "has_delay_proof": has_delay_proof,
            "has_boarding_pass": has_boarding_pass,
            "has_passport": has_passport,
            "has_exit_entry_record": has_exit_entry_record,
            "note": (
                f"缺少必备材料：{'、'.join(missing_required)}" if missing_required
                else "必备材料齐全"
            ),
        }

        # ===== 新增：可预见因素/欺诈检测（情形6）=====
        fraud_check = _check_foreseeability_fraud(parsed=parsed, claim_info=claim_info)
        result["fraud_foreseeability_check"] = fraud_check

        # ===== 新增：遗产继承场景检测 =====
        result["inheritance_check"] = _check_inheritance_scenario(claim_info=claim_info)

        # ===== 新增：未成年/限制行为能力人检测 =====
        result["capacity_check"] = _check_legal_capacity(claim_info=claim_info)

        # ===== 新增：姓名一致性校验 =====
        # 比对登机牌/延误证明上的乘客姓名 vs 保单被保险人姓名
        result["name_match_check"] = _check_name_match(
            parsed=parsed,
            claim_info=claim_info,
            vision_extract=vision_extract or {},
        )

        # ===== 新增：同天投保时刻校验 =====
        # 若投保日与计划起飞日为同一天，需比较具体时分秒
        # 投保时刻 >= 计划起飞时刻 → 拒赔（出境当天投保，当天航班延误不属于责任）
        result["same_day_policy_check"] = _check_same_day_policy(
            parsed=parsed,
            claim_info=claim_info,
        )

        # ===== 新增：出境地区与保险计划文本兜底匹配 =====
        # 当三字码无法确定承保区域时，用描述文本兜底判断
        result["coverage_area_text_check"] = _check_coverage_area_text(
            parsed=parsed,
            claim_info=claim_info,
            dep_iata=dep_iata,
            arr_iata=arr_iata,
            dep_info=result.get("dep_airport") or {},
            arr_info=result.get("arr_airport") or {},
        )

    except Exception as e:
        result["debug_notes"].append(f"hardcheck异常: {e}")
        LOGGER.warning(f"[_run_hardcheck] 硬校验异常: {e}", extra=log_extra(stage="fd_hardcheck", attempt=0))

    return result


def _run_payout_calc(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    policy_excerpt: str,
) -> Dict[str, Any]:
    """
    阶段10: 赔付金额预计算（代码侧，取长原则已在 computed_delay 中完成）
    """
    try:
        cd = (parsed or {}).get("computed_delay") or {}
        final_minutes = cd.get("final_minutes")
        if not isinstance(final_minutes, int) or final_minutes <= 0:
            return {
                "status": "skip",
                "note": "延误时长未知或为0，无法预计算金额",
                "final_amount": None,
            }

        insured_amount_raw = (
            claim_info.get("Insured_Amount")
            or claim_info.get("insured_amount")
        )
        claim_amount_raw = (
            claim_info.get("Claim_Amount")
            or claim_info.get("claim_amount")
        )

        def _to_float(v: Any) -> Optional[float]:
            try:
                return float(str(v).replace(",", "").strip()) if v else None
            except Exception:
                return None

        insured_amount = _to_float(insured_amount_raw)
        claim_amount = _to_float(claim_amount_raw)

        remaining_coverage_raw = (
            claim_info.get("Remaining_Coverage")
            or claim_info.get("remaining_coverage")
        )
        remaining_coverage = _to_float(remaining_coverage_raw)

        result = calculate_payout(
            delay_minutes=final_minutes,
            claim_amount=claim_amount,
            insured_amount=insured_amount,
            remaining_coverage=remaining_coverage,
            policy_terms_excerpt=policy_excerpt,
        )
        result["status"] = "calculated"
        return result
    except Exception as e:
        LOGGER.warning(f"[_run_payout_calc] 金额计算异常: {e}", extra=log_extra(stage="fd_payout", attempt=0))
        return {"status": "error", "note": str(e), "final_amount": None}


def _truthy(v: Any) -> Optional[bool]:
    if v is True:
        return True
    if v is False:
        return False
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "yes", "y", "1"}:
            return True
        if s in {"false", "no", "n", "0"}:
            return False
        if s in {"unknown", ""}:
            return None
    return None


def _parse_utc_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    s = str(value).strip()
    if not s or s.lower() == "unknown":
        return None
    # 无时区后缀的字符串不得当作 UTC（否则会把「材料上的本地时刻」误解析成 UTC，导致取长原则算出过大的延误）
    if not _has_timezone(s):
        return None
    # 常见格式：
    # - 2026-02-26 15:49Z
    # - 2026-02-26T15:49:00Z
    # - 2026-02-26 15:49:00+00:00
    s2 = s.replace(" ", "T")
    if s2.endswith("Z"):
        s2 = s2[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _has_timezone(value: str) -> bool:
    """判断时间字符串是否包含时区信息（ISO8601 格式的 +HH:MM 或 Z 结尾）"""
    if not value:
        return False
    return "Z" in value or "+" in value or value.count("-") > 3  # ISO8601 with offset


def _parse_tz_offset(tz_hint: Any) -> Optional[timezone]:
    if not tz_hint:
        return None
    s = str(tz_hint).strip()
    if not s or s.lower() == "unknown":
        return None
    # 支持：UTC+8 / UTC+08:00 / UTC-5
    m = re.search(r"UTC\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", s, flags=re.IGNORECASE)
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    hours = int(m.group(2))
    minutes = int(m.group(3) or "0")
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _parse_local_dt(value: Any, tz_hint: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    if not s or s.lower() == "unknown":
        return None
    tz = _parse_tz_offset(tz_hint)
    if tz is None:
        return None
    # 允许：YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=tz).astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _parse_local_dt_iana(value: Any, iana_tz: Optional[str]) -> Optional[datetime]:
    """将无时区的本地时间串按 IANA 时区（如 Asia/Makassar）解析为 UTC。"""
    if not value or not iana_tz or str(iana_tz).strip().lower() in ("", "unknown"):
        return None
    s = str(value).strip()
    if not s or s.lower() == "unknown":
        return None
    # 已含显式偏移的交给 _parse_utc_dt
    if _has_timezone(s):
        return None
    try:
        zi = ZoneInfo(str(iana_tz).strip())
    except Exception:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.replace(tzinfo=zi).astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _compute_delay_minutes(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    按规则"取长原则"计算延误分钟数：
    - 优先口径：planned_dep -> alt_dep，planned_arr -> alt_arr（替代航班）
    - 兜底口径：planned_dep -> actual_dep，planned_arr -> actual_arr（飞常准实际时间）
    若关键时间点无法换算为 UTC，则返回 unknown 并给出缺口说明。
    """
    utc = (parsed or {}).get("utc") or {}
    schedule_local = (parsed or {}).get("schedule_local") or {}
    alternate_local = (parsed or {}).get("alternate_local") or {}
    actual_local = (parsed or {}).get("actual_local") or {}
    # 出发地机场 IANA 时区：用于解析「无时区后缀」的材料/模型输出的本地时刻
    _route = (parsed or {}).get("route") or {}
    _dep_iata = str(_route.get("dep_iata") or "").strip().upper()
    _airport_iana: Optional[str] = None
    if _dep_iata and _dep_iata not in ("UNKNOWN", "NULL", "NONE", ""):
        _ap = resolve_country(_dep_iata)
        if _ap.get("found") and str(_ap.get("timezone") or "").lower() != "unknown":
            _airport_iana = str(_ap["timezone"])

    planned_dep_utc = (
        _parse_utc_dt(utc.get("planned_dep_utc"))
        or _parse_utc_dt(schedule_local.get("planned_dep"))
        or _parse_local_dt_iana(schedule_local.get("planned_dep"), _airport_iana)
    )
    planned_arr_utc = (
        _parse_utc_dt(utc.get("planned_arr_utc"))
        or _parse_utc_dt(schedule_local.get("planned_arr"))
        or _parse_local_dt_iana(schedule_local.get("planned_arr"), _airport_iana)
    )
    # alt/actual 时间：同样优先用显式 UTC（含时区后缀的 ISO 串），否则统一按出发地机场时区解析
    # 注意：不走 _parse_local_dt(..., timezone_hint)，因为 material_hint 多为 unknown 导致解析失败
    alt_dep_utc = (
        _parse_utc_dt(utc.get("alt_dep_utc"))
        or _parse_utc_dt(alternate_local.get("alt_dep"))
        or _parse_local_dt_iana(alternate_local.get("alt_dep"), _airport_iana)
    )
    alt_arr_utc = (
        _parse_utc_dt(utc.get("alt_arr_utc"))
        or _parse_utc_dt(alternate_local.get("alt_arr"))
        or _parse_local_dt_iana(alternate_local.get("alt_arr"), _airport_iana)
    )
    actual_dep_utc = (
        _parse_utc_dt(actual_local.get("actual_dep"))
        or _parse_local_dt_iana(actual_local.get("actual_dep"), _airport_iana)
    )
    actual_arr_utc = (
        _parse_utc_dt(actual_local.get("actual_arr"))
        or _parse_local_dt_iana(actual_local.get("actual_arr"), _airport_iana)
    )

    a_minutes: Optional[int] = None
    b_minutes: Optional[int] = None
    missing = []
    method = "max(planned_dep->alt_dep, planned_arr->alt_arr)"

    if planned_dep_utc and alt_dep_utc:
        delta = int((alt_dep_utc - planned_dep_utc).total_seconds() // 60)
        if delta >= 0:
            a_minutes = delta
    else:
        if not planned_dep_utc:
            missing.append("planned_dep(需可换算时区)")
        if not alt_dep_utc:
            missing.append("alt_dep(改签后实际起飞时间/需可换算时区)")

    if planned_arr_utc and alt_arr_utc:
        delta = int((alt_arr_utc - planned_arr_utc).total_seconds() // 60)
        if delta >= 0:
            b_minutes = delta
    else:
        if not planned_arr_utc:
            missing.append("planned_arr(需可换算时区)")
        if not alt_arr_utc:
            missing.append("alt_arr(替代抵达原目的地时间/需可换算时区)")

    candidates = [m for m in [a_minutes, b_minutes] if isinstance(m, int)]
    final_minutes = max(candidates) if candidates else None

    # 兜底：无替代航班时间时，用飞常准实际时间计算延误（planned→actual）
    if final_minutes is None and (actual_dep_utc or actual_arr_utc):
        c_minutes: Optional[int] = None
        d_minutes: Optional[int] = None
        if planned_dep_utc and actual_dep_utc:
            delta = int((actual_dep_utc - planned_dep_utc).total_seconds() // 60)
            if delta >= 0:
                c_minutes = delta
        if planned_arr_utc and actual_arr_utc:
            delta = int((actual_arr_utc - planned_arr_utc).total_seconds() // 60)
            if delta >= 0:
                d_minutes = delta
        actual_candidates = [m for m in [c_minutes, d_minutes] if isinstance(m, int)]
        if actual_candidates:
            final_minutes = max(actual_candidates)
            method = "max(planned_dep->actual_dep, planned_arr->actual_arr)[飞常准实际时间]"
            missing = []  # 有实际时间，清空缺口说明

    return {
        "a_minutes": a_minutes,
        "b_minutes": b_minutes,
        "final_minutes": final_minutes,
        "method": method,
        "missing": missing,
        "planned_dep_utc": planned_dep_utc.isoformat() if planned_dep_utc else None,
        "planned_arr_utc": planned_arr_utc.isoformat() if planned_arr_utc else None,
        "alt_dep_utc": alt_dep_utc.isoformat() if alt_dep_utc else None,
        "alt_arr_utc": alt_arr_utc.isoformat() if alt_arr_utc else None,
        "actual_dep_utc": actual_dep_utc.isoformat() if actual_dep_utc else None,
        "actual_arr_utc": actual_arr_utc.isoformat() if actual_arr_utc else None,
    }


def _parse_threshold_minutes(policy_terms_excerpt: str) -> Optional[int]:
    """
    从条款要点/摘录中提取起赔门槛（分钟）。
    - 支持：起赔标准：4小时 / 延误满2小时 / 赔付门槛4小时 等中文表述
    - 若无法解析，返回 None（上层使用默认兜底）
    """
    s = str(policy_terms_excerpt or "")
    patterns = [
        r"起赔标准\s*[:：]?\s*(\d+)\s*小时",
        r"延误满\s*(\d+)\s*小时",
        r"赔付门槛\s*[:：]?\s*(\d+)\s*小时",
        r"延误(?:时间)?\s*达(?:到)?\s*(\d+)\s*小时",
    ]
    for p in patterns:
        m = re.search(p, s)
        if m:
            try:
                return int(m.group(1)) * 60
            except Exception:
                continue
    return None


def _augment_with_computed_delay(*, parsed: Dict[str, Any], policy_terms_excerpt: str) -> Dict[str, Any]:
    parsed = dict(parsed or {})
    computed = _compute_delay_minutes(parsed)
    threshold_minutes = _parse_threshold_minutes(policy_terms_excerpt) or 5 * 60

    computed["threshold_minutes"] = threshold_minutes
    computed["threshold_source"] = "policy_terms_excerpt" if _parse_threshold_minutes(policy_terms_excerpt) else "default(5h)"
    computed["threshold_met"] = (
        isinstance(computed.get("final_minutes"), int) and computed["final_minutes"] >= threshold_minutes
    )
    parsed["computed_delay"] = computed
    return parsed


def _postprocess_audit_result(
    *,
    parsed: Dict[str, Any],
    audit: Dict[str, Any],
    policy_terms_excerpt: str,
    hardcheck: Optional[Dict[str, Any]] = None,
    payout_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    对模型输出做轻量确定性修正（集成全部硬校验结果）：
    - 若缺少书面延误证明或证明缺关键字段：强制不直接拒赔，改为"需补齐资料"
    - 若关键时间点不足以计算/核验延误：倾向"需补齐资料"
    - 代码侧硬校验：延误时长/阈值（取长原则）
    - 集成 hardcheck：战争因素/境内中转/承保区域 命中时强制拒赔
    - 集成 payout_result：写回代码计算的赔付金额
    """
    try:
        # 1) 阈值硬门禁：未达门槛不得"通过"；若可计算且未达门槛，则应拒赔
        parsed = _augment_with_computed_delay(parsed=parsed or {}, policy_terms_excerpt=policy_terms_excerpt or "")
        cd = (parsed or {}).get("computed_delay") or {}
        final_minutes = cd.get("final_minutes")
        threshold_minutes = cd.get("threshold_minutes") or 5 * 60
        threshold_met = bool(cd.get("threshold_met")) if isinstance(final_minutes, int) else None

        current = str((audit or {}).get("audit_result") or "").strip()

        # 1.1 统一把代码计算出的延误分钟写回 audit.key_data（避免模型计算漂移）
        audit = dict(audit or {})
        audit.setdefault("key_data", {})
        if isinstance(audit["key_data"], dict) and isinstance(final_minutes, int):
            audit["key_data"]["delay_duration_minutes"] = final_minutes
        audit.setdefault("logic_check", {})
        if isinstance(audit["logic_check"], dict) and threshold_met is not None:
            audit["logic_check"]["threshold_met"] = threshold_met

        # 1.2 explanation 兜底：若 AI 返回 JSON 被截断导致 explanation 缺失，用 key_data 构造基本说明
        if not str(audit.get("explanation") or "").strip():
            kd = audit.get("key_data") or {}
            name = kd.get("passenger_name", "")
            mins = kd.get("delay_duration_minutes", "")
            reason = kd.get("reason", "")
            parts = []
            if name:
                parts.append(f"被保险人：{name}")
            if mins:
                parts.append(f"延误时长：{mins}分钟")
            if reason:
                parts.append(f"延误原因：{reason}")
            if parts:
                audit["explanation"] = "；".join(parts)

        # ── 优先级1：硬免责条款检查（最高优先级，命中立即拒赔，不受门槛/材料影响）──
        if hardcheck:
            # 保单有效期不符（以原出发航班计划起飞时间为基准）
            policy_cov = hardcheck.get("policy_coverage_check") or {}
            if policy_cov.get("in_coverage") is False:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["policy_coverage_out_triggered"] = True
                cov_note = str(policy_cov.get("note") or "原出发航班计划起飞时间不在保单有效期内")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【超出有效期】{cov_note}"
                return audit

            # 纯中国大陆国内航班 → 不赔
            domestic_check = hardcheck.get("domestic_flight_check") or {}
            if domestic_check.get("is_pure_domestic_cn") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["pure_domestic_cn_triggered"] = True
                dep = domestic_check.get("dep_iata", "")
                arr = domestic_check.get("arr_iata", "")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【纯国内航班不赔】出发地 {dep} 和目的地 {arr} 均在中国大陆，本保险仅承保含国际/境外段的航班"
                return audit

            # 战争因素
            war_risk = hardcheck.get("war_risk") or {}
            if war_risk.get("is_war_risk"):
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["war_risk_triggered"] = True
                war_note = war_risk.get("note", "命中战争/冲突风险维护表")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【战争因素免责】{war_note}"
                return audit

            # 承保区域不符（三字码判定）
            coverage = hardcheck.get("coverage_area") or {}
            if coverage.get("in_coverage") is False:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["coverage_out_of_area_triggered"] = True
                cov_note = str(coverage.get("note") or "超出承保区域，不予赔付")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【承保区域不符】{cov_note}"
                return audit

            # 承保区域不符（文本兜底判定）
            coverage_text = hardcheck.get("coverage_area_text_check") or {}
            if coverage_text.get("in_coverage") is False:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["coverage_text_out_of_area_triggered"] = True
                cov_note = str(coverage_text.get("note") or "出境地区不在保险计划承保范围内")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【承保区域不符】{cov_note}"
                return audit

            # 境内中转免责
            transit = hardcheck.get("transit_check") or {}
            if transit.get("is_domestic_cn") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["transit_domestic_triggered"] = True
                iata = transit.get("iata", "")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【境内中转免责】中转地 {iata} 在境内，不予赔付"
                return audit

            # 中转接驳延误免责
            missed_conn = hardcheck.get("missed_connection_check") or {}
            if missed_conn.get("is_missed_connection") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["missed_connection_triggered"] = True
                audit["audit_result"] = "拒绝"
                audit["explanation"] = "【中转接驳免责】前序航班延误导致无法搭乘后续接驳航班，不予赔付"
                return audit

            # 非民航客运班机
            passenger_check = hardcheck.get("passenger_civil_check") or {}
            if passenger_check.get("is_passenger_civil") is False:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["non_passenger_civil_triggered"] = True
                fn = passenger_check.get("flight_no", "")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【非客运航班】航班 {fn} 非民航客运班机，不在赔付范围内"
                return audit

            # 遗产继承场景 → 标记人工复核（不硬拒赔）
            inheritance_check = hardcheck.get("inheritance_check") or {}
            if inheritance_check.get("is_inheritance_suspected"):
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["inheritance_suspected"] = True
                note = str(inheritance_check.get("note") or "申请人与被保险人不一致，疑似遗产继承场景")
                # 不直接拒赔，改为补材
                if str(audit.get("audit_result") or "") not in ("拒绝",):
                    audit["audit_result"] = "需补齐资料"
                    audit["explanation"] = f"【疑似遗产继承】{note}，请补充合法继承权证明文件（遗嘱/亲属关系证明等）"
                    return audit

            # 未成年/限制行为能力人 → 要求补充监护人证明
            capacity_check = hardcheck.get("capacity_check") or {}
            if capacity_check.get("needs_guardian"):
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["needs_guardian"] = True
                note = str(capacity_check.get("note") or "被保险人为未成年人或限制民事行为能力人")
                if str(audit.get("audit_result") or "") not in ("拒绝",):
                    audit["audit_result"] = "需补齐资料"
                    audit["explanation"] = f"【需监护人材料】{note}，请补充监护人身份证明及监护关系证明"
                    return audit

            # 同天投保时刻校验
            same_day_check = hardcheck.get("same_day_policy_check") or {}
            if same_day_check.get("is_denied") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["same_day_policy_triggered"] = True
                note = str(same_day_check.get("note") or "出境当天投保，投保时刻不早于计划起飞时刻，不属于保障责任")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【同天投保免责】{note}"
                return audit

            # 姓名不一致 → 拒赔
            name_check = hardcheck.get("name_match_check") or {}
            if name_check.get("match_result") == "mismatch":
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["name_mismatch_triggered"] = True
                note = str(name_check.get("note") or "登机牌/延误证明上的乘客姓名与保单被保险人姓名不符")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【姓名不符】{note}"
                return audit

            # 可预见因素/欺诈（confirmed 级别才硬拒赔）
            fraud_check = hardcheck.get("fraud_foreseeability_check") or {}
            if fraud_check.get("fraud_suspected") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["fraud_suspected"] = True
                fraud_reason = str(fraud_check.get("reason") or "").strip() or str(fraud_check.get("note") or "").strip()
                fraud_level = str(fraud_check.get("fraud_level") or "none").strip().lower()
                if fraud_level == "confirmed":
                    if isinstance(audit["logic_check"], dict):
                        audit["logic_check"]["exclusion_triggered"] = True
                    audit["audit_result"] = "拒绝"
                    audit["explanation"] = f"【可预见因素免责】{fraud_reason}"
                    return audit
                # suspect：仅标注，不拦截，继续后续判断
                audit.setdefault("logic_check", {})
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = False

        # ── 优先级2：延误时长门槛硬检查 ──
        if isinstance(final_minutes, int) and final_minutes < int(threshold_minutes):
            audit["audit_result"] = "拒绝"
            audit["explanation"] = (
                f"【未达起赔门槛】延误时长{final_minutes}分钟，未达起赔门槛{int(threshold_minutes)}分钟（取长原则计算），不予赔付。"
            )
            return audit

        # ── 优先级3：必备材料缺失 → 补材 ──
        if hardcheck:
            req_check = hardcheck.get("required_materials_check") or {}
            missing_required = req_check.get("missing_required") or []
            if missing_required:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["required_materials_missing"] = missing_required
                missing_str = "、".join(missing_required)
                audit["audit_result"] = "需补齐资料"
                audit["explanation"] = f"【必备材料缺失】请补充：{missing_str}"
                return audit

        # ── 写回代码计算的赔付金额 ──
        if payout_result and payout_result.get("status") == "calculated":
            final_amount = payout_result.get("final_amount")
            if isinstance(final_amount, (int, float)) and final_amount >= 0:
                audit.setdefault("payout_suggestion", {})
                if isinstance(audit["payout_suggestion"], dict):
                    audit["payout_suggestion"]["amount"] = final_amount
                    audit["payout_suggestion"]["currency"] = payout_result.get("currency", "CNY")
                    audit["payout_suggestion"]["basis"] = payout_result.get("basis", "")
                    audit["payout_suggestion"]["source"] = "code_calculated"

        return audit
    except Exception:
        return audit


# ─────────────────────────────────────────────────────────────────────────────
# 重复理赔检测
# ─────────────────────────────────────────────────────────────────────────────

# 已结案的状态关键词（含英文/中文）
_CONCLUDED_STATUS_KEYWORDS = [
    # 赔付相关
    "通过", "赔付", "已赔", "已付", "支付成功", "已赔付", "已支付",
    "approved", "paid", "settled",
    # 拒赔相关
    "拒绝", "拒赔", "事后理赔拒赔",
    "denied", "rejected", "declined",
    # 结案相关
    "结案", "closed", "concluded", "completed",
]


def _is_concluded_status(status: Any) -> bool:
    """判断某个案件状态是否已结案（已给出最终审批结论）。"""
    if status is None:
        return False
    s = str(status).strip().lower()
    if not s or s in ("", "null", "none", "unknown", "pending", "in_review", "待审", "审核中", "处理中"):
        return False
    return any(kw in s for kw in _CONCLUDED_STATUS_KEYWORDS)


def _is_same_event(current: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """
    检查两个案件是否为同一事件。

    匹配规则（4个维度，都需满足）：
    1. ID_Number 相同（同一被保险人）
    2. Product_Name 相同（同一保险产品）
    3. BenefitName 相同（同一保险类型，如航班延误）
    4. Date_of_Accident 相同（同一事故日期，null 不作判断依据）
    注：Description_of_Accident 不作强匹配，用户填写习惯不同可能导致描述有差异

    Args:
        current: 当前案件信息
        candidate: 历史案件信息

    Returns:
        True 表示为同一事件，False 表示不同事件
    """
    # 1. ID_Number 必须相同（同一人）
    current_id = (current.get("ID_Number") or "").strip()
    candidate_id = (candidate.get("ID_Number") or "").strip()
    if not current_id or not candidate_id or current_id != candidate_id:
        return False

    # 2. Product_Name 必须相同（同一保险产品）
    current_product = (current.get("Product_Name") or "").strip()
    candidate_product = (candidate.get("Product_Name") or "").strip()
    if not current_product or not candidate_product or current_product != candidate_product:
        return False

    # 3. BenefitName 必须相同（同一保险类型）
    current_benefit = (current.get("BenefitName") or "").strip()
    candidate_benefit = (candidate.get("BenefitName") or "").strip()
    if not current_benefit or not candidate_benefit or current_benefit != candidate_benefit:
        return False

    # 4. Date_of_Accident 必须相同（同一事故日期；null 不作判断依据）
    current_date = (current.get("Date_of_Accident") or "").strip()[:10]
    candidate_date = (candidate.get("Date_of_Accident") or "").strip()[:10]
    if current_date and candidate_date and current_date != candidate_date:
        return False

    # 5. Description_of_Accident：不作强匹配
    # 描述文字因用户填写习惯不同可能有差异，不用于排除同一事件

    return True


def _check_duplicate_claim(
    claim_info: Dict[str, Any],
    forceid: str,
) -> Optional[Dict[str, Any]]:
    """
    检测重复理赔（SamePolicyClaim 字段）。

    规则：
    1. 如果 SamePolicyClaim 中有同一事件的已结案案件 → 直接拒赔
    2. 如果 SamePolicyClaim 中有同一事件但未结案的案件 → 继续审核，但标记为"以最新案件为主"
    3. 如果 SamePolicyClaim 中没有同一事件 → 正常走审核流程

    同一事件判断（5个维度都需满足）：
    - ID_Number 相同（同一被保险人）
    - Product_Name 相同（同一保险产品）
    - BenefitName 相同（同一保险类型）
    - Date_of_Accident 相同（同一事故日期，null 不作判断依据）
    - Description_of_Accident 一致（null 不作判断依据）

    返回值：
    - None：无重复理赔，继续正常审核
    - dict：重复理赔早退结果或标记信息（直接返回给调用方）
    """
    same_policy = claim_info.get("SamePolicyClaim")
    if same_policy is None:
        return None

    # 兼容单个对象（非列表）情况
    if isinstance(same_policy, dict):
        same_policy = [same_policy]

    if not isinstance(same_policy, list) or not same_policy:
        return None

    # 遍历 SamePolicyClaim，检查是否存在同一事件
    concluded_match = None  # 已结案的同一事件
    unconcluded_matches = []  # 未结案的同一事件列表

    for item in same_policy:
        if not isinstance(item, dict):
            continue

        # 检查是否为同一事件（4维度匹配）
        if not _is_same_event(claim_info, item):
            continue

        # 尝试多种字段名取案件号
        claim_id = (
            item.get("ClaimId")
            or item.get("claim_id")
            or item.get("CaseNo")
            or item.get("case_no")
            or item.get("Id")
            or item.get("id")
            or ""
        )
        claim_id = str(claim_id).strip()

        # 尝试多种字段名取状态（优先取 Final_Status）
        status = (
            item.get("Final_Status")
            or item.get("final_status")
            or item.get("Status")
            or item.get("status")
            or item.get("AuditResult")
            or item.get("audit_result")
            or item.get("Result")
            or item.get("result")
            or item.get("Conclusion")
            or item.get("conclusion")
        )

        if _is_concluded_status(status):
            # 找到已结案的同一事件 → 记录并立即返回拒赔
            if not concluded_match:
                concluded_match = (claim_id, status)
        else:
            # 未结案的同一事件 → 记录到列表
            unconcluded_matches.append((claim_id, status))

    # 场景1：已结案的同一事件 → 直接拒赔
    if concluded_match:
        claim_id, status = concluded_match
        ref_no = f"#{claim_id}" if claim_id else "已有案件"
        reason = f"重复理赔：您本次申请的理赔已在{ref_no}做出赔付结论。根据一事不二理原则，本次重复申请不予赔付。"
        remark = f"航班延误: 拒绝。{reason}"
        return {
            "forceid": forceid,
            "claim_type": "flight_delay",
            "Remark": remark,
            "IsAdditional": "N",
            "KeyConclusions": [
                {
                    "checkpoint": "重复理赔检测",
                    "Eligible": "N",
                    "Remark": remark,
                }
            ],
            "flight_delay_audit": {
                "audit_result": "拒绝",
                "explanation": reason,
                "logic_check": {
                    "exclusion_triggered": True,
                    "duplicate_claim_triggered": True,
                    "duplicate_ref_claim_id": claim_id,
                },
            },
            "DebugInfo": {
                "debug": [],
                "flight_delay": None,
                "duplicate_check": {
                    "triggered": True,
                    "scenario": "concluded",
                    "ref_claim_id": claim_id,
                    "ref_status": str(status),
                    "reason": reason,
                },
            },
            "reason": reason,
        }

    # 场景2：未结案的同一事件 → 直接拒赔（严审模式：可以错杀，不要错放）
    if unconcluded_matches:
        claim_id, status = unconcluded_matches[0]
        ref_no = f"#{claim_id}" if claim_id else "已有案件"
        reason = f"重复理赔：您本次申请的理赔与{ref_no}为同一事件（审核中）。根据一事不二理原则，本次重复申请不予赔付。"
        remark = f"航班延误: 拒绝。{reason}"
        return {
            "forceid": forceid,
            "claim_type": "flight_delay",
            "Remark": remark,
            "IsAdditional": "N",
            "KeyConclusions": [
                {
                    "checkpoint": "重复理赔检测",
                    "Eligible": "N",
                    "Remark": remark,
                }
            ],
            "flight_delay_audit": {
                "audit_result": "拒绝",
                "explanation": reason,
                "logic_check": {
                    "exclusion_triggered": True,
                    "duplicate_claim_triggered": True,
                    "duplicate_ref_claim_id": claim_id,
                },
            },
            "DebugInfo": {
                "debug": [],
                "flight_delay": None,
                "duplicate_check": {
                    "triggered": True,
                    "scenario": "unconcluded",
                    "ref_claim_id": claim_id,
                    "ref_status": str(status),
                    "reason": reason,
                },
            },
            "reason": reason,
        }

    # 场景3：SamePolicyClaim 中无同一事件 → 正常走审核流程
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 硬免责前置拦截（在 AI 判定之前执行）
# ─────────────────────────────────────────────────────────────────────────────

def _check_inheritance_scenario(claim_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    遗产继承场景检测：
    若申请人与被保险人姓名/证件号不一致，且无委托代办标志，
    则疑似遗产继承场景，需补充合法继承权证明文件。

    Returns:
        {
            "is_inheritance_suspected": bool,
            "applicant_name": str,
            "insured_name": str,
            "applicant_id": str,
            "insured_id": str,
            "note": str,
        }
    """
    # 申请人信息
    applicant_name = str(claim_info.get("Applicant_Name") or claim_info.get("applicant_name") or "").strip()
    applicant_id = str(claim_info.get("Applicant_ID") or claim_info.get("applicant_id") or "").strip()

    # 被保险人信息
    insured_name = str(
        claim_info.get("Insured_Name") or claim_info.get("insured_name")
        or claim_info.get("BeneficiaryName") or ""
    ).strip()
    insured_id = str(
        claim_info.get("ID_Number") or claim_info.get("id_number")
        or claim_info.get("Insured_ID") or ""
    ).strip()

    # 若任一侧信息缺失，无法判断
    if not applicant_name or not insured_name:
        return {
            "is_inheritance_suspected": False,
            "applicant_name": applicant_name or "unknown",
            "insured_name": insured_name or "unknown",
            "applicant_id": applicant_id or "unknown",
            "insured_id": insured_id or "unknown",
            "note": "申请人或被保险人姓名缺失，无法判断是否为遗产继承场景",
        }

    def _norm(s: str) -> str:
        return re.sub(r"[\s\-]", "", s).upper()

    name_match = _norm(applicant_name) == _norm(insured_name)
    id_match = (not applicant_id or not insured_id) or (_norm(applicant_id) == _norm(insured_id))

    if name_match and id_match:
        return {
            "is_inheritance_suspected": False,
            "applicant_name": applicant_name,
            "insured_name": insured_name,
            "applicant_id": applicant_id,
            "insured_id": insured_id,
            "note": "申请人与被保险人一致，非遗产继承场景",
        }

    # 姓名或证件号不一致 → 疑似遗产继承
    reason_parts = []
    if not name_match:
        reason_parts.append(f"姓名不一致（申请人={applicant_name}，被保险人={insured_name}）")
    if applicant_id and insured_id and _norm(applicant_id) != _norm(insured_id):
        reason_parts.append(f"证件号不一致（申请人={applicant_id}，被保险人={insured_id}）")

    return {
        "is_inheritance_suspected": True,
        "applicant_name": applicant_name,
        "insured_name": insured_name,
        "applicant_id": applicant_id,
        "insured_id": insured_id,
        "note": "；".join(reason_parts) + "，疑似遗产继承场景，需补充合法继承权证明文件",
    }


def _check_legal_capacity(claim_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    未成年/限制民事行为能力人检测：
    从被保险人证件号（身份证/护照）提取出生年月，判断是否未成年（< 18岁）。
    未成年人需补充监护人身份证明及监护关系证明。

    支持：
    - 中国大陆身份证（18位，第7-14位为出生日期 YYYYMMDD）
    - 港澳台/外籍：无法从证件号提取年龄，降级为 unknown

    Returns:
        {
            "needs_guardian": bool | None,
            "age": int | None,
            "id_type": str,
            "id_number": str,
            "note": str,
        }
    """
    from datetime import date as _date

    id_type = str(claim_info.get("ID_Type") or claim_info.get("id_type") or "").strip()
    id_number = str(claim_info.get("ID_Number") or claim_info.get("id_number") or "").strip()

    if not id_number:
        return {
            "needs_guardian": None,
            "age": None,
            "id_type": id_type or "unknown",
            "id_number": "unknown",
            "note": "证件号缺失，无法判断是否为未成年人",
        }

    # 中国大陆身份证：18位，第7-14位为 YYYYMMDD
    is_cn_id = (
        re.fullmatch(r"\d{17}[\dXx]", id_number)
        and (not id_type or any(kw in id_type for kw in ["身份证", "ID", "居民"]))
    )

    if is_cn_id:
        try:
            birth_str = id_number[6:14]  # YYYYMMDD
            birth_date = datetime.strptime(birth_str, "%Y%m%d").date()
            today = _date.today()
            # 精确计算年龄
            age = today.year - birth_date.year - (
                (today.month, today.day) < (birth_date.month, birth_date.day)
            )
            if age < 18:
                return {
                    "needs_guardian": True,
                    "age": age,
                    "id_type": id_type or "身份证",
                    "id_number": id_number,
                    "note": f"被保险人年龄{age}岁，为未成年人，需补充监护人身份证明及监护关系证明",
                }
            elif age < 0 or age > 120:
                return {
                    "needs_guardian": None,
                    "age": None,
                    "id_type": id_type or "身份证",
                    "id_number": id_number,
                    "note": f"从证件号解析的年龄({age}岁)异常，建议人工核查",
                }
            else:
                return {
                    "needs_guardian": False,
                    "age": age,
                    "id_type": id_type or "身份证",
                    "id_number": id_number,
                    "note": f"被保险人年龄{age}岁，具备完全民事行为能力",
                }
        except Exception:
            pass

    # 护照/港澳台/外籍：无法从证件号提取年龄
    return {
        "needs_guardian": None,
        "age": None,
        "id_type": id_type or "unknown",
        "id_number": id_number,
        "note": f"证件类型({id_type or '未知'})无法从证件号提取年龄，如申请人为未成年人请人工核查",
    }


def _check_name_match(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    vision_extract: Dict[str, Any],
) -> Dict[str, Any]:
    """
    校验登机牌/延误证明上的乘客姓名与保单被保险人姓名是否一致。

    姓名来源优先级：
    - 材料侧：vision_extract.passenger_name > parsed.passenger.name
    - 保单侧：claim_info.Insured_Name > claim_info.Applicant_Name > claim_info.insured_name

    匹配规则：
    - 忽略大小写、空格、中间名缩写差异
    - 中文姓名：完全匹配
    - 英文姓名：姓+名均出现即视为匹配（顺序不限）
    - 任一侧姓名未知 → unknown，不拒赔，建议人工复核

    Returns:
        {
            "match_result": "match" | "mismatch" | "unknown",
            "material_name": str,
            "policy_name": str,
            "note": str,
        }
    """
    # 从材料侧提取姓名
    material_name = ""
    v_passenger = vision_extract.get("passenger_name") or ""
    if not _is_unknown(str(v_passenger).strip()):
        material_name = str(v_passenger).strip()
    if not material_name:
        p_passenger = (parsed or {}).get("passenger") or {}
        p_name = str(p_passenger.get("name") or p_passenger.get("passenger_name") or "").strip()
        if not _is_unknown(p_name):
            material_name = p_name

    # 从保单侧提取姓名
    policy_name = ""
    for field in ("Insured_Name", "insured_name", "Applicant_Name", "applicant_name", "BeneficiaryName"):
        v = str(claim_info.get(field) or "").strip()
        if v and not _is_unknown(v):
            policy_name = v
            break

    if not material_name or not policy_name:
        return {
            "match_result": "unknown",
            "material_name": material_name or "unknown",
            "policy_name": policy_name or "unknown",
            "note": "姓名信息不足（材料侧或保单侧姓名未知），无法比对，建议人工复核",
        }

    def _normalize(name: str) -> str:
        return re.sub(r"[\s\-·•/]", "", name).upper()

    m_norm = _normalize(material_name)
    p_norm = _normalize(policy_name)

    # 完全匹配（忽略空格/连字符/大小写）
    if m_norm == p_norm:
        return {
            "match_result": "match",
            "material_name": material_name,
            "policy_name": policy_name,
            "note": f"姓名一致：材料={material_name}，保单={policy_name}",
        }

    # 英文姓名宽松匹配：将姓名拆成词组，检查双方词组是否互相包含
    # 处理"ZHANG SAN" vs "SAN ZHANG" 或 "ZHANG/SAN" 等格式
    def _name_tokens(name: str) -> set:
        tokens = set(re.split(r"[\s\-·•/,]+", name.upper()))
        # 过滤单字母缩写（中间名缩写）
        return {t for t in tokens if len(t) > 1}

    m_tokens = _name_tokens(material_name)
    p_tokens = _name_tokens(policy_name)

    if m_tokens and p_tokens:
        # 双方主要词组互相包含（允许一方多出中间名等）
        if m_tokens <= p_tokens or p_tokens <= m_tokens or m_tokens == p_tokens:
            return {
                "match_result": "match",
                "material_name": material_name,
                "policy_name": policy_name,
                "note": f"姓名宽松匹配一致：材料={material_name}，保单={policy_name}",
            }
        # 至少有一个主要词（姓或名）相同
        if len(m_tokens & p_tokens) >= 1 and (len(m_tokens) <= 2 or len(p_tokens) <= 2):
            return {
                "match_result": "match",
                "material_name": material_name,
                "policy_name": policy_name,
                "note": f"姓名部分匹配（含姓或名）：材料={material_name}，保单={policy_name}，建议人工确认",
            }

    return {
        "match_result": "mismatch",
        "material_name": material_name,
        "policy_name": policy_name,
        "note": f"姓名不符：材料上乘客姓名={material_name}，保单被保险人姓名={policy_name}，请核实是否为同一人",
    }


def _check_same_day_policy(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
) -> Dict[str, Any]:
    """
    同天投保时刻校验：
    若投保日（Effective_Date）与计划起飞日（planned_dep）为同一天，
    则比较具体时分秒：
    - 投保时刻 >= 计划起飞时刻 → is_denied=True（出境当天投保，当天航班延误不属于责任）
    - 投保时刻 < 计划起飞时刻 → is_denied=False（投保在先，属于保障责任）
    - 任一时刻无法解析到分钟精度 → is_denied=None，建议人工复核

    Returns:
        {
            "is_denied": bool | None,
            "effective_datetime": str,
            "planned_dep": str,
            "same_day": bool | None,
            "note": str,
        }
    """
    from app.skills.policy_booking import _parse_datetime_str

    # 投保时刻（需精确到时分秒）
    effective_raw = str(
        claim_info.get("Effective_Date")
        or claim_info.get("effective_date")
        or claim_info.get("Insurance_Period_From")
        or claim_info.get("Policy_Start_Date")
        or ""
    ).strip()

    # 计划起飞时刻
    sched_local = (parsed or {}).get("schedule_local") or {}
    planned_dep_raw = str(sched_local.get("planned_dep") or "").strip()

    ef_dt = _parse_datetime_str(effective_raw)
    dep_dt = _parse_datetime_str(planned_dep_raw)

    if ef_dt is None or dep_dt is None:
        return {
            "is_denied": None,
            "effective_datetime": effective_raw or "unknown",
            "planned_dep": planned_dep_raw or "unknown",
            "same_day": None,
            "note": "投保时刻或计划起飞时刻缺失/无法解析，无法判定同天投保，建议人工复核",
        }

    same_day = (ef_dt.date() == dep_dt.date())

    if not same_day:
        return {
            "is_denied": False,
            "effective_datetime": ef_dt.isoformat(),
            "planned_dep": dep_dt.isoformat(),
            "same_day": False,
            "note": f"投保日({ef_dt.date()})与计划起飞日({dep_dt.date()})不同天，无需同天投保校验",
        }

    # 同天：比较时分秒
    # 投保时刻精度：若 Effective_Date 仅含日期（无时分秒），视为当天 00:00:00
    # 这是保守原则：若投保时刻不明确，默认为当天最早时刻，不轻易拒赔
    ef_has_time = len(effective_raw.replace("-", "").replace("/", "").replace(" ", "").replace("T", "")) > 8
    if not ef_has_time:
        # 仅有日期，无法判定时刻先后，交人工
        return {
            "is_denied": None,
            "effective_datetime": ef_dt.isoformat(),
            "planned_dep": dep_dt.isoformat(),
            "same_day": True,
            "note": f"投保日与计划起飞日同为{ef_dt.date()}，但投保时刻精度不足（仅含日期），无法判定时刻先后，建议人工复核",
        }

    is_denied = ef_dt >= dep_dt

    if is_denied:
        note = (
            f"出境当天投保：投保时刻({ef_dt.strftime('%Y-%m-%d %H:%M:%S')}) "
            f">= 计划起飞时刻({dep_dt.strftime('%Y-%m-%d %H:%M:%S')})，"
            "出境当天投保的航班延误不属于保障责任"
        )
    else:
        note = (
            f"同天投保但投保在先：投保时刻({ef_dt.strftime('%Y-%m-%d %H:%M:%S')}) "
            f"< 计划起飞时刻({dep_dt.strftime('%Y-%m-%d %H:%M:%S')})，属于保障责任"
        )

    return {
        "is_denied": is_denied,
        "effective_datetime": ef_dt.isoformat(),
        "planned_dep": dep_dt.isoformat(),
        "same_day": True,
        "note": note,
    }


def _check_coverage_area_text(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    dep_iata: str,
    arr_iata: str,
    dep_info: Dict[str, Any],
    arr_info: Dict[str, Any],
) -> Dict[str, Any]:
    """
    出境地区与保险计划文本兜底匹配。
    当三字码无法确定承保区域时（coverage_area.in_coverage=None），
    用保险计划名称/产品名称/承保区域描述文本做关键词匹配，
    判断出发地/目的地是否在承保范围内。

    规则：
    - 若保险计划含"全球/global/worldwide" → 全球覆盖，通过
    - 若保险计划含"亚洲/Asia" → 检查出发地/目的地是否在亚洲
    - 若保险计划含"欧洲/Europe" → 检查出发地/目的地是否在欧洲
    - 若保险计划含"美洲/America" → 检查出发地/目的地是否在美洲
    - 若保险计划含"中国大陆/Mainland" → 仅限中国大陆出发，国内航班不赔（已由 domestic_flight_check 处理）
    - 无法判断 → unknown，建议人工复核

    Returns:
        {
            "in_coverage": bool | None,
            "region_hint": str,
            "dep_country": str,
            "arr_country": str,
            "note": str,
        }
    """
    # 收集所有可用的保险计划/区域描述文本
    text_sources = [
        str(claim_info.get("Product_Name") or ""),
        str(claim_info.get("BenefitName") or ""),
        str(claim_info.get("Coverage_Area") or ""),
        str(claim_info.get("coverage_area") or ""),
        str(claim_info.get("Plan_Name") or ""),
        str(claim_info.get("plan_name") or ""),
        str(claim_info.get("Insurance_Company") or ""),
    ]
    combined = " ".join(text_sources).lower()

    dep_cc = str(dep_info.get("country_code") or "").upper()
    arr_cc = str(arr_info.get("country_code") or "").upper()
    dep_found = dep_info.get("found", False)
    arr_found = arr_info.get("found", False)

    # 区域关键词映射：区域名 → 该区域的国家代码集合（常见国家）
    _ASIA_CC = {
        "CN", "JP", "KR", "TH", "SG", "MY", "ID", "PH", "VN", "IN",
        "HK", "MO", "TW", "MM", "KH", "LA", "BD", "NP", "LK", "PK",
        "MN", "KZ", "UZ", "AZ", "GE", "AM", "TJ", "TM", "KG",
        "AE", "SA", "QA", "KW", "BH", "OM", "JO", "IL", "TR", "IR", "IQ",
    }
    _EUROPE_CC = {
        "GB", "FR", "DE", "IT", "ES", "NL", "BE", "CH", "AT", "SE",
        "NO", "DK", "FI", "PT", "GR", "PL", "CZ", "HU", "RO", "BG",
        "HR", "SK", "SI", "EE", "LV", "LT", "IE", "LU", "MT", "CY",
        "IS", "AL", "BA", "ME", "MK", "RS", "UA", "BY", "MD", "RU",
    }
    _AMERICA_CC = {
        "US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "VE", "EC",
        "BO", "PY", "UY", "GY", "SR", "CU", "DO", "JM", "HT", "TT",
        "PA", "CR", "GT", "HN", "SV", "NI", "BZ",
    }
    _AFRICA_CC = {
        "ZA", "EG", "NG", "KE", "ET", "GH", "TZ", "UG", "MZ", "ZM",
        "ZW", "AO", "CM", "CI", "SN", "MG", "TN", "MA", "DZ", "LY",
    }
    _OCEANIA_CC = {"AU", "NZ", "FJ", "PG", "SB", "VU", "WS", "TO", "KI", "FM"}

    def _country_in_region(cc: str, region_set: set) -> bool:
        return cc in region_set

    # 全球覆盖
    if any(kw in combined for kw in ["全球", "global", "worldwide", "全世界"]):
        return {
            "in_coverage": True,
            "region_hint": "全球",
            "dep_country": dep_cc,
            "arr_country": arr_cc,
            "note": "保险计划为全球覆盖，出境地区在承保范围内",
        }

    # 亚洲
    if any(kw in combined for kw in ["亚洲", "asia", "亚太", "asia pacific", "apac"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _ASIA_CC) or _country_in_region(arr_cc, _ASIA_CC)
            return {
                "in_coverage": in_cov,
                "region_hint": "亚洲/亚太",
                "dep_country": dep_cc,
                "arr_country": arr_cc,
                "note": (
                    f"保险计划承保亚洲/亚太，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})"
                    + ("在承保区域内" if in_cov else "均不在亚洲/亚太承保区域内")
                ),
            }

    # 欧洲
    if any(kw in combined for kw in ["欧洲", "europe", "欧盟", "schengen", "申根"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _EUROPE_CC) or _country_in_region(arr_cc, _EUROPE_CC)
            return {
                "in_coverage": in_cov,
                "region_hint": "欧洲",
                "dep_country": dep_cc,
                "arr_country": arr_cc,
                "note": (
                    f"保险计划承保欧洲，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})"
                    + ("在承保区域内" if in_cov else "均不在欧洲承保区域内")
                ),
            }

    # 美洲
    if any(kw in combined for kw in ["美洲", "america", "北美", "north america", "南美", "south america"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _AMERICA_CC) or _country_in_region(arr_cc, _AMERICA_CC)
            return {
                "in_coverage": in_cov,
                "region_hint": "美洲",
                "dep_country": dep_cc,
                "arr_country": arr_cc,
                "note": (
                    f"保险计划承保美洲，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})"
                    + ("在承保区域内" if in_cov else "均不在美洲承保区域内")
                ),
            }

    # 非洲
    if any(kw in combined for kw in ["非洲", "africa"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _AFRICA_CC) or _country_in_region(arr_cc, _AFRICA_CC)
            return {
                "in_coverage": in_cov,
                "region_hint": "非洲",
                "dep_country": dep_cc,
                "arr_country": arr_cc,
                "note": (
                    f"保险计划承保非洲，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})"
                    + ("在承保区域内" if in_cov else "均不在非洲承保区域内")
                ),
            }

    # 大洋洲
    if any(kw in combined for kw in ["大洋洲", "oceania", "澳洲", "australia", "新西兰"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _OCEANIA_CC) or _country_in_region(arr_cc, _OCEANIA_CC)
            return {
                "in_coverage": in_cov,
                "region_hint": "大洋洲",
                "dep_country": dep_cc,
                "arr_country": arr_cc,
                "note": (
                    f"保险计划承保大洋洲，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})"
                    + ("在承保区域内" if in_cov else "均不在大洋洲承保区域内")
                ),
            }

    # 无法从文本判断
    return {
        "in_coverage": None,
        "region_hint": "unknown",
        "dep_country": dep_cc,
        "arr_country": arr_cc,
        "note": "无法从保险计划名称/描述文本判断承保区域，建议人工确认出境地区是否符合保险计划",
    }


def _check_hardcheck_exclusion(hardcheck: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    检查 hardcheck 结果中是否命中硬免责条款。
    命中则返回完整的 audit dict（audit_result="拒绝"），调用方直接跳过 AI 判定。
    未命中则返回 None，继续走 AI 判定。

    覆盖的免责条款（优先级从高到低）：
    0. 保单有效期不符（以原出发航班计划起飞时间为基准）
    1. 战争/冲突风险
    2. 承保区域不符
    3. 境内中转免责
    4. 中转接驳延误免责
    5. 非民航客运班机
    """
    if not hardcheck:
        return None

    def _make_denial(reason: str, flag: str) -> Dict[str, Any]:
        return {
            "audit_result": "拒绝",
            "confidence_score": 1.0,
            "key_data": {},
            "logic_check": {
                "exclusion_triggered": True,
                flag: True,
            },
            "payout_suggestion": {"currency": "CNY", "amount": 0, "basis": "免责条款命中"},
            "explanation": reason,
        }

    # 0. 保单有效期不符（以原出发航班计划起飞时间为基准）
    policy_cov = hardcheck.get("policy_coverage_check") or {}
    if policy_cov.get("in_coverage") is False:
        note = str(policy_cov.get("note") or "原出发航班计划起飞时间不在保单有效期内")
        return _make_denial(f"【超出有效期】{note}", "policy_coverage_out_triggered")

    # 0.5 纯中国大陆国内航班 → 不赔
    domestic_check = hardcheck.get("domestic_flight_check") or {}
    if domestic_check.get("is_pure_domestic_cn") is True:
        dep = domestic_check.get("dep_iata", "")
        arr = domestic_check.get("arr_iata", "")
        return _make_denial(f"【纯国内航班不赔】出发地 {dep} 和目的地 {arr} 均在中国大陆，本保险仅承保含国际/境外段的航班", "pure_domestic_cn_triggered")

    # 1. 战争因素
    war_risk = hardcheck.get("war_risk") or {}
    if war_risk.get("is_war_risk"):
        note = war_risk.get("note", "命中战争/冲突风险维护表")
        return _make_denial(f"【战争因素免责】{note}", "war_risk_triggered")

    # 2. 承保区域不符（三字码判定）
    coverage = hardcheck.get("coverage_area") or {}
    if coverage.get("in_coverage") is False:
        note = str(coverage.get("note") or "超出承保区域，不予赔付")
        return _make_denial(f"【承保区域不符】{note}", "coverage_out_of_area_triggered")

    # 2.5 承保区域不符（文本兜底判定）
    coverage_text = hardcheck.get("coverage_area_text_check") or {}
    if coverage_text.get("in_coverage") is False:
        note = str(coverage_text.get("note") or "出境地区不在保险计划承保范围内")
        return _make_denial(f"【承保区域不符】{note}", "coverage_text_out_of_area_triggered")

    # 3. 境内中转免责
    transit = hardcheck.get("transit_check") or {}
    if transit.get("is_domestic_cn") is True:
        iata = transit.get("iata", "")
        return _make_denial(f"【境内中转免责】中转地 {iata} 在境内，不予赔付", "transit_domestic_triggered")

    # 4. 中转接驳延误免责
    missed_conn = hardcheck.get("missed_connection_check") or {}
    if missed_conn.get("is_missed_connection") is True:
        return _make_denial("【中转接驳免责】前序航班延误导致无法搭乘后续接驳航班，不予赔付", "missed_connection_triggered")

    # 5. 非民航客运班机
    passenger_check = hardcheck.get("passenger_civil_check") or {}
    if passenger_check.get("is_passenger_civil") is False:
        fn = passenger_check.get("flight_no", "")
        return _make_denial(f"【非客运航班】航班 {fn} 非民航客运班机，不在赔付范围内", "non_passenger_civil_triggered")

    # 6. 同天投保时刻校验：投保时刻 >= 计划起飞时刻 → 拒赔
    same_day_check = hardcheck.get("same_day_policy_check") or {}
    if same_day_check.get("is_denied") is True:
        note = str(same_day_check.get("note") or "出境当天投保，投保时刻不早于计划起飞时刻，不属于保障责任")
        return _make_denial(f"【同天投保免责】{note}", "same_day_policy_triggered")

    # 7. 姓名不一致 → 拒赔（confirmed 级别）
    name_check = hardcheck.get("name_match_check") or {}
    if name_check.get("match_result") == "mismatch":
        note = str(name_check.get("note") or "登机牌/延误证明上的乘客姓名与保单被保险人姓名不符")
        return _make_denial(f"【姓名不符】{note}", "name_mismatch_triggered")

    return None
