#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则知识库基础类型定义
"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class RuleResult:
    """规则判定结果"""
    passed: bool                         # True=通过，False=拒赔/需补齐资料
    action: str                          # "approve" | "reject" | "supplement" | "continue"
    reason: str                          # 人类可读原因
    detail: Dict[str, Any] = field(default_factory=dict)  # 调试信息
