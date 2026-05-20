from __future__ import annotations

from typing import Any, Dict


def is_system_failure_reason(reason: str) -> bool:
    """判断 AI 返回的 reason 字符串是否表明系统侧异常（而非业务判定）。"""
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


def build_stage_error_return_from_reason(
    *,
    forceid: str,
    checkpoint: str,
    reason: str,
    ctx: Dict[str, Any],
) -> Dict[str, Any]:
    """
    基于字符串原因构建"转人工"错误回包。

    与 build_stage_error_return 的区别：该函数接收已提取的字符串 reason，
    用于 AI 调用成功但返回了系统失败标记的场景（而非调用本身抛出异常）。
    """
    msg = (reason or "系统异常").strip()[:200]
    return {
        "forceid": forceid,
        "Remark": f"需要人工审核: {checkpoint}系统异常: {msg}",
        "IsAdditional": "Y",
        "KeyConclusions": [
            {
                "checkpoint": checkpoint,
                "Eligible": "N",
                "Remark": f"{checkpoint}系统异常，已转人工审核: {msg}",
            }
        ],
        "DebugInfo": ctx,
    }