"""
flight_delay stages — 赔付金额计算。
"""

from __future__ import annotations

from typing import Any, Dict

from app.logging_utils import LOGGER, log_extra
from app.skills.compensation import calculate_payout, parse_tier_config_from_terms


def _run_payout_calc(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    policy_excerpt: str,
) -> Dict[str, Any]:
    """阶段10: 赔付金额预计算（代码侧，取长原则已在 computed_delay 中完成）。"""
    try:
        cd = (parsed or {}).get("computed_delay") or {}
        final_minutes = cd.get("final_minutes")
        if not isinstance(final_minutes, int) or final_minutes <= 0:
            return {"status": "not_applicable", "note": "延误时长未知或为0，不进入金额计算", "final_amount": None}

        final_hours = final_minutes / 60.0
        claim_amount = str(claim_info.get("Insured_Amount") or claim_info.get("insured_amount") or "")
        cap = None
        if claim_amount:
            try:
                cap = float(str(claim_amount).replace(",", "").strip())
            except Exception:
                cap = None

        try:
            terms_config = parse_tier_config_from_terms(policy_excerpt)
        except Exception:
            terms_config = None

        payout_result = calculate_payout(
            delay_hours=final_hours,
            claim_amount=cap,
            tier_config=terms_config,
        )

        return {"status": "calculated", **payout_result}

    except Exception as e:
        LOGGER.warning(f"[_run_payout_calc] 金额计算异常: {e}", extra=log_extra(stage="fd_payout", attempt=0))
        return {"status": "error", "note": str(e), "final_amount": None}
