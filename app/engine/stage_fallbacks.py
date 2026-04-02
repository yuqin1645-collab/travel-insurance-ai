from __future__ import annotations

from typing import Any, Dict


def build_stage_error_return(
    *,
    forceid: str,
    checkpoint: str,
    err: Exception,
    ctx: Dict[str, Any],
    remark_prefix: str = "需要人工审核",
) -> Dict[str, Any]:
    """
    单阶段调用异常时的统一返回体：一律转人工（IsAdditional=Y）。
    """
    msg = str(err)[:200]
    return {
        "forceid": forceid,
        "Remark": f"{remark_prefix}: {checkpoint}系统异常: {msg}",
        "IsAdditional": "Y",
        "KeyConclusions": [
            {
                "checkpoint": checkpoint,
                "Eligible": "N",
                "Remark": f"{checkpoint}系统异常，已转人工审核",
            }
        ],
        "DebugInfo": ctx,
    }

