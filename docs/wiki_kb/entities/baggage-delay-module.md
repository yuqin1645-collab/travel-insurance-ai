---
title: 行李延误模块
created: 2026-05-05
updated: 2026-05-05
type: entity
tags: [baggage-delay, module, pipeline]
sources: [raw/articles/baggage-delay-pipeline.md, raw/articles/baggage-delay-rules.md]
confidence: high
---

# 行李延误模块 (Baggage Delay)

## 概述
行李延误（Baggage Delay）是 travel-insurance-ai 的核心险种模块之一，负责自动审核行李延误理赔案件。

## 目录结构
```
app/modules/baggage_delay/
├── module.py
├── pipeline.py              ← 编排层
└── stages/
    ├── __init__.py
    ├── utils.py             ← 工具函数
    ├── handlers.py          ← 校验函数
    └── calculator.py        ← 计算函数
```

## 赔付阶梯

| 延误时长 | 赔付金额 |
|----------|----------|
| 6小时 ≤ 时长 < 12小时 | 500 元 |
| 12小时 ≤ 时长 < 18小时 | 1000 元 |
| 时长 ≥ 18小时 | 1500 元（保额上限） |

## 核心审核流程
1. 保单有效性校验
2. 材料完整性检查
3. 信息一致性核对
4. 延误时长计算（实际签收时间 - 首次航班到达时间）
5. 赔付金额核算
6. 审核结果判定（通过/拒赔/补件/人工复核）

## 相关页面
- [[pipeline-architecture]] — Pipeline 架构
- [[rule-system]] — 规则系统
- [[baggage-delay-compensation]] — 赔付规则详解
