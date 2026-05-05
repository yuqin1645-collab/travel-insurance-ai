---
title: 航班延误模块
created: 2026-05-05
updated: 2026-05-05
type: entity
tags: [flight-delay, module, pipeline]
sources: [app/modules/flight_delay/pipeline.py, app/modules/flight_delay/module.py]
confidence: high
---

# 航班延误模块 (Flight Delay)

## 概述
航班延误（Flight Delay）是 travel-insurance-ai 的核心险种模块之一，负责自动审核航班延误理赔案件。采用 Pipeline 编排模式，pipeline.py 只做 stage 串联，业务逻辑在 stages/ 子目录中。

## 目录结构
```
app/modules/flight_delay/
├── module.py                 ← 模块注册（FlightDelayModule）
├── pipeline.py               ← 编排层（~729行，串联所有 stage）
└── stages/
    ├── __init__.py           ← re-export
    ├── utils.py              ← 工具函数（_is_unknown, _parse_utc_dt, _merge_aviation_into_parsed 等）
    ├── validators.py         ← 校验函数（继承检测、行为能力、姓名匹配、同天投保、承保区域、硬免责）
    ├── hardcheck.py          ← 硬校验集合（_run_hardcheck + 可预见因素欺诈检测）
    ├── delay_calc.py         ← 延误时长计算（_compute_delay_minutes + _augment_with_computed_delay）
    ├── payout.py             ← 赔付金额计算（_run_payout_calc）
    ├── duplicate.py          ← 重复理赔检测
    └── postprocess.py        ← 规则兜底后处理
```

## 审核流程（12 个阶段）

```
stage0_duplicate: 重复理赔检测
    ↓
stage0_vision: 视觉/OCR 材料抽取（MaterialExtractor - VISION_DIRECT 策略）
    ↓
stage1: AI 数据解析与时区标准化（_ai_flight_delay_parse_async）
    ↓
stage1.2: 合并 Vision 抽取结果（_merge_vision_into_parsed）
    ↓
stage1.3: 飞常准航班权威数据查询（FlightLookupSkill.lookup_status）
    ↓
stage1.4: 接驳/替代航班飞常准查询
    ↓
stage_hardcheck: 代码侧硬校验集合（_run_hardcheck）
    ↓
stage10: 赔付金额预计算（_run_payout_calc）
    ↓
stage2_precheck: 硬免责前置拦截
    ↓
stage2: AI 理赔判定
    ↓
postprocess: 规则兜底后处理
```

## 模块注册

```python
class FlightDelayModule:
    name = "航班延误"
    claim_type = "flight_delay"
    # 条款路径: static/旅行险条款/flight_delay/航班延误保险条款.txt
```

## 关键设计决策

1. **飞常准数据优先**：航班时间以飞常准 VariFlight MCP API 为准，失败时降级 mock
2. **取长原则**：延误时长取起飞延误和到达延误的较长者
3. **机场匹配**：改签场景下校验替代航班与原航班在同一机场
4. **联程特殊处理**：联程航班只看末段到达，前程正常到达时不触发误机免责
5. **Vision 辅助**：视觉抽取结果合并到 AI 解析结果中，补充航班号、时间、机场码等信息

## 相关页面
- [[flight-delay-compensation]] — 赔付规则详解
- [[pipeline-architecture]] — Pipeline 架构
- [[rule-system]] — 规则系统
- [[flight-lookup-skill]] — 飞常准航班查询技能
- [[shared-rules-usage]] — 共享规则使用情况
