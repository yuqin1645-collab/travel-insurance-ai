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
    延误时长 = 行李实际签收时间（最晚） - 首次乘坐航班实际到达时间
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

    arrival_dt = _parse_dt_flexible(parsed.get("flight_actual_arrival_time"))
    receipt_list = _collect_receipt_times(parsed)
    receipt_dt = max(receipt_list) if receipt_list else None

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

    parsed_hours = _extract_delay_hours_from_parsed(parsed)
    if parsed_hours is not None:
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
