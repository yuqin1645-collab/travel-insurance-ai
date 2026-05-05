---
title: 行李损坏/随身财产模块
created: 2026-05-05
updated: 2026-05-05
type: entity
tags: [baggage-damage, module, pipeline]
sources: [app/modules/baggage_damage/pipeline.py, app/modules/baggage_damage/module.py, app/modules/baggage_damage/handlers.py]
confidence: high
---

# 行李损坏/随身财产模块 (Baggage Damage)

## 概述
行李损坏/随身财产（Baggage Damage）模块负责审核随身财产险理赔案件，涵盖承运人责任、托运行李损坏等场景。使用 **AuditPipeline** 声明式管道框架，将原有的手写串联逻辑替换为标准化阶段处理器。

## 目录结构（11 个文件）
```
app/modules/baggage_damage/
├── module.py          ← 模块注册（BaggageDamageModule）
├── pipeline.py        ← 编排层（使用 AuditPipeline 框架）
├── stages.py          ← AI 审核异步函数（4个阶段）
├── handlers.py        ← StageHandler 子类（将 stages.py 包装为 AuditPipeline handler）
├── accident.py        ← 除外责任门禁早退逻辑
├── materials.py       ← 材料门禁早退逻辑
├── coverage.py        ← 保障责任 + 系统异常处理
├── compensation.py    ← 赔付计算早退逻辑（零赔付/原价不可靠）
├── decision.py        ← 拒赔回包构建
├── final.py           ← 赔付通过回包构建
└── extractors.py      ← 数据提取工具
```

## 审核流程（4 个 AI 阶段）

```
precheck（前置校验，非 AI）
    ↓
OCR 材料提取（MaterialExtractor - OCR_THEN_LLM 策略）
    ↓
travel_hint（出行日期提示，非 AI）
    ↓
Stage 2: accident  — 事故判责 + 免责触发
    ↓
Stage 3: materials — 材料完整性
    ↓
Stage 4: coverage  — 保障责任
    ↓
Stage 5: compensation — 赔付计算（内含最终 approval 构建）
```

## 早退逻辑

| 阶段 | 早退条件 | 结果 |
|------|---------|------|
| accident | `is_excluded=True` | 直接拒赔 |
| materials | `is_complete=False` 或缺少购买凭证 | 补件通知 |
| coverage | `coverage_eligible=False` 或系统异常 | 拒赔/转人工 |
| compensation | `final_amount≤0` 或原价不可靠 | 零赔/转人工 |

## 模块注册

```python
class BaggageDamageModule:
    name = "随身财产"
    claim_type = "baggage_damage"
    # 条款路径: static/旅行险条款/baggage_damage/个人随身物品保险条款.txt
```

## 与 baggage_delay 的架构差异

| 维度 | baggage_delay | baggage_damage |
|------|--------------|----------------|
| 编排框架 | 手写 StageRunner | AuditPipeline 声明式框架 |
| 阶段处理器 | 直接调用异步函数 | StageHandler 子类包装 |
| 早退机制 | 内联 if-return | early_return 字段统一处理 |
| 拆分程度 | stages/ 子目录（7个文件） | 扁平结构（11个文件，按职责拆分） |

## 相关页面
- [[pipeline-architecture]] — Pipeline 架构
- [[rule-system]] — 规则系统
- [[baggage-delay-module]] — 行李延误模块
- [[shared-rules-usage]] — 共享规则使用情况
