# 模块化架构与新增案件模板

## 1. 文档目标

这份文档用于明确我们工作台项目后续的推荐架构，重点解决以下问题：

- 新增 10+ 案件类型时，代码不会继续堆进一个大文件。
- `prompts/` 可以按案件类型隔离，避免加载错 Prompt。
- 条款文件可以按案件类型强约束映射，避免加载错条款。
- 业务 Bug 可以快速定位到目录、文件和函数，而不是先读完整个项目。

当前项目已经具备模块化雏形，但仍处于“旧主流程 + 新模块化并存”的迁移阶段。后续应继续沿着“通用引擎 + 业务模块 + 路由入口”的方向推进。

## 2. 当前项目的推荐分层

推荐将项目长期固定为四层职责：

### 2.1 入口编排层

文件：`app/claim_ai_reviewer.py`

这一层只负责：

- 读取案件
- 识别 `claim_type`
- 根据 `claim_type` 选择模块
- 调度批量执行
- 保存审核结果

这一层不应该再继续新增业务细节判断。

### 2.2 通用引擎层

目录：`app/engine/`

这一层只放所有案件类型都能复用的流程能力，不放任何险种专属规则。

建议职责如下：

- `workflow.py`：阶段执行、重试、熔断、通用 debug 记录
- `precheck.py`：保单有效期、重复理赔、金额预处理等通用预检查
- `errors.py`：统一异常返回体
- `stage_fallbacks.py`：单阶段失败时的统一兜底返回
- `pipeline_log.py`：统一阶段日志输出
- `pipeline_labels.py`：阶段名称常量
- `travel_hint.py`：只记录提示、不直接做赔拒决策的通用逻辑

判断标准：

- 换一个案件类型还能复用，就放 `engine/`
- 带有明确业务口径，就不要放 `engine/`

### 2.3 业务模块层

目录：`app/modules/`

这一层按案件类型拆目录，一个目录只服务一个 `claim_type`。

例如：

- `app/modules/baggage_damage/`
- `app/modules/flight_delay/`

每个模块目录建议保持固定结构：

- `module.py`：模块声明，提供 `claim_type`、`prompt_namespace`、`policy_terms_path`
- `pipeline.py`：该险种的纯编排层（只做 stage 串联和结果传递，不超过 500 行）
- `stages/`：该险种的具体 stage 实现子目录（见下方"Pipeline 拆分规范"）
- `extractors.py`：关键字段硬抽取
- `materials.py`：材料门禁、缺件规则
- `coverage.py`：保障责任规则
- `accident.py`：事故与除外责任规则
- `compensation.py`：赔付核算
- `decision.py`：拒赔/转人工返回体
- `final.py`：审核通过返回体

这样做的收益是：

- 材料问题优先看 `materials.py`
- 金额问题优先看 `compensation.py`
- 责任问题优先看 `coverage.py`
- 抽取问题优先看 `extractors.py`
- 流程顺序问题优先看 `pipeline.py`

### 2.4 专业能力层

目录：`app/skills/`

这一层放可被多个模块复用的查询或判定能力，不直接承担最终赔付结论。

例如：

- 航班查询
- 天气查询
- 机场归属地判断
- 战争风险查询
- 保单生效窗口判断
- 通用赔付阶梯计算

这一层的定位是：

- `modules/` 决定业务怎么审
- `skills/` 提供审核时要用的专业能力

## 2.5 Pipeline 拆分规范（强制）

> **核心原则：`pipeline.py` 只做编排，不做实现。**

`pipeline.py` 文件行数**不得超过 500 行**。超过时必须拆分出 `stages/` 子目录。

### 为什么强制拆分

之前的 `flight_delay/pipeline.py` 曾经膨胀到 3190 行，`baggage_delay/pipeline.py` 膨胀到 1149 行。原因：编排逻辑、纯工具函数、业务校验、计算函数全部混在一个文件中。后果：

- 无法快速定位某个 stage 的代码在哪个位置
- 修改一个校验函数需要滚动几百行才能找到
- 新增 stage 时不知道该插在哪里
- 新人无法通过目录结构理解模块内部流程

### 拆分后的目录结构

```
app/modules/<claim_type>/
├── module.py
├── pipeline.py              ← 纯编排层（≤500行），只做 stage 串联
└── stages/
    ├── __init__.py          ← re-export 所有 stage 函数
    ├── utils.py             ← 纯工具函数（_safe_float, _parse_date, _is_unknown 等）
    ├── handlers.py          ← handler/check 函数（保单校验、材料门禁、一致性校验等）
    ├── calculator.py        ← 计算函数（延误时长、赔付金额、档位等）
    ├── validators.py        ← 校验函数（姓名匹配、同天投保、承保区域等）
    ├── hardcheck.py         ← 硬校验集合（调用 validators + skills）
    ├── payout.py            ← 赔付计算
    ├── delay_calc.py        ← 延误时长计算
    ├── postprocess.py       ← 后处理（AI 结果兜底修正）
    └── duplicate.py         ← 重复理赔检测
```

**不是每个文件都必须存在**，根据险种实际需要裁剪。但 `utils.py`、`__init__.py` 是必选的。

### `pipeline.py` 只允许做什么

- imports（从 `stages/` 子模块导入）
- 主入口函数（如 `review_xxx_async()`）
- stage 串联逻辑（调用 stages 函数，组装返回值）
- 日志输出

### `pipeline.py` 不允许做什么

- 定义纯工具函数（`_safe_float`、`_parse_date` 等）
- 定义业务校验函数（`_check_xxx`）
- 定义计算函数（`_compute_xxx`）
- 定义结果组装函数（`_result`）——应放在 `stages/utils.py`

### `stages/__init__.py` 模板

```python
"""
<claim_type> stages — 统一 re-export。
"""

from .utils import (
    _safe_float, _parse_date, _extract_delay_hours, _result,
)
from .handlers import (
    _check_policy_validity, _material_gate, _check_exclusions,
)
from .calculator import (
    _compute_delay_hours_by_rule, _compute_payout_with_rules,
)

__all__ = [
    # utils
    "_safe_float", "_parse_date", "_extract_delay_hours", "_result",
    # handlers
    "_check_policy_validity", "_material_gate", "_check_exclusions",
    # calculator
    "_compute_delay_hours_by_rule", "_compute_payout_with_rules",
]
```

### 外部 import 兼容

`pipeline.py` 瘦身后，必须保持外部 import 兼容。通过两种方式：

1. 主入口函数（`review_xxx_async`）仍在 `pipeline.py` 中定义
2. 需要外部访问的 stage 函数，在 `pipeline.py` 顶部 import 后自动可达：

```python
# pipeline.py 顶部
from app.modules.<claim_type>.stages import _compute_xxx

# 外部可访问
from app.modules.<claim_type>.pipeline import review_xxx_async, _compute_xxx
```

### 新险种开发 Checklist

开发新险种 pipeline 时，必须遵守：

- [ ] `pipeline.py` 行数 ≤ 500 行
- [ ] 纯工具函数放在 `stages/utils.py`
- [ ] handler/check 函数放在 `stages/handlers.py`
- [ ] 计算函数放在 `stages/calculator.py`
- [ ] `stages/__init__.py` re-export 所有 stage 函数
- [ ] `pipeline.py` 只做编排，不做实现
- [ ] 外部 import 路径不变（`from app.modules.<claim_type>.pipeline import ...`）

## 3. Prompts 隔离规范

`prompts/` 必须按案件类型命名空间隔离，禁止继续平铺扩展。

推荐结构：

```text
prompts/
├─ common/
├─ baggage_damage/
│  ├─ 01_coverage_check.txt
│  ├─ 02_material_check.txt
│  ├─ 03_accident_judgment.txt
│  ├─ 04_compensation_calculation.txt
│  └─ 05_final_summary.txt
├─ flight_delay/
│  ├─ 00_vision_extract.txt
│  ├─ 01_data_parse_and_timezone.txt
│  └─ 02_audit_decision.txt
└─ ...更多 claim_type/
```

调用方式统一为：

```python
prompt_loader.format(
    "02_material_check",
    namespace="baggage_damage",
    claim_info_json=claim_info_json,
)
```

规范要求：

- 每个模块只能加载自己的 `namespace`
- `common/` 只放跨模块通用片段
- 同名 Prompt 可以存在于不同 `namespace` 下，但不允许跨模块复用错误目录

## 4. 条款隔离规范

条款文件必须按案件类型目录隔离，并通过注册表统一选择。

推荐结构：

```text
static/
└─ 旅行险条款/
   ├─ baggage_damage/
   │  └─ 个人随身物品保险条款.txt
   ├─ flight_delay/
   │  └─ 航班延误保险条款.txt
   └─ ...更多 claim_type/
```

条款映射必须由注册表集中管理，例如：

```python
class PolicyTermsRegistry:
    def resolve(self, claim_type: str) -> Path:
        mapping = {
            "baggage_damage": config.POLICY_TERMS_DIR / "baggage_damage" / "个人随身物品保险条款.txt",
            "flight_delay": config.POLICY_TERMS_DIR / "flight_delay" / "航班延误保险条款.txt",
        }
        if claim_type not in mapping:
            raise ValueError(f"未配置条款映射: {claim_type}")
        return mapping[claim_type]
```

规范要求：

- 条款路径不能在业务流程中临时拼接
- 所有条款选择都必须走 registry
- 映射不存在时应直接报错，不允许静默降级到其他险种条款

## 5. 推荐目录树

```text
project/
├─ main.py
├─ app/
│  ├─ claim_ai_reviewer.py
│  ├─ config.py
│  ├─ logging_utils.py
│  ├─ engine/
│  │  ├─ workflow.py
│  │  ├─ precheck.py
│  │  ├─ errors.py
│  │  ├─ stage_fallbacks.py
│  │  ├─ pipeline_log.py
│  │  ├─ pipeline_labels.py
│  │  └─ travel_hint.py
│  ├─ infra/
│  │  ├─ openrouter_client.py
│  │  ├─ gemini_vision_client.py
│  │  ├─ ocr_service.py
│  │  ├─ ocr_cache.py
│  │  ├─ document_processor.py
│  │  ├─ document_cache.py
│  │  ├─ vision_preprocessor.py
│  │  └─ privacy_masking.py
│  ├─ modules/
│  │  ├─ base.py
│  │  ├─ registry.py
│  │  ├─ baggage_damage/
│  │  │  ├─ module.py
│  │  │  ├─ pipeline.py              ← 纯编排层（≤500行）
│  │  │  ├─ stages/                  ← stage 实现子目录
│  │  │  │  ├─ __init__.py
│  │  │  │  ├─ utils.py
│  │  │  │  ├─ handlers.py
│  │  │  │  └─ calculator.py
│  │  │  ├─ extractors.py
│  │  │  ├─ materials.py
│  │  │  ├─ accident.py
│  │  │  ├─ coverage.py
│  │  │  ├─ compensation.py
│  │  │  ├─ decision.py
│  │  │  └─ final.py
│  │  ├─ flight_delay/
│  │  │  ├─ module.py
│  │  │  ├─ pipeline.py              ← 纯编排层（≤500行）
│  │  │  └─ stages/                  ← stage 实现子目录
│  │  │     ├─ __init__.py
│  │  │     ├─ utils.py
│  │  │     ├─ hardcheck.py
│  │  │     ├─ payout.py
│  │  │     ├─ delay_calc.py
│  │  │     ├─ postprocess.py
│  │  │     ├─ duplicate.py
│  │  │     └─ validators.py
│  │  └─ ...更多 claim_type/（同上结构）
│  ├─ skills/
│  │  ├─ airport.py
│  │  ├─ flight_lookup.py
│  │  ├─ weather.py
│  │  ├─ war_risk.py
│  │  ├─ policy_booking.py
│  │  └─ compensation.py
│  └─ registries/
│     ├─ policy_terms_registry.py
│     └─ prompt_registry.py
├─ prompts/
├─ static/
├─ claims_data/
├─ review_results/
├─ logs/
├─ scripts/
└─ docs/
```

说明：

- `infra/` 是推荐中的下一步整理方向，用于承接当前散落在 `app/` 根目录的 OCR、Vision、文档处理、模型客户端等基础设施代码。
- 这一步不是必须立即实施，但建议作为后续整理目标保留。

## 6. 新增一个案件类型时的标准步骤

以后新增任意案件类型，例如 `trip_cancellation`，建议固定按以下步骤落地：

1. 在 `app/modules/` 下新增 `trip_cancellation/`
2. 新建 `module.py`，声明模块元信息
3. 新建 `stages/` 子目录，包含 `__init__.py`、`utils.py`、`handlers.py`、`calculator.py`（见下方骨架）
4. 新建 `pipeline.py`，纯编排层，从 `stages/` 导入并串联 stage（≤500 行）
5. 在 `prompts/trip_cancellation/` 下放 Prompt 模板
6. 在 `static/旅行险条款/trip_cancellation/` 下放条款文件
7. 在 `app/modules/registry.py` 中注册新模块
8. 在条款注册表中增加该险种的条款映射
9. 在 `claim_ai_reviewer.py` 的案件识别逻辑中将其路由到该模块

新增模块应该是”新增一个目录并注册”，而不是”修改旧模块内部逻辑”。

## 7. 新增案件类型的标准代码骨架

下面给出推荐的最小代码骨架，后续新增模块时可以直接按这个模板复制。

### 7.1 `module.py`

```python
from __future__ import annotations

from app.config import config
from app.modules.base import ClaimModule, ModuleContext


class TripCancellationModule:
    name = "旅行取消"
    claim_type = "trip_cancellation"

    def get_context(self) -> ModuleContext:
        return ModuleContext(
            claim_type=self.claim_type,
            prompt_namespace=self.claim_type,
            policy_terms_path=(
                config.POLICY_TERMS_DIR
                / "trip_cancellation"
                / "旅行取消保险条款.txt"
            ),
        )


MODULE: ClaimModule = TripCancellationModule()
```

### 7.2 `stages/` 子目录

每个新险种必须先建 `stages/` 子目录，再写 `pipeline.py`。推荐最小骨架：

```
app/modules/trip_cancellation/
├── module.py
├── pipeline.py
└── stages/
    ├── __init__.py          ← re-export
    ├── utils.py             ← _safe_float, _parse_date, _is_unknown, _result 等
    ├── handlers.py          ← _check_policy_validity, _material_gate, _check_exclusions 等
    └── calculator.py        ← _compute_xxx, _compute_payout 等
```

**`stages/utils.py` 示例：**

```python
"""
trip_cancellation stages — 纯工具函数。
"""
from __future__ import annotations
from typing import Any, Optional

def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        s = str(value).strip().replace(",", "")
        if not s:
            return None
        return float(s)
    except Exception:
        return None

def _result(forceid: str, remark: str, is_additional: str,
            conclusions: list, debug: dict) -> dict:
    audit_result = "通过" if remark.startswith("审核通过") else \
                   "需补件" if is_additional == "Y" or remark.startswith("需补件") else "拒绝"
    return {
        "forceid": forceid,
        "claim_type": "trip_cancellation",
        "Remark": remark,
        "IsAdditional": is_additional,
        "KeyConclusions": conclusions,
        "trip_cancellation_audit": {"audit_result": audit_result, "explanation": remark},
        "DebugInfo": debug,
    }
```

**`stages/__init__.py` 示例：**

```python
"""
trip_cancellation stages — 统一 re-export。
"""
from .utils import _safe_float, _result
from .handlers import _check_policy_validity, _material_gate
from .calculator import _compute_payout

__all__ = [
    "_safe_float", "_result",
    "_check_policy_validity", "_material_gate",
    "_compute_payout",
]
```

### 7.3 `pipeline.py`（编排层）

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import aiohttp

from app.engine.workflow import StageRunner
from app.engine.stage_fallbacks import build_stage_error_return
from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
from app.logging_utils import LOGGER, log_extra

from app.modules.trip_cancellation.stages.utils import (
    _safe_float, _result, _extract_file_names,
)
from app.modules.trip_cancellation.stages.handlers import (
    _check_policy_validity, _material_gate, _check_exclusions,
)
from app.modules.trip_cancellation.stages.calculator import (
    _compute_payout,
)


async def review_trip_cancellation_async(
    *,
    reviewer: Any,
    claim_folder: Path,
    claim_info: Dict[str, Any],
    policy_terms: str,
    index: int,
    total: int,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """旅行取消审核主流程（编排层）。"""
    forceid = str(claim_info.get("forceid") or "unknown")
    debug: Dict[str, Any] = {"debug": []}
    runner = StageRunner(ctx=debug, forceid=forceid)

    LOGGER.info(
        f"[{index}/{total}] 旅行取消审核开始",
        extra=log_extra(forceid=forceid, stage="trip_cancellation_start", attempt=0),
    )
    conclusions = []

    # 0) 视觉识别
    vision_extract = {}
    try:
        extractor = MaterialExtractor(reviewer=reviewer, forceid=forceid)
        extraction = await extractor.extract(
            claim_folder=claim_folder,
            claim_info=claim_info,
            strategy=ExtractionStrategy.VISION_DIRECT,
            prompt_name="00_vision_extract",
            session=session,
        )
        raw_vision = extraction.vision_data
        if isinstance(raw_vision, dict):
            vision_extract = raw_vision
    except Exception as _ve:
        LOGGER.warning(f"[{forceid}] 视觉识别失败: {_ve}")
    debug["vision_extract"] = vision_extract

    # 1) AI 结构化抽取
    parsed, err = await runner.run(
        "trip_cancellation_parse",
        reviewer._ai_trip_cancellation_parse_async,
        claim_info,
        policy_terms,
        session=session,
        max_retries=2,
        retry_sleep=2.0,
    )
    if err:
        return build_stage_error_return(forceid=forceid, checkpoint="信息解析", err=err, ctx=debug)

    # 2) 前置校验（调用 stages 函数）
    policy_violation = _check_policy_validity(claim_info, debug)
    if policy_violation:
        return _result(forceid, policy_violation, "N", conclusions, debug)

    # 3) 材料门禁
    # ... 继续串联 stages 函数 ...

    return _result(forceid, "审核通过", "N", conclusions, debug)
```

### 7.4 `registry.py`

```python
from app.modules.baggage_damage.module import MODULE as BAGGAGE_DAMAGE_MODULE
from app.modules.flight_delay.module import MODULE as FLIGHT_DELAY_MODULE
from app.modules.trip_cancellation.module import MODULE as TRIP_CANCELLATION_MODULE


modules = {
    BAGGAGE_DAMAGE_MODULE.claim_type: BAGGAGE_DAMAGE_MODULE,
    FLIGHT_DELAY_MODULE.claim_type: FLIGHT_DELAY_MODULE,
    TRIP_CANCELLATION_MODULE.claim_type: TRIP_CANCELLATION_MODULE,
}
```

### 7.4 `claim_ai_reviewer.py` 中的路由建议

推荐将案件识别和模块执行集中在一个小范围内，示意如下：

```python
claim_type = detect_claim_type(claim_info, claim_folder)
reviewer.set_claim_type(claim_type)

if claim_type == "flight_delay":
    return await review_flight_delay_async(...)
if claim_type == "trip_cancellation":
    return await review_trip_cancellation_async(...)

return await review_baggage_damage_async(...)
```

后续再进一步，可以继续把这部分抽成单独 router，避免入口文件持续膨胀。

## 8. Bug 快速定位规范

为了保证后续迭代中容易修 Bug，建议团队统一遵守以下规则：

### 8.1 文件职责单一

- 一个业务阶段尽量对应一个文件
- 一个硬抽取规则对应一个函数
- 不再把多个阶段继续塞回总控文件

### 8.2 Debug 信息标准化

`DebugInfo` 至少建议包含：

- 当前 `claim_type`
- 每个 stage 的输入摘要
- 每个 stage 的输出摘要
- 每个 stage 的异常原因
- 命中的抽取规则名
- 关键材料识别来源

### 8.3 日志上下文统一

建议统一在日志 `extra` 中补充：

- `forceid`
- `stage`
- `attempt`
- `module`

这样查看日志时，能立即知道问题归属哪个模块。

### 8.4 排查映射建议

- 材料缺件或误判：先看 `materials.py`
- 条款责任问题：先看 `coverage.py`
- 除外责任问题：先看 `accident.py`
- 金额错误：先看 `compensation.py`
- OCR/字段抽取问题：先看 `extractors.py`
- 流程跳转错误：先看 `pipeline.py`
- 模块选错：先看 `claim_ai_reviewer.py` 或未来的 router

## 9. 迁移建议

不建议一次性推倒重来，建议分三步推进：

### 第一步：先保留现有能力，强化路由与隔离

- 确保所有 Prompt 都按 `namespace` 加载
- 确保条款都走 registry
- 确保结果按 `claim_type` 落目录

### 第二步：把随身财产主流程下沉成独立 `pipeline.py`

- 从 `app/claim_ai_reviewer.py` 中拆出随身财产主流程
- 迁移到 `app/modules/baggage_damage/pipeline.py`
- reviewer 仅保留路由与公共初始化

### 第三步：继续下沉剩余业务细节

- 把 reviewer 中残留的随身财产专属辅助函数逐步迁入模块目录
- 保持 reviewer 最终只承担入口与调度职责

当前进展补充：

- 已将随身财产异步阶段实现集中到 `app/modules/baggage_damage/stages.py`
- `app/modules/baggage_damage/pipeline.py` 已直接调用模块阶段函数，不再依赖 reviewer 中的同名异步实现
- 下一步建议继续清理 `app/claim_ai_reviewer.py` 中遗留的旧同步/演示代码，避免同名旧逻辑干扰定位

## 10. 现状目录 -> 目标目录迁移清单

这一节用于把当前项目中的主要文件，映射到后续推荐结构，方便排期和逐步改造。

### 10.1 建议保留在原位的文件

这些文件当前职责相对清晰，可以先保留位置不变：

- `app/config.py`
- `app/logging_utils.py`
- `app/prompt_loader.py`
- `app/modules/base.py`
- `app/modules/registry.py`
- `app/engine/workflow.py`
- `app/engine/precheck.py`
- `app/engine/errors.py`
- `app/engine/stage_fallbacks.py`
- `app/engine/pipeline_log.py`
- `app/engine/pipeline_labels.py`
- `app/engine/travel_hint.py`
- `app/modules/baggage_damage/accident.py`
- `app/modules/baggage_damage/materials.py`
- `app/modules/baggage_damage/coverage.py`
- `app/modules/baggage_damage/compensation.py`
- `app/modules/baggage_damage/decision.py`
- `app/modules/baggage_damage/final.py`
- `app/modules/baggage_damage/extractors.py`
- `app/modules/baggage_damage/module.py`
- `app/modules/flight_delay/module.py`
- `app/modules/flight_delay/pipeline.py`
- `app/skills/*.py`

说明：

- 这批文件已经基本符合“通用能力”或“模块内单一职责”的方向，短期无需强制移动目录。
- 这批文件更适合先做接口稳定和补测试，而不是先做路径重命名。

### 10.2 建议后续迁入 `app/infra/` 的文件

当前这些文件都属于基础设施能力，长期建议统一收口到 `app/infra/`：

- `app/openrouter_client.py` -> `app/infra/openrouter_client.py`
- `app/gemini_vision_client.py` -> `app/infra/gemini_vision_client.py`
- `app/ocr_service.py` -> `app/infra/ocr_service.py`
- `app/ocr_cache.py` -> `app/infra/ocr_cache.py`
- `app/document_processor.py` -> `app/infra/document_processor.py`
- `app/document_cache.py` -> `app/infra/document_cache.py`
- `app/vision_preprocessor.py` -> `app/infra/vision_preprocessor.py`
- `app/privacy_masking.py` -> `app/infra/privacy_masking.py`

迁移建议：

- 第一阶段先不改文件路径，只在文档中统一认知为“基础设施层”。
- 第二阶段如果开始做大规模整理，再批量迁目录并同步修 import。

### 10.3 建议拆分或收缩的文件

#### `app/claim_ai_reviewer.py`

这是当前最需要持续收缩的文件。

它未来应该只保留：

- 初始化公共组件
- 读取案件和批量执行
- 识别 `claim_type`
- 路由到对应模块 pipeline
- 保存审核结果

建议迁出的内容：

- 随身财产主审核流程 -> `app/modules/baggage_damage/pipeline.py`
- 随身财产专属 AI 阶段函数 -> 逐步迁入 `baggage_damage/` 目录
- 仅属于某个险种的辅助函数 -> 对应模块目录

短期目标：

- 把“随身财产主流程”先整体迁到 `pipeline.py`
- 把 reviewer 变成“批量 runner + router”

#### `app/quality_assessment.py`

这个文件当前更像“横切能力”，建议后续二选一：

- 如果用于所有险种审核质量评估，迁到 `app/engine/` 或 `app/infra/`
- 如果只是某个模块的验收能力，则迁到对应模块目录

目前建议：

- 先保留原地
- 后续根据实际调用范围再决定最终归属

#### `app/policy_terms_registry.py`

当前文件已经承担注册表角色，但长期建议移动到更明确的位置：

- `app/registries/policy_terms_registry.py`

目前建议：

- 先保留原地，不阻塞开发
- 等后续如果补 `prompt_registry.py`、`claim_type_registry.py` 时一起归档到 `registries/`

### 10.4 需要补齐的新文件

为了让架构真正闭环，后续建议补这几个文件：

- `app/modules/<新claim_type>/stages/`
  作用：新险种的 stage 实现子目录（必选，见 2.5 节 Pipeline 拆分规范）
- `app/modules/<新claim_type>/pipeline.py`
  作用：纯编排层，从 stages/ 导入并串联（≤500 行）
- `app/router.py` 或 `app/modules/router.py`
  作用：集中管理 claim type 检测与模块路由
- `app/registries/prompt_registry.py`
  作用：可选，用于集中管理 Prompt 名称常量，降低拼写错误
- `app/modules/flight_delay/parse.py`
  作用：从 `pipeline.py` 中继续拆出解析逻辑
- `app/modules/flight_delay/hard_checks.py`
  作用：从 `pipeline.py` 中继续拆出硬校验逻辑

### 10.5 迁移优先级建议

建议按下面顺序改，风险最低：

1. 新建 `app/modules/baggage_damage/pipeline.py`，把当前随身财产主流程迁进去。
2. 把 `claim_ai_reviewer.py` 中的路由逻辑收口，只保留模块识别和分发。
3. 继续把 reviewer 中残留的随身财产专属辅助函数迁入 `baggage_damage/`。
4. 根据节奏再决定是否引入 `app/router.py`。
5. 等模块边界稳定后，再做 `infra/` 和 `registries/` 的目录整理。

已完成：
- `flight_delay/pipeline.py` 已拆分为编排层（~370行）+ `stages/` 子目录（8个文件）
- `baggage_delay/pipeline.py` 已拆分为编排层（~280行）+ `stages/` 子目录（4个文件）
- 两个模块的外部 import 路径保持不变，`claim_ai_reviewer.py` 无需修改

### 10.6 建议的最小排期方案

如果按“尽量不打断当前开发”为原则，建议这样排：

- 第 1 周：完成 `baggage_damage/pipeline.py`，收缩 reviewer 主流程
- 第 2 周：梳理 reviewer 中剩余的随身财产专属函数，逐步迁移
- 第 3 周：补 `router.py` 或统一 claim type 检测函数
- 第 4 周以后：按需要逐步整理 `infra/` 和 `registries/`

这样做的好处是：

- 不需要停下来重构一整个项目
- 可以边支持新模块边清理旧主文件
- 风险集中在有限几个文件里，便于回滚和验证

## 11. 最终结论

项目后续的目标，不是“一个大文件支持多种案件类型”，而是：

- `claim_ai_reviewer.py` 只负责路由和批量执行
- `engine/` 只负责通用流程能力
- `modules/<claim_type>/` 只负责本险种业务
- `skills/` 只负责可复用的外部能力
- `prompts/` 和条款文件都按 `claim_type` 强隔离

如果后续严格按这套结构扩展，那么新增案件类型、维护老模块、定位 Bug、排查条款与 Prompt 误加载问题，都会比当前模式更稳定、更容易管理。

## 12. 当前收尾状态

### 12.1 随身财产模块

截至 2026-03-24，随身财产模块的迁移状态如下：

- `app/modules/baggage_damage/pipeline.py` 已成为随身财产主流程入口
- `app/modules/baggage_damage/stages.py` 已集中承接保障判断、材料审核、事故判责、赔付核算等阶段逻辑
- `app/claim_ai_reviewer.py` 仍保留部分旧实现与演示代码，但当前活跃主链已经不再依赖 reviewer 中的随身财产阶段实现

这意味着当前排查随身财产问题时，优先查看：

- `pipeline.py`：看阶段编排和阶段输入输出
- `stages.py`：看具体审核逻辑
- `extractors.py`：看购买金额、第三方赔付等硬抽取规则

### 12.2 航班延误 / 行李延误模块

截至 2026-04-28，航班延误和行李延误模块已完成 `pipeline.py` + `stages/` 拆分：

| 模块 | 原始行数 | 拆分后 pipeline | stages/ 文件数 |
|------|---------|----------------|---------------|
| flight_delay | 3190 行 | ~370 行 | 8 个文件 |
| baggage_delay | 1149 行 | ~280 行 | 4 个文件 |

- `pipeline.py` 现在是纯编排层，只做 stage 串联
- 所有业务逻辑下沉到 `stages/utils.py`、`stages/handlers.py`、`stages/calculator.py` 等
- 外部 import 路径不变（`from app.modules.flight_delay.pipeline import review_flight_delay_async`）

## 13. 本地 Python 环境说明

当前工作台里 `python` 不可执行，不是代码问题，而是本机解释器没有正确可用：

- `python.exe` 命中的是 `WindowsApps` 里的 App Execution Alias 占位程序，不是真正的 Python 解释器
- `py.exe` 虽然存在，但本机没有已安装且已注册给 launcher 使用的 Python 版本

因此当前环境会出现：

- `python --version` 报“系统无法访问此文件”
- `py --version` 报 `No installed Python found!`

这会影响：

- 不能直接运行本地 Python 脚本
- 不能做基于解释器的语法校验、导入校验、单测执行

如果后续要恢复本地校验能力，需要补一套真实可执行的 Python 安装，并让 PATH / launcher 能正确找到解释器。
