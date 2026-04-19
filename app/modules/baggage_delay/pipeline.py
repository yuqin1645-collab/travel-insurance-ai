import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

import aiohttp

from app.engine.workflow import StageRunner
from app.logging_utils import LOGGER, log_extra
from app.skills.flight_lookup import get_flight_lookup_skill
from app.rules.common.policy_validity import check as _rules_check_policy_validity
from app.rules.common.material_gate import check as _rules_material_gate, BAGGAGE_DELAY_KEYWORDS
from app.rules.flight.exclusions import check as _rules_check_exclusions, BAGGAGE_DELAY_EXCLUSIONS
from app.rules.claim_types.baggage_delay import compute_payout as _rules_compute_payout


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


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _extract_delay_hours(text: str) -> Optional[float]:
    if not text:
        return None
    candidates: List[float] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:小时|小時|h|hour|hours)", text, flags=re.IGNORECASE):
        candidates.append(float(m.group(1)))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:天|day|days)", text, flags=re.IGNORECASE):
        candidates.append(float(m.group(1)) * 24.0)
    if not candidates:
        return None
    return max(candidates)


def _extract_delay_hours_from_parsed(parsed: Dict[str, Any]) -> Optional[float]:
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("delay_hours")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        m = re.search(r"(\d+(?:\.\d+)?)", raw)
        if m:
            return float(m.group(1))
    return None


def _parse_dt_flexible(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "unknown":
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _extract_date_yyyy_mm_dd(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    m = re.search(r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})", s)
    if not m:
        return ""
    return m.group(1).replace("/", "-")


def _collect_receipt_times(parsed: Dict[str, Any]) -> List[datetime]:
    values: List[datetime] = []
    if not isinstance(parsed, dict):
        return values

    direct = _parse_dt_flexible(parsed.get("baggage_receipt_time"))
    if direct:
        values.append(direct)

    # 支持多份签收证明：receipt_times 列表中取最晚时间
    for item in parsed.get("receipt_times") or []:
        dt = _parse_dt_flexible(item)
        if dt:
            values.append(dt)
    return values


def _compute_delay_hours_by_rule(parsed: Dict[str, Any], text_blob: str) -> Dict[str, Any]:
    """
    行李延误规则口径：
    延误时长 = 行李实际签收时间（最晚） - 首次乘坐航班实际到达时间
    """
    result: Dict[str, Any] = {
        "delay_hours": None,
        "method": "unknown",
        "flight_actual_arrival_time": None,
        "baggage_receipt_time": None,
    }
    if not isinstance(parsed, dict):
        v = _extract_delay_hours(text_blob)
        if v is not None:
            result.update({"delay_hours": v, "method": "text_fallback"})
        return result

    arrival_dt = _parse_dt_flexible(parsed.get("flight_actual_arrival_time"))
    receipt_list = _collect_receipt_times(parsed)
    receipt_dt = max(receipt_list) if receipt_list else None

    if arrival_dt and receipt_dt and receipt_dt >= arrival_dt:
        delta_hours = (receipt_dt - arrival_dt).total_seconds() / 3600.0
        result.update(
            {
                "delay_hours": round(delta_hours, 2),
                "method": "arrival_receipt_delta",
                "flight_actual_arrival_time": arrival_dt.strftime("%Y-%m-%d %H:%M"),
                "baggage_receipt_time": receipt_dt.strftime("%Y-%m-%d %H:%M"),
            }
        )
        return result

    # 时间字段不完整时回退到模型/文本小时数
    parsed_hours = _extract_delay_hours_from_parsed(parsed)
    if parsed_hours is not None:
        result.update({"delay_hours": parsed_hours, "method": "parsed_delay_hours"})
        return result

    text_hours = _extract_delay_hours(text_blob)
    if text_hours is not None:
        result.update({"delay_hours": text_hours, "method": "text_fallback"})
    return result


def _classify_aviation_failure(aviation_lookup: Dict[str, Any]) -> str:
    """
    将官方航班查询失败分为：
    - system_error: 数据源/网络/配置异常 -> 人工复核
    - evidence_gap: 航班信息不足或未命中 -> 补件
    - none: 无失败或无查询
    """
    if not isinstance(aviation_lookup, dict) or not aviation_lookup:
        return "none"
    if aviation_lookup.get("success") is True:
        return "none"
    err = str(aviation_lookup.get("error") or "").lower()
    system_markers = [
        "api key",
        "http ",
        "请求失败",
        "timeout",
        "network",
        "ssl",
        "解析失败",
        "connection",
        "mcp",
    ]
    if any(m in err for m in system_markers):
        return "system_error"
    evidence_markers = [
        "未找到航班",
        "error_code=10",
        "查询失败",
        "不支持",
        "返回错误",
    ]
    if any(m in err for m in evidence_markers):
        return "evidence_gap"
    return "evidence_gap"


def _extract_file_names(claim_info: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for item in claim_info.get("FileList") or []:
        if not isinstance(item, dict):
            continue
        file_url = str(item.get("FileUrl") or "").strip()
        if not file_url:
            continue
        path_name = unquote(urlparse(file_url).path.split("/")[-1])
        if path_name:
            names.append(path_name.lower())
    return names


def _check_policy_validity(claim_info: Dict[str, Any], debug: Dict[str, Any]) -> Optional[str]:
    # 保单有效期综合校验（委托 rules.common.policy_validity）
    result = _rules_check_policy_validity(claim_info)
    debug["policy_validity"] = result.detail
    if not result.passed:
        return result.reason
    return None


def _material_gate(text_blob: str, file_names: List[str]) -> List[str]:
    missing: List[str] = []
    if not file_names:
        missing.append("缺少附件材料（需上传票据、行李延误证明、签收证明）")
        return missing

    joined = f"{text_blob} {' '.join(file_names)}".lower()
    keywords = {
        "理赔申请书": ["理赔申请", "申请书", "claim form"],
        "被保险人身份证正反面": ["身份证", "identity"],
        "被保险人银行卡（借记卡）": ["银行卡", "借记卡", "bank card"],
        "交通票据（机票/登机牌/行程单）": ["机票", "登机牌", "行程单", "ticket", "boarding"],
        "行李延误证明（含航班及原因）": ["行李延误", "行李不正常", "pir", "baggage", "delay proof"],
        "行李签收时间证明": ["签收", "领取", "receipt", "delivered"],
        "护照照片页、签证页、出入境盖章页": ["护照", "签证", "出入境", "passport", "visa", "exit"],
        "其他确认保险事故性质原因的相关材料": ["委托书", "监护人", "关系证明"],
    }
    for label, words in keywords.items():
        if not any(w in joined for w in words):
            missing.append(label)
    return missing


def _check_special_materials(claim_info: Dict[str, Any], text_blob: str, file_names: List[str]) -> List[str]:
    """
    特殊场景材料校验：未成年人、委托代办
    """
    needs: List[str] = []
    is_minor = str(claim_info.get("Is_Minor") or "").strip().lower() == "true"
    is_agent = str(claim_info.get("Is_Agent") or "").strip().lower() == "true"
    joined = f"{text_blob} {' '.join(file_names)}".lower()
    if is_minor:
        if not any(kw in joined for kw in ["出生", "出生证", "出生医学证明", "户口簿", "监护关系"]):
            needs.append("未成年人：补充监护人身份证正反面、出生证或可证明监护关系的户口簿")
    if is_agent:
        if not any(kw in joined for kw in ["委托", "授权", "受托人"]):
            needs.append("委托代办：补充授权委托书、受托人身份证")
    return needs


def _check_info_consistency(claim_info: Dict[str, Any], ai_parsed: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    信息一致性校验：
    - 航班号一致性（延误证明、登机牌、保单、官方记录）
    - 行李牌号一致性
    - 姓名匹配
    - 行李归属（同行人行李均登记在一人名下，仅该登记人有资格）
    """
    flags: List[Dict[str, str]] = []
    flight_no = str(ai_parsed.get("flight_no") or "").strip().upper()
    ticket_flight = str(claim_info.get("Flight_Number") or "").strip().upper()
    policy_flight = str(claim_info.get("Policy_FlightNumber") or "").strip().upper()
    aviation_flight = str(claim_info.get("Aviation_FlightNumber") or "").strip().upper()
    if flight_no and ticket_flight and flight_no != ticket_flight:
        flags.append({"checkpoint": "航班号一致性", "Eligible": "否", "Remark": f"延误证明航班号{flight_no}与机票{ ticket_flight }不匹配"})
    if flight_no and policy_flight and flight_no != policy_flight:
        flags.append({"checkpoint": "航班号一致性", "Eligible": "否", "Remark": f"延误证明航班号{flight_no}与保单{ policy_flight }不匹配"})
    if flight_no and aviation_flight and flight_no != aviation_flight:
        flags.append({"checkpoint": "航班号一致性", "Eligible": "否", "Remark": f"延误证明航班号{flight_no}与官方记录{ aviation_flight }不匹配"})

    baggage_tag = str(ai_parsed.get("baggage_tag") or "").strip()
    boarding_baggage_tag = str(claim_info.get("Baggage_Tag") or "").strip()
    if baggage_tag and boarding_baggage_tag and baggage_tag != boarding_baggage_tag:
        flags.append({"checkpoint": "行李牌号一致性", "Eligible": "否", "Remark": f"行李牌号{baggage_tag}与登机牌{ boarding_baggage_tag }不匹配"})

    passenger_name = str(ai_parsed.get("passenger_name") or "").strip()
    insured_name = str(claim_info.get("Insured_And_Policy") or claim_info.get("Insured_Name") or "").strip()
    if passenger_name and insured_name and passenger_name != insured_name:
        flags.append({"checkpoint": "姓名匹配", "Eligible": "否", "Remark": f"行李材料登记姓名{passenger_name}与保单权益人{ insured_name }不匹配"})

    travel_group = claim_info.get("Travel_Group_Members") or []
    if isinstance(travel_group, list) and len(travel_group) > 1:
        for member in travel_group:
            if isinstance(member, dict):
                m_name = str(member.get("Name") or "").strip()
                m_baggage = str(member.get("Baggage_Owner") or "").strip()
                if m_baggage and m_baggage != passenger_name:
                    flags.append({"checkpoint": "行李归属", "Eligible": "否", "Remark": f"同行人{m_name}的行李登记在{ m_baggage }名下，非本人托运"})
    return flags


def _check_exclusions(claim_info: Dict[str, Any], text_blob: str, parsed: Dict[str, Any]) -> Optional[str]:
    """条款除外责任校验（委托 rules.flight.exclusions）"""
    content = f"{str(claim_info.get('Description_of_Accident') or '')} {str(claim_info.get('Assessment_Remark') or '')} {text_blob}"
    extra = str(parsed.get("notes") or "")
    result = _rules_check_exclusions(content, BAGGAGE_DELAY_EXCLUSIONS, extra_text=extra)
    if not result.passed:
        return result.reason
    return None


def _compute_payout_with_rules(
    delay_hours: float,
    claim_amount: Optional[float],
    cap: Optional[float],
    ai_parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
) -> float:
    """赔付金额核算（委托 rules.claim_types.baggage_delay）"""
    personal_claim = _safe_float(claim_info.get("Personal_Effect_Claim_Amount"))
    result = _rules_compute_payout(delay_hours, claim_amount, cap, personal_claim)
    return result.detail.get("payout", 0.0)


def _material_gate(text_blob: str, file_names: List[str]) -> List[str]:
    """材料门禁校验（委托 rules.common.material_gate，使用行李延误关键词映射）"""
    result = _rules_material_gate(text_blob, file_names, BAGGAGE_DELAY_KEYWORDS)
    return result.detail.get("missing", [])


def _result(forceid: str, remark: str, is_additional: str, conclusions: List[Dict[str, str]], debug: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "forceid": forceid,
        "claim_type": "baggage_delay",
        "Remark": remark,
        "IsAdditional": is_additional,
        "KeyConclusions": conclusions,
        "DebugInfo": debug,
    }


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
    forceid = str(claim_info.get("forceid") or "unknown")
    description = str(claim_info.get("Description_of_Accident") or "")
    assessment = str(claim_info.get("Assessment_Remark") or "")
    text_blob = f"{description}\n{assessment}".strip()
    file_names = _extract_file_names(claim_info)

    debug: Dict[str, Any] = {
        "policy_terms_excerpt": (policy_terms or "")[:1000],
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

    # 0) AI结构化抽取（失败时降级到规则解析）
    ai_parsed, parse_err = await runner.run(
        "baggage_delay_parse",
        reviewer._ai_baggage_delay_parse_async,
        claim_info,
        text_blob,
        session=session,
        max_retries=2,
        retry_sleep=2.0,
    )
    if parse_err:
        debug["parse_warning"] = str(parse_err)[:200]
    if isinstance(ai_parsed, dict):
        debug["ai_parsed"] = ai_parsed

    # 前置准入校验（保单有效性、有效期、身份匹配）
    policy_violation = _check_policy_validity(claim_info, debug)
    if policy_violation:
        conclusions.append({"checkpoint": "前置准入", "Eligible": "否", "Remark": policy_violation})
        return _result(forceid, policy_violation, "N", conclusions, debug)

    # 免责条款校验（条款除外）
    exclusion_reason = _check_exclusions(claim_info, text_blob, ai_parsed or {})
    if exclusion_reason:
        conclusions.append({"checkpoint": "免责条款", "Eligible": "否", "Remark": exclusion_reason})
        return _result(forceid, f"拒赔：{exclusion_reason}", "N", conclusions, debug)

    # 0.5) 官方航班数据补强：优先用飞常准的实际到达时间作为时长起算点
    aviation_lookup: Dict[str, Any] = {}
    try:
        if isinstance(ai_parsed, dict):
            flight_no = str(ai_parsed.get("flight_no") or "").strip()
            dep_iata = str(ai_parsed.get("dep_iata") or "").strip().upper()
            arr_iata = str(ai_parsed.get("arr_iata") or "").strip().upper()
            flight_date = (
                _extract_date_yyyy_mm_dd(ai_parsed.get("flight_date"))
                or _extract_date_yyyy_mm_dd(claim_info.get("Date_of_Accident"))
            )
            if flight_no and flight_date:
                skill = get_flight_lookup_skill()
                aviation_lookup = await skill.lookup_status(
                    flight_no=flight_no,
                    date=flight_date,
                    dep_iata=dep_iata if dep_iata and dep_iata != "UNKNOWN" else None,
                    arr_iata=arr_iata if arr_iata and arr_iata != "UNKNOWN" else None,
                    session=session,
                )
                if aviation_lookup.get("success"):
                    actual_arr = aviation_lookup.get("actual_arr")
                    if actual_arr:
                        ai_parsed["flight_actual_arrival_time"] = actual_arr
                        debug["arrival_source"] = "variflight_actual_arr"
                else:
                    debug["arrival_source"] = "material_or_llm_fallback"
    except Exception as e:
        debug["aviation_lookup_warning"] = str(e)[:200]
    debug["aviation_lookup"] = aviation_lookup
    aviation_failure_type = _classify_aviation_failure(aviation_lookup)
    debug["aviation_failure_type"] = aviation_failure_type
    if aviation_failure_type == "system_error":
        conclusions.append(
            {
                "checkpoint": "官方航班数据",
                "Eligible": "需人工判断",
                "Remark": f"官方航班查询异常: {str(aviation_lookup.get('error') or '')[:120]}",
            }
        )
    elif aviation_failure_type == "evidence_gap":
        conclusions.append(
            {
                "checkpoint": "官方航班数据",
                "Eligible": "需补件",
                "Remark": "官方航班数据未命中，需补充可核验航班号/日期/航段信息",
            }
        )
    elif aviation_lookup.get("success") is True:
        conclusions.append(
            {
                "checkpoint": "官方航班数据",
                "Eligible": "是",
                "Remark": "已获取官方实际到达时间用于时长核算",
            }
        )

    # 1) 事故属性校验：若为行李丢失，不属于本责任
    parsed_accident_type = str((ai_parsed or {}).get("accident_type") or "").strip().lower()
    if parsed_accident_type == "baggage_loss" or (("行李丢失" in text_blob) and ("延误" not in text_blob)):
        conclusions.append({"checkpoint": "事故类型", "Eligible": "否", "Remark": "事故为行李丢失，需转随身财产损失责任"})
        return _result(
            forceid,
            "拒赔：事故类型为行李丢失，非托运行李延误责任",
            "N",
            conclusions,
            debug,
        )
    conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "未发现行李丢失单独触发，继续按行李延误审核"})

    # 3) 材料门禁（含必备材料、特殊场景）
    missing_materials = _material_gate(text_blob, file_names)
    if isinstance(ai_parsed, dict):
        if str(ai_parsed.get("has_boarding_or_ticket") or "").lower() == "false":
            missing_materials.append("交通票据（机票/登机牌/行程单）")
        if str(ai_parsed.get("has_baggage_delay_proof") or "").lower() == "false":
            missing_materials.append("行李延误证明（含航班及原因）")
        if str(ai_parsed.get("has_baggage_receipt_time_proof") or "").lower() == "false":
            missing_materials.append("行李签收时间证明")
        # 特殊场景材料校验
        special_needs = _check_special_materials(claim_info, text_blob, file_names)
        missing_materials.extend(special_needs)
        if missing_materials:
            missing_materials = sorted(set(missing_materials))
    debug["missing_materials"] = missing_materials
    if missing_materials:
        conclusions.append({"checkpoint": "材料完整性", "Eligible": "需补件", "Remark": "；".join(missing_materials)})
        return _result(
            forceid,
            "需补件：" + "；".join(missing_materials),
            "Y",
            conclusions,
            debug,
        )
    conclusions.append({"checkpoint": "材料完整性", "Eligible": "是", "Remark": "关键材料关键词已覆盖（基于文本与附件文件名）"})

    # 4) 人工复核触发
    manual_flags = []
    manual_keywords = ["手写", "多语言", "伪造", "ps", "涂改", "矛盾", "争议", "模糊"]
    for kw in manual_keywords:
        if kw in text_blob.lower():
            manual_flags.append(kw)
    parsed_risk = str((ai_parsed or {}).get("manual_review_risk") or "").strip().lower()
    if parsed_risk and parsed_risk not in {"none", "unknown"}:
        manual_flags.append(parsed_risk)
    if manual_flags:
        debug["manual_review_flags"] = manual_flags
        conclusions.append({"checkpoint": "人工复核触发", "Eligible": "需人工判断", "Remark": f"命中关键词: {','.join(manual_flags)}"})
        return _result(forceid, "转人工复核：存在材料识别或真实性争议", "Y", conclusions, debug)

    # 5) 延误时长核算与门槛
    delay_calc = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
    delay_hours = delay_calc.get("delay_hours")
    debug["delay_calc"] = delay_calc
    if delay_hours is None:
        if aviation_failure_type == "system_error":
            return _result(
                forceid,
                "转人工复核：官方航班数据查询异常，无法完成时长核算",
                "Y",
                conclusions,
                debug,
            )
        conclusions.append({"checkpoint": "延误时长", "Eligible": "需补件", "Remark": "未识别到明确延误时长或签收时间信息"})
        return _result(
            forceid,
            "需补件：请补充行李签收证明（含签收时间）或承运人出具的行李延误时长证明",
            "Y",
            conclusions,
            debug,
        )
    if delay_hours < 6:
        conclusions.append({"checkpoint": "赔付门槛", "Eligible": "否", "Remark": f"延误时长{delay_hours:.2f}小时，未达到6小时"})
        return _result(forceid, "拒赔：行李延误时长未达到6小时赔付门槛", "N", conclusions, debug)
    conclusions.append({"checkpoint": "赔付门槛", "Eligible": "是", "Remark": f"延误时长{delay_hours:.2f}小时，达到赔付门槛"})

    # 信息一致性校验
    consistency_flags = _check_info_consistency(claim_info, ai_parsed or {})
    for flag in consistency_flags:
        conclusions.append(flag)
    if any(f.get("Eligible") == "否" for f in consistency_flags):
        return _result(forceid, "拒赔：信息一致性校验失败（航班号/行李牌号/姓名不匹配）", "N", conclusions, debug)

    # 5.5) AI审核意见（可选，失败不影响主流程）
    ai_audit, audit_err = await runner.run(
        "baggage_delay_audit",
        reviewer._ai_baggage_delay_audit_async,
        claim_info,
        {
            "delay_hours": delay_hours,
            "missing_materials": missing_materials,
            "manual_flags": manual_flags,
            "rule_conclusions": conclusions,
            "ai_parsed": ai_parsed or {},
        },
        policy_terms or "",
        session=session,
        max_retries=1,
        retry_sleep=1.0,
    )
    if audit_err:
        debug["audit_warning"] = str(audit_err)[:200]
    elif isinstance(ai_audit, dict):
        debug["ai_audit"] = ai_audit

    # 6) 赔付核算
    claim_amount = _safe_float(claim_info.get("Amount"))
    insured_amount = _safe_float(claim_info.get("Insured_Amount"))
    remaining_coverage = _safe_float(claim_info.get("Remaining_Coverage"))
    cap = None
    if insured_amount is not None and remaining_coverage is not None:
        cap = min(insured_amount, remaining_coverage)
    elif insured_amount is not None:
        cap = insured_amount
    elif remaining_coverage is not None:
        cap = remaining_coverage
    payout = _compute_payout_with_rules(
        delay_hours,
        claim_amount,
        cap,
        ai_parsed or {},
        claim_info,
    )
    debug["amounts"] = {
        "claim_amount": claim_amount,
        "insured_amount": insured_amount,
        "remaining_coverage": remaining_coverage,
        "cap_used": cap,
        "payout": payout,
    }

    conclusions.append({"checkpoint": "赔付核算", "Eligible": "是", "Remark": f"按阶梯核算赔付金额{payout:.2f}元"})
    return _result(
        forceid,
        f"审核通过：行李延误{delay_hours:.2f}小时，建议赔付{payout:.2f}元",
        "N",
        conclusions,
        debug,
    )
