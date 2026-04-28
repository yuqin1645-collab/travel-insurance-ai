#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行李延误险特有规则
包含：赔付档位配置、责任竞合处理
来源：baggage_delay/pipeline.py:400-413 的 _compute_payout()，迁移后调用 skills/compensation.tier_lookup()
"""

from typing import Any, Dict, List, Optional

from app.rules.base import RuleResult
from app.skills.compensation import tier_lookup

RULE_ID = "claim_types.baggage_delay"
RULE_VERSION = "1.0"
DESCRIPTION = "行李延误险特有规则：6h起赔、赔付档位（500/1000/1500）、责任竞合"

# 行李延误险赔付档位（供 skills/compensation.tier_lookup() 使用）
BAGGAGE_DELAY_TIERS: List[Dict[str, Any]] = [
    {"min_hours": 6,  "max_hours": 12, "amount": 500,  "currency": "CNY", "label": "延误满6小时"},
    {"min_hours": 12, "max_hours": 18, "amount": 1000, "currency": "CNY", "label": "延误满12小时"},
    {"min_hours": 18, "max_hours": None, "amount": 1500, "currency": "CNY", "label": "延误满18小时"},
]

PROMPT_BLOCK = """
【行李延误险赔付规则】
起赔门槛：行李延误满6小时。
赔付档位：
- 满6小时不足12小时：500元
- 满12小时不足18小时：1000元
- 满18小时及以上：1500元（最高保额）

延误时长计算口径：
延误时长 = 行李实际签收时间（最晚） - 首次乘坐航班实际到达时间

责任竞合处理：
若同一案件同时申请"行李延误"与"随身财产损失"，按较高额度赔付，不重复理赔。

除外情形（命中任一则拒赔）：
1. 海关或其他政府部门没收、扣留、隔离、检验或销毁
2. 未将行李延误一事通知有关公共交通工具承运人
3. 非于该次旅行时托运之行李
4. 权益人留置其行李于公共交通工具承运人或其代理人
5. 战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱
6. 恐怖活动
""".strip()


def compute_payout(
    delay_hours: float,
    claim_amount: Optional[float] = None,
    cap: Optional[float] = None,
    personal_effect_claim: Optional[float] = None,
) -> RuleResult:
    """
    行李延误赔付金额核算（调用 skills/compensation.tier_lookup()）。

    Args:
        delay_hours: 延误小时数
        claim_amount: 申请赔付金额（可选）
        cap: 保额上限（min(insured_amount, remaining_coverage)）
        personal_effect_claim: 随身财产损失申请金额（责任竞合时使用）

    Returns:
        RuleResult：detail["payout"] 为最终赔付金额
    """
    delay_minutes = int(delay_hours * 60)
    tier_result = tier_lookup(delay_minutes, BAGGAGE_DELAY_TIERS)
    base = float(tier_result["amount"])

    # 起赔门槛检查
    if base == 0:
        return RuleResult(
            passed=False,
            action="reject",
            reason=f"行李延误时长{delay_hours:.2f}小时，未达到6小时赔付门槛",
            detail={"delay_hours": delay_hours, "payout": 0.0, "tier_result": tier_result},
        )

    # 责任竞合：若有随身财产损失申请，按较高额度赔付
    if personal_effect_claim is not None and personal_effect_claim > 0:
        base = max(base, personal_effect_claim)

    # 索赔金额校准（取申请金额与核算金额的较小值）
    if claim_amount is not None and claim_amount > 0:
        base = min(base, claim_amount)

    # 保额上限
    if cap is not None and cap >= 0:
        base = min(base, cap)

    return RuleResult(
        passed=True,
        action="approve",
        reason=f"按阶梯核算赔付金额{base:.2f}元",
        detail={
            "delay_hours": delay_hours,
            "payout": round(base, 2),
            "tier_result": tier_result,
            "personal_effect_claim": personal_effect_claim,
        },
    )
