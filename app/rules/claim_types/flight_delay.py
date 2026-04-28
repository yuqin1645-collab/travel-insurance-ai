#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
航班延误险特有规则
包含：赔付档位配置、改签场景提示词块
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.rules.base import RuleResult

RULE_ID = "claim_types.flight_delay"
RULE_VERSION = "1.0"
DESCRIPTION = "航班延误险特有规则：赔付档位（5h起赔，300/600/900/1200）、改签场景判定"

# 航班延误险赔付档位（供 skills/compensation.tier_lookup() 使用）
FLIGHT_DELAY_TIERS: List[Dict[str, Any]] = [
    {"min_hours": 5,  "max_hours": 10, "amount": 300,  "currency": "CNY", "label": "延误满5小时"},
    {"min_hours": 10, "max_hours": 15, "amount": 600,  "currency": "CNY", "label": "延误满10小时"},
    {"min_hours": 15, "max_hours": 20, "amount": 900,  "currency": "CNY", "label": "延误满15小时"},
    {"min_hours": 20, "max_hours": None, "amount": 1200, "currency": "CNY", "label": "延误满20小时"},
]

PROMPT_BLOCK = """
【航班延误险赔付规则】
起赔门槛：延误满5小时。
赔付档位：
- 满5小时不足10小时：300元
- 满10小时不足15小时：600元
- 满15小时不足20小时：900元
- 满20小时及以上：1200元（最高保额）

延误时长计算——取长原则（必须遵循）：
取以下两者的较长者作为赔付时长：
a) 自原订开出时间起算，至实际（或改签后）开出时间
b) 自原订到达时间起算，至实际（或改签后）抵达原计划目的地的到达时间

改签场景时长计算（重要）：
- 基准时间：以原始被取消航班的计划起飞/到达时间为基准
- 结束时间：以改签后实际乘坐航班的实际起飞/到达时间
- 严禁：不得将改签航班自身的运营延误视为索赔延误时长
""".strip()


def check_rebooking_scenario(
    planned_dep: Optional[str],
    planned_arr: Optional[str],
    alt_dep: Optional[str],
    alt_arr: Optional[str],
) -> RuleResult:
    """
    改签场景延误时长判定（基础版本，不依赖第三方数据）。
    仅在 pipeline 中调用飞常准数据后使用。

    Args:
        planned_dep: 原计划起飞时间（ISO格式）
        planned_arr: 原计划到达时间（ISO格式）
        alt_dep: 改签后实际起飞时间（ISO格式）
        alt_arr: 改签后实际到达时间（ISO格式）

    Returns:
        RuleResult：detail["delay_minutes"] 为计算结果（分钟）
    """
    def _parse(s: Optional[str]):
        if not s:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(s[:len(fmt)], fmt)
            except Exception:
                continue
        return None

    pd = _parse(planned_dep)
    pa = _parse(planned_arr)
    ad = _parse(alt_dep)
    aa = _parse(alt_arr)

    candidates = []
    if pd and ad:
        candidates.append(int((ad - pd).total_seconds() / 60))
    if pa and aa:
        candidates.append(int((aa - pa).total_seconds() / 60))

    if not candidates:
        return RuleResult(
            passed=False,
            action="supplement",
            reason="改签场景时间信息不完整，无法计算延误时长",
            detail={"planned_dep": planned_dep, "alt_arr": alt_arr},
        )

    delay_minutes = max(candidates)
    return RuleResult(
        passed=delay_minutes > 0,
        action="continue" if delay_minutes > 0 else "reject",
        reason=f"改签场景延误时长：{delay_minutes} 分钟（取长原则）",
        detail={"delay_minutes": delay_minutes, "candidates": candidates},
    )
