from __future__ import annotations

from typing import Any, Dict


def build_approval_return(
    *,
    forceid: str,
    final_amount: float,
    coverage_result: Dict[str, Any],
    material_result: Dict[str, Any],
    accident_result: Dict[str, Any],
    manual_review_hint: str,
    ctx: Dict[str, Any],
) -> Dict[str, Any]:
    """
    全部通过，同意赔付：统一构造最终返回体（IsAdditional=N）。
    """
    return {
        "forceid": forceid,
        "Remark": f"审核通过,同意赔付{final_amount}元",
        "IsAdditional": "N",
        "KeyConclusions": [
            {
                "checkpoint": "保障责任核对",
                "Eligible": "Y",
                "Remark": coverage_result.get("reason", ""),
            },
            {
                "checkpoint": "材料完整性核对",
                "Eligible": "Y",
                "Remark": ("材料齐全（建议人工核对: " + manual_review_hint + "）") if manual_review_hint else "材料齐全",
            },
            {
                "checkpoint": "保障范围核对",
                "Eligible": "Y",
                "Remark": accident_result.get("coverage_reason", ""),
            },
            {
                "checkpoint": "除外责任核对",
                "Eligible": "Y",
                "Remark": "未触发除外责任",
            },
            {
                "checkpoint": "赔偿金额核对",
                "Eligible": "Y",
                "Remark": f"最终赔付金额: {final_amount}元",
            },
        ],
        "DebugInfo": ctx,
    }

