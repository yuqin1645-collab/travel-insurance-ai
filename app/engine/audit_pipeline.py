"""
通用审核流程框架 — app/engine/audit_pipeline.py

将所有险种的 AI 审核流程抽象为 5 个标准阶段的管道：

  Stage 1  time_check        时间有效性校验（保单期间、事故日期）
  Stage 2  exclusion_check   免责条款触发判断
  Stage 3  materials_check   材料完整性审核（缺件检查）
  Stage 4  coverage_check    条款符合性审核（是否在保障范围内）
  Stage 5  compensation_calc 赔付金额计算

每个阶段由 AuditStage 协议描述，各险种只需实现对应的 StageHandler 即可。
对于某险种不需要的阶段，可将对应 handler 设为 None 跳过。

典型用法::

    from app.engine.audit_pipeline import AuditPipeline, StageHandler

    class MyTimeCheck(StageHandler):
        stage_key = "time_check"
        async def run(self, ctx):
            # ...业务逻辑...
            return {"valid": True, "reason": ""}

    pipeline = AuditPipeline(
        forceid=forceid,
        claim_info=claim_info,
        stages=[MyTimeCheck(), MyExclusionCheck(), ...],
        ctx=ctx,
        runner=runner,
    )
    result = await pipeline.execute()

设计原则
--------
1. **零业务耦合**：本文件不含任何险种特定规则，只控制流程顺序和早退逻辑。
2. **可选阶段**：任何阶段均可省略（handler=None），管道自动跳过。
3. **统一早退**：每个阶段可返回 ``early_return`` 字段，非 None 则整个流程立即结束。
4. **统一错误处理**：阶段 run() 抛出异常时由管道捕获，调用 on_error() 构建系统异常回包。
5. **复用 StageRunner**：管道内部直接使用已有的 StageRunner（重试 + 熔断器）。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp

from app.engine.workflow import StageRunner
from app.engine.pipeline_log import log_stage
from app.engine.stage_fallbacks import build_stage_error_return
from app.logging_utils import LOGGER, log_extra


# ─────────────────────────────────────────────────────────────────────────────
# 阶段接口
# ─────────────────────────────────────────────────────────────────────────────

class StageHandler(abc.ABC):
    """
    单个审核阶段的抽象基类。

    子类只需实现 ``run()``，其余生命周期钩子可选覆盖。
    """

    # 阶段唯一标识，对应 PipelineLabels 的 key
    stage_key: str = "unknown_stage"

    # 对应 StageRunner 的 checkpoint 名（用于错误回包），默认与 stage_key 相同
    @property
    def checkpoint(self) -> str:
        return self.stage_key

    @abc.abstractmethod
    async def run(self, ctx: "PipelineContext") -> Dict[str, Any]:
        """
        执行阶段逻辑。

        参数
        ----
        ctx : PipelineContext
            管道上下文，包含 claim_info、reviewer、session、各前序阶段结果等。

        返回
        ----
        Dict，必须包含字段：
          - ``stage_result``  : 本阶段的结构化输出（任意 dict）
          - ``early_return``  : Optional[Dict]，非 None 则整个流程立即终止并返回此 dict

        子类可只返回业务字段，管道会自动包装：
        若返回的 dict 中不含 ``early_return`` key，则视为 early_return=None。
        若返回的 dict 不含 ``stage_result`` key，则整个 dict 被视为 stage_result。
        """
        ...

    def on_error(
        self,
        *,
        forceid: str,
        err: Exception,
        ctx_dict: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        阶段运行抛异常时的错误回包构造器。
        默认使用 ``build_stage_error_return``，可覆盖以实现自定义逻辑。
        """
        return build_stage_error_return(
            forceid=forceid,
            checkpoint=self.checkpoint,
            err=err,
            ctx=ctx_dict,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 管道上下文
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineContext:
    """
    审核管道运行时上下文。

    每个阶段均可读写此对象，前序阶段的结果自动累积到 ``stage_results`` 中。
    """

    # 必填
    forceid: str
    claim_info: Dict[str, Any]
    reviewer: Any                           # AIClaimReviewer 实例
    session: aiohttp.ClientSession
    policy_terms: str

    # 审核序号（用于日志）
    index: int = 1
    total: int = 1

    # 材料提取结果（由 MaterialExtractor 填充，在 stages 之前执行）
    ocr_results: Dict[str, Any] = field(default_factory=dict)
    vision_data: Dict[str, Any] = field(default_factory=dict)

    # 各阶段结果累积区（key = StageHandler.stage_key）
    stage_results: Dict[str, Any] = field(default_factory=dict)

    # 调试信息 & 日志
    debug: List[Any] = field(default_factory=list)

    # ── 便捷访问器 ──────────────────────────────────────────────────────────

    def get_stage(self, key: str) -> Optional[Dict[str, Any]]:
        """安全获取某阶段结果，不存在时返回空 dict。"""
        return self.stage_results.get(key) or {}

    def as_debug_dict(self) -> Dict[str, Any]:
        """导出为 debug 字典（供保存到结果文件）。"""
        return {"debug": self.debug, "stage_results": self.stage_results}


# ─────────────────────────────────────────────────────────────────────────────
# 管道执行器
# ─────────────────────────────────────────────────────────────────────────────

class AuditPipeline:
    """
    通用审核管道执行器。

    将若干 StageHandler 串联成一条有序管道，依次执行，遇到 early_return 则提前终止。

    参数
    ----
    handlers : List[StageHandler | None]
        阶段处理器列表，None 表示跳过该位置的阶段。
        建议按以下顺序传入（可省略）：
          [TimeCheckHandler, ExclusionCheckHandler, MaterialsCheckHandler,
           CoverageCheckHandler, CompensationCalcHandler]

    stage_max_retries : 每个阶段 StageRunner 的最大重试次数（默认 3）
    stage_retry_sleep : 首次重试等待秒数（指数退避基数，默认 3.0）
    """

    def __init__(
        self,
        *,
        ctx: PipelineContext,
        handlers: List[Optional[StageHandler]],
        stage_max_retries: int = 3,
        stage_retry_sleep: float = 3.0,
    ) -> None:
        self._ctx = ctx
        self._handlers = [h for h in handlers if h is not None]
        self._max_retries = stage_max_retries
        self._retry_sleep = stage_retry_sleep

        # 建立 StageRunner（共享 ctx.debug）
        _runner_ctx: Dict[str, Any] = {
            "debug": ctx.debug,
            **ctx.stage_results,
        }
        self._runner = StageRunner(ctx=_runner_ctx, forceid=ctx.forceid)

    async def execute(self) -> Dict[str, Any]:
        """
        顺序执行所有阶段，返回最终审核结果。

        - 若某阶段返回 early_return，立即停止并返回该值。
        - 若某阶段 run() 抛出异常，由 handler.on_error() 构建错误回包并返回。
        - 所有阶段正常完成后，调用 ``_build_final_result()``——子类可覆盖。
        """
        ctx = self._ctx
        forceid = ctx.forceid

        for handler in self._handlers:
            stage_key = handler.stage_key

            log_stage(
                forceid=forceid,
                index=ctx.index,
                total=ctx.total,
                stage_key=stage_key,
            )

            # 用 StageRunner 包裹，享受重试 + 熔断器
            raw_result, err = await self._runner.run(
                stage_key,
                self._call_handler,
                handler,
                max_retries=self._max_retries,
                retry_sleep=self._retry_sleep,
            )

            if err is not None:
                LOGGER.error(
                    f"[{ctx.index}/{ctx.total}] 阶段 {stage_key} 最终失败: {err}",
                    extra=log_extra(forceid=forceid, stage=stage_key, attempt=0),
                )
                return handler.on_error(
                    forceid=forceid,
                    err=err,
                    ctx_dict=ctx.as_debug_dict(),
                )

            # 解包结果
            stage_result, early_return = self._unpack(raw_result)
            ctx.stage_results[stage_key] = stage_result

            if early_return is not None:
                LOGGER.info(
                    f"[{ctx.index}/{ctx.total}] 阶段 {stage_key} 触发早退",
                    extra=log_extra(forceid=forceid, stage=stage_key, attempt=0),
                )
                return early_return

        # 所有阶段完成，交给子类/调用方构建最终结果
        return await self._build_final_result()

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    @staticmethod
    async def _call_handler(
        handler: StageHandler,
        ctx: "PipelineContext",
    ) -> Any:
        """包装为 StageRunner 可调用的协程函数。"""
        return await handler.run(ctx)

    # 注意：execute 中调用时需要把 ctx 传给 _call_handler
    # 但 StageRunner.run 的签名是 func(*args)，所以我们需要额外处理
    async def _run_stage(
        self,
        handler: StageHandler,
    ) -> Any:
        """直接执行 handler，不通过 StageRunner（内部调用）。"""
        return await handler.run(self._ctx)

    @staticmethod
    def _unpack(raw: Any) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        解包 handler 的返回值。

        约定：
          若 raw 包含 "early_return" key → 分离出来
          若 raw 包含 "stage_result"  key → 用它作为本阶段结果
          否则 → 整个 raw 作为 stage_result，early_return=None
        """
        if not isinstance(raw, dict):
            return ({} if raw is None else {"value": raw}), None

        early = raw.get("early_return")      # None 表示不早退
        if "stage_result" in raw:
            return raw["stage_result"], early
        if "early_return" in raw:
            # 移除 early_return key，剩余部分作为 stage_result
            rest = {k: v for k, v in raw.items() if k != "early_return"}
            return rest, early
        return raw, None

    async def _build_final_result(self) -> Dict[str, Any]:
        """
        所有阶段正常完成后的最终结果构建。

        默认行为：把所有阶段结果合并后返回，供调用方后处理。
        子类或调用方通常会覆盖此逻辑（例如 build_approval_return）。

        更推荐的做法：在最后一个 StageHandler（通常是 CompensationCalc）
        的 run() 中直接返回完整的最终回包（通过 early_return 机制传出），
        这样 _build_final_result 可以保持为简单的合并器。
        """
        return {
            "forceid": self._ctx.forceid,
            "stage_results": self._ctx.stage_results,
            "DebugInfo": self._ctx.as_debug_dict(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 修复：StageRunner 的调用方式适配
# ─────────────────────────────────────────────────────────────────────────────
# StageRunner.run(stage_name, func, *args) → func(*args)
# 我们需要把 (handler, ctx) 一起传进去。
# 为此覆盖 AuditPipeline.execute 中的调用，改用 lambda 适配器。

# 替换 execute 中的 _call_handler 为闭包版本，避免 ctx 传递问题。
# （上方 _call_handler 为静态方法作为文档，实际执行见下方 _make_stage_fn）

def _make_stage_fn(handler: StageHandler, ctx: PipelineContext):
    """构造一个无参协程，供 StageRunner 调用。"""
    async def _fn():
        return await handler.run(ctx)
    return _fn


# 打补丁：覆盖 AuditPipeline.execute 使用 _make_stage_fn
_orig_execute = AuditPipeline.execute

async def _patched_execute(self: AuditPipeline) -> Dict[str, Any]:
    ctx = self._ctx
    forceid = ctx.forceid

    for handler in self._handlers:
        stage_key = handler.stage_key

        log_stage(
            forceid=forceid,
            index=ctx.index,
            total=ctx.total,
            stage_key=stage_key,
        )

        stage_fn = _make_stage_fn(handler, ctx)

        raw_result, err = await self._runner.run(
            stage_key,
            stage_fn,
            max_retries=self._max_retries,
            retry_sleep=self._retry_sleep,
        )

        if err is not None:
            LOGGER.error(
                f"[{ctx.index}/{ctx.total}] 阶段 {stage_key} 最终失败: {err}",
                extra=log_extra(forceid=forceid, stage=stage_key, attempt=0),
            )
            return handler.on_error(
                forceid=forceid,
                err=err,
                ctx_dict=ctx.as_debug_dict(),
            )

        stage_result, early_return = AuditPipeline._unpack(raw_result)
        ctx.stage_results[stage_key] = stage_result

        if early_return is not None:
            LOGGER.info(
                f"[{ctx.index}/{ctx.total}] 阶段 {stage_key} 触发早退",
                extra=log_extra(forceid=forceid, stage=stage_key, attempt=0),
            )
            return early_return

    return await self._build_final_result()


AuditPipeline.execute = _patched_execute  # type: ignore[method-assign]
