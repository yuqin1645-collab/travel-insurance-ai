#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则注册表
记录每条规则的元数据，便于追踪版本与使用方
"""

from typing import Dict, Any

# 注册表：rule_id -> 元数据
RULE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "common.policy_validity": {
        "version": "1.1",
        "description": "保单有效期判定：主险状态校验 + 4时间点任一在期内 + 身份匹配",
        "used_by": ["baggage_delay", "flight_delay"],
    },
    "common.identity_check": {
        "version": "1.0",
        "description": "申请人与保单权益人姓名/证件号一致性校验",
        "used_by": ["baggage_delay", "flight_delay"],
    },
    "common.material_gate": {
        "version": "1.0",
        "description": "材料门禁：参数化关键词映射，校验必备材料是否齐全",
        "used_by": ["baggage_delay", "flight_delay"],
    },
    "flight.exclusions": {
        "version": "1.0",
        "description": "条款除外责任校验：战争/罢工/恐怖活动/海关没收等",
        "used_by": ["baggage_delay", "flight_delay"],
    },
    "claim_types.flight_delay": {
        "version": "1.0",
        "description": "航班延误险特有规则：赔付档位、改签场景判定",
        "used_by": ["flight_delay"],
    },
    "claim_types.baggage_delay": {
        "version": "1.0",
        "description": "行李延误险特有规则：6h起赔、赔付档位、责任竞合",
        "used_by": ["baggage_delay"],
    },
}


def get_rule_meta(rule_id: str) -> Dict[str, Any]:
    """获取规则元数据"""
    return RULE_REGISTRY.get(rule_id, {})
