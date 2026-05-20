from __future__ import annotations

"""
随身财产险审核主流程 — app/modules/baggage_damage/pipeline.py

直接使用 StageRunner 串联 4 个审核阶段，与 flight_delay / baggage_delay 模式一致。

审核流程：
  precheck（前置校验，非 AI）
  ↓ OCR 材料提取（MaterialExtractor - OCR_THEN_LLM 策略）
  ↓ travel_hint（出行日期提示，非 AI）
  ↓ Stage 1: accident  — 事故判责 + 免责触发
  ↓ Stage 2: materials — 材料完整性
  ↓ Stage 3: coverage  — 保障责任（含回溯 accident 阶段校验）
  ↓ Stage 4: compensation — 赔付计算 → 最终结论
"""

from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

from app.engine.errors import build_exception_result
from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
from app.engine.pipeline_shared import build_stage_error_return_from_reason, is_system_failure_reason
from app.engine.precheck import run_precheck
from app.engine.stage_fallbacks import build_stage_error_return
from app.engine.travel_hint import record_travel_vs_effective_hint
from app.engine.workflow import StageRunner
from app.logging_utils import LOGGER, log_extra
from app.modules.baggage_damage.accident import build_exclusion_early_return
from app.modules.baggage_damage.compensation import (
    build_unreliable_price_manual_return,
    build_zero_payout_return,
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


async def review_baggage_damage_async(
    *,
    reviewer: Any,
    claim_folder: Path,
    claim_info: Dict[str, Any],
    policy_terms: str,
    index: int,
    total: int,
    session: aiohttp.ClientSession,
    stage_max_retries: int,
    stage_retry_sleep: float,
    ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    forceid = str(claim_info.get("forceid") or "unknown")
    debug_list = (ctx or {}).get("debug", []) if ctx else []

    # StageRunner 共享上下文（用于记录各阶段 debug）
    pipeline_ctx: Dict[str, Any] = {"debug": debug_list}
    runner = StageRunner(ctx=pipeline_ctx, forceid=forceid)

    try:
        # ── 前置校验（非 AI，快速）─────────────────────────────────────────
        pre = run_precheck(claim_info=claim_info, forceid=forceid, ctx={"debug": debug_list})
        if pre.early_return:
            return pre.early_return

        # ── 材料提取（OCR 策略）──────────────────────────────────────────────
        extractor = MaterialExtractor(reviewer=reviewer, forceid=forceid)
        extraction = await extractor.extract(
            claim_folder=claim_folder,
            claim_info=claim_info,
            strategy=ExtractionStrategy.OCR_THEN_LLM,
            session=session,
        )
        ocr_results = extraction.ocr_results

        # ── 出行日期提示（非 AI，仅记录警告）────────────────────────────────
        record_travel_vs_effective_hint(
            reviewer=reviewer,
            claim_info=claim_info,
            ocr_results=ocr_results,
            forceid=forceid,
            index=index,
            total=total,
            ctx={"debug": debug_list},
        )

        # ══════════════════════════════════════════════════════════════════════
        # Stage 1: 事故判责 + 免责触发
        # ══════════════════════════════════════════════════════════════════════
        LOGGER.info(
            f"[{index}/{total}] 随身财产-阶段1: 事故判责...",
            extra=log_extra(forceid=forceid, stage="accident", attempt=0),
        )
        accident_result, err = await runner.run(
            "accident",
            ai_judge_accident_async,
            reviewer,
            claim_info,
            ocr_results,
            policy_terms,
            session,
            max_retries=stage_max_retries,
            retry_sleep=stage_retry_sleep,
        )
        if err:
            return build_stage_error_return(
                forceid=forceid, checkpoint="accident", err=err, ctx=pipeline_ctx,
            )

        exclusion_early = build_exclusion_early_return(
            forceid=forceid, accident_result=accident_result, ctx=pipeline_ctx,
        )
        if exclusion_early:
            return exclusion_early

        # ══════════════════════════════════════════════════════════════════════
        # Stage 2: 材料完整性审核
        # ══════════════════════════════════════════════════════════════════════
        LOGGER.info(
            f"[{index}/{total}] 随身财产-阶段2: 材料完整性...",
            extra=log_extra(forceid=forceid, stage="materials", attempt=0),
        )
        material_result, err = await runner.run(
            "materials",
            ai_check_materials_async,
            reviewer,
            claim_info,
            ocr_results,
            policy_terms,
            session,
            max_retries=stage_max_retries,
            retry_sleep=stage_retry_sleep,
        )
        if err:
            return build_stage_error_return(
                forceid=forceid, checkpoint="materials", err=err, ctx=pipeline_ctx,
            )

        material_early = build_material_gate_early_return(
            forceid=forceid,
            material_result=material_result,
            ctx=pipeline_ctx,
            ensure_purchase_proof=True,
        )
        if material_early:
            return material_early

        # ══════════════════════════════════════════════════════════════════════
        # Stage 3: 保障责任符合性审核（含回溯 accident 阶段校验）
        # ══════════════════════════════════════════════════════════════════════
        LOGGER.info(
            f"[{index}/{total}] 随身财产-阶段3: 保障责任...",
            extra=log_extra(forceid=forceid, stage="coverage", attempt=0),
        )
        coverage_result, err = await runner.run(
            "coverage",
            ai_check_coverage_async,
            reviewer,
            claim_info,
            policy_terms,
            session,
            max_retries=stage_max_retries,
            retry_sleep=stage_retry_sleep,
        )
        if err:
            return build_stage_error_return(
                forceid=forceid, checkpoint="coverage", err=err, ctx=pipeline_ctx,
            )

        # 系统侧异常 → 转人工
        coverage_reason = str(coverage_result.get("reason", "") or "")
        if is_system_failure_reason(coverage_reason):
            return build_stage_error_return_from_reason(
                forceid=forceid,
                checkpoint="coverage_check",
                reason=coverage_reason,
                ctx=pipeline_ctx,
            )

        # 保障责任不符合 → 拒赔
        coverage_eligible = (
            coverage_result.get("has_coverage", False)
            and coverage_result.get("in_coverage_period", False)
            and not coverage_result.get("exceeds_limit", True)
        )
        if not coverage_eligible:
            return build_denial_return(
                forceid=forceid,
                checkpoint="coverage_check",
                remark=str(coverage_result.get("reason", "") or "coverage_not_matched"),
                ctx=pipeline_ctx,
            )

        # 回溯检查 accident 阶段系统失败
        accident_reason = str(accident_result.get("reason", "") or "")
        if is_system_failure_reason(accident_reason):
            return build_stage_error_return_from_reason(
                forceid=forceid,
                checkpoint="accident_check",
                reason=accident_reason,
                ctx=pipeline_ctx,
            )

        # 事故不在保障范围内 → 拒赔
        if not accident_result.get("is_covered", False):
            return build_denial_return(
                forceid=forceid,
                checkpoint="accident_check",
                remark=str(accident_result.get("coverage_reason", "") or "accident_not_covered"),
                ctx=pipeline_ctx,
            )

        # ══════════════════════════════════════════════════════════════════════
        # Stage 4: 赔付金额计算
        # ══════════════════════════════════════════════════════════════════════
        LOGGER.info(
            f"[{index}/{total}] 随身财产-阶段4: 赔付计算...",
            extra=log_extra(forceid=forceid, stage="compensation", attempt=0),
        )
        needs_manual = bool(material_result.get("needs_manual_review", False))
        manual_review_hint = (
            (material_result.get("manual_review_reason") or "").strip()
            if needs_manual else ""
        )

        compensation_result, err = await runner.run(
            "compensation",
            ai_calculate_compensation_async,
            reviewer,
            claim_info,
            ocr_results,
            policy_terms,
            coverage_result,
            session,
            max_retries=stage_max_retries,
            retry_sleep=stage_retry_sleep,
        )
        if err:
            return build_stage_error_return(
                forceid=forceid, checkpoint="compensation", err=err, ctx=pipeline_ctx,
            )

        # 原价无法可靠确定 → 转人工
        early_comp = build_unreliable_price_manual_return(
            forceid=forceid, compensation_result=compensation_result, ctx=pipeline_ctx,
        )
        if early_comp:
            return early_comp

        final_amount = compensation_result.get("final_amount", 0)
        # 零赔付
        if final_amount <= 0:
            return build_zero_payout_return(
                forceid=forceid, compensation_result=compensation_result, ctx=pipeline_ctx,
            )

        # 全部通过 → 赔付
        return build_approval_return(
            forceid=forceid,
            final_amount=float(final_amount or 0),
            coverage_result=coverage_result,
            material_result=material_result,
            accident_result=accident_result,
            manual_review_hint=manual_review_hint,
            ctx=pipeline_ctx,
        )

    except Exception as err:
        LOGGER.error(
            f"[{index}/{total}] exception: {err}",
            extra=log_extra(forceid=forceid, stage="exception", attempt=0),
            exc_info=True,
        )
        return build_exception_result(
            forceid=forceid,
            err=err,
            ctx={"debug": debug_list},
        )