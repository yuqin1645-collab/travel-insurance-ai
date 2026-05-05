---
title: 提示词系统
created: 2026-05-05
updated: 2026-05-05
type: concept
tags: [prompts, architecture, convention]
sources: [raw/articles/claude-md.md]
confidence: high
---

# 提示词系统 (Prompt System)

## 概述
使用 `{{include:block_name}}` 语法实现提示词模块化复用。两个以上险种共用的提示词段落必须抽取到 `prompts/_shared/`。

## 共享块列表

| 文件 | 内容 |
|------|------|
| `policy_validity_block.txt` | 保单有效期判定规则（4时间点 + 安联顺延/提前规则） |
| `war_exclusion_block.txt` | 战争/社会风险/恐怖活动除外责任 |
| `identity_check_block.txt` | 申请人与权益人身份匹配规则 |
| `flight_info_extract_block.txt` | 航班信息识别核心规则及 JSON 字段结构 |

## 使用方式
在险种提示词中通过 `{{include:block_name}}` 引用，PromptLoader 会自动展开。

## 相关页面
- [[rule-system]] — 规则系统
- [[pipeline-architecture]] — Pipeline 架构
