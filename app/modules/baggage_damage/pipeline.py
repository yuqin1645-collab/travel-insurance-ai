from __future__ import annotations

"""
随身财产险审核主流程 — app/modules/baggage_damage/pipeline.py

使用 AuditPipeline 框架将原有的手写串联逻辑替换为声明式管道，
保持对外接口（review_baggage_damage_async 签名）完全不变。

审核流程：
  precheck（前置校验，非 AI）
  ↓ OCR 材料提取（MaterialExtractor - OCR_THEN_LLM 策略）
  ↓ travel_hint（出行日期提示，非 AI）
  ↓ Stage 2: accident  — 事故判责 + 免责触发
  ↓ Stage 3: materials — 材料完整性
  ↓ Stage 4: coverage  — 保障责任
  ↓ Stage 5: compensation — 赔付计算（内含最终 approval 构建）
"""

import traceback
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

from app.engine.audit_pipeline import AuditPipeline, PipelineContext
from app.engine.errors import build_exception_result
from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
from app.engine.precheck import run_precheck
from app.engine.travel_hint import record_travel_vs_effective_hint
from app.logging_utils import LOGGER, log_extra
from app.modules.baggage_damage.handlers import (
    AccidentCheckHandler,
    CompensationCalcHandler,
    CoverageCheckHandler,
    MaterialsCheckHandler,
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

        # ── 构建 PipelineContext ──────────────────────────────────────────────
        pipe_ctx = PipelineContext(
            forceid=forceid,
            claim_info=claim_info,
            reviewer=reviewer,
            session=session,
            policy_terms=policy_terms,
            index=index,
            total=total,
            ocr_results=ocr_results,
            vision_data=extraction.vision_data,
            debug=debug_list,
        )

        # ── 构建并执行 AuditPipeline ─────────────────────────────────────────
        pipeline = AuditPipeline(
            ctx=pipe_ctx,
            handlers=[
                AccidentCheckHandler(),
                MaterialsCheckHandler(),
                CoverageCheckHandler(),
                CompensationCalcHandler(),
            ],
            stage_max_retries=stage_max_retries,
            stage_retry_sleep=stage_retry_sleep,
        )
        return await pipeline.execute()

    except Exception as err:
        LOGGER.error(
            f"[{index}/{total}] exception: {err}",
            extra=log_extra(forceid=forceid, stage="exception", attempt=0),
        )
        traceback.print_exc()
        return build_exception_result(
            forceid=forceid,
            err=err,
            ctx={"debug": debug_list},
        )
