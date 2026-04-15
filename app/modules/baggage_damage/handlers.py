"""
随身财产险审核阶段处理器 — app/modules/baggage_damage/handlers.py

将 stages.py 中的 4 个异步函数包装为 StageHandler 子类，
使 baggage_damage pipeline 可以接入通用 AuditPipeline 框架。

各阶段对应关系：
  AccidentCheckHandler   → ai_judge_accident_async   (免责 + 事故范围)
  MaterialsCheckHandler  → ai_check_materials_async  (缺件)
  CoverageCheckHandler   → ai_check_coverage_async   (保障责任)
  CompensationCalcHandler→ ai_calculate_compensation_async (赔付计算)

早退逻辑（与原 pipeline.py 一致）：
  - AccidentCheckHandler  : is_excluded=True → 直接拒赔
  - MaterialsCheckHandler : is_complete=False → 补件通知
  - CoverageCheckHandler  : not coverage_eligible → 拒赔
  - CompensationCalcHandler: amount<=0 → 零赔; 无法确定原价 → 转人工
    全部通过 → 返回 build_approval_return 作为 early_return（终止管道并输出）

注意：early_return 机制在 AuditPipeline 中统一处理，handler.run() 只需把
早退结果放在返回 dict 的 "early_return" 字段里即可。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.engine.audit_pipeline import PipelineContext, StageHandler
from app.modules.baggage_damage.accident import build_exclusion_early_return
from app.modules.baggage_damage.compensation import (
    build_unreliable_price_manual_return,
    build_zero_payout_return,
)
from app.modules.baggage_damage.coverage import (
    build_stage_system_failure_return,
    is_system_failure_reason,
)
from app.modules.baggage_damage.decision import build_denial_return
from app.modules.baggage_damage.final import build_approval_return
from app.modules.baggage_damage.materials import build_material_gate_early_return
from app.modules.baggage_damage.stages import (
    ai_calculate_compensation_async,
    ai_check_coverage_async,
    ai_check_materials_async,
    ai_judge_accident_async,
)
from app.logging_utils import LOGGER, log_extra


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2：免责 + 事故范围判断
# ─────────────────────────────────────────────────────────────────────────────

class AccidentCheckHandler(StageHandler):
    """
    调用 ai_judge_accident_async，判断：
      1. 是否触发免责条款 (is_excluded=True) → 早退拒赔
      2. 是否在保障范围内 (is_covered=False) → 后续 coverage 阶段再处理

    本阶段只做早退 for exclusion，其余推迟到 coverage 阶段二次验证。
    """

    stage_key = "accident"

    async def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        result = await ai_judge_accident_async(
            ctx.reviewer,
            ctx.claim_info,
            ctx.ocr_results,
            ctx.policy_terms,
            ctx.session,
        )

        # 免责触发 → 立即早退
        early = build_exclusion_early_return(
            forceid=ctx.forceid,
            accident_result=result,
            ctx=ctx.as_debug_dict(),
        )
        if early:
            LOGGER.info(
                f"[{ctx.index}/{ctx.total}] reject: exclusion matched",
                extra=log_extra(forceid=ctx.forceid, stage=self.stage_key, attempt=0),
            )

        return {
            "stage_result": result,
            "early_return": early,
        }


# ────────────────────────────────────────────────────────────────────────���────
# Stage 3：材料完整性审核
# ─────────────────────────────────────────────────────────────────────────────

class MaterialsCheckHandler(StageHandler):
    """
    调用 ai_check_materials_async，判断材料是否完整。
    is_complete=False → 补件通知（early_return）。
    """

    stage_key = "materials"

    async def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        result = await ai_check_materials_async(
            ctx.reviewer,
            ctx.claim_info,
            ctx.ocr_results,
            ctx.policy_terms,
            ctx.session,
        )

        LOGGER.info(
            f"[{ctx.index}/{ctx.total}] materials complete: {str(result.get('reason', ''))[:50]}",
            extra=log_extra(forceid=ctx.forceid, stage=self.stage_key, attempt=0),
        )

        early = build_material_gate_early_return(
            forceid=ctx.forceid,
            material_result=result,
            ctx=ctx.as_debug_dict(),
            ensure_purchase_proof=True,
        )
        if early:
            missing = (result.get("missing_materials") or []) if isinstance(result, dict) else []
            LOGGER.info(
                f"[{ctx.index}/{ctx.total}] need additional materials: {len(missing) if isinstance(missing, list) else 0}",
                extra=log_extra(forceid=ctx.forceid, stage=self.stage_key, attempt=0),
            )

        return {
            "stage_result": result,
            "early_return": early,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4：保障责任符合性审核
# ─────────────────────────────────────────────────────────────────────────────

class CoverageCheckHandler(StageHandler):
    """
    调用 ai_check_coverage_async，判断是否在保障范围内。
    同时回溯检查 accident 阶段的系统失败状态。
    """

    stage_key = "coverage"

    async def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        result = await ai_check_coverage_async(
            ctx.reviewer,
            ctx.claim_info,
            ctx.policy_terms,
            ctx.session,
        )

        forceid = ctx.forceid
        coverage_reason = str(result.get("reason", "") or "")

        # 系统侧异常 → 转人工
        if is_system_failure_reason(coverage_reason):
            LOGGER.warning(
                f"[{ctx.index}/{ctx.total}] coverage system failure: {coverage_reason[:200]}",
                extra=log_extra(forceid=forceid, stage=self.stage_key, attempt=0),
            )
            return {
                "stage_result": result,
                "early_return": build_stage_system_failure_return(
                    forceid=forceid,
                    stage_checkpoint="coverage_check",
                    reason=coverage_reason,
                    ctx=ctx.as_debug_dict(),
                ),
            }

        # 保障责任不符合 → 拒赔
        coverage_eligible = (
            result.get("has_coverage", False)
            and result.get("in_coverage_period", False)
            and not result.get("exceeds_limit", True)
        )
        if not coverage_eligible:
            LOGGER.info(
                f"[{ctx.index}/{ctx.total}] reject: not covered",
                extra=log_extra(forceid=forceid, stage=self.stage_key, attempt=0),
            )
            return {
                "stage_result": result,
                "early_return": build_denial_return(
                    forceid=forceid,
                    checkpoint="coverage_check",
                    remark=str(result.get("reason", "") or "coverage_not_matched"),
                    ctx=ctx.as_debug_dict(),
                ),
            }

        # 回溯检查 accident 阶段系统失败
        accident_result: Dict[str, Any] = ctx.get_stage("accident")
        accident_reason = str(accident_result.get("reason", "") or "")
        if is_system_failure_reason(accident_reason):
            LOGGER.warning(
                f"[{ctx.index}/{ctx.total}] accident system failure: {accident_reason[:200]}",
                extra=log_extra(forceid=forceid, stage="accident", attempt=0),
            )
            return {
                "stage_result": result,
                "early_return": build_stage_system_failure_return(
                    forceid=forceid,
                    stage_checkpoint="accident_check",
                    reason=accident_reason,
                    ctx=ctx.as_debug_dict(),
                ),
            }

        # 事故不在保障范围内 → 拒赔
        if not accident_result.get("is_covered", False):
            LOGGER.info(
                f"[{ctx.index}/{ctx.total}] reject: outside coverage scope",
                extra=log_extra(forceid=forceid, stage="accident", attempt=0),
            )
            return {
                "stage_result": result,
                "early_return": build_denial_return(
                    forceid=forceid,
                    checkpoint="accident_check",
                    remark=str(accident_result.get("coverage_reason", "") or "accident_not_covered"),
                    ctx=ctx.as_debug_dict(),
                ),
            }

        return {
            "stage_result": result,
            "early_return": None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Stage 5：赔付金额计算
# ─────────────────────────────────────────────────────────────────────────────

class CompensationCalcHandler(StageHandler):
    """
    调用 ai_calculate_compensation_async，计算最终赔付金额。
    通过后，构建 approval 回包并以 early_return 形式终止管道。
    """

    stage_key = "compensation"

    async def run(self, ctx: PipelineContext) -> Dict[str, Any]:
        coverage_result: Dict[str, Any] = ctx.get_stage("coverage")
        material_result: Dict[str, Any] = ctx.get_stage("materials")
        accident_result: Dict[str, Any] = ctx.get_stage("accident")

        needs_manual = bool(material_result.get("needs_manual_review", False))
        manual_review_hint = (
            (material_result.get("manual_review_reason") or "").strip() if needs_manual else ""
        )

        result = await ai_calculate_compensation_async(
            ctx.reviewer,
            ctx.claim_info,
            ctx.ocr_results,
            ctx.policy_terms,
            coverage_result,
            ctx.session,
        )

        forceid = ctx.forceid
        final_amount = result.get("final_amount", 0)

        # 原价无法可靠确定 → 转人工
        early_comp = build_unreliable_price_manual_return(
            forceid=forceid,
            compensation_result=result,
            ctx=ctx.as_debug_dict(),
        )
        if early_comp:
            LOGGER.info(
                f"[{ctx.index}/{ctx.total}] need manual review: unreliable purchase amount",
                extra=log_extra(forceid=forceid, stage=self.stage_key, attempt=0),
            )
            return {
                "stage_result": result,
                "early_return": early_comp,
            }

        # 零赔付
        if final_amount <= 0:
            LOGGER.info(
                f"[{ctx.index}/{ctx.total}] zero payout",
                extra=log_extra(forceid=forceid, stage=self.stage_key, attempt=0),
            )
            return {
                "stage_result": result,
                "early_return": build_zero_payout_return(
                    forceid=forceid,
                    compensation_result=result,
                    ctx=ctx.as_debug_dict(),
                ),
            }

        # 全部通过 → 赔付
        LOGGER.info(
            f"[{ctx.index}/{ctx.total}] approved: {final_amount}",
            extra=log_extra(forceid=forceid, stage="final", attempt=0),
        )
        return {
            "stage_result": result,
            "early_return": build_approval_return(
                forceid=forceid,
                final_amount=float(final_amount or 0),
                coverage_result=coverage_result,
                material_result=material_result,
                accident_result=accident_result,
                manual_review_hint=manual_review_hint,
                ctx=ctx.as_debug_dict(),
            ),
        }
