from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import aiohttp

from app.logging_utils import LOGGER, log_extra
from app.engine.workflow import StageRunner
from app.engine.stage_fallbacks import build_stage_error_return
from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor

from app.modules.flight_delay.stages.utils import _policy_excerpt_or_default
from app.modules.flight_delay.stages.hardcheck import _run_hardcheck
from app.modules.flight_delay.stages.payout import _run_payout_calc
from app.modules.flight_delay.stages.delay_calc import _augment_with_computed_delay
from app.modules.flight_delay.stages.postprocess import _postprocess_audit_result
from app.modules.flight_delay.stages.duplicate import _check_duplicate_claim
from app.modules.flight_delay.stages.validators import _check_hardcheck_exclusion
from app.modules.flight_delay.stages.vision_merge import merge_vision_into_parsed
from app.modules.flight_delay.stages.aviation_lookup import lookup_aviation_data
from app.modules.flight_delay.stages.alt_flight_lookup import lookup_alt_flight_data


async def review_flight_delay_async(
    *,
    reviewer: Any,
    claim_folder: Path,
    claim_info: Dict[str, Any],
    policy_terms: str,
    index: int,
    total: int,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """
    航班延误审核主流程（编排层）：
    - stage0: 重复理赔检测
    - stage0_vision: 视觉/OCR 材料抽取
    - stage1: AI 数据解析与时区标准化
    - stage1.2: 合并 Vision 抽取结果
    - stage1.3: 飞常准航班权威数据查询
    - stage1.4: 接驳/替代航班飞常准查询
    - stage_hardcheck: 代码侧硬校验集合
    - stage10: 赔付金额预计算
    - stage2_precheck: 硬免责前置拦截
    - stage2: AI 理赔判定
    - postprocess: 规则兜底后处理
    """
    forceid = str(claim_info.get("forceid") or "unknown")
    ctx: Dict[str, Any] = {"debug": [], "flight_delay": None}

    runner = StageRunner(ctx=ctx, forceid=forceid)
    free_text = (claim_info.get("Description_of_Accident") or "").strip()

    # ========== stage0_duplicate: 重复理赔检测 ==========
    duplicate_check = _check_duplicate_claim(claim_info=claim_info, forceid=forceid)
    if duplicate_check:
        LOGGER.info(
            f"[{index}/{total}] 重复理赔检测命中: {duplicate_check.get('reason', '')}",
            extra=log_extra(forceid=forceid, stage="fd_duplicate_check", attempt=0),
        )
        return duplicate_check
    # 代码侧重复检测未命中，注入结果防止 LLM 自行判定重复理赔
    ctx["fd_duplicate_check"] = {"triggered": False, "note": "代码侧重复理赔检测未命中"}

    # ========== stage0_vision: 视觉/OCR 材料抽取 ==========
    LOGGER.info(
        f"[{index}/{total}] 航班延误-视觉抽取: 启动材料提取...",
        extra=log_extra(forceid=forceid, stage="fd_vision_extract", attempt=0),
    )
    vision_extract: Dict[str, Any] = {}
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
        elif isinstance(raw_vision, list) and raw_vision and isinstance(raw_vision[0], dict):
            vision_extract = raw_vision[0]
        else:
            LOGGER.warning(
                f"[{forceid}] 视觉抽取结果类型非 dict：{type(raw_vision).__name__}",
                extra=log_extra(forceid=forceid, stage="fd_vision_extract", attempt=0),
            )
    except Exception as _ve:
        LOGGER.warning(
            f"[{forceid}] 视觉抽取阶段异常（降级跳过）: {_ve}",
            extra=log_extra(forceid=forceid, stage="fd_vision_extract", attempt=0),
        )
    ctx["flight_delay_vision_extract"] = vision_extract

    # ========== stage1: 数据解析与时区标准化 ==========
    LOGGER.info(f"[{index}/{total}] 航班延误-阶段1: 数据解析与时区标准化...", extra=log_extra(forceid=forceid, stage="fd_parse", attempt=0))
    parsed, err = await runner.run(
        "fd_parse",
        reviewer._ai_flight_delay_parse_async,
        claim_info,
        free_text,
        session=session,
        max_retries=2,
        retry_sleep=2.0,
    )
    if err:
        return build_stage_error_return(forceid=forceid, checkpoint="航班信息解析/时区标准化", err=err, ctx=ctx)
    ctx["flight_delay_parse"] = parsed

    # ========== stage1.2: 合并 Vision 抽取结果 ==========
    parsed = merge_vision_into_parsed(parsed, vision_extract)
    ctx["flight_delay_parse"] = parsed

    # ========== stage1.3: 飞常准航班权威数据查询 ==========
    aviation_data = await lookup_aviation_data(
        parsed=parsed,
        vision_extract=vision_extract,
        forceid=forceid,
        session=session,
    )
    parsed = aviation_data["parsed"]
    ctx["flight_delay_parse"] = parsed
    ctx["flight_delay_aviation_lookup"] = aviation_data["aviation_result"]
    ctx["flight_delay_aviation_all_candidates"] = aviation_data["aviation_results_all"]
    cf_candidates = aviation_data["cf_candidates"]

    # ========== stage1.4: 接驳/替代航班飞常准查询 ==========
    alt_data = await lookup_alt_flight_data(
        parsed=parsed,
        vision_extract=vision_extract,
        cf_candidates=cf_candidates,
        forceid=forceid,
        session=session,
    )
    parsed = alt_data["parsed"]
    ctx["flight_delay_parse"] = parsed
    for key, val in alt_data["alt_results"].items():
        ctx[f"flight_delay_{key}"] = val

    policy_excerpt = _policy_excerpt_or_default(claim_info, policy_terms)
    parsed = _augment_with_computed_delay(parsed=parsed, policy_terms_excerpt=policy_excerpt, free_text=free_text)
    ctx["flight_delay_parse_enriched"] = parsed

    # ========== stage_hardcheck: 代码侧硬校验集合 ==========
    LOGGER.info(f"[{index}/{total}] 航班延误-硬校验: Skills B/C/E/H/I...", extra=log_extra(forceid=forceid, stage="fd_hardcheck", attempt=0))
    hardcheck = _run_hardcheck(parsed=parsed, claim_info=claim_info, policy_excerpt=policy_excerpt, free_text=free_text, vision_extract=ctx.get("flight_delay_vision_extract") or {}, claim_folder=claim_folder)
    ctx["flight_delay_hardcheck"] = hardcheck

    # ========== 阶段10: 赔付金额预计算（代码侧） ==========
    payout_result = _run_payout_calc(parsed=parsed, claim_info=claim_info, policy_excerpt=policy_excerpt)
    ctx["flight_delay_payout"] = payout_result

    # ========== stage2_precheck: 硬免责前置拦截 ==========
    exclusion_result = _check_hardcheck_exclusion(hardcheck=hardcheck)
    if exclusion_result:
        LOGGER.info(
            f"[{index}/{total}] 硬免责命中，跳过AI判定: {exclusion_result['explanation'][:80]}",
            extra=log_extra(forceid=forceid, stage="fd_exclusion_precheck", attempt=0),
        )
        audit = exclusion_result
        ctx["flight_delay_audit"] = audit
        ctx["flight_delay_audit_post"] = audit
        audit_result = audit["audit_result"]
        is_additional = "Y" if audit_result == "需补齐资料" else "N"
        remark = "航班延误: " + audit["explanation"]
        return {
            "forceid": forceid,
            "ClaimId": claim_info.get("ClaimId", ""),
            "claim_type": "flight_delay",
            "Remark": remark,
            "IsAdditional": is_additional,
            "KeyConclusions": [{"checkpoint": "航班延误审核", "Eligible": "N", "Remark": remark}],
            "flight_delay_audit": audit,
            "DebugInfo": ctx,
        }

    # 将 hardcheck 结果注入 parsed，让 AI 审核阶段能感知代码侧判定
    # 避免 AI 独立重新判定已被代码豁免的免责条款
    parsed["hardcheck_code_assessment"] = {
        "missed_connection_check": hardcheck.get("missed_connection_check", {}),
        "transit_check": hardcheck.get("transit_check", {}),
        "duplicate_claim_check": ctx.get("fd_duplicate_check", {"triggered": False}),
    }

    # ========== stage2: AI 理赔判定 ==========
    LOGGER.info(f"[{index}/{total}] 航班延误-阶段2: 理赔判定...", extra=log_extra(forceid=forceid, stage="fd_audit", attempt=0))
    audit, err = await runner.run(
        "fd_audit",
        reviewer._ai_flight_delay_audit_async,
        claim_info,
        parsed,
        policy_excerpt,
        session=session,
        max_retries=2,
        retry_sleep=2.0,
        payout_json=payout_result,
    )
    if err:
        return build_stage_error_return(forceid=forceid, checkpoint="航班延误理赔判定", err=err, ctx=ctx)
    ctx["flight_delay_audit"] = audit

    # ========== 规则兜底后处理 ==========
    audit = _postprocess_audit_result(
        parsed=parsed,
        audit=audit,
        policy_terms_excerpt=policy_excerpt,
        hardcheck=hardcheck,
        payout_result=payout_result,
        free_text=free_text,
    )
    ctx["flight_delay_audit_post"] = audit

    # ========== 组装标准输出 ==========
    audit_result = str(audit.get("audit_result") or "").strip()
    is_additional = "Y" if audit_result == "需补齐资料" else "N"
    remark_prefix = "航班延误: "
    remark = remark_prefix + str(audit.get("explanation") or audit_result or "完成判定")

    hardcheck_notes: List[str] = []
    if hardcheck.get("war_risk", {}).get("is_war_risk"):
        affected = hardcheck.get("war_risk", {}).get("affected_locations", [])
        location_str = f"（受影响地区：{', '.join(affected)}）" if affected else ""
        hardcheck_notes.append(f"[战争风险] {hardcheck['war_risk'].get('note', '')}{location_str}")
    if hardcheck.get("transit_check", {}).get("is_domestic_cn"):
        hardcheck_notes.append(f"[境内中转免责] 中转地={hardcheck['transit_check'].get('iata')}")
    if hardcheck.get("coverage_area", {}).get("in_coverage") is False:
        hardcheck_notes.append("[超出承保区域]")
    if hardcheck.get("coverage_area_text_check", {}).get("in_coverage") is False:
        hardcheck_notes.append(f"[文本兜底-超出承保区域] {hardcheck['coverage_area_text_check'].get('note','')}")
    if hardcheck.get("same_day_policy_check", {}).get("is_denied") is True:
        hardcheck_notes.append(f"[同天投保免责] {hardcheck['same_day_policy_check'].get('note','')}")
    if hardcheck.get("name_match_check", {}).get("match_result") == "mismatch":
        hardcheck_notes.append(f"[姓名不符] {hardcheck['name_match_check'].get('note','')}")
    if hardcheck.get("inheritance_check", {}).get("is_inheritance_suspected"):
        hardcheck_notes.append(f"[疑似遗产继承] {hardcheck['inheritance_check'].get('note','')}")
    if hardcheck.get("capacity_check", {}).get("needs_guardian"):
        hardcheck_notes.append(f"[未成年/限制行为能力人] {hardcheck['capacity_check'].get('note','')}")
    if hardcheck.get("missed_connection_check", {}).get("is_missed_connection"):
        hardcheck_notes.append("[中转接驳免责]")
    if hardcheck.get("passenger_civil_check", {}).get("is_passenger_civil") is False:
        hardcheck_notes.append(f"[非客运航班] {hardcheck['passenger_civil_check'].get('flight_no', '')}")
    if hardcheck.get("fraud_foreseeability_check", {}).get("fraud_suspected"):
        hardcheck_notes.append("[欺诈嫌疑-需人工复核]")
    missing_req = hardcheck.get("required_materials_check", {}).get("missing_required") or []
    if missing_req:
        hardcheck_notes.append(f"[缺必备材料] {'、'.join(missing_req)}")
    if hardcheck_notes:
        remark += "；硬校验标记：" + "；".join(hardcheck_notes)

    key_conclusions = [
        {
            "checkpoint": "航班延误审核",
            "Eligible": "Y" if audit_result == "通过" else "N",
            "Remark": remark,
        }
    ]

    return {
        "forceid": forceid,
        "ClaimId": claim_info.get("ClaimId", ""),
        "claim_type": "flight_delay",
        "Remark": remark,
        "IsAdditional": is_additional,
        "KeyConclusions": key_conclusions,
        "flight_delay_audit": audit,
        "DebugInfo": ctx,
    }
