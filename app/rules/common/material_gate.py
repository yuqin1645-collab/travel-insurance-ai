#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用规则：材料门禁（参数化关键词映射）
来源：baggage_delay/pipeline.py:252-272 和 382-397（两处重复定义，合并为通用版本）
"""

from typing import Any, Dict, List

from app.rules.base import RuleResult

RULE_ID = "common.material_gate"
RULE_VERSION = "1.0"
DESCRIPTION = "材料门禁：参数化关键词映射，校验必备材料是否齐全"

PROMPT_BLOCK = """
【材料完整性校验规则】
通过在文本内容和附件文件名中搜索关键词来判断必备材料是否已提交。
- 每类必备材料对应一组关键词，任意关键词命中即视为已提供。
- 全部必备材料命中 → 材料完整。
- 存在未命中的必备材料 → 需补齐资料，需在补件说明中列出缺失材料名称。
""".strip()

# 航班延误险必备材料关键词映射
FLIGHT_DELAY_KEYWORDS: Dict[str, List[str]] = {
    "理赔申请书": ["理赔申请", "申请书", "claim form"],
    "被保险人身份证": ["身份证", "identity"],
    "交通票据（机票/登机牌/行程单）": ["机票", "登机牌", "行程单", "ticket", "boarding"],
    "延误证明（含延误时间及原因）": ["延误证明", "delay", "延误信函", "不正常航班"],
}

# 行李延误险必备材料关键词映射（含签收时间证明）
BAGGAGE_DELAY_KEYWORDS: Dict[str, List[str]] = {
    "理赔申请书": ["理赔申请", "申请书", "claim form"],
    "被保险人身份证正反面": ["身份证", "identity"],
    "被保险人银行卡（借记卡）": ["银行卡", "借记卡", "bank card"],
    "交通票据（机票/登机牌/行程单）": ["机票", "登机牌", "行程单", "ticket", "boarding"],
    "行李延误证明（含航班及原因）": ["行李延误", "行李不正常", "pir", "baggage", "delay proof"],
    "行李签收时间证明": ["签收", "领取", "receipt", "delivered"],
    "护照照片页、签证页、出入境盖章页": ["护照", "签证", "出入境", "passport", "visa", "exit"],
    "其他确认保险事故性质原因的相关材料": ["委托书", "监护人", "关系证明"],
}


def check(
    text_blob: str,
    file_names: List[str],
    keyword_map: Dict[str, List[str]],
) -> RuleResult:
    """
    材料门禁校验。

    Args:
        text_blob: 案件描述文本（Description_of_Accident + Assessment_Remark）
        file_names: 附件文件名列表（已小写化）
        keyword_map: 必备材料关键词映射（{ 材料名称: [关键词列表] }）

    Returns:
        RuleResult：
            passed=True  → 材料齐全，action="continue"
            passed=False → 缺材料，action="supplement"，detail["missing"] 列出缺失材料
    """
    if not file_names:
        return RuleResult(
            passed=False,
            action="supplement",
            reason="缺少附件材料（需上传必要票据和证明文件）",
            detail={"missing": list(keyword_map.keys()), "no_files": True},
        )

    joined = f"{text_blob} {' '.join(file_names)}".lower()
    missing: List[str] = []

    for label, words in keyword_map.items():
        if not any(w in joined for w in words):
            missing.append(label)

    if missing:
        return RuleResult(
            passed=False,
            action="supplement",
            reason="需补齐资料：" + "；".join(missing),
            detail={"missing": missing},
        )

    return RuleResult(
        passed=True,
        action="continue",
        reason="材料完整性校验通过",
        detail={"missing": []},
    )
