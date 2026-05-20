"""
baggage_delay stages — AI审计、赔付核算与最终结果组装。
从 pipeline.py 提取，保持原逻辑不变。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.engine.workflow import StageRunner
from app.logging_utils import LOGGER, log_extra

from .calculator import _compute_tier_amount, _compute_payout_with_rules, _compute_delay_hours_by_rule
from .utils import _parse_dt_flexible, _result, _safe_float

BAGGAGE_DELAY_THRESHOLD_HOURS = 6


async def run_post_process(
    *,
    reviewer: Any,
    claim_info: Dict[str, Any],
    ai_parsed: Dict[str, Any],
    policy_terms: str,
    text_blob: str,
    vision_extract: Dict[str, Any],
    aviation_failure_type: str,
    conclusions: List[Dict[str, str]],
    debug: Dict[str, Any],
    runner: StageRunner,
    session: Any,
    forceid: str,
) -> Dict[str, Any]:
    """AI审计 + 赔付核算 + 最终结果组装。"""

    # ── 延误时长核算与门槛 ────────────────────────────────────────────────
    if debug.get("transfer_flight_receipt", {}).get("receipt_time_set"):
        delay_calc = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
        delay_calc["receipt_time_source"] = "transfer_flight_arrival"
        delay_hours = delay_calc.get("delay_hours")

        if delay_hours is None:
            parsed_delay = (ai_parsed or {}).get("delay_hours")
            if parsed_delay is not None:
                try:
                    delay_hours = float(parsed_delay)
                    delay_calc["delay_hours"] = delay_hours
                    delay_calc["method"] = "transfer_flight_fallback"
                except (ValueError, TypeError):
                    pass

        if delay_hours is None:
            transfer_receipt = debug.get("transfer_flight_receipt", {})
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
        if delay_hours is not None and delay_hours >= BAGGAGE_DELAY_THRESHOLD_HOURS:
            conclusions.append({
                "checkpoint": "行李签收时间",
                "Eligible": "是",
                "Remark": f"以后续转运航班到达时间为行李签收时间代理，延误时长{delay_hours_str}，达到赔付门槛",
            })
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
        if delay_calc.get("receipt_time_midnight_placeholder"):
            debug["midnight_placeholder_override"] = (
                f"延误时长{delay_hours:.2f}h但签收时间为00:00占位符，时长不可靠，转AI审计"
            )
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

    # ── 材料门禁后的人工复核 flags（仅视觉 risk_flags） ─────────────────
    manual_flags = []
    vision_risk_flags = vision_extract.get("risk_flags") or []
    if isinstance(vision_risk_flags, list):
        for flag in vision_risk_flags:
            flag_lower = str(flag).strip().lower()
            if flag_lower in ("handwritten", "手写"):
                manual_flags.append("handwritten")
            elif flag_lower in ("conflict", "冲突"):
                manual_flags.append("conflict")
            elif flag_lower in ("fraud_suspect", "疑似伪造"):
                manual_flags.append("疑似伪造")
    parsed_risk = str((ai_parsed or {}).get("manual_review_risk") or "").strip().lower()
    if parsed_risk and parsed_risk not in {"none", "unknown"}:
        manual_flags.append(parsed_risk)

    missing_materials = debug.get("missing_materials") or []
    if manual_flags:
        if manual_flags == ["conflict"] and not missing_materials:
            debug["manual_review_flags"] = manual_flags
            debug["conflict_downgraded"] = True
            conclusions.append({"checkpoint": "人工复核触发", "Eligible": "是", "Remark": "AI标记conflict但材料完整，降级为审核通过（非实质性责任冲突）"})
        else:
            debug["manual_review_flags"] = manual_flags
            conclusions.append({"checkpoint": "人工复核触发", "Eligible": "需人工判断", "Remark": f"命中关键词: {','.join(manual_flags)}"})
            return _result(forceid, "转人工复核：存在材料识别或真实性争议", "Y", conclusions, debug)

    # ── AI审核意见 ────────────────────────────────────────────────────────
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
            for item in ai_missing:
                if any(kw in item for kw in vision_confirmed):
                    continue
                if item not in set(missing_materials):
                    missing_materials.append(item)
            missing_materials = sorted(set(missing_materials))
            debug["missing_materials"] = missing_materials
        if ai_audit_result == "需补齐资料" and missing_materials:
            if debug.get("receipt_proxy_accepted"):
                receipt_kw = {"签收", "receipt", "领取", "提取", "取件", "派送", "送达", "交付"}
                non_receipt_missing = [
                    m for m in missing_materials
                    if not any(kw in m.lower() for kw in receipt_kw)
                ]
                if not non_receipt_missing:
                    LOGGER.info(
                        "baggage_delay: 转运航班代理签收已确认，覆盖AI模型误判补件",
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

    # ── 赔付核算 ──────────────────────────────────────────────────────────
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