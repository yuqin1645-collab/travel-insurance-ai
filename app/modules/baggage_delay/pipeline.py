"""
行李延误审核主流程 — app/modules/baggage_delay/pipeline.py

纯编排层，只做 stage 串联。所有业务实现已提取到 stages/ 子目录：
  - stages/aviation_lookup.py    官方航班查询 + 联程/改签修正
  - stages/vision_merge.py       视觉结果合并
  - stages/accident_validation.py 事故类型校验（丢失 vs 延误）
  - stages/material_gate.py       材料门禁
  - stages/post_process.py        AI审计 + 赔付核算 + 最终结果
  - stages/handlers.py           准入校验（保单/身份/免责/国内航班/到达时间）
  - stages/calculator.py         延误时长 + 赔付金额计算
  - stages/utils.py              工具函数
"""

from pathlib import Path
from typing import Any, Dict, List

import aiohttp

from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
from app.engine.workflow import StageRunner
from app.logging_utils import LOGGER, log_extra
from app.modules.flight_delay.stages.duplicate import _check_duplicate_claim

from app.modules.baggage_delay.stages.accident_validation import validate_accident_type
from app.modules.baggage_delay.stages.aviation_lookup import run_aviation_lookup
from app.modules.baggage_delay.stages.handlers import (
    _check_actual_arrival_vs_policy,
    _check_domestic_flight,
    _check_exclusions,
    _check_info_consistency,
    _check_policy_validity,
    _try_transfer_flight_receipt_time,
)
from app.modules.baggage_delay.stages.material_gate import run_material_gate
from app.modules.baggage_delay.stages.post_process import run_post_process
from app.modules.baggage_delay.stages.utils import (
    _classify_aviation_failure,
    _extract_file_names,
    _result,
)
from app.modules.baggage_delay.stages.vision_merge import _merge_vision_to_parsed

BAGGAGE_DELAY_THRESHOLD_HOURS = 6
POLICY_EXCERPT_MAX_CHARS = 1000


async def review_baggage_delay_async(
    *,
    reviewer: Any,
    claim_folder: Path,
    claim_info: Dict[str, Any],
    policy_terms: str,
    index: int,
    total: int,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """行李延误审核主流程（编排层）。"""
    forceid = str(claim_info.get("forceid") or "unknown")
    description = str(claim_info.get("Description_of_Accident") or "")
    assessment = str(claim_info.get("Assessment_Remark") or "")
    text_blob = f"{description}\n{assessment}".strip()
    file_names = _extract_file_names(claim_info)

    debug: Dict[str, Any] = {
        "policy_terms_excerpt": (policy_terms or "")[:POLICY_EXCERPT_MAX_CHARS],
        "claim_folder": str(claim_folder),
        "file_count": len(file_names),
        "file_names_sample": file_names[:10],
        "debug": [],
    }
    runner = StageRunner(ctx=debug, forceid=forceid)

    LOGGER.info(
        f"[{index}/{total}] 行李延误审核开始",
        extra=log_extra(forceid=forceid, stage="baggage_delay_start", attempt=0),
    )
    conclusions: List[Dict[str, str]] = []

    # ========== stage0_duplicate: 重复理赔检测 ==========
    duplicate_check = _check_duplicate_claim(claim_info=claim_info, forceid=forceid)
    if duplicate_check:
        LOGGER.info(
            f"[{index}/{total}] 重复理赔检测命中: {duplicate_check.get('reason', '')}",
            extra=log_extra(forceid=forceid, stage="bd_duplicate_check", attempt=0),
        )
        return duplicate_check

    # ========== stage0_vision: 视觉识别 ==========
    vision_extract: Dict[str, Any] = {}
    try:
        extractor = MaterialExtractor(reviewer=reviewer, forceid=forceid)
        extraction = await extractor.extract(
            claim_folder=claim_folder, claim_info=claim_info,
            strategy=ExtractionStrategy.VISION_DIRECT,
            prompt_name="00_vision_extract", session=session,
        )
        raw_vision = extraction.vision_data
        if isinstance(raw_vision, dict):
            vision_extract = raw_vision
        elif isinstance(raw_vision, list) and raw_vision and isinstance(raw_vision[0], dict):
            vision_extract = raw_vision[0]
        LOGGER.info(
            f"[{index}/{total}] 视觉识别完成: has_boarding={vision_extract.get('has_boarding_or_ticket')} "
            f"has_delay_proof={vision_extract.get('has_baggage_delay_proof')} "
            f"has_receipt_proof={vision_extract.get('has_baggage_receipt_time_proof')}",
            extra=log_extra(forceid=forceid, stage="baggage_delay_vision", attempt=0),
        )
    except Exception as _ve:
        LOGGER.warning(
            f"[{index}/{total}] 视觉识别失败（降级到纯文本）: {_ve}",
            extra=log_extra(forceid=forceid, stage="baggage_delay_vision", attempt=0),
        )
    debug["vision_extract"] = vision_extract

    # ========== stage0_5: AI结构化抽取 + 视觉合并 ==========
    ai_parsed, parse_err = await runner.run(
        "baggage_delay_parse", reviewer._ai_baggage_delay_parse_async,
        claim_info, text_blob, session=session,
        max_retries=2, retry_sleep=2.0,
    )
    if parse_err:
        debug["parse_warning"] = str(parse_err)[:200]
    if isinstance(ai_parsed, dict):
        debug["ai_parsed"] = ai_parsed

    if vision_extract and isinstance(ai_parsed, dict):
        ai_parsed = await _merge_vision_to_parsed(
            vision_extract=vision_extract, ai_parsed=ai_parsed,
            claim_info=claim_info, claim_folder=claim_folder,
            debug=debug, reviewer=reviewer, session=session, text_blob=text_blob,
        )
    elif vision_extract and not isinstance(ai_parsed, dict):
        ai_parsed = dict(vision_extract)

    # 行李转运航班修复
    _clear_baggage_forwarding_alternate(ai_parsed, vision_extract, debug)

    # ========== 前置准入校验 ==========
    policy_violation = _check_policy_validity(claim_info, debug, vision_extract=vision_extract)
    if policy_violation:
        conclusions.append({"checkpoint": "前置准入", "Eligible": "否", "Remark": policy_violation})
        policy_action = debug.get("policy_validity_action", "reject")
        return _result(forceid, policy_violation, "S" if policy_action == "supplement" else "N", conclusions, debug)

    identity_violation = _check_info_consistency(claim_info, ai_parsed or {})
    if identity_violation:
        conclusions.append({"checkpoint": "身份一致性", "Eligible": "否", "Remark": identity_violation})
        return _result(forceid, identity_violation, "N", conclusions, debug)

    domestic_reason = _check_domestic_flight(vision_extract, ai_parsed or {}, claim_info)
    if domestic_reason:
        conclusions.append({"checkpoint": "航段检查", "Eligible": "否", "Remark": domestic_reason})
        return _result(forceid, domestic_reason, "N", conclusions, debug)

    exclusion_reason = _check_exclusions(claim_info, text_blob, ai_parsed or {})
    if exclusion_reason:
        conclusions.append({"checkpoint": "免责条款", "Eligible": "否", "Remark": exclusion_reason})
        return _result(forceid, f"拒赔：{exclusion_reason}", "N", conclusions, debug)

    # ========== 官方航班数据补强 ==========
    aviation_lookup: Dict[str, Any] = {}
    try:
        aviation_lookup = await run_aviation_lookup(
            ai_parsed=ai_parsed, vision_extract=vision_extract,
            claim_info=claim_info, debug=debug, session=session,
        )
    except Exception as e:
        debug["aviation_lookup_warning"] = str(e)[:200]
    debug["aviation_lookup"] = aviation_lookup

    _record_aviation_conclusion(aviation_lookup, ai_parsed, conclusions, debug)

    # ========== 转运航班到达时间回退 ==========
    transfer_flight_debug = await _try_transfer_flight_receipt_time(
        ai_parsed or {}, vision_extract, session,
    )
    debug["transfer_flight_receipt"] = transfer_flight_debug

    # ========== 实际到达时间 vs 保单有效期 ==========
    arrival_policy_reason = _check_actual_arrival_vs_policy(claim_info, ai_parsed or {}, debug)
    if arrival_policy_reason:
        conclusions.append({"checkpoint": "保单有效期", "Eligible": "否", "Remark": arrival_policy_reason})
        return _result(forceid, arrival_policy_reason, "N", conclusions, debug)

    # ========== 事故类型校验 ==========
    accident_result = validate_accident_type(
        ai_parsed=ai_parsed, vision_extract=vision_extract,
        text_blob=text_blob, conclusions=conclusions, debug=debug,
    )
    if accident_result and accident_result.get("early_return"):
        return _result(forceid, accident_result["remark"], "N", conclusions, debug)

    # ========== 材料门禁 ==========
    missing_materials = run_material_gate(
        ai_parsed=ai_parsed, vision_extract=vision_extract,
        claim_info=claim_info, text_blob=text_blob,
        file_names=file_names, debug=debug,
    )
    if missing_materials:
        conclusions.append({"checkpoint": "材料完整性", "Eligible": "需补齐资料", "Remark": "；".join(missing_materials)})
        return _result(forceid, "需补齐资料：" + "；".join(missing_materials), "Y", conclusions, debug)
    conclusions.append({"checkpoint": "材料完整性", "Eligible": "是", "Remark": "视觉识别确认关键材料已提供"})

    # ========== AI审计 + 赔付核算 + 最终结果 ==========
    return await run_post_process(
        reviewer=reviewer, claim_info=claim_info, ai_parsed=ai_parsed,
        policy_terms=policy_terms, text_blob=text_blob,
        vision_extract=vision_extract,
        aviation_failure_type=debug.get("aviation_failure_type", "none"),
        conclusions=conclusions, debug=debug, runner=runner,
        session=session, forceid=forceid,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 小工具函数
# ══════════════════════════════════════════════════════════════════════════════

def _clear_baggage_forwarding_alternate(
    ai_parsed: Dict[str, Any],
    vision_extract: Dict[str, Any],
    debug: Dict[str, Any],
) -> None:
    """若 alternate.alt_flight_no 是行李转运航班（非乘客改签），清除改签标记。"""
    if not isinstance(ai_parsed, dict):
        return
    _alt = ai_parsed.get("alternate") or {}
    if not isinstance(_alt, dict):
        return
    _alt_fn = str(_alt.get("alt_flight_no") or "").strip().upper()
    if not _alt_fn:
        return
    import re as _re
    _bf_hints = [
        "行李装载", "行李装在", "行李装在今日", "行李转运", "行李已装载",
        "行李将搭乘", "行李运抵", "行李托运回", "行李送达", "行李将运",
        "您的行李将", "行李会搭乘", "行李后续",
        "baggage loaded", "baggage forwarded", "baggage will arrive",
        "baggage will travel", "luggage will arrive",
    ]
    for _src in (ai_parsed, vision_extract):
        for _fl in (_src.get("all_flights_found") or []):
            if str(_fl.get("flight_no") or "").strip().upper() == _alt_fn:
                _role = str(_fl.get("role_hint") or "").strip()
                _src_f = str(_fl.get("source") or "").strip()
                if any(h in _role or h in _src_f for h in _bf_hints):
                    _alt["is_connecting_rebooking"] = "false"
                    _alt["is_connecting_missed"] = "false"
                    debug["alternate_cleared_baggage_forwarding"] = (
                        f"alternate航班号{_alt_fn}为行李转运航班，非乘客改签，清除改签标记"
                    )
                    return


def _record_aviation_conclusion(
    aviation_lookup: Dict[str, Any],
    ai_parsed: Dict[str, Any],
    conclusions: List[Dict[str, str]],
    debug: Dict[str, Any],
) -> None:
    """记录官方航班查询结论到 conclusions 和 debug。"""
    aviation_failure_type = _classify_aviation_failure(aviation_lookup)
    debug["aviation_failure_type"] = aviation_failure_type

    if aviation_failure_type == "system_error":
        conclusions.append({
            "checkpoint": "官方航班数据", "Eligible": "需人工判断",
            "Remark": f"官方航班查询异常: {str(aviation_lookup.get('error') or '')[:120]}",
        })
    elif aviation_failure_type == "evidence_gap":
        has_boarding = str(ai_parsed.get("has_boarding_or_ticket") or "").strip().lower() == "true"
        has_flight_info = bool(
            (ai_parsed.get("flight_no") and str(ai_parsed.get("flight_no")).strip().lower() not in ("unknown", ""))
            and (ai_parsed.get("flight_date") and str(ai_parsed.get("flight_date")).strip().lower() not in ("unknown", ""))
        )
        if has_boarding and has_flight_info:
            conclusions.append({
                "checkpoint": "官方航班数据", "Eligible": "是",
                "Remark": "以材料中的航班信息为准（官方航班数据源未命中，不影响审核）",
            })
        else:
            conclusions.append({
                "checkpoint": "官方航班数据", "Eligible": "需补齐资料",
                "Remark": "官方航班数据未命中，需补充可核验航班号/日期/航段信息",
            })
    elif aviation_lookup.get("success") is True:
        conclusions.append({
            "checkpoint": "官方航班数据", "Eligible": "是",
            "Remark": "已获取官方实际到达时间用于时长核算",
        })