from __future__ import annotations

from typing import Any, Dict, Optional


def build_exception_result(
    *,
    forceid: str = "unknown",
    err: Exception,
    ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    统一的异常兜底返回体：系统异常一律转人工（IsAdditional=Y）。
    """
    msg = str(err)
    debug = ctx if isinstance(ctx, dict) else {"debug": [{"stage": "unknown", "attempt": 1, "error": msg[:200]}]}
    return {
        "forceid": forceid or "unknown",
        "Remark": f"系统异常: {msg}",
        "IsAdditional": "Y",
        "KeyConclusions": [
            {
                "checkpoint": "系统处理",
                "Eligible": "N",
                "Remark": f"处理异常，需要人工审核: {msg}",
            }
        ],
        "DebugInfo": debug,
    }

