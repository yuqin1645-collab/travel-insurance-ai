#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用规则：条款除外责任校验
来源：baggage_delay/pipeline.py:333-352 的 _check_exclusions()，参数化通用版本
"""

from typing import Any, Dict, List, Optional, Tuple

from app.rules.base import RuleResult

RULE_ID = "flight.exclusions"
RULE_VERSION = "1.0"
DESCRIPTION = "条款除外责任校验：战争/罢工/恐怖活动/海关没收等，参数化通用版本"

PROMPT_BLOCK = """
【条款除外责任】
命中以下任意情形即拒赔：
1. 战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱
2. 恐怖活动
3. 海关或其他政府部门没收、扣留、隔离、检验或销毁
4. 权益人行为导致（如未通知承运人、留置行李等）

航班延误险额外除外情形：
- 可预见因素（投保时已知延误/取消）
- 被保险人未准时登乘原计划航班

行李延误险额外除外情形：
- 未将行李延误一事通知有关公共交通工具承运人
- 非于该次旅行时托运之行李
- 权益人留置其行李于公共交通工具承运人或其代理人
""".strip()

# 行李延误险除外条款（关键词, 拒赔原因）
BAGGAGE_DELAY_EXCLUSIONS: List[Tuple[str, str]] = [
    ("海关或其他政府部门没收、扣留、隔离、检验或销毁", "海关/政府部门没收导致，属除外责任"),
    ("未将行李延误一事通知有关公共交通工具承运人", "未通知承运人，属除外责任"),
    ("非于该次旅行时托运之行李", "非本次旅行托运行李，属除外责任"),
    ("权益人留置其行李于公共交通工具承运人或其代理人", "行李被留置，属除外责任"),
    ("战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱", "战争/罢工等社会风险，属除外责任"),
    ("恐怖活动", "恐怖活动，属除外责任"),
]

# 航班延误险除外条款
FLIGHT_DELAY_EXCLUSIONS: List[Tuple[str, str]] = [
    ("战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱", "战争/罢工等社会风险，属除外责任"),
    ("恐怖活动", "恐怖活动，属除外责任"),
    ("海关或其他政府部门没收、扣留", "海关/政府部门干预，属除外责任"),
]


def check(
    content: str,
    exclusion_checks: List[Tuple[str, str]],
    extra_text: Optional[str] = None,
) -> RuleResult:
    """
    条款除外责任校验。

    Args:
        content: 案件文本内容（description + assessment + parsed notes 拼接后小写化）
        exclusion_checks: 除外条款列表 [(关键词, 拒赔原因), ...]
        extra_text: 额外检查文本（如 parsed.notes）

    Returns:
        RuleResult：
            passed=True  → 无除外情形，action="continue"
            passed=False → 命中除外条款，action="reject"，reason 为拒赔原因
    """
    full_content = content.lower()
    if extra_text:
        full_content += " " + str(extra_text).lower()

    for keyword, reason in exclusion_checks:
        if keyword in full_content:
            return RuleResult(
                passed=False,
                action="reject",
                reason=reason,
                detail={"matched_keyword": keyword},
            )

    return RuleResult(
        passed=True,
        action="continue",
        reason="未命中任何除外责任条款",
        detail={},
    )
