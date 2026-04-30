#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用规则：申请人身份与保单权益人一致性校验
来源：baggage_delay/pipeline.py:237-248 身份校验段落
"""

from typing import Any, Dict

from app.rules.base import RuleResult

RULE_ID = "common.identity_check"
RULE_VERSION = "1.0"
DESCRIPTION = "申请人姓名/证件号与保单权益人一致性校验"

PROMPT_BLOCK = """
【身份匹配校验规则】
- 申请人姓名（Claimant_Name）必须与保单权益人姓名（Insured_Name）一致。
- 申请人证件号（Claimant_IDNumber）必须与保单权益人证件号（Insured_IDNumber）一致。
- 例外情形：被保险人为未成年人时，允许监护人（父母/亲属）代办，Relationship 字段需注明。
- 身份不匹配且无合理说明 → 拒赔；缺少材料证明 → 需补齐资料。
""".strip()


def check(claim_info: Dict[str, Any]) -> RuleResult:
    """
    申请人与保单权益人身份一致性校验。

    Args:
        claim_info: 案件信息字典

    Returns:
        RuleResult：passed=True 表示通过，passed=False 表示拒赔
    """
    claimant_name = str(claim_info.get("Claimant_Name") or "").strip()
    insured_name = str(claim_info.get("Insured_Name") or "").strip()
    claimant_id = str(claim_info.get("Claimant_IDNumber") or "").strip()
    insured_id = str(claim_info.get("Insured_IDNumber") or "").strip()

    detail: Dict[str, Any] = {
        "claimant_name": claimant_name,
        "insured_name": insured_name,
        "claimant_id_suffix": claimant_id[-4:] if len(claimant_id) >= 4 else claimant_id,
        "insured_id_suffix": insured_id[-4:] if len(insured_id) >= 4 else insured_id,
    }

    # 姓名不一致时，检查是否有监护关系豁免
    if claimant_name and insured_name and claimant_name != insured_name:
        relationship = str(claim_info.get("Relationship") or "").strip().lower()
        allowed_relationships = {"parent", "guardian", "监护人", "父母", "亲属"}
        if relationship not in allowed_relationships:
            detail["relationship"] = relationship
            return RuleResult(
                passed=False,
                action="reject",
                reason="拒赔：申请人身份与保单权益人信息不匹配",
                detail=detail,
            )
        detail["relationship_exemption"] = relationship

    # 证件号不一致
    if claimant_id and insured_id and claimant_id != insured_id:
        return RuleResult(
            passed=False,
            action="reject",
            reason="拒赔：申请人证件号与保单权益人不匹配",
            detail=detail,
        )

    return RuleResult(
        passed=True,
        action="continue",
        reason="身份校验通过",
        detail=detail,
    )
