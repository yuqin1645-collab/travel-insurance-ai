from __future__ import annotations

from typing import Any, Dict, Optional


def build_exclusion_early_return(
    *,
    forceid: str,
    accident_result: Dict[str, Any],
    ctx: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    除外责任门禁：一旦明确触发除外责任，直接拒赔（不因缺件继续后续流程）。
    返回：
    - dict: 直接作为 review_claim_async 的返回值（拒赔）
    - None: 表示未触发除外责任
    """
    if not bool(accident_result.get("is_excluded", False)):
        return None

    reason = str(accident_result.get("exclusion_reason") or "触发除外责任条款")
    return {
        "forceid": forceid,
        "Remark": f"拒赔: {reason}",
        "IsAdditional": "N",
        "KeyConclusions": [
            {
                "checkpoint": "除外责任核对",
                "Eligible": "N",
                "Remark": reason,
            }
        ],
        "DebugInfo": ctx,
    }

