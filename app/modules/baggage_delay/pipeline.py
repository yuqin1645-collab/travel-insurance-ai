import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from app.engine.workflow import StageRunner
from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
from app.logging_utils import LOGGER, log_extra
from app.skills.flight_lookup import get_flight_lookup_skill
from app.vision_preprocessor import prepare_attachments_for_claim

from app.modules.baggage_delay.stages.utils import (
    _extract_date_yyyy_mm_dd,
    _classify_aviation_failure,
    _extract_file_names,
    _result,
)
from app.modules.baggage_delay.stages.handlers import (
    _check_policy_validity,
    _material_gate,
    _check_special_materials,
    _check_info_consistency,
    _check_airline_baggage_record_exception,
    _check_exclusions,
    _try_transfer_flight_receipt_time,
)
from app.modules.baggage_delay.stages.calculator import (
    _compute_delay_hours_by_rule,
    _compute_payout_with_rules,
    _compute_tier_amount,
)


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
        "policy_terms_excerpt": (policy_terms or "")[:1000],
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
        for key in (
            "has_boarding_or_ticket", "has_baggage_delay_proof", "has_baggage_receipt_time_proof",
            "has_baggage_tag_proof",
            "has_airline_baggage_record", "airline_baggage_record_name",
            "airline_baggage_record_flight", "airline_baggage_record_pieces",
            "flight_actual_arrival_time", "baggage_receipt_time", "receipt_times", "delay_hours",
            "has_id_proof", "has_passport", "has_exit_entry_record", "exit_datetime",
            "has_bank_card_proof", "risk_flags",
            "all_flights_found",
        ):
            vision_val = vision_extract.get(key)
            parsed_val = ai_parsed.get(key)
            if vision_val is not None and str(vision_val).lower() not in ("unknown", "", "[]"):
                ai_parsed[key] = vision_val
            elif parsed_val is None:
                ai_parsed[key] = vision_val
        for key in ("flight_no", "flight_date", "dep_iata", "arr_iata"):
            vision_val = vision_extract.get(key)
            if vision_val and str(vision_val).lower() not in ("unknown", ""):
                existing = ai_parsed.get(key)
                if existing is None or str(existing).lower() in ("unknown", ""):
                    ai_parsed[key] = vision_val

        # 安全网：交叉校验
        proof_source = vision_extract.get("baggage_delay_proof_source") or ""
        if proof_source and str(proof_source).lower() not in ("unknown", ""):
            hd_val = ai_parsed.get("has_baggage_delay_proof")
            if not hd_val or str(hd_val).lower() == "false":
                ai_parsed["has_baggage_delay_proof"] = True
                debug.setdefault("auto_corrected", []).append("has_baggage_delay_proof: PIR报告存在但 vision 误判为 false，已自动纠正")
            ht_val = ai_parsed.get("has_baggage_tag_proof")
            if not ht_val or str(ht_val).lower() == "false":
                ai_parsed["has_baggage_tag_proof"] = True
                debug.setdefault("auto_corrected", []).append("has_baggage_tag_proof: PIR报告含航班+行李信息，等效行李牌，已自动纠正")

        receipt_time = vision_extract.get("baggage_receipt_time") or ""
        if receipt_time and str(receipt_time).lower() not in ("unknown", ""):
            low_confidence_markers = ["/unknown", "/未知", "~", "约", "左右", "estimated", "大概"]
            is_low_confidence = any(m in str(receipt_time) for m in low_confidence_markers)
            if not is_low_confidence:
                hr_val = ai_parsed.get("has_baggage_receipt_time_proof")
                if not hr_val or str(hr_val).lower() == "false":
                    ai_parsed["has_baggage_receipt_time_proof"] = True
                    debug.setdefault("auto_corrected", []).append("has_baggage_receipt_time_proof: 签收时间已提取但 vision 误判为 false，已自动纠正")
            else:
                ai_parsed["baggage_receipt_time"] = None
                # 同时清除 delay_hours：无有效签收时间时，delay_hours 是模型估算值，不可靠
                ai_parsed["delay_hours"] = None
                debug.setdefault("auto_corrected", []).append(f"baggage_receipt_time: 清除低置信度时间值 {receipt_time}")

        vision_notes = str(vision_extract.get("notes") or "").strip()

        # 校验：如果 vision notes 明确说行李延误证明缺失，纠正 has_baggage_delay_proof 为 false
        # Vision 模型有时会因 PIR 报告或物品清单而判定 has_baggage_delay_proof=True，
        # 但 notes 中又明确说"行李延误证明文件缺失"——这是矛盾的，应以 notes 为准
        delay_proof_missing_markers = [
            "行李延误证明文件缺失", "行李延误证明缺失", "行李延误证明.*缺失",
            "未见行李延误证明", "无行李延误证明",
        ]
        for marker in delay_proof_missing_markers:
            if re.search(marker, vision_notes):
                if ai_parsed.get("has_baggage_delay_proof") not in (None, False):
                    ai_parsed["has_baggage_delay_proof"] = False
                    ai_parsed["delay_hours"] = None
                    debug.setdefault("auto_corrected", []).append(
                        f"has_baggage_delay_proof: vision notes明确行李延误证明缺失，纠正为 false"
                    )
                break

        # 校验：如果 vision notes 明确说明签收时间来自航空公司邮件通知/转运航班预计到达时间，
        # 说明并非真正的行李签收证明，应将 has_baggage_receipt_time_proof 纠正为 false
        receipt_time_email_markers = [
            "航空公司邮件", "邮件通知", "邮件预计", "邮件预计",
            "转运航班", "行李搭乘", "预计.*到达", "行李将搭乘",
            "luggage will arrive", "baggage will arrive",
        ]
        if ai_parsed.get("has_baggage_receipt_time_proof") and vision_notes:
            for marker in receipt_time_email_markers:
                if re.search(marker, vision_notes):
                    ai_parsed["has_baggage_receipt_time_proof"] = False
                    ai_parsed["baggage_receipt_time"] = None
                    ai_parsed["delay_hours"] = None
                    debug["no_receipt_proof_confirmed"] = True
                    debug.setdefault("auto_corrected", []).append(
                        f"has_baggage_receipt_time_proof: vision notes明确时间来自邮件/转运航班，非实际签收证明，纠正为 false"
                    )
                    break

        # PIR二次聚焦提取
        needs_pir_extract = (
            ai_parsed.get("has_baggage_delay_proof") is True
            and not debug.get("no_receipt_proof_confirmed")
            and (not ai_parsed.get("baggage_receipt_time")
                 or str(ai_parsed.get("baggage_receipt_time")).lower() in ("unknown", ""))
            and (not ai_parsed.get("delay_hours")
                 or str(ai_parsed.get("delay_hours")).lower() in ("unknown", ""))
        )
        if needs_pir_extract:
            try:
                processed_attachments, _ = prepare_attachments_for_claim(
                    claim_folder, claim_info=claim_info, max_attachments=0
                )
                attachment_paths = [a.path for a in processed_attachments]
                if not attachment_paths:
                    debug["pir_receipt_extract"] = {"attempted": False, "reason": "无可用图片附件"}
                else:
                    pir_extract = await reviewer._ai_pir_receipt_time_extract_async(
                        attachment_paths=attachment_paths,
                        claim_info=claim_info,
                        session=session,
                    )
                    if isinstance(pir_extract, dict):
                        receipt = pir_extract.get("baggage_receipt_time")
                        confidence = str(pir_extract.get("confidence") or "").lower()
                        if receipt and str(receipt).lower() not in ("unknown", "") and confidence in ("high", "medium"):
                            ai_parsed["baggage_receipt_time"] = receipt
                            if pir_extract.get("receipt_times"):
                                ai_parsed["receipt_times"] = pir_extract["receipt_times"]
                            pir_delay = pir_extract.get("delay_hours")
                            if pir_delay and str(pir_delay).lower() != "unknown":
                                ai_parsed["delay_hours"] = pir_delay
                            debug.setdefault("auto_corrected", []).append(
                                f"baggage_receipt_time: PIR二次提取成功 {receipt}（置信度: {confidence}）"
                            )
                        else:
                            debug["pir_receipt_extract"] = {
                                "attempted": True, "result": "未提取到有效签收时间",
                                "confidence": confidence,
                            }
            except Exception as e:
                debug["pir_receipt_extract_warning"] = str(e)[:200]

    elif vision_extract and not isinstance(ai_parsed, dict):
        ai_parsed = dict(vision_extract)

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
            if flight_no and flight_date:
                skill = get_flight_lookup_skill()
                aviation_lookup = await skill.lookup_status(
                    flight_no=flight_no,
                    date=flight_date,
                    dep_iata=dep_iata if dep_iata and dep_iata != "UNKNOWN" else None,
                    arr_iata=arr_iata if arr_iata and arr_iata != "UNKNOWN" else None,
                    session=session,
                )
                if aviation_lookup.get("success"):
                    actual_arr = aviation_lookup.get("actual_arr")
                    if actual_arr:
                        ai_parsed["flight_actual_arrival_time"] = actual_arr
                        debug["arrival_source"] = "variflight_actual_arr"
                else:
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
        conclusions.append(
            {"checkpoint": "官方航班数据", "Eligible": "需补件",
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

    # 事故类型校验
    parsed_accident_type = str((ai_parsed or {}).get("accident_type") or "").strip().lower()
    if parsed_accident_type == "baggage_loss" or (("行李丢失" in text_blob) and ("延误" not in text_blob)):
        conclusions.append({"checkpoint": "事故类型", "Eligible": "否", "Remark": "事故为行李丢失，需转随身财产损失责任"})
        return _result(forceid, "拒赔：事故类型为行李丢失，非托运行李延误责任", "N", conclusions, debug)
    conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "未发现行李丢失单独触发，继续按行李延误审核"})

    # 材料门禁
    missing_materials: List[str] = []
    if isinstance(ai_parsed, dict):
        def _has_flag(key: str) -> str:
            return str(ai_parsed.get(key) or "unknown").strip().lower()

        flag = _has_flag("has_boarding_or_ticket")
        if flag == "false":
            missing_materials.append("交通票据（机票/登机牌/行程单）")
        elif flag == "unknown":
            if not any(w in f"{text_blob} {' '.join(file_names)}".lower()
                       for w in ["机票", "登机牌", "行程单", "ticket", "boarding", "itinerary"]):
                missing_materials.append("交通票据（机票/登机牌/行程单）")

        delay_proof_flag = _has_flag("has_baggage_delay_proof")
        receipt_proof_flag = _has_flag("has_baggage_receipt_time_proof")
        joined_text = f"{text_blob} {' '.join(file_names)}".lower()
        delay_proof_kw = any(w in joined_text for w in ["行李延误", "行李不正常", "pir", "baggage delay", "delay proof", "property irregularity"])
        receipt_proof_kw = any(w in joined_text for w in ["签收", "领取", "receipt", "delivered", "delivery"])

        has_delay_proof = delay_proof_flag == "true" or (delay_proof_flag == "unknown" and delay_proof_kw)
        has_receipt_proof = receipt_proof_flag == "true" or (receipt_proof_flag == "unknown" and receipt_proof_kw)

        if not has_delay_proof and not has_receipt_proof:
            missing_materials.append("行李延误证明或行李签收单（航空公司出具的行李延误时数/原因书面证明，或含具体签收时间的行李签收单，二选一）")

        tag_flag = _has_flag("has_baggage_tag_proof")
        if tag_flag == "unknown":
            v_tag = str(vision_extract.get("has_baggage_tag_proof") or "unknown").strip().lower()
            if v_tag not in ("unknown", ""):
                tag_flag = v_tag

        if tag_flag in ("false", "unknown"):
            exception_met = _check_airline_baggage_record_exception(
                vision_extract, ai_parsed or {}, claim_info, joined_text
            )
            if exception_met:
                debug["baggage_tag_exception"] = "航空公司官方行李记录满足替代条件，视同行李牌已提供"
            else:
                missing_materials.append("托运行李牌照片（含姓名、航班信息、行李牌号码）")

        id_flag = _has_flag("has_id_proof")
        passport_flag = _has_flag("has_passport")
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
        conclusions.append({"checkpoint": "材料完整性", "Eligible": "需补件", "Remark": "；".join(missing_materials)})
        return _result(forceid, "需补件：" + "；".join(missing_materials), "Y", conclusions, debug)
    conclusions.append({"checkpoint": "材料完整性", "Eligible": "是", "Remark": "视觉识别确认关键材料已提供"})

    # 人工复核触发
    manual_flags = []
    manual_keywords = ["手写", "多语言", "伪造", "ps", "涂改", "矛盾", "争议", "模糊"]
    for kw in manual_keywords:
        if kw in text_blob.lower():
            manual_flags.append(kw)
    parsed_risk = str((ai_parsed or {}).get("manual_review_risk") or "").strip().lower()
    if parsed_risk and parsed_risk not in {"none", "unknown"}:
        manual_flags.append(parsed_risk)
    if manual_flags:
        debug["manual_review_flags"] = manual_flags
        conclusions.append({"checkpoint": "人工复核触发", "Eligible": "需人工判断", "Remark": f"命中关键词: {','.join(manual_flags)}"})
        return _result(forceid, "转人工复核：存在材料识别或真实性争议", "Y", conclusions, debug)

    # 延误时长核算与门槛
    delay_calc = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
    delay_hours = delay_calc.get("delay_hours")
    if debug.get("transfer_flight_receipt", {}).get("receipt_time_set"):
        delay_calc["receipt_time_source"] = "transfer_flight_arrival"
        delay_calc = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
        delay_hours = delay_calc.get("delay_hours")
        delay_hours_str = f"{delay_hours:.2f}小时" if delay_hours is not None else "未知"
        conclusions.append({
            "checkpoint": "行李签收时间",
            "Eligible": "需补件",
            "Remark": f"以行李签收证明中的明确日期/时间为准；无签收证明时，以后续转运航班到达时间为辅助参考，待补件后按实际签收时间修正。当前估算延误时长{delay_hours_str}。",
        })
        return _result(
            forceid,
            f"需补件：行李签收证明（含签收时间），当前以后续转运航班到达时间辅助参考，估算行李延误{delay_hours_str}，待补件后按实际签收时间修正。",
            "Y", conclusions, debug,
        )
    debug["delay_calc"] = delay_calc
    if delay_hours is None:
        if aviation_failure_type == "system_error":
            return _result(forceid, "转人工复核：官方航班数据查询异常，无法完成时长核算", "Y", conclusions, debug)
        conclusions.append({"checkpoint": "延误时长", "Eligible": "需补件", "Remark": "未识别到明确延误时长或签收时间信息"})
        return _result(forceid, "需补件：请补充行李签收证明（含签收时间）或承运人出具的行李延误时长证明", "Y", conclusions, debug)
    if delay_hours < 6:
        conclusions.append({"checkpoint": "赔付门槛", "Eligible": "否", "Remark": f"延误时长{delay_hours:.2f}小时，未达到6小时"})
        return _result(forceid, "拒赔：行李延误时长未达到6小时赔付门槛", "N", conclusions, debug)
    conclusions.append({"checkpoint": "赔付门槛", "Eligible": "是", "Remark": f"延误时长{delay_hours:.2f}小时，达到赔付门槛"})

    # 信息一致性校验
    consistency_violation = _check_info_consistency(claim_info, ai_parsed or {})
    if consistency_violation:
        conclusions.append({"checkpoint": "信息一致性", "Eligible": "否", "Remark": consistency_violation})
        return _result(forceid, consistency_violation, "N", conclusions, debug)

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
            conclusions.append({"checkpoint": "AI审计补件", "Eligible": "需补件", "Remark": "；".join(missing_materials)})
            return _result(forceid, "需补件：" + "；".join(missing_materials), "Y", conclusions, debug)
        elif ai_audit_result == "拒绝":
            reason = str(ai_audit.get("reason") or ai_audit.get("explanation") or "AI审核拒赔")
            conclusions.append({"checkpoint": "AI审计", "Eligible": "否", "Remark": reason})
            return _result(forceid, f"拒赔：{reason}", "N", conclusions, debug)

    # 赔付核算
    from app.modules.baggage_delay.stages.utils import _safe_float
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
