---
title: 共享规则使用情况
created: 2026-05-05
updated: 2026-05-05
type: concept
tags: [rules, policy-validity, identity-check, material-gate, exclusion]
sources: [app/rules/common/policy_validity.py, app/rules/common/identity_check.py, app/rules/common/material_gate.py, app/rules/flight/exclusions.py]
confidence: high
---

# 共享规则使用情况

## 概述
travel-insurance-ai 的规则系统遵循"能复用的规则一律不重写，直接 import"原则。四个公共规则被 baggage_delay、flight_delay、baggage_damage 三个险种模块共享使用。

## 四大共享规则

### 1. policy_validity — 保单有效期判定

- **文件**: `app/rules/common/policy_validity.py`
- **RULE_ID**: `common.policy_validity` (v1.1)
- **功能**:
  - 主险合同状态检查（terminated/expired → 拒赔）
  - 5 个时间点任一在期内即视为承保（OR 逻辑）
  - 安联专属顺延/提前规则（±15天）
- **使用方**: baggage_delay, flight_delay（通过 hardcheck 中的 `policy_coverage_check`）
- **PROMPT_BLOCK**: 已抽取到 `prompts/_shared/policy_validity_block.txt`

### 2. identity_check — 身份一致性校验

- **文件**: `app/rules/common/identity_check.py`
- **RULE_ID**: `common.identity_check` (v1.0)
- **功能**:
  - 申请人姓名 vs 保单权益人姓名
  - 申请人证件号 vs 保单权益人证件号
  - 未成年人监护人豁免（Relationship 字段）
- **使用方**: baggage_delay, flight_delay, baggage_damage
- **PROMPT_BLOCK**: 已抽取到 `prompts/_shared/identity_check_block.txt`

### 3. material_gate — 材料门禁

- **文件**: `app/rules/common/material_gate.py`
- **RULE_ID**: `common.material_gate` (v1.0)
- **功能**: 参数化关键词映射，校验必备材料是否齐全
- **内置关键词映射**:
  - `FLIGHT_DELAY_KEYWORDS`: 理赔申请书、身份证、交通票据、延误证明
  - `BAGGAGE_DELAY_KEYWORDS`: 理赔申请书、身份证、银行卡、交通票据、行李延误证明、签收时间证明、护照、其他
- **使用方**: baggage_delay, flight_delay
- **调用方式**: `check(text_blob, file_names, keyword_map)` → RuleResult

### 4. exclusions — 条款除外责任

- **文件**: `app/rules/flight/exclusions.py`
- **RULE_ID**: `flight.exclusions` (v1.0)
- **功能**: 参数化除外责任校验
- **内置除外列表**:
  - `BAGGAGE_DELAY_EXCLUSIONS`: 6 项（海关没收、未通知承运人、非本次行李、留置、战争/罢工、恐怖活动）
  - `FLIGHT_DELAY_EXCLUSIONS`: 3 项（战争/罢工、恐怖活动、海关干预）
- **使用方**: baggage_delay, flight_delay
- **PROMPT_BLOCK**: 已抽取到 `prompts/_shared/war_exclusion_block.txt`

## 各险种使用矩阵

| 共享规则 | baggage_delay | flight_delay | baggage_damage |
|---------|:---:|:---:|:---:|
| policy_validity | ✅ | ✅ (hardcheck) | ❌ (独立实现) |
| identity_check | ✅ | ✅ (validators) | ✅ |
| material_gate | ✅ | ✅ | ❌ (独立材料门禁) |
| exclusions | ✅ | ✅ | ❌ (独立免责判定) |

## 共享提示词块

| 文件 | 内容 | 引用方式 |
|------|------|---------|
| `prompts/_shared/policy_validity_block.txt` | 保单有效期判定规则 | `{{include:policy_validity_block}}` |
| `prompts/_shared/war_exclusion_block.txt` | 战争/社会风险/恐怖活动除外 | `{{include:war_exclusion_block}}` |
| `prompts/_shared/identity_check_block.txt` | 申请人与权益人身份匹配 | `{{include:identity_check_block}}` |
| `prompts/_shared/flight_info_extract_block.txt` | 航班信息识别核心规则 | `{{include:flight_info_extract_block}}` |

## 新险种接入共享规则的标准流程

1. 在 pipeline.py 顶部导入：
```python
from app.rules.common.policy_validity import check as check_policy_validity
from app.rules.common.identity_check import check as check_identity
from app.rules.common.material_gate import check as check_material_gate
from app.rules.flight.exclusions import check as check_exclusions
```
2. 在提示词中使用 `{{include:}}` 引用共享块
3. 险种特有内容（门槛、档位、特殊除外）保留在各险种提示词中

## 相关页面
- [[rule-system]] — 规则系统架构
- [[pipeline-architecture]] — Pipeline 架构
- [[flight-delay-compensation]] — 航班延误赔付规则
- [[baggage-delay-compensation]] — 行李延误赔付规则
