"""
阶段10: 赔付金额核算
Skill/计算逻辑：延误时长 -> 金额 tier 映射
用于：根据延误小时数按条款档位计算赔付金额
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.logging_utils import LOGGER

# 默认赔付档位（若条款未结构化，使用此兜底）
# 规则：每满5小时赔付300元，最高保额1200元（最多4档）
# 起赔门槛：满5小时
_DEFAULT_TIER_CONFIG: List[Dict[str, Any]] = [
    {"min_hours": 5,  "max_hours": 10, "amount": 300,  "currency": "CNY", "label": "延误满5小时"},
    {"min_hours": 10, "max_hours": 15, "amount": 600,  "currency": "CNY", "label": "延误满10小时"},
    {"min_hours": 15, "max_hours": 20, "amount": 900,  "currency": "CNY", "label": "延误满15小时"},
    {"min_hours": 20, "max_hours": None, "amount": 1200, "currency": "CNY", "label": "延误满20小时"},
]


def tier_lookup(
    delay_minutes: int,
    tier_config: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    根据延误分钟数在 tier_config 中查找对应赔付档位。

    Args:
        delay_minutes: 延误分钟数（已由代码侧取长原则计算）
        tier_config: 档位配置（可从条款解析结果传入；默认使用兜底配置）

    Returns:
        {
            "delay_minutes": int,
            "delay_hours_display": str,   # 展示用，如 "4小时30分"
            "matched_tier": dict | None,   # 命中的档位
            "amount": int | float,         # 建议赔付金额
            "currency": str,
            "basis": str,                  # 依据说明
        }
    """
    config = tier_config or _DEFAULT_TIER_CONFIG
    delay_hours = delay_minutes / 60.0

    display_h = int(delay_minutes // 60)
    display_m = int(delay_minutes % 60)
    if display_m > 0:
        delay_hours_display = f"{display_h}小时{display_m}分"
    else:
        delay_hours_display = f"{display_h}小时"

    # 按 min_hours 降序排列，取第一个满足的档位（即"最高匹配"）
    # 例如 min_hours=[2,4]，延误6小时，取4小时那档（更高档）
    sorted_config = sorted(config, key=lambda x: x.get("min_hours", 0), reverse=True)

    for tier in sorted_config:
        min_h = tier.get("min_hours", 0)
        max_h = tier.get("max_hours")
        if delay_hours >= min_h and (max_h is None or delay_hours < max_h):
            return {
                "delay_minutes": delay_minutes,
                "delay_hours_display": delay_hours_display,
                "matched_tier": tier,
                "amount": tier.get("amount", 0),
                "currency": tier.get("currency", "CNY"),
                "basis": f"延误{delay_hours_display}，命中档位：{tier.get('label', str(tier))}",
            }

    # 未命中任何档位（低于起赔门槛）
    min_tier_hours = min(t.get("min_hours", 0) for t in config) if config else 4
    return {
        "delay_minutes": delay_minutes,
        "delay_hours_display": delay_hours_display,
        "matched_tier": None,
        "amount": 0,
        "currency": "CNY",
        "basis": f"延误{delay_hours_display}，未达起赔门槛{min_tier_hours}小时，不予赔付",
    }


def parse_tier_config_from_terms(policy_terms_excerpt: str) -> Optional[List[Dict[str, Any]]]:
    """
    从条款摘录中解析赔付档位配置（支持常见中文表述）。
    返回 None 表示无法解析（使用默认兜底）。

    支持格式：
    - "延误满4小时 赔付300元"
    - "延误4小时以上，赔付300元"
    - "4小时300元；6小时500元"
    """
    if not policy_terms_excerpt:
        return None

    text = policy_terms_excerpt

    # 尝试解析多档位格式
    patterns = [
        # "延误满X小时赔付Y元" / "延误满X小时，给付Y元"
        r"延误满\s*(\d+)\s*小时[，,]?\s*(?:赔付|给付|赔偿|支付)\s*(\d+(?:\.\d+)?)\s*元",
        # "延误X小时以上赔Y元"
        r"延误\s*(\d+)\s*小时(?:以上|以上者)?[，,]?\s*(?:赔付|给付|赔偿|支付)\s*(\d+(?:\.\d+)?)\s*元",
        # "X小时Y元"（简化格式）
        r"(\d+)\s*小时\s*[，,]?\s*(\d+(?:\.\d+)?)\s*元",
    ]

    tiers = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            try:
                h = int(m.group(1))
                amt = float(m.group(2))
                # 去重
                if not any(t["min_hours"] == h for t in tiers):
                    tiers.append({
                        "min_hours": h,
                        "max_hours": None,
                        "amount": int(amt) if amt == int(amt) else amt,
                        "currency": "CNY",
                        "label": f"延误满{h}小时",
                    })
            except Exception:
                continue

    if not tiers:
        return None

    # 排序后设置 max_hours
    tiers.sort(key=lambda x: x["min_hours"])
    for i in range(len(tiers) - 1):
        tiers[i]["max_hours"] = tiers[i + 1]["min_hours"]

    return tiers


def calculate_payout(
    delay_minutes: int,
    claim_amount: Optional[float],
    insured_amount: Optional[float],
    policy_terms_excerpt: str = "",
    remaining_coverage: Optional[float] = None,
) -> Dict[str, Any]:
    """
    计算最终赔付金额（结合条款档位 + 索赔金额 + 限额比对）。

    规则：
    - 先按延误时长查 tier -> calculated_amount
    - claim_amount > calculated_amount: 按 calculated_amount 赔付
    - claim_amount <= calculated_amount: 按 claim_amount 赔付
    - 赔付金额不超过 insured_amount（保单限额）
    - 赔付金额不超过 remaining_coverage（剩余保额）
    - 最终 = min(calculated_amount, claim_amount, remaining_coverage)

    Returns:
        {
            "delay_minutes": int,
            "calculated_amount": float,
            "claim_amount": float | None,
            "insured_amount": float | None,
            "final_amount": float,
            "currency": str,
            "basis": str,
            "notes": [...],
        }
    """
    tier_config = parse_tier_config_from_terms(policy_terms_excerpt) or _DEFAULT_TIER_CONFIG
    tier_result = tier_lookup(delay_minutes, tier_config)
    calculated = float(tier_result["amount"])
    notes: List[str] = [tier_result["basis"]]

    final = calculated
    if claim_amount is not None:
        if claim_amount < calculated:
            final = claim_amount
            notes.append(f"索赔金额{claim_amount}元 < 计算金额{calculated}元，按索赔金额赔付")
        elif claim_amount > calculated:
            notes.append(f"索赔金额{claim_amount}元 > 计算金额{calculated}元，按计算金额赔付")

    if insured_amount is not None and final > insured_amount:
        final = insured_amount
        notes.append(f"赔付金额超过保单限额{insured_amount}元，按限额赔付")

    if remaining_coverage is not None and final > remaining_coverage:
        final = remaining_coverage
        notes.append(f"赔付金额超过剩余保额{remaining_coverage}元，按剩余保额赔付")

    return {
        "delay_minutes": delay_minutes,
        "calculated_amount": calculated,
        "claim_amount": claim_amount,
        "insured_amount": insured_amount,
        "remaining_coverage": remaining_coverage,
        "final_amount": final,
        "currency": tier_result.get("currency", "CNY"),
        "basis": "；".join(notes),
        "notes": notes,
    }
