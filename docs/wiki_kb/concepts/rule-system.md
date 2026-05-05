---
title: 规则系统
created: 2026-05-05
updated: 2026-05-05
type: concept
tags: [rules, architecture, convention]
sources: [raw/articles/claude-md.md]
confidence: high
---

# 规则系统 (Rule System)

## 概述
规则库位于 `app/rules/`，将审核逻辑模块化，支持跨险种复用。核心原则：**能复用的规则一律不重写，直接 import**。

## 规则目录结构

| 目录 | 用途 |
|------|------|
| `app/rules/common/` | 两个以上险种共用的规则 |
| `app/rules/flight/` | 航班/行李相关的飞行类逻辑 |
| `app/rules/claim_types/<type>.py` | 特定险种专属规则 |

## 现有公共规则

| 规则文件 | 功能 |
|----------|------|
| `policy_validity.py` | 保单有效期、主险状态、安联顺延规则 |
| `identity_check.py` | 申请人与权益人姓名/证件号一致性 |
| `material_gate.py` | 必备材料门禁（关键词映射） |
| `flight/exclusions.py` | 战争/罢工/恐怖活动等除外责任 |

## 新规则文件规范
每个规则文件必须包含三项：
1. **元数据**：`RULE_ID`、`RULE_VERSION`、`DESCRIPTION`
2. **PROMPT_BLOCK**：供 `{{include:}}` 使用的自然语言提示词块
3. **check() 函数**：返回 `RuleResult` 的 Python 判定函数

## 相关页面
- [[pipeline-architecture]] — Pipeline 架构
- [[ruleresult]] — RuleResult 数据类
- [[prompt-system]] — 提示词系统
