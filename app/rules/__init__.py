#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则知识库
统一导出常用规则函数，供各险种 pipeline 调用
"""

from app.rules.base import RuleResult
from app.rules.registry import RULE_REGISTRY, get_rule_meta

from app.rules.common.policy_validity import check as check_policy_validity
from app.rules.common.identity_check import check as check_identity
from app.rules.common.material_gate import (
    check as check_material_gate,
    FLIGHT_DELAY_KEYWORDS,
    BAGGAGE_DELAY_KEYWORDS,
)
from app.rules.flight.exclusions import (
    check as check_exclusions,
    FLIGHT_DELAY_EXCLUSIONS,
    BAGGAGE_DELAY_EXCLUSIONS,
)

__all__ = [
    "RuleResult",
    "RULE_REGISTRY",
    "get_rule_meta",
    "check_policy_validity",
    "check_identity",
    "check_material_gate",
    "FLIGHT_DELAY_KEYWORDS",
    "BAGGAGE_DELAY_KEYWORDS",
    "check_exclusions",
    "FLIGHT_DELAY_EXCLUSIONS",
    "BAGGAGE_DELAY_EXCLUSIONS",
]
