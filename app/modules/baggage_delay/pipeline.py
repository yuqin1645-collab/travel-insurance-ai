import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

import aiohttp

from app.engine.workflow import StageRunner
from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
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
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        # 去掉时区信息，统一用 naive datetime 计算延误时长
        return dt.replace(tzinfo=None)
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


def _check_policy_validity(claim_info: Dict[str, Any], debug: Dict[str, Any],
                           vision_extract: Optional[Dict[str, Any]] = None) -> Optional[str]:
    # 保单有效期综合校验（委托 rules.common.policy_validity）
    # 将 vision 提取的出境时间和真实事故日期注入 claim_info（不修改原对象，用浅拷贝）
    info = dict(claim_info)
    if vision_extract:
        exit_dt = vision_extract.get("exit_datetime")
        if exit_dt and str(exit_dt).strip().lower() not in ("", "unknown"):
            info.setdefault("First_Exit_Date", exit_dt)
        # 用材料中提取的真实事故日期覆盖 claim_info 的 Date_of_Accident
        # （系统录入的 Date_of_Accident 可能有误，以材料为准）
        accident_dt = vision_extract.get("accident_date_in_materials")
        if accident_dt and str(accident_dt).strip().lower() not in ("", "unknown"):
            info["Date_of_Accident"] = accident_dt
        # 注入航班日期（必须含完整年份）：来源优先级 flight_date > flight_actual_arrival_time 日期部分
        # flight_date 由 vision prompt 从行程单/出入境记录/行李延误证明/签收单/PIR 中提取
        flight_date_v = vision_extract.get("flight_date")
        if flight_date_v and str(flight_date_v).strip().lower() not in ("", "unknown"):
            info.setdefault("Flight_Date", str(flight_date_v).strip())
        elif not info.get("Flight_Date") and not info.get("Policy_FlightDate"):
            # 次选：从 flight_actual_arrival_time 取日期部分（实际到达时间含年份）
            arr_time = vision_extract.get("flight_actual_arrival_time")
            if arr_time and str(arr_time).strip().lower() not in ("", "unknown"):
                info.setdefault("Flight_Date", str(arr_time).strip()[:10])
    result = _rules_check_policy_validity(info)
    debug["policy_validity"] = result.detail
    debug["policy_validity_action"] = result.action
    if not result.passed:
        return result.reason  # 调用方根据 debug["policy_validity_action"] 区分 reject/supplement
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


def _check_info_consistency(claim_info: Dict[str, Any], ai_parsed: Dict[str, Any]) -> Optional[str]:
    """
    信息一致性校验：
    - 姓名匹配（被保险人 vs 材料中识别到的被保险人姓名）→ 不匹配直接拒赔
    - 航班号一致性、行李牌号一致性 → 记录到 flags 供下游参考
    """
    insured_name = str(
        claim_info.get("Insured_And_Policy") or claim_info.get("Insured_Name") or ""
    ).strip()

    # 优先用 vision 识别的被保险人姓名，兜底用 ai_parsed 里的 passenger_name
    material_insured = str(
        ai_parsed.get("insured_name_in_materials") or ai_parsed.get("passenger_name") or ""
    ).strip()

    if insured_name and material_insured and material_insured.lower() not in ("unknown", ""):
        # 统一大写比较，处理中英文混合姓名格式差异（如 "ZHANG SAN" vs "张三"）
        if insured_name.upper().replace(" ", "") != material_insured.upper().replace(" ", ""):
            return (
                f"拒赔：材料中被保险人姓名[{material_insured}]与保单权益人[{insured_name}]不匹配，"
                "请确认材料与保单是否对应同一被保险人"
            )
    return None


def _check_airline_baggage_record_exception(
    vision_extract: Dict[str, Any],
    ai_parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    text_blob: str,
) -> bool:
    """
    航空公司官方行李记录替代托运行李牌的例外检查。

    当同时满足以下条件时，可用官方行李记录替代行李牌：
    1. 单人申请，无同行人
    2. 行李记录姓名、航班、日期与登机牌/机票100%匹配
    3. 行李件数为1件，与理赔件数一致
    """
    # 1) 检查是否存在航空公司官方行李记录
    has_airline_record = False
    baggage_record_info = {}

    # 读 ai_parsed 中的行李记录标识
    if ai_parsed.get("has_airline_baggage_record") == "true":
        has_airline_record = True
        baggage_record_info = {
            "name": ai_parsed.get("airline_baggage_record_name", ""),
            "flight": ai_parsed.get("airline_baggage_record_flight", ""),
            "pieces": ai_parsed.get("airline_baggage_record_pieces", ""),
        }

    # 双重保险：vision_extract 也读
    if not has_airline_record and vision_extract.get("has_airline_baggage_record") == "true":
        has_airline_record = True
        baggage_record_info = {
            "name": vision_extract.get("airline_baggage_record_name", ""),
            "flight": vision_extract.get("airline_baggage_record_flight", ""),
            "pieces": vision_extract.get("airline_baggage_record_pieces", ""),
        }

    if not has_airline_record:
        return False

    # 2) 检查是否单人申请（无同行人）
    fellow_travelers = claim_info.get("Fellow_Travelers") or claim_info.get("Co_Applicants") or ""
    if str(fellow_travelers).strip().lower() not in ("", "none", "null", "无"):
        return False  # 有同行人，不适用例外

    # 3) 检查行李件数是否为1件
    pieces = str(baggage_record_info.get("pieces") or "").strip().lower()
    if pieces not in ("1", "one", "壹", "1件"):
        return False  # 多件行李，不适用例外

    # 4) 检查姓名/航班匹配（宽松匹配，有识别到即可）
    record_name = str(baggage_record_info.get("name") or "").strip()
    insured_name = str(claim_info.get("Insured_And_Policy") or claim_info.get("Insured_Name") or "").strip()

    if record_name and insured_name:
        if record_name.upper().replace(" ", "") != insured_name.upper().replace(" ", ""):
            return False  # 姓名不匹配

    return True


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


def _compute_tier_amount(delay_hours: float) -> int:
    """根据延误时长计算档位金额（纯代码逻辑，不调 LLM）"""
    if delay_hours >= 18:
        return 1500
    elif delay_hours >= 12:
        return 1000
    elif delay_hours >= 6:
        return 500
    return 0


def _material_gate(text_blob: str, file_names: List[str]) -> List[str]:
    """材料门禁校验（委托 rules.common.material_gate，使用行李延误关键词映射）"""
    result = _rules_material_gate(text_blob, file_names, BAGGAGE_DELAY_KEYWORDS)
    return result.detail.get("missing", [])


def _result(forceid: str, remark: str, is_additional: str, conclusions: List[Dict[str, str]], debug: Dict[str, Any]) -> Dict[str, Any]:
    if remark.startswith("审核通过") or remark.startswith("赔付"):
        audit_result = "通过"
    elif is_additional == "Y" or remark.startswith("需补件") or remark.startswith("转人工"):
        audit_result = "需补件"
    else:
        audit_result = "拒绝"
    return {
        "forceid": forceid,
        "claim_type": "baggage_delay",
        "Remark": remark,
        "IsAdditional": is_additional,
        "KeyConclusions": conclusions,
        "baggage_delay_audit": {"audit_result": audit_result, "explanation": remark},
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

    # 0) 视觉识别：读取本地图片/PDF，提取材料中的关键字段（登机牌、PIR单、签收证明等）
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

    # 0.5) AI结构化抽取（纯文本兜底，补充视觉识别未覆盖的字段）
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

    # 合并视觉识别结果到 ai_parsed（视觉结果优先，文本解析兜底）
    if vision_extract and isinstance(ai_parsed, dict):
        for key in (
            "has_boarding_or_ticket", "has_baggage_delay_proof", "has_baggage_receipt_time_proof",
            "has_baggage_tag_proof",  # 行李牌只有 vision_extract 能看到，必须合并
            "has_airline_baggage_record", "airline_baggage_record_name",  # 航空公司行李记录（替代凭证）
            "airline_baggage_record_flight", "airline_baggage_record_pieces",
            "flight_actual_arrival_time", "baggage_receipt_time", "receipt_times", "delay_hours",
            "has_id_proof", "has_passport", "has_exit_entry_record", "exit_datetime",
            "has_bank_card_proof", "risk_flags",
        ):
            vision_val = vision_extract.get(key)
            parsed_val = ai_parsed.get(key)
            # 视觉结果非 unknown 时覆盖文本结果
            if vision_val is not None and str(vision_val).lower() not in ("unknown", "", "[]"):
                ai_parsed[key] = vision_val
            elif parsed_val is None:
                ai_parsed[key] = vision_val
        # 同步基础航班字段（vision 非 unknown 时覆盖 ai_parsed 的 unknown）
        for key in ("flight_no", "flight_date", "dep_iata", "arr_iata"):
            vision_val = vision_extract.get(key)
            if vision_val and str(vision_val).lower() not in ("unknown", ""):
                existing = ai_parsed.get(key)
                if existing is None or str(existing).lower() in ("unknown", ""):
                    ai_parsed[key] = vision_val

        # 安全网：交叉校验材料识别的一致性
        # 如果 vision 找到了 PIR 报告（baggage_delay_proof_source 有内容），但 has_baggage_delay_proof 为 false，自动纠正
        proof_source = vision_extract.get("baggage_delay_proof_source") or ""
        if proof_source and str(proof_source).lower() not in ("unknown", ""):
            if not ai_parsed.get("has_baggage_delay_proof"):
                ai_parsed["has_baggage_delay_proof"] = True
                debug.setdefault("auto_corrected", []).append("has_baggage_delay_proof: PIR报告存在但 vision 误判为 false，已自动纠正")
            # PIR 报告含旅客姓名+航班号+行李件数+PIR编号，按规则等同于行李牌
            if not ai_parsed.get("has_baggage_tag_proof"):
                ai_parsed["has_baggage_tag_proof"] = True
                debug.setdefault("auto_corrected", []).append("has_baggage_tag_proof: PIR报告含航班+行李信息，等效行李牌，已自动纠正")
        # 如果 vision 识别到签收证明的时间（baggage_receipt_time 有具体值），但 has_baggage_receipt_time_proof 为 false，自动纠正
        receipt_time = vision_extract.get("baggage_receipt_time") or ""
        if receipt_time and str(receipt_time).lower() not in ("unknown", ""):
            if not ai_parsed.get("has_baggage_receipt_time_proof"):
                ai_parsed["has_baggage_receipt_time_proof"] = True
                debug.setdefault("auto_corrected", []).append("has_baggage_receipt_time_proof: 签收时间已提取但 vision 误判为 false，已自动纠正")
    elif vision_extract and not isinstance(ai_parsed, dict):
        ai_parsed = dict(vision_extract)

    # 前置准入校验（保单有效性、有效期、身份匹配）
    policy_violation = _check_policy_validity(claim_info, debug, vision_extract=vision_extract)
    if policy_violation:
        conclusions.append({"checkpoint": "前置准入", "Eligible": "否", "Remark": policy_violation})
        policy_action = debug.get("policy_validity_action", "reject")
        if policy_action == "supplement":
            return _result(forceid, policy_violation, "S", conclusions, debug)
        return _result(forceid, policy_violation, "N", conclusions, debug)

    # 身份一致性校验（被保险人姓名与材料中识别的姓名对比）
    identity_violation = _check_info_consistency(claim_info, ai_parsed or {})
    if identity_violation:
        conclusions.append({"checkpoint": "身份一致性", "Eligible": "否", "Remark": identity_violation})
        return _result(forceid, identity_violation, "N", conclusions, debug)

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

    # 3) 材料门禁：优先使用视觉识别的 has_* 字段判断，true=通过，false=缺件，unknown=降级到文件名扫描
    missing_materials: List[str] = []
    if isinstance(ai_parsed, dict):
        def _has_flag(key: str) -> str:
            return str(ai_parsed.get(key) or "unknown").strip().lower()

        # 登机牌/行程单
        flag = _has_flag("has_boarding_or_ticket")
        if flag == "false":
            missing_materials.append("交通票据（机票/登机牌/行程单）")
        elif flag == "unknown":
            if not any(w in f"{text_blob} {' '.join(file_names)}".lower()
                       for w in ["机票", "登机牌", "行程单", "ticket", "boarding", "itinerary"]):
                missing_materials.append("交通票据（机票/登机牌/行程单）")

        # 行李延误证明 OR 行李签收时间证明：二选一有其一即可（PIR单/航司书面证明/签收单）
        delay_proof_flag = _has_flag("has_baggage_delay_proof")
        receipt_proof_flag = _has_flag("has_baggage_receipt_time_proof")
        joined_text = f"{text_blob} {' '.join(file_names)}".lower()
        delay_proof_kw = any(w in joined_text for w in ["行李延误", "行李不正常", "pir", "baggage delay", "delay proof", "property irregularity"])
        receipt_proof_kw = any(w in joined_text for w in ["签收", "领取", "receipt", "delivered", "delivery"])

        has_delay_proof = delay_proof_flag == "true" or (delay_proof_flag == "unknown" and delay_proof_kw)
        has_receipt_proof = receipt_proof_flag == "true" or (receipt_proof_flag == "unknown" and receipt_proof_kw)

        if not has_delay_proof and not has_receipt_proof:
            missing_materials.append("行李延误证明或行李签收单（航空公司出具的行李延误时数/原因书面证明，或含具体签收时间的行李签收单，二选一）")

        # 托运行李牌（必须有：含姓名、航班信息、行李牌号码）
        # 优先读合并后的 ai_parsed，vision_extract 已在上方合并进来
        tag_flag = _has_flag("has_baggage_tag_proof")
        # 双重保险：vision_extract 原始值直接再读一次，防止合并遗漏
        if tag_flag == "unknown":
            v_tag = str(vision_extract.get("has_baggage_tag_proof") or "unknown").strip().lower()
            if v_tag not in ("unknown", ""):
                tag_flag = v_tag

        # 例外规则：航空公司官方行李记录可替代托运行李牌
        # 适用条件：单人申请 + 行李记录姓名/航班与登机牌匹配 + 仅1件行李
        if tag_flag in ("false", "unknown"):
            exception_met = _check_airline_baggage_record_exception(
                vision_extract, ai_parsed or {}, claim_info, joined_text
            )
            if exception_met:
                debug["baggage_tag_exception"] = "航空公司官方行李记录满足替代条件，视同行李牌已提供"
            else:
                missing_materials.append("托运行李牌照片（含姓名、航班信息、行李牌号码）")

        # 身份证（可用视觉 has_id_proof 或 has_passport 替代，两者有一即可）
        id_flag = _has_flag("has_id_proof")
        passport_flag = _has_flag("has_passport")
        if id_flag == "false" and passport_flag == "false":
            missing_materials.append("被保险人身份证正反面或护照")

        # 护照（视觉明确识别为缺失时才要求补件）
        if passport_flag == "false" and id_flag in ("false", "unknown"):
            missing_materials.append("护照照片页、签证页、出入境盖章页")

        # 银行卡（非阻断性材料，仅在视觉明确识别为缺失时记录，不触发补件）
        bank_flag = _has_flag("has_bank_card_proof")
        if bank_flag == "false":
            debug["bank_card_warning"] = "视觉识别未见银行卡信息，建议人工确认打款账号"

        # 特殊场景材料校验
        special_needs = _check_special_materials(claim_info, text_blob, file_names)
        missing_materials.extend(special_needs)
        missing_materials = sorted(set(missing_materials))
    else:
        # ai_parsed 解析失败时降级到文件名关键词扫描
        missing_materials = _material_gate(text_blob, file_names)

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
    conclusions.append({"checkpoint": "材料完整性", "Eligible": "是", "Remark": "视觉识别确认关键材料已提供"})

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
    consistency_violation = _check_info_consistency(claim_info, ai_parsed or {})
    if consistency_violation:
        conclusions.append({"checkpoint": "信息一致性", "Eligible": "否", "Remark": consistency_violation})
        return _result(forceid, consistency_violation, "N", conclusions, debug)

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
        # 延误时长和档位以代码计算为准，覆盖 LLM 自算的结果（防止模型计算漂移）
        ai_audit["delay_hours"] = delay_hours
        # 根据代码计算的时长重新核算档位
        ai_audit["tier_amount"] = _compute_tier_amount(delay_hours)
        debug["ai_audit"] = ai_audit
        # 合并 AI 审计的补件列表：ai_audit 可能发现材料门禁未识别到的缺项
        ai_audit_result = str(ai_audit.get("audit_result") or "").strip()
        ai_missing = [m for m in (ai_audit.get("missing_materials") or []) if m and str(m).strip()]
        if ai_missing:
            # 去重合并：只追加 ai_audit 额外发现的缺项
            existing_set = set(missing_materials)
            for item in ai_missing:
                if item not in existing_set:
                    missing_materials.append(item)
                    existing_set.add(item)
            missing_materials = sorted(set(missing_materials))
            debug["missing_materials"] = missing_materials
        # 若 ai_audit 判断需补件但材料门禁已通过，仍触发补件流程
        if ai_audit_result == "需补齐资料" and missing_materials:
            conclusions.append({"checkpoint": "AI审计补件", "Eligible": "需补件", "Remark": "；".join(missing_materials)})
            return _result(
                forceid,
                "需补件：" + "；".join(missing_materials),
                "Y",
                conclusions,
                debug,
            )
        elif ai_audit_result == "拒绝":
            reason = str(ai_audit.get("reason") or ai_audit.get("explanation") or "AI审核拒赔")
            conclusions.append({"checkpoint": "AI审计", "Eligible": "否", "Remark": reason})
            return _result(forceid, f"拒赔：{reason}", "N", conclusions, debug)

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
