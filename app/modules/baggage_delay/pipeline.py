import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from app.engine.workflow import StageRunner
from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
from app.logging_utils import LOGGER, log_extra
from app.skills.flight_lookup import get_flight_lookup_skill

from app.modules.baggage_delay.stages.utils import (
    _extract_date_yyyy_mm_dd,
    _classify_aviation_failure,
    _extract_file_names,
    _result,
    _safe_float,
    _parse_dt_flexible,
)
from app.modules.baggage_delay.stages.handlers import (
    _check_policy_validity,
    _material_gate,
    _check_special_materials,
    _check_info_consistency,
    _check_airline_baggage_record_exception,
    _check_exclusions,
    _check_domestic_flight,
    _check_actual_arrival_vs_policy,
    _try_transfer_flight_receipt_time,
)
from app.modules.baggage_delay.stages.calculator import (
    _compute_delay_hours_by_rule,
    _compute_payout_with_rules,
    _compute_tier_amount,
)
from app.modules.baggage_delay.stages.vision_merge import _merge_vision_to_parsed
from app.modules.flight_delay.stages.duplicate import _check_duplicate_claim

# 模块级常量
BAGGAGE_DELAY_THRESHOLD_HOURS = 6
POLICY_EXCERPT_MAX_CHARS = 1000


async def review_baggage_delay_async(
    *,
    reviewer: Any,
    claim_folder: Path,
    claim_info: Dict[str, Any],
    policy_terms: str,
    index: int,
    total: int,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """行李延误审核主流程（编排层）。"""
    forceid = str(claim_info.get("forceid") or "unknown")
    description = str(claim_info.get("Description_of_Accident") or "")
    assessment = str(claim_info.get("Assessment_Remark") or "")
    text_blob = f"{description}\n{assessment}".strip()
    file_names = _extract_file_names(claim_info)

    debug: Dict[str, Any] = {
        "policy_terms_excerpt": (policy_terms or "")[:POLICY_EXCERPT_MAX_CHARS],
        "claim_folder": str(claim_folder),
        "file_count": len(file_names),
        "file_names_sample": file_names[:10],
        "debug": [],
    }
    runner = StageRunner(ctx=debug, forceid=forceid)

    LOGGER.info(
        f"[{index}/{total}] 行李延误审核开始",
        extra=log_extra(forceid=forceid, stage="baggage_delay_start", attempt=0),
    )
    conclusions: List[Dict[str, str]] = []

    # stage0_duplicate: 重复理赔检测
    duplicate_check = _check_duplicate_claim(claim_info=claim_info, forceid=forceid)
    if duplicate_check:
        LOGGER.info(
            f"[{index}/{total}] 重复理赔检测命中: {duplicate_check.get('reason', '')}",
            extra=log_extra(forceid=forceid, stage="bd_duplicate_check", attempt=0),
        )
        return duplicate_check

    # 0) 视觉识别
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
        LOGGER.info(
            f"[{index}/{total}] 视觉识别完成: has_boarding={vision_extract.get('has_boarding_or_ticket')} "
            f"has_delay_proof={vision_extract.get('has_baggage_delay_proof')} "
            f"has_receipt_proof={vision_extract.get('has_baggage_receipt_time_proof')}",
            extra=log_extra(forceid=forceid, stage="baggage_delay_vision", attempt=0),
        )
    except Exception as _ve:
        LOGGER.warning(
            f"[{index}/{total}] 视觉识别失败（降级到纯文本）: {_ve}",
            extra=log_extra(forceid=forceid, stage="baggage_delay_vision", attempt=0),
        )
    debug["vision_extract"] = vision_extract

    # 0.5) AI结构化抽取
    ai_parsed, parse_err = await runner.run(
        "baggage_delay_parse",
        reviewer._ai_baggage_delay_parse_async,
        claim_info,
        text_blob,
        session=session,
        max_retries=2,
        retry_sleep=2.0,
    )
    if parse_err:
        debug["parse_warning"] = str(parse_err)[:200]
    if isinstance(ai_parsed, dict):
        debug["ai_parsed"] = ai_parsed

    # 合并视觉识别结果到 ai_parsed
    if vision_extract and isinstance(ai_parsed, dict):
        ai_parsed = await _merge_vision_to_parsed(
            vision_extract=vision_extract,
            ai_parsed=ai_parsed,
            claim_info=claim_info,
            claim_folder=claim_folder,
            debug=debug,
            reviewer=reviewer,
            session=session,
            text_blob=text_blob,
        )
    elif vision_extract and not isinstance(ai_parsed, dict):
        ai_parsed = dict(vision_extract)

    # 行李转运航班修复：若 alternate.alt_flight_no 是行李转运航班（非乘客改签），清除改签标记
    if isinstance(ai_parsed, dict):
        _alt = ai_parsed.get("alternate") or {}
        if isinstance(_alt, dict):
            _alt_fn = str(_alt.get("alt_flight_no") or "").strip().upper()
            if _alt_fn:
                _import_re = __import__("re")
                _bf_hints = [
                    "行李装载", "行李装在", "行李装在今日", "行李转运", "行李已装载",
                    "行李将搭乘", "行李运抵", "行李托运回", "行李送达", "行李将运",
                    "您的行李将", "行李会搭乘", "行李后续",
                    "baggage loaded", "baggage forwarded", "baggage will arrive",
                    "baggage will travel", "luggage will arrive",
                ]
                for _src in (ai_parsed, vision_extract):
                    for _fl in (_src.get("all_flights_found") or []):
                        if str(_fl.get("flight_no") or "").strip().upper() == _alt_fn:
                            _role = str(_fl.get("role_hint") or "").strip()
                            _src_f = str(_fl.get("source") or "").strip()
                            if any(h in _role or h in _src_f for h in _bf_hints):
                                _alt["is_connecting_rebooking"] = "false"
                                _alt["is_connecting_missed"] = "false"
                                debug["alternate_cleared_baggage_forwarding"] = (
                                    f"alternate航班号{_alt_fn}为行李转运航班，非乘客改签，清除改签标记"
                                )
                                break
                    if debug.get("alternate_cleared_baggage_forwarding"):
                        break

    # 前置准入校验
    policy_violation = _check_policy_validity(claim_info, debug, vision_extract=vision_extract)
    if policy_violation:
        conclusions.append({"checkpoint": "前置准入", "Eligible": "否", "Remark": policy_violation})
        policy_action = debug.get("policy_validity_action", "reject")
        if policy_action == "supplement":
            return _result(forceid, policy_violation, "S", conclusions, debug)
        return _result(forceid, policy_violation, "N", conclusions, debug)

    # 身份一致性校验
    identity_violation = _check_info_consistency(claim_info, ai_parsed or {})
    if identity_violation:
        conclusions.append({"checkpoint": "身份一致性", "Eligible": "否", "Remark": identity_violation})
        return _result(forceid, identity_violation, "N", conclusions, debug)

    # 纯国内航班检测（优先级高于免责条款——产品类型不匹配是更根本的问题）
    domestic_reason = _check_domestic_flight(vision_extract, ai_parsed or {}, claim_info)
    if domestic_reason:
        conclusions.append({"checkpoint": "航段检查", "Eligible": "否", "Remark": domestic_reason})
        return _result(forceid, domestic_reason, "N", conclusions, debug)

    # 免责条款校验
    exclusion_reason = _check_exclusions(claim_info, text_blob, ai_parsed or {})
    if exclusion_reason:
        conclusions.append({"checkpoint": "免责条款", "Eligible": "否", "Remark": exclusion_reason})
        return _result(forceid, f"拒赔：{exclusion_reason}", "N", conclusions, debug)

    # 官方航班数据补强
    aviation_lookup: Dict[str, Any] = {}
    try:
        if isinstance(ai_parsed, dict):
            flight_no = str(ai_parsed.get("flight_no") or "").strip()
            dep_iata = str(ai_parsed.get("dep_iata") or "").strip().upper()
            arr_iata = str(ai_parsed.get("arr_iata") or "").strip().upper()
            flight_date = (
                _extract_date_yyyy_mm_dd(ai_parsed.get("flight_date"))
                or _extract_date_yyyy_mm_dd(claim_info.get("Date_of_Accident"))
            )

            # 航班号格式校验：标准格式为 2位字母 + 1~4位数字
            import re as _re
            _FLIGHT_NO_PATTERN = _re.compile(r'^[A-Za-z]{2}\d{1,4}$')
            _valid_flight_no = bool(_FLIGHT_NO_PATTERN.match(flight_no)) if flight_no else False

            # 行李转运航班关键词（用于过滤）
            _BAGGAGE_FORWARDING_HINTS = [
                # 行李转运/装载类
                "行李装载", "行李装在", "行李装在今日", "行李转运", "行李已装载",
                # 行李将搭乘/运抵类（航司邮件常见描述）
                "行李将搭乘", "行李运抵", "行李托运回", "行李送达", "行李将运",
                "您的行李将", "行李会搭乘", "行李后续",
                # 英文
                "baggage loaded", "baggage forwarded", "baggage will arrive",
                "baggage will travel", "luggage will arrive", "luggage will be",
                "baggage will be forwarded", "baggage will be delivered",
            ]

            # 第一步：从 all_flights_found 中预先识别行李转运航班号
            _baggage_forwarding_nos: set = set()
            for _src in (ai_parsed, vision_extract):
                for _fl in (_src.get("all_flights_found") or []):
                    _fn = str(_fl.get("flight_no") or "").strip()
                    if not _fn or not _FLIGHT_NO_PATTERN.match(_fn):
                        continue
                    _role = str(_fl.get("role_hint") or "").strip()
                    _src_field = str(_fl.get("source") or "").strip()
                    if any(h in _role or h in _src_field for h in _BAGGAGE_FORWARDING_HINTS):
                        _baggage_forwarding_nos.add(_fn.upper())

            # 第二步：若主 flight_no 命中行李转运航班，拒绝并标记
            if _valid_flight_no and flight_no.upper() in _baggage_forwarding_nos:
                debug["flight_no_baggage_forwarding_rejected"] = (
                    f"{flight_no} 为行李转运航班（非乘客航班），拒绝作为主航班查询，从 all_flights_found 回退"
                )
                _valid_flight_no = False

            # P1 修复：联程航班场景 — 行李延误的触发点是旅客实际到达最终目的地的末段航班
            # 如果有多个航段，将目标航班设为末程航班，确保航空查询优先使用末程实际到达时间
            _itinerary_segments = (ai_parsed.get("itinerary_segments") or vision_extract.get("itinerary_segments") or [])
            if _valid_flight_no and len(_itinerary_segments) > 1:
                # 改签场景：优先使用 alternate 中的改签航班，而非 itinerary_segments 的末程
                _alternate = ai_parsed.get("alternate") or vision_extract.get("alternate") or {}
                _is_rebooking = str(_alternate.get("is_connecting_rebooking") or "").strip().lower() == "true"
                if _is_rebooking:
                    _alt_fn = str(_alternate.get("alt_flight_no") or "").strip().upper()
                    _alt_date = _extract_date_yyyy_mm_dd(_alternate.get("alt_dep") or _alternate.get("alt_arr"))
                    if _alt_fn and _FLIGHT_NO_PATTERN.match(_alt_fn) and _alt_fn != flight_no.upper():
                        debug["flight_no_corrected_for_connecting"] = (
                            f"改签场景，目标航班从 {flight_no} 修正为改签航班 {_alt_fn}（{_alt_date}）"
                        )
                        flight_no = _alt_fn
                        flight_date = _alt_date or flight_date
                        _valid_flight_no = True
                else:
                    # 非改签联程：只取与事故日期接近的航段（排除返程航班）
                    _accident_date = _extract_date_yyyy_mm_dd(claim_info.get("Date_of_Accident"))
                    _outbound_segments = []
                    for _seg in _itinerary_segments:
                        if not isinstance(_seg, dict):
                            continue
                        _seg_date = _extract_date_yyyy_mm_dd(_seg.get("original_date"))
                        if _seg_date and _accident_date:
                            # 与事故日期相差 <=3 天视为同一旅程方向
                            try:
                                from datetime import datetime as _dt
                                _diff = abs((_dt.strptime(_seg_date, "%Y-%m-%d") - _dt.strptime(_accident_date, "%Y-%m-%d")).days)
                                if _diff <= 3:
                                    _outbound_segments.append(_seg)
                            except ValueError:
                                _outbound_segments.append(_seg)
                        else:
                            _outbound_segments.append(_seg)

                    if len(_outbound_segments) > 1:
                        _last_segment = None
                        for _seg in _outbound_segments:
                            _seg_no = _seg.get("segment_no") or 0
                            if _last_segment is None or _seg_no > _last_segment.get("segment_no", 0):
                                _last_segment = _seg
                        if _last_segment and isinstance(_last_segment, dict):
                            _last_fn = str(_last_segment.get("original_flight_no") or "").strip().upper()
                            _last_date = _extract_date_yyyy_mm_dd(_last_segment.get("original_date")) or flight_date
                            _last_arr = ""
                            for _fl in (ai_parsed.get("all_flights_found") or vision_extract.get("all_flights_found") or []):
                                if isinstance(_fl, dict) and str(_fl.get("flight_no") or "").strip().upper() == _last_fn:
                                    _last_arr = str(_fl.get("arr_iata") or "").strip().upper()
                                    break
                            if _last_fn and _last_fn != flight_no.upper():
                                debug["flight_no_corrected_for_connecting"] = (
                                    f"联程航班 {len(_outbound_segments)} 段（去程），目标航班从 {flight_no} 修正为末程 {_last_fn}"
                                )
                                flight_no = _last_fn
                                flight_date = _last_date
                                arr_iata = _last_arr
                                _valid_flight_no = bool(_FLIGHT_NO_PATTERN.match(flight_no)) if flight_no else False

            # 第三步：收集 all_flights_found 中的候选航班号（去重保序，过滤行李转运）
            _candidates: list = []
            _seen = set()
            if _valid_flight_no:
                _candidates.append((flight_no, flight_date, dep_iata, arr_iata))
                _seen.add(flight_no.upper())
            for _src in (ai_parsed, vision_extract):
                for _fl in (_src.get("all_flights_found") or []):
                    _fn = str(_fl.get("flight_no") or "").strip()
                    if not _fn or not _FLIGHT_NO_PATTERN.match(_fn) or _fn.upper() in _seen:
                        continue
                    if _fn.upper() in _baggage_forwarding_nos:
                        debug.setdefault("baggage_forwarding_filtered", []).append(
                            f"{_fn}（role_hint={_fl.get('role_hint')}, source={_fl.get('source')}）")
                        continue
                    _fd = _extract_date_yyyy_mm_dd(_fl.get("date")) or flight_date
                    _dep = str(_fl.get("dep_iata") or "").strip().upper()
                    _arr = str(_fl.get("arr_iata") or "").strip().upper()
                    _candidates.append((_fn, _fd, _dep if _dep != "UNKNOWN" else "", _arr if _arr != "UNKNOWN" else ""))
                    _seen.add(_fn.upper())

            if not _valid_flight_no and _candidates:
                # 联程航班场景：优先选日期最晚的航班（末段），因为行李延误的触发点是旅客实际到达最终目的地的末段航班
                _best_idx = 0
                for _i, (_fn, _fd, _dep, _arr) in enumerate(_candidates):
                    if _fd and _candidates[_best_idx][1] and _fd > _candidates[_best_idx][1]:
                        _best_idx = _i
                debug["flight_no_corrected"] = f"{flight_no} -> {_candidates[_best_idx][0]}（从 all_flights_found 回退，优先末段航班）"
                flight_no = _candidates[_best_idx][0]
                flight_date = _candidates[_best_idx][1]
                dep_iata = _candidates[_best_idx][2]
                arr_iata = _candidates[_best_idx][3]
                # 同步回 ai_parsed，确保后续计算器使用正确的航班号
                ai_parsed["flight_no"] = flight_no
                if flight_date:
                    ai_parsed["flight_date"] = flight_date

            # 记录修正后的目标航班号，用于后续校验航空查询结果
            _target_flight_no = flight_no.upper() if flight_no else None

            # 过滤有效候选并并行查询
            _valid_candidates = [(_fn, _fd, _dep, _arr) for _fn, _fd, _dep, _arr in _candidates[:5] if _fn and _fd]
            if _valid_candidates:
                skill = get_flight_lookup_skill()
                tasks = [
                    skill.lookup_status(
                        flight_no=_fn,
                        date=_fd,
                        dep_iata=_dep if _dep and _dep != "UNKNOWN" else None,
                        arr_iata=_arr if _arr and _arr != "UNKNOWN" else None,
                        session=session,
                    )
                    for _fn, _fd, _dep, _arr in _valid_candidates
                ]
                raw_results = await asyncio.gather(*tasks, return_exceptions=True)
                # 优先使用目标航班（修正后的航班）的查询结果
                _target_result = None
                _fallback_result = None
                for (_fn, _fd, _dep, _arr), raw in zip(_valid_candidates, raw_results):
                    if isinstance(raw, Exception):
                        one_result = {"success": False, "error": str(raw), "_exception": True}
                    else:
                        one_result = raw
                    if one_result.get("success") and one_result.get("actual_arr"):
                        if _fn.upper() == _target_flight_no:
                            _target_result = (_fn, one_result)
                            break
                        elif _fallback_result is None:
                            _fallback_result = (_fn, one_result)
                    else:
                        debug.setdefault("aviation_candidates_tried", []).append(
                            {"flight_no": _fn, "date": _fd, "success": False,
                             "error": str(one_result.get("error") or "")[:80]}
                        )
                # 优先用目标航班结果，否则用第一个成功的备选
                _chosen = _target_result or _fallback_result
                if _chosen:
                    _fn_chosen, _result_chosen = _chosen
                    ai_parsed["flight_actual_arrival_time"] = _result_chosen["actual_arr"]
                    debug["arrival_source"] = "variflight_actual_arr"
                    aviation_lookup = _result_chosen
                    if _target_result is None and _fallback_result is not None:
                        debug["aviation_used_fallback"] = f"目标航班{_target_flight_no}未查到，使用{_fallback_result[0]}"

            if not aviation_lookup.get("success"):
                debug["arrival_source"] = "material_or_llm_fallback"
    except Exception as e:
        debug["aviation_lookup_warning"] = str(e)[:200]
    debug["aviation_lookup"] = aviation_lookup
    aviation_failure_type = _classify_aviation_failure(aviation_lookup)
    debug["aviation_failure_type"] = aviation_failure_type
    if aviation_failure_type == "system_error":
        conclusions.append(
            {"checkpoint": "官方航班数据", "Eligible": "需人工判断",
             "Remark": f"官方航班查询异常: {str(aviation_lookup.get('error') or '')[:120]}"}
        )
    elif aviation_failure_type == "evidence_gap":
        # 如果Vision已确认材料中有登机牌/机票+航班信息，aviation_lookup失败不影响审核
        has_boarding = str(ai_parsed.get("has_boarding_or_ticket") or "").strip().lower() == "true"
        has_flight_info = bool(
            (ai_parsed.get("flight_no") and str(ai_parsed.get("flight_no")).strip().lower() not in ("unknown", ""))
            and (ai_parsed.get("flight_date") and str(ai_parsed.get("flight_date")).strip().lower() not in ("unknown", ""))
        )
        if has_boarding and has_flight_info:
            conclusions.append(
                {"checkpoint": "官方航班数据", "Eligible": "是",
                 "Remark": "以材料中的航班信息为准（官方航班数据源未命中，不影响审核）"}
            )
        else:
            conclusions.append(
                {"checkpoint": "官方航班数据", "Eligible": "需补齐资料",
                 "Remark": "官方航班数据未命中，需补充可核验航班号/日期/航段信息"}
            )
    elif aviation_lookup.get("success") is True:
        conclusions.append(
            {"checkpoint": "官方航班数据", "Eligible": "是",
             "Remark": "已获取官方实际到达时间用于时长核算"}
        )

    # 转运航班到达时间回退
    transfer_flight_debug = await _try_transfer_flight_receipt_time(
        ai_parsed or {}, vision_extract, session,
    )
    debug["transfer_flight_receipt"] = transfer_flight_debug

    # 实际到达时间 vs 保单有效期检查
    arrival_policy_reason = _check_actual_arrival_vs_policy(claim_info, ai_parsed or {}, debug)
    if arrival_policy_reason:
        conclusions.append({"checkpoint": "保单有效期", "Eligible": "否", "Remark": arrival_policy_reason})
        return _result(forceid, arrival_policy_reason, "N", conclusions, debug)

    # 事故类型校验：行李丢失 vs 行李延误
    # 关键区分：行李是否最终找回。有有效签收时间=已找回=延误，无签收时间=需人工确认
    parsed_accident_type = str((ai_parsed or {}).get("accident_type") or "").strip().lower()
    raw_receipt = (ai_parsed or {}).get("baggage_receipt_time")
    has_receipt_time = bool(raw_receipt) and _parse_dt_flexible(str(raw_receipt)) is not None

    # P1 修复：签收时间来源放宽 — 即使 baggage_receipt_time 被 vision_merge 清除，
    # 只要 receipt_times 列表中有有效时间，也认为行李已找回（非永久丢失）
    _receipt_times_list = (ai_parsed or {}).get("receipt_times") or []
    _has_any_receipt_time = has_receipt_time or any(
        _parse_dt_flexible(str(rt)) is not None
        for rt in _receipt_times_list
        if rt and str(rt).lower() not in ("unknown", "")
    )

    # P1 修复：行李延误证明放宽 — 有PIR报告/签收单/不正常行李运输签收单，
    # 即使尚未提取到具体签收时间，也不轻易判定为永久丢失
    _has_delay_proof = str((ai_parsed or {}).get("has_baggage_delay_proof") or "").strip().lower() == "true"
    _has_delay_proof_source = str((vision_extract or {}).get("baggage_delay_proof_source") or "").strip()
    _has_receipt_doc = any(kw in _has_delay_proof_source for kw in ["签收单", "不正常行李", "运输签收", "行李事故记录"])

    delay_calc_temp = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
    has_calculable_delay = delay_calc_temp.get("delay_hours") is not None

    # 行李丢失误判修复：很多案件中AI将"行李未到/行李延误"误标为baggage_loss
    # 宽松策略：只要有航班信息+延误描述，不轻易判定为丢失
    if parsed_accident_type == "baggage_loss" and has_receipt_time:
        # 有签收时间说明行李已找回，本质是延误而非丢失
        if isinstance(ai_parsed, dict):
            ai_parsed["accident_type"] = "baggage_delay"
            ai_parsed["accident_type_note"] = "行李曾报丢失但已找回/送达，按行李延误审核"
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "行李曾报丢失但已找回/送达，按行李延误审核"})
    elif parsed_accident_type == "baggage_loss" and has_calculable_delay:
        # 能计算出延误时长，说明不是永久丢失
        if isinstance(ai_parsed, dict):
            ai_parsed["accident_type"] = "baggage_delay"
            ai_parsed["accident_type_note"] = "可计算延误时长，按行李延误审核"
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "可计算延误时长，按行李延误审核"})
    elif parsed_accident_type == "baggage_loss" and _has_any_receipt_time:
        # P1 修复：receipt_times 列表中有有效签收时间，说明行李已找回
        if isinstance(ai_parsed, dict):
            ai_parsed["accident_type"] = "baggage_delay"
            ai_parsed["accident_type_note"] = "receipt_times列表中有签收时间，行李已找回，按行李延误审核"
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "签收时间列表中有有效时间，行李已找回，按行李延误审核"})
    elif parsed_accident_type == "baggage_loss" and (_has_delay_proof or _has_receipt_doc):
        # P1 修复：有行李延误证明（PIR报告/签收单），不轻易判定为永久丢失
        if isinstance(ai_parsed, dict):
            ai_parsed["accident_type"] = "baggage_delay"
            ai_parsed["accident_type_note"] = f"有行李延误证明（{_has_delay_proof_source}），按行李延误审核"
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": f"有{_has_delay_proof_source}，按行李延误审核"})
    elif parsed_accident_type == "baggage_loss" and not has_receipt_time and not has_calculable_delay:
        # 真正无法计算延误时长的丢失案件，仍需转人身财产损失
        # 但这里给一个兜底：检查文本中是否有"找到""送达""领取"等关键词
        found_keywords = ["找到", "送达", "领取", "取回", "收到", "delivered", "found", "recovered", "retrieved"]
        text_has_found = any(kw in text_blob.lower() for kw in found_keywords)
        if text_has_found:
            # 文本中提到行李找到了，不是丢失
            if isinstance(ai_parsed, dict):
                ai_parsed["accident_type"] = "baggage_delay"
                ai_parsed["accident_type_note"] = "文本提及行李已找到/送达，按行李延误审核"
            conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "文本提及行李已找到/送达，按行李延误审核"})
        else:
            conclusions.append({"checkpoint": "事故类型", "Eligible": "否", "Remark": "事故为行李丢失，需转随身财产损失责任"})
            return _result(forceid, "拒赔：事故类型为行李丢失，非托运行李延误责任", "N", conclusions, debug)
    elif ("行李丢失" in text_blob) and ("延误" not in text_blob) and not _has_any_receipt_time and not has_calculable_delay:
        # P1 修复：用 _has_any_receipt_time 替代 has_receipt_time，检查 receipt_times 列表
        # 文本提到丢失且未提到延误，且无签收时间无延误时长
        # 但如果有"找到""送达"等关键词，仍可能是延误
        found_keywords = ["找到", "送达", "领取", "取回", "收到", "delivered", "found", "recovered"]
        text_has_found = any(kw in text_blob.lower() for kw in found_keywords)
        if text_has_found:
            conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "文本提及行李已找到/送达，按行李延误审核"})
        else:
            conclusions.append({"checkpoint": "事故类型", "Eligible": "否", "Remark": "事故为行李丢失，需转随身财产损失责任"})
            return _result(forceid, "拒赔：事故类型为行李丢失，非托运行李延误责任", "N", conclusions, debug)
    else:
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "未发现行李丢失单独触发，继续按行李延误审核"})

    # 材料门禁
    missing_materials: List[str] = []
    if isinstance(ai_parsed, dict):
        def _has_flag(key: str) -> str:
            return str(ai_parsed.get(key) or "unknown").strip().lower()

        flag = _has_flag("has_boarding_or_ticket")
        if flag == "false":
            # Vision 判定为 false 时，仍检查文本关键词兜底
            if not any(w in f"{text_blob} {' '.join(file_names)}".lower()
                       for w in ["机票", "登机牌", "行程单", "ticket", "boarding", "itinerary"]):
                missing_materials.append("交通票据（机票/登机牌/行程单）")
        elif flag == "unknown":
            if not any(w in f"{text_blob} {' '.join(file_names)}".lower()
                       for w in ["机票", "登机牌", "行程单", "ticket", "boarding", "itinerary"]):
                missing_materials.append("交通票据（机票/登机牌/行程单）")

        delay_proof_flag = _has_flag("has_baggage_delay_proof")
        receipt_proof_flag = _has_flag("has_baggage_receipt_time_proof")
        joined_text = f"{text_blob} {' '.join(file_names)}".lower()
        delay_proof_kw = any(w in joined_text for w in ["行李延误", "行李不正常", "行李事故", "行李未到", "pir", "baggage delay", "delay proof", "property irregularity", "lost baggage", "baggage claim", "worldtracer", "world tracer"])
        receipt_proof_kw = any(w in joined_text for w in ["签收", "领取", "提取", "取件", "派送", "送达", "交付", "行李到达", "行李已", "receipt", "delivered", "delivery", "received", "collected", "picked up", "acknowledgement", "acknowledgment"])

        has_delay_proof = delay_proof_flag == "true" or (delay_proof_flag != "true" and delay_proof_kw)
        has_receipt_proof = receipt_proof_flag == "true" or (receipt_proof_flag != "true" and receipt_proof_kw)

        if not has_delay_proof and not has_receipt_proof:
            missing_materials.append("行李延误证明或行李签收单（航空公司出具的行李延误时数/原因书面证明，或含具体签收时间的行李签收单，二选一）")

        # P1 材料门禁放宽：当行李延误证明已确认且其他关键材料齐全时，
        # 不把行李签收证明缺失作为阻断性补件要求，放行到后续时长计算阶段
        boarding_flag = _has_flag("has_boarding_or_ticket")
        tag_flag = _has_flag("has_baggage_tag_proof")
        id_flag = _has_flag("has_id_proof")
        passport_flag = _has_flag("has_passport")

        has_boarding = boarding_flag == "true" or any(w in f"{text_blob} {' '.join(file_names)}".lower()
                       for w in ["机票", "登机牌", "行程单", "ticket", "boarding", "itinerary"])

        # 行李牌校验（含替代凭证）
        has_baggage_tag = tag_flag == "true"
        if tag_flag in ("false", "unknown"):
            if tag_flag == "unknown":
                v_tag = str(vision_extract.get("has_baggage_tag_proof") or "unknown").strip().lower()
                if v_tag not in ("unknown", ""):
                    has_baggage_tag = v_tag == "true"
            if not has_baggage_tag:
                exception_met = _check_airline_baggage_record_exception(
                    vision_extract, ai_parsed or {}, claim_info, joined_text
                )
                if exception_met:
                    has_baggage_tag = True
                    debug["baggage_tag_exception"] = "航空公司官方行李记录满足替代条件，视同行李牌已提供"

        has_id = id_flag == "true" or passport_flag == "true"

        # 关键材料全部确认时，签收证明缺失不阻断
        key_materials_confirmed = has_delay_proof and has_boarding and has_baggage_tag and has_id
        if key_materials_confirmed and not has_receipt_proof:
            debug["receipt_proof_relaxed"] = (
                "行李延误证明+登机牌+行李牌+身份证已确认，签收证明缺失不阻断，放行到时长计算阶段"
            )

        # 行李牌缺失检查（使用上面已计算的 has_baggage_tag）
        if not has_baggage_tag:
            missing_materials.append("托运行李牌照片（含姓名、航班信息、行李牌号码）")

        # 身份证/护照缺失检查（使用上面已计算的 id_flag / passport_flag）
        if id_flag == "false" and passport_flag == "false":
            missing_materials.append("被保险人身份证正反面或护照")
        if passport_flag == "false" and id_flag in ("false", "unknown"):
            missing_materials.append("护照照片页、签证页、出入境盖章页")

        bank_flag = _has_flag("has_bank_card_proof")
        if bank_flag == "false":
            debug["bank_card_warning"] = "视觉识别未见银行卡信息，建议人工确认打款账号"

        special_needs = _check_special_materials(claim_info, text_blob, file_names)
        missing_materials.extend(special_needs)
        missing_materials = sorted(set(missing_materials))
    else:
        missing_materials = _material_gate(text_blob, file_names)

    debug["missing_materials"] = missing_materials
    if missing_materials:
        conclusions.append({"checkpoint": "材料完整性", "Eligible": "需补齐资料", "Remark": "；".join(missing_materials)})
        return _result(forceid, "需补齐资料：" + "；".join(missing_materials), "Y", conclusions, debug)
    conclusions.append({"checkpoint": "材料完整性", "Eligible": "是", "Remark": "视觉识别确认关键材料已提供"})

    # 人工复核触发：仅基于 Vision 识别结果中的 risk_flags，不在旅客自述文本中搜索
    # （旅客描述"手写纸条"等是正常事实陈述，不代表材料有风险）
    manual_flags = []
    vision_risk_flags = vision_extract.get("risk_flags") or []
    if isinstance(vision_risk_flags, list):
        for flag in vision_risk_flags:
            flag_lower = str(flag).strip().lower()
            # Vision risk_flags 中的值映射到人工复核标记
            # 注意：multilang 不作为人工复核触发——国际旅行行李延误案件中出现
            # 多语言材料（葡萄牙语PIR、英语邮件、中文GDS截图等）是正常现象
            if flag_lower in ("handwritten", "手写"):
                manual_flags.append("handwritten")
            elif flag_lower in ("conflict", "冲突"):
                manual_flags.append("conflict")
            elif flag_lower in ("fraud_suspect", "疑似伪造"):
                manual_flags.append("疑似伪造")
    parsed_risk = str((ai_parsed or {}).get("manual_review_risk") or "").strip().lower()
    if parsed_risk and parsed_risk not in {"none", "unknown"}:
        manual_flags.append(parsed_risk)
    if manual_flags:
        # 如果仅有 "conflict" 标记且所有材料已视觉确认，降级为警告而非阻断
        if manual_flags == ["conflict"] and not missing_materials:
            debug["manual_review_flags"] = manual_flags
            debug["conflict_downgraded"] = True
            conclusions.append({"checkpoint": "人工复核触发", "Eligible": "是", "Remark": "AI标记conflict但材料完整，降级为审核通过（非实质性责任冲突）"})
        else:
            debug["manual_review_flags"] = manual_flags
            conclusions.append({"checkpoint": "人工复核触发", "Eligible": "需人工判断", "Remark": f"命中关键词: {','.join(manual_flags)}"})
            return _result(forceid, "转人工复核：存在材料识别或真实性争议", "Y", conclusions, debug)

    # 延误时长核算与门槛
    if debug.get("transfer_flight_receipt", {}).get("receipt_time_set"):
        delay_calc = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
        delay_calc["receipt_time_source"] = "transfer_flight_arrival"
        delay_hours = delay_calc.get("delay_hours")

        # Fallback：如果 delay_calc 返回 None，尝试使用 ai_parsed 中预计算的 delay_hours
        if delay_hours is None:
            parsed_delay = (ai_parsed or {}).get("delay_hours")
            if parsed_delay is not None:
                try:
                    delay_hours = float(parsed_delay)
                    delay_calc["delay_hours"] = delay_hours
                    delay_calc["method"] = "transfer_flight_fallback"
                except (ValueError, TypeError):
                    pass

        # 新增 Fallback：直接从转运航班到达时间和航班到达时间计算
        if delay_hours is None:
            transfer_receipt = debug.get("transfer_flight_receipt", {})
            # receipt_time_set 就是转运航班到达时间
            transfer_arr_str = transfer_receipt.get("receipt_time_set")
            flight_arr_str = (ai_parsed or {}).get("flight_actual_arrival_time")
            if transfer_arr_str and flight_arr_str:
                transfer_dt = _parse_dt_flexible(transfer_arr_str)
                flight_dt = _parse_dt_flexible(flight_arr_str)
                if transfer_dt and flight_dt and transfer_dt >= flight_dt:
                    delay_hours = round((transfer_dt - flight_dt).total_seconds() / 3600.0, 2)
                    delay_calc["delay_hours"] = delay_hours
                    delay_calc["method"] = "transfer_flight_direct_calc"

        delay_hours_str = f"{delay_hours:.2f}小时" if delay_hours is not None else "未知"
        # 转运航班到达时间作为代理签收时间，若延误已超门槛则直接通过
        if delay_hours is not None and delay_hours >= BAGGAGE_DELAY_THRESHOLD_HOURS:
            conclusions.append({
                "checkpoint": "行李签收时间",
                "Eligible": "是",
                "Remark": f"以后续转运航班到达时间为行李签收时间代理，延误时长{delay_hours_str}，达到赔付门槛",
            })
            # 标记签收时间已由转运航班代理确认，防止 AI 模型误判补件
            debug["receipt_proxy_accepted"] = True
        else:
            conclusions.append({
                "checkpoint": "行李签收时间",
                "Eligible": "需补齐资料",
                "Remark": f"以行李签收证明中的明确日期/时间为准；无签收证明时，以后续转运航班到达时间为辅助参考，待补件后按实际签收时间修正。当前估算延误时长{delay_hours_str}。",
            })
            return _result(
                forceid,
                f"需补齐资料：行李签收证明（含签收时间），当前以后续转运航班到达时间辅助参考，估算行李延误{delay_hours_str}，待补件后按实际签收时间修正。",
                "Y", conclusions, debug,
            )
    delay_calc = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
    delay_hours = delay_calc.get("delay_hours")
    debug["delay_calc"] = delay_calc
    if delay_hours is None:
        if aviation_failure_type == "system_error":
            return _result(forceid, "转人工复核：官方航班数据查询异常，无法完成时长核算", "Y", conclusions, debug)
        conclusions.append({"checkpoint": "延误时长", "Eligible": "需补齐资料", "Remark": "未识别到明确延误时长或签收时间信息"})
        return _result(forceid, "需补齐资料：请补充行李签收证明（含签收时间）或承运人出具的行李延误时长证明", "Y", conclusions, debug)
    if delay_hours < BAGGAGE_DELAY_THRESHOLD_HOURS:
        # 特殊处理1：签收时间为00:00占位符时，延误时长极不可靠
        if delay_calc.get("receipt_time_midnight_placeholder"):
            debug["midnight_placeholder_override"] = (
                f"延误时长{delay_hours:.2f}h但签收时间为00:00占位符，时长不可靠，转AI审计"
            )
        # 特殊处理2：2-6h灰度区，不直接拒赔，继续走AI审计
        elif delay_hours >= 2:
            debug["delay_gray_zone"] = (
                f"延误时长{delay_hours:.2f}h处于2-6h灰度区，继续走AI审计"
            )
            conclusions.append({"checkpoint": "赔付门槛", "Eligible": "是", "Remark": f"延误时长{delay_hours:.2f}小时，处于灰度区，交AI审计综合判定"})
        else:
            conclusions.append({"checkpoint": "赔付门槛", "Eligible": "否", "Remark": f"延误时长{delay_hours:.2f}小时，未达到{BAGGAGE_DELAY_THRESHOLD_HOURS}小时"})
            return _result(forceid, "拒赔：行李延误时长未达到6小时赔付门槛", "N", conclusions, debug)
    elif delay_hours >= BAGGAGE_DELAY_THRESHOLD_HOURS:
        conclusions.append({"checkpoint": "赔付门槛", "Eligible": "是", "Remark": f"延误时长{delay_hours:.2f}小时，达到赔付门槛"})

    # 信息一致性校验（已在身份一致性阶段完成，此处不再重复调用）
    # consistency_violation = _check_info_consistency(claim_info, ai_parsed or {})

    # AI审核意见
    ai_audit, audit_err = await runner.run(
        "baggage_delay_audit",
        reviewer._ai_baggage_delay_audit_async,
        claim_info,
        {
            "delay_hours": delay_hours,
            "missing_materials": missing_materials,
            "manual_flags": manual_flags,
            "rule_conclusions": conclusions,
            "ai_parsed": ai_parsed or {},
        },
        policy_terms or "",
        session=session,
        max_retries=1,
        retry_sleep=1.0,
    )
    if audit_err:
        debug["audit_warning"] = str(audit_err)[:200]
    elif isinstance(ai_audit, dict):
        ai_audit["delay_hours"] = delay_hours
        ai_audit["tier_amount"] = _compute_tier_amount(delay_hours)
        debug["ai_audit"] = ai_audit
        ai_audit_result = str(ai_audit.get("audit_result") or "").strip()
        ai_missing = [m for m in (ai_audit.get("missing_materials") or []) if m and str(m).strip()]
        if ai_missing:
            vision_confirmed = set()
            if isinstance(ai_parsed, dict):
                if str(ai_parsed.get("has_boarding_or_ticket") or "").strip().lower() in ("true",):
                    vision_confirmed.update(["登机牌", "电子客票", "行程单", "机票"])
                if str(ai_parsed.get("has_baggage_delay_proof") or "").lower() in ("true",):
                    vision_confirmed.update(["行李延误证明", "行李不正常", "PIR"])
                if str(ai_parsed.get("has_baggage_receipt_time_proof") or "").lower() in ("true",):
                    vision_confirmed.update(["行李签收证明", "签收单"])
                if str(ai_parsed.get("has_baggage_tag_proof") or "").lower() in ("true",):
                    vision_confirmed.add("托运行李牌")
            filtered_missing = []
            for item in ai_missing:
                if any(kw in item for kw in vision_confirmed):
                    continue
                if item not in set(missing_materials):
                    missing_materials.append(item)
            missing_materials = sorted(set(missing_materials))
            debug["missing_materials"] = missing_materials
        if ai_audit_result == "需补齐资料" and missing_materials:
            # 若转运航班代理签收已确认且延误超门槛，AI 模型误判补件 → 覆盖
            if debug.get("receipt_proxy_accepted"):
                receipt_kw = {"签收", "receipt", "领取", "提取", "取件", "派送", "送达", "交付"}
                non_receipt_missing = [
                    m for m in missing_materials
                    if not any(kw in m.lower() for kw in receipt_kw)
                ]
                if not non_receipt_missing:
                    LOGGER.info(
                        f"baggage_delay: 转运航班代理签收已确认，覆盖AI模型误判补件",
                        extra=log_extra(forceid=forceid, stage="baggage_delay_audit", attempt=0),
                    )
                    missing_materials = []
                    debug["missing_materials"] = []
                    debug["ai_supplement_overridden"] = True
                else:
                    missing_materials = non_receipt_missing
                    debug["missing_materials"] = non_receipt_missing
            if missing_materials:
                conclusions.append({"checkpoint": "AI审计补件", "Eligible": "需补齐资料", "Remark": "；".join(missing_materials)})
                return _result(forceid, "需补齐资料：" + "；".join(missing_materials), "Y", conclusions, debug)
        elif ai_audit_result == "拒绝":
            reason = str(ai_audit.get("reason") or ai_audit.get("explanation") or "AI审核拒赔")
            conclusions.append({"checkpoint": "AI审计", "Eligible": "否", "Remark": reason})
            return _result(forceid, f"拒赔：{reason}", "N", conclusions, debug)

    # 赔付核算
    claim_amount = _safe_float(claim_info.get("Amount"))
    insured_amount = _safe_float(claim_info.get("Insured_Amount"))
    remaining_coverage = _safe_float(claim_info.get("Remaining_Coverage"))
    cap = None
    if insured_amount is not None and remaining_coverage is not None:
        cap = min(insured_amount, remaining_coverage)
    elif insured_amount is not None:
        cap = insured_amount
    elif remaining_coverage is not None:
        cap = remaining_coverage
    payout = _compute_payout_with_rules(
        delay_hours, claim_amount, cap, ai_parsed or {}, claim_info,
    )
    debug["amounts"] = {
        "claim_amount": claim_amount,
        "insured_amount": insured_amount,
        "remaining_coverage": remaining_coverage,
        "cap_used": cap,
        "payout": payout,
    }

    conclusions.append({"checkpoint": "赔付核算", "Eligible": "是", "Remark": f"按阶梯核算赔付金额{payout:.2f}元"})
    return _result(
        forceid,
        f"审核通过：行李延误{delay_hours:.2f}小时，建议赔付{payout:.2f}元",
        "N", conclusions, debug,
    )
