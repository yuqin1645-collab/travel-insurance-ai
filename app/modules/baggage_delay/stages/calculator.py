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
    _parse_dt_to_utc,
    _resolve_iana_fallback,
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

    # 解析IATA机场代码 → IANA时区（用于无时区时间字符串的时区推断）
    route = parsed.get("route") or {}
    dep_iata = str(route.get("dep_iata") or "").strip().upper()
    arr_iata = str(route.get("arr_iata") or "").strip().upper()
    dep_iana = _resolve_iana_fallback(dep_iata) if dep_iata else None
    arr_iana = _resolve_iana_fallback(arr_iata) if arr_iata else None

    # 选取行李应到达时间：
    # 联程/改签场景优先用 alternate 的预计到达时间（行李应运抵最终目的地的时间）
    arrival_dt = _parse_dt_to_utc(parsed.get("flight_actual_arrival_time"), iana_hint=arr_iana)
    # 先收集签收时间列表（用于alternate到达时间自检）
    receipt_list = _collect_receipt_times(parsed, iana_hint=arr_iana)

    alternate = parsed.get("alternate") or {}
    if isinstance(alternate, dict):
        is_rebooking = str(alternate.get("is_connecting_rebooking") or "").strip().lower() == "true"
        if is_rebooking:
            # 改签场景：行李应运抵的时间点为改签航班的**到达时间**（alt_arr），
            # 因为乘客改签后，行李随同乘客搭乘改签航班，应运抵目的地时间为到达时间。
            # 若行李签收时间 = 改签航班到达时间，说明行李正常随机到达，无额外延误。
            alt_arr_raw = alternate.get("alt_arr") or alternate.get("alt_dep")
            alt_arr_dt = _parse_dt_to_utc(alt_arr_raw, iana_hint=arr_iana)
            # 自检1：若 alt_arr 与行李签收时间完全相同，说明行李随改签航班正常到达，
            # 无额外延误，直接返回 0 小时
            if alt_arr_dt and receipt_list:
                receipt_max = max(receipt_list)
                if (alt_arr_dt.year == receipt_max.year and alt_arr_dt.month == receipt_max.month
                        and alt_arr_dt.day == receipt_max.day and alt_arr_dt.hour == receipt_max.hour
                        and alt_arr_dt.minute == receipt_max.minute):
                    result.update({
                        "delay_hours": 0,
                        "method": "rebooking_normal_arrival",
                        "flight_actual_arrival_time": alt_arr_dt.strftime("%Y-%m-%d %H:%M"),
                        "baggage_receipt_time": receipt_max.strftime("%Y-%m-%d %H:%M"),
                        "rebooking_note": "行李签收时间=改签航班到达时间，行李随机正常到达，无额外延误",
                    })
                    return result
            # 自检2：若 alternate 航班号不符合标准格式（2字母+1~4数字），也舍弃
            if alt_arr_dt:
                import re as _re
                alt_flight_no = str(alternate.get("alt_flight_no") or "").strip()
                if alt_flight_no and not _re.match(r'^[A-Za-z]{2}\d{1,4}$', alt_flight_no):
                    alt_arr_dt = None
                    result["alternate_flight_no_invalid"] = f"alternate航班号'{alt_flight_no}'格式不符，非标准航班号，舍弃"
            if alt_arr_dt and (not arrival_dt or alt_arr_dt > arrival_dt):
                arrival_dt = alt_arr_dt
                result["arrival_time_source"] = "alternate_arrival_rebooking"
            else:
                result["arrival_time_source"] = "original_arrival"
        else:
            # 联程/非改签场景：用 alternate 的预计到达时间
            alt_arr_raw = alternate.get("alt_arr") or alternate.get("alt_dep")
            alt_arr_dt = _parse_dt_to_utc(alt_arr_raw, iana_hint=arr_iana)
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
        # 航司邮件/APP通知中的行李送达时间：当无实际签收证明时，航司官方邮件/APP中的送达时间
        # 可作为合理代理（特别是行李转运航班场景）
        elif receipt_source in ("email_estimate", "app_estimate") and receipt_list:
            result["receipt_source_accepted_as_proxy"] = (
                f"来源{receipt_source}为航司官方渠道的送达时间，在无实际签收证明时作为合理代理"
            )
        # 来源不明但有实际时间数据：接受计算（避免过度严格导致时长无法计算）
        elif receipt_source in ("", "unknown") and receipt_list:
            result["receipt_source_unknown_accepted"] = "来源不明但有实际时间数据，接受用于延误时长计算"
        else:
            # 签收时间来源明确但不是有效类型（如 pir_creation），跳过计算
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
        # 自检：当无可靠签收时间证明时，Vision/LLM 输出的 delay_hours 不可信
        # 可靠签收时间来源：actual_receipt/airport_counter/courier_delivery
        # email_estimate/app_estimate/unknown 等来源不足以支撑 parsed_delay_hours
        _is_reliable_receipt = receipt_source in valid_receipt_sources and receipt_list
        if not _is_reliable_receipt:
            result["parsed_delay_rejected"] = (
                f"无可靠行李签收时间证明（来源={receipt_source or 'unknown'}），"
                f"LLM提取的delay_hours={parsed_hours}不可信（可能为航班延误而非行李延误），跳过"
            )
        elif parsed_hours == 0:
            result["parsed_delay_rejected"] = "无行李签收时间证明，Vision输出的delay_hours=0不可信，跳过"
        else:
            result.update({"delay_hours": parsed_hours, "method": "parsed_delay_hours"})
            return result

    text_hours = _extract_delay_hours(text_blob)
    if text_hours is not None:
        # 同样：无可靠签收时间时，纯文本提取不可信
        _is_reliable_receipt = receipt_source in valid_receipt_sources and receipt_list
        if not _is_reliable_receipt:
            result["text_fallback_rejected"] = (
                f"无可靠行李签收时间证明（来源={receipt_source or 'unknown'}），"
                f"文本提取的delay_hours={text_hours}不可信，跳过"
            )
        else:
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
