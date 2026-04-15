from __future__ import annotations

from typing import Any, Dict, Optional


def build_unreliable_price_manual_return(
    *,
    forceid: str,
    compensation_result: Dict[str, Any],
    ctx: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    已提供购买凭证但无法可靠识别原价：转人工（IsAdditional=Y），不要落成“无可赔付金额”。
    """
    purchase_dbg = (
        (compensation_result.get("extraction_debug") or {}).get("purchase")
        if isinstance(compensation_result.get("extraction_debug"), dict)
        else None
    )
    purchase_amt = None
    try:
        if isinstance(purchase_dbg, dict):
            purchase_amt = purchase_dbg.get("amount")
    except Exception:
        purchase_amt = None

    if (purchase_amt is None) and float(compensation_result.get("original_value") or 0) <= 0:
        return {
            "forceid": forceid,
            "Remark": "需要人工审核: 已提交购买凭证，但系统未能可靠识别原价/实付金额，请人工核对购买凭证金额后再核算。",
            "IsAdditional": "Y",
            "KeyConclusions": [
                {
                    "checkpoint": "赔偿金额核对",
                    "Eligible": "N",
                    "Remark": "已提交购买凭证，但未能从凭证中可靠识别原价/实付金额，需人工核算。",
                }
            ],
            "DebugInfo": ctx,
        }
    return None


def build_zero_payout_return(
    *,
    forceid: str,
    compensation_result: Dict[str, Any],
    ctx: Dict[str, Any],
) -> Dict[str, Any]:
    """
    核算后应付=0：输出“无可赔付金额”口径（不等同于条款拒赔）。
    """
    ov = float(compensation_result.get("original_value") or 0)
    dep_months = int(float(compensation_result.get("depreciation_months") or 0))
    dep_rate = float(compensation_result.get("depreciation_rate") or 0)
    tp_paid = float(compensation_result.get("third_party_compensation") or 0)

    accident_type = ((ctx.get("accident") or {}).get("accident_type") or "") if isinstance(ctx, dict) else ""
    tp_label = "航司赔付" if "承运人" in str(accident_type) else "第三方赔付"

    if ov > 0 and dep_rate > 0 and dep_months > 0 and tp_paid > 0:
        formula_remark = (
            f"{ov:.0f}*（1-每月折旧率{dep_rate*100:.0f}%*损失时的购买月数{dep_months}）"
            f"-{tp_label}{tp_paid:.0f}元<0元，无可赔付金额，歉难给付。"
        )
    else:
        formula_remark = compensation_result.get("reason") or "赔偿金额为0，无可赔付金额，歉难给付。"

    return {
        "forceid": forceid,
        "Remark": formula_remark,
        "IsAdditional": "N",
        "KeyConclusions": [
            {
                "checkpoint": "赔偿金额核对",
                "Eligible": "N",
                "Remark": compensation_result.get("reason", ""),
            }
        ],
        "DebugInfo": ctx,
    }

