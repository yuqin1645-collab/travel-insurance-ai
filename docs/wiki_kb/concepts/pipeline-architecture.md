---
title: Pipeline 架构
created: 2026-05-05
updated: 2026-05-05
type: concept
tags: [pipeline, architecture, convention]
sources: [raw/articles/claude-md.md]
confidence: high
---

# Pipeline 架构

## 概述
travel-insurance-ai 的核心审核流程采用 **Pipeline 编排模式**。`pipeline.py` 只做 stage 串联编排，所有业务逻辑放在 `stages/` 子目录中。

## 强制拆分规范
- `pipeline.py` 行数**不得超过 500 行**
- 超过时必须拆分出 `stages/` 子目录
- `pipeline.py` 禁止定义纯工具函数或业务校验函数

## 标准目录结构
```
app/modules/<claim_type>/
├── module.py
├── pipeline.py              ← 纯编排层（≤500行）
└── stages/
    ├── __init__.py          ← re-export 所有 stage 函数
    ├── utils.py             ← 纯工具函数
    ├── handlers.py          ← handler/check 函数
    └── calculator.py        ← 计算函数
```

## 相关页面
- [[rule-system]] — 规则库架构
- [[prompt-system]] — 提示词系统
- [[baggage-delay-module]] — 行李延误模块
- [[flight-delay-module]] — 航班延误模块
