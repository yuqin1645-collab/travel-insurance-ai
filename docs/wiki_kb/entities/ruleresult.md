---
title: RuleResult 数据类
created: 2026-05-05
updated: 2026-05-05
type: entity
tags: [rules, pipeline, architecture]
sources: [raw/articles/claude-md.md]
confidence: high
---

# RuleResult

## 概述
所有规则检查函数的统一返回类型，用于在 pipeline 中传递判定结果。

## 字段定义

```python
@dataclass
class RuleResult:
    passed: bool    # True=通过，False=拒赔/需补齐资料
    action: str     # "approve" | "reject" | "supplement" | "continue"
    reason: str     # 人类可读原因（可直接用于 Remark 字段）
    detail: dict    # 调试信息（写入 DebugInfo）
```

## 使用方式
在 pipeline 中调用规则检查后，根据 `RuleResult.passed` 决定是否继续后续 stage。

## 相关页面
- [[rule-system]] — 规则系统
- [[pipeline-architecture]] — Pipeline 架构
