"""
baggage_delay stages — 延误时长计算与赔付核算。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.rules.claim_types.baggage_delay import compute_payout as _rules_compute_payout

from .utils import (
    _collect_receipt_times,
    _extract_delay_hours,
    _extract_delay_hours_from_parsed,
    _parse_dt_flexible,
    _safe_float,
)


def _compute_delay_hours_by_rule(
    parsed: Dict[str, Any],
    text_blob: str,
) -> Dict[str, Any]:
    """
    行李延误规则口径：
    延误时长 = 行李实际签收时间（最晚） - 行李应到达时间

    行李应到达时间的选取优先级：
    1. 联程/改签场景：alternate 航班的预计到达时间（最终目的地）
    2. 非联程场景：首次乘坐航班实际到达时间

    时区处理：如果时间字符串不含时区信息，尝试从IATA机场代码反推时区。
    """
    result: Dict[str, Any] = {
        "delay_hours": None,
        "method": "unknown",
        "flight_actual_arrival_time": None,
        "baggage_receipt_time": None,
    }
    if not isinstance(parsed, dict):
        v = _extract_delay_hours(text_blob)
        if v is not None:
            result.update({"delay_hours": v, "method": "text_fallback"})
        return result

    # 选取行李应到达时间：
    # 联程/改签场景优先用 alternate 的预计到达时间（行李应运抵最终目的地的时间）
    arrival_dt = _parse_dt_flexible(parsed.get("flight_actual_arrival_time"))
    # 先收集签收时间列表（用于alternate到达时间自检）
    receipt_list = _collect_receipt_times(parsed)

    alternate = parsed.get("alternate") or {}
    if isinstance(alternate, dict):
        is_rebooking = str(alternate.get("is_connecting_rebooking") or "").strip().lower() == "true"
        if is_rebooking:
            # 改签场景：行李应运抵的时间点为改签航班的**起飞时间**（alt_dep），
            # 而非到达时间。因为乘客改签后，行李应随同乘客搭乘改签航班。
            # 延误时长 = 行李签收时间 - 改签航班起飞时间
            alt_dep_raw = alternate.get("alt_dep")
            alt_dep_dt = _parse_dt_flexible(alt_dep_raw)
            if alt_dep_dt and (not arrival_dt or alt_dep_dt > arrival_dt):
                arrival_dt = alt_dep_dt
                result["arrival_time_source"] = "alternate_departure"
            else:
                result["arrival_time_source"] = "original_arrival"
        else:
            # 联程/非改签场景：用 alternate 的预计到达时间
            alt_arr_raw = alternate.get("alt_arr") or alternate.get("alt_dep")
            alt_arr_dt = _parse_dt_flexible(alt_arr_raw)
            # 自检：若 alternate 到达时间与行李签收时间完全相同（同日同时），
            # 说明 Vision 将签收时间误填入了 alt_arr，不可用于计算
            if alt_arr_dt and receipt_list:
                receipt_max = max(receipt_list)
                if (alt_arr_dt.year == receipt_max.year and alt_arr_dt.month == receipt_max.month
                        and alt_arr_dt.day == receipt_max.day and alt_arr_dt.hour == receipt_max.hour
                        and alt_arr_dt.minute == receipt_max.minute):
                    alt_arr_dt = None  # 舍弃，回退到原航班到达时间
                    result["alternate_arrival_rejected"] = "alternate到达时间与行李签收时间相同，判定为数据重复，回退到原航班到达时间"
            if alt_arr_dt and (not arrival_dt or alt_arr_dt > arrival_dt):
                arrival_dt = alt_arr_dt
                result["arrival_time_source"] = "alternate_arrival"
            else:
                result["arrival_time_source"] = "original_arrival"
    else:
        result["arrival_time_source"] = "original_arrival"

    receipt_dt = max(receipt_list) if receipt_list else None

    # 签收时间来源校验：只有 actual_receipt/airport_counter/courier_delivery 才是有效签收时间
    # PIR创建时间、邮件预估、文字描述等不得用于计算延误时长
    receipt_source = str(parsed.get("baggage_receipt_time_source") or "").strip().lower()
    valid_receipt_sources = {"actual_receipt", "airport_counter", "courier_delivery"}
    if receipt_source and receipt_source not in valid_receipt_sources:
        # 改签场景例外：当 alternate.is_connecting_rebooking=true 且 AI 解析出了签收时间，
        # 即使来源不明也接受（因为转运航班到达时间本身就是一种间接签收时间）
        is_rebooking = str(alternate.get("is_connecting_rebooking") or "").strip().lower() == "true"
        if is_rebooking and receipt_list:
            result["receipt_source_overridden"] = "改签场景，AI解析出的签收时间来源不明但接受作为计算依据"
        else:
            # 签收时间来源明确但不是有效类型（如 pir_creation/email_estimate），跳过计算
            result["receipt_source_invalid"] = True
            result["receipt_source"] = receipt_source
            receipt_dt = None

    # 00:00占位符检测：签收时间仅有日期无时间信息时，Vision可能默认为00:00
    # 这种情况下的延误时长计算极不可靠，标记警告
    if receipt_dt and receipt_dt.hour == 0 and receipt_dt.minute == 0:
        result["receipt_time_midnight_placeholder"] = True

    if arrival_dt and receipt_dt and receipt_dt >= arrival_dt:
        delta_hours = (receipt_dt - arrival_dt).total_seconds() / 3600.0
        result.update(
            {
                "delay_hours": round(delta_hours, 2),
                "method": "arrival_receipt_delta",
                "flight_actual_arrival_time": arrival_dt.strftime("%Y-%m-%d %H:%M"),
                "baggage_receipt_time": receipt_dt.strftime("%Y-%m-%d %H:%M"),
            }
        )
        return result

    # 如果计算失败，尝试用文本提取的延误时长
    parsed_hours = _extract_delay_hours_from_parsed(parsed)
    if parsed_hours is not None:
        # 自检：当无签收时间证明时，Vision 输出的 delay_hours=0 不可信
        if parsed_hours == 0 and not receipt_list:
            result["parsed_delay_rejected"] = "无行李签收时间证明，Vision输出的delay_hours=0不可信，跳过"
        else:
            result.update({"delay_hours": parsed_hours, "method": "parsed_delay_hours"})
            return result

    text_hours = _extract_delay_hours(text_blob)
    if text_hours is not None:
        result.update({"delay_hours": text_hours, "method": "text_fallback"})
    return result


def _compute_tier_amount(delay_hours: float) -> int:
    """根据延误时长计算档位金额（纯代码逻辑，不调 LLM）。"""
    if delay_hours >= 18:
        return 1500
    elif delay_hours >= 12:
        return 1000
    elif delay_hours >= 6:
        return 500
    return 0


def _compute_payout_with_rules(
    delay_hours: float,
    claim_amount: Optional[float],
    cap: Optional[float],
    ai_parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
) -> float:
    """赔付金额核算（委托 rules.claim_types.baggage_delay）。"""
    personal_claim = _safe_float(claim_info.get("Personal_Effect_Claim_Amount"))
    result = _rules_compute_payout(delay_hours, claim_amount, cap, personal_claim)
    return result.detail.get("payout", 0.0)
