from __future__ import annotations

from typing import Any, Dict


def build_denial_return(
    *,
    forceid: str,
    checkpoint: str,
    remark: str,
    ctx: Dict[str, Any],
) -> Dict[str, Any]:
    """
    统一构造拒赔返回体（IsAdditional=N）。
    """
    return {
        "forceid": forceid,
        "Remark": f"拒赔: {remark}",
        "IsAdditional": "N",
        "KeyConclusions": [
            {
                "checkpoint": checkpoint,
                "Eligible": "N",
                "Remark": remark,
            }
        ],
        "DebugInfo": ctx,
    }

