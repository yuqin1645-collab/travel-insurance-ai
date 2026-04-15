from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def is_system_failure_reason(reason: str) -> bool:
    r = (reason or "").strip()
    if not r:
        return False
    keywords = (
        "API调用失败",
        "系统异常",
        "Cannot connect to host",
        "timeout",
        "timed out",
        "429",
        "503",
    )
    return any(k in r for k in keywords)


def build_stage_system_failure_return(
    *,
    forceid: str,
    stage_checkpoint: str,
    reason: str,
    ctx: Dict[str, Any],
) -> Dict[str, Any]:
    """
    将“系统异常/调用异常”统一输出为转人工（IsAdditional=Y），避免误判为拒赔/通过。
    """
    msg = (reason or "系统异常").strip()
    return {
        "forceid": forceid,
        "Remark": f"需要人工审核: {stage_checkpoint}系统异常: {msg[:200]}",
        "IsAdditional": "Y",
        "KeyConclusions": [
            {
                "checkpoint": stage_checkpoint,
                "Eligible": "N",
                "Remark": f"{stage_checkpoint}系统异常，已转人工审核: {msg[:200]}",
            }
        ],
        "DebugInfo": ctx,
    }

