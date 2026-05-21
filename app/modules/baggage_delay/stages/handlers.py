"""
baggage_delay stages — handler/check 函数（保单有效性、材料门禁、特殊材料、一致性、航空记录例外、除外责任、转运航班）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from app.rules.common.policy_validity import check as _rules_check_policy_validity
from app.rules.common.material_gate import check as _rules_material_gate, BAGGAGE_DELAY_KEYWORDS
from app.rules.flight.exclusions import check as _rules_check_exclusions, BAGGAGE_DELAY_EXCLUSIONS
from app.skills.flight_lookup import get_flight_lookup_skill
from app.vision_preprocessor import prepare_attachments_for_claim

from .utils import (
    _extract_date_yyyy_mm_dd,
    _parse_dt_flexible,
)


def _check_policy_validity(
    claim_info: Dict[str, Any],
    debug: Dict[str, Any],
    vision_extract: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """保单有效期综合校验（委托 rules.common.policy_validity）。"""
    info = dict(claim_info)
    if vision_extract:
        exit_dt = vision_extract.get("exit_datetime")
        if exit_dt and str(exit_dt).strip().lower() not in ("", "unknown"):
            info.setdefault("First_Exit_Date", exit_dt)
        accident_dt = vision_extract.get("accident_date_in_materials")
        if accident_dt and str(accident_dt).strip().lower() not in ("", "unknown"):
            info.setdefault("Date_of_Accident", accident_dt)
        flight_date_v = vision_extract.get("flight_date")
        if flight_date_v and str(flight_date_v).strip().lower() not in ("", "unknown"):
            info.setdefault("Flight_Date", str(flight_date_v).strip())
        elif not info.get("Flight_Date") and not info.get("Policy_FlightDate"):
            arr_time = vision_extract.get("flight_actual_arrival_time")
            if arr_time and str(arr_time).strip().lower() not in ("", "unknown"):
                info.setdefault("Flight_Date", str(arr_time).strip()[:10])
    result = _rules_check_policy_validity(info)
    debug["policy_validity"] = result.detail
    debug["policy_validity_action"] = result.action
    if not result.passed:
        return result.reason
    return None


def _material_gate(text_blob: str, file_names: List[str]) -> List[str]:
    """材料门禁校验（委托 rules.common.material_gate）。"""
    result = _rules_material_gate(text_blob, file_names, BAGGAGE_DELAY_KEYWORDS)
    return result.detail.get("missing", [])


def _check_special_materials(
    claim_info: Dict[str, Any],
    text_blob: str,
    file_names: List[str],
) -> List[str]:
    """特殊场景材料校验：未成年人、委托代办。"""
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


def _check_info_consistency(
    claim_info: Dict[str, Any],
    ai_parsed: Dict[str, Any],
) -> Optional[str]:
    """信息一致性校验：被保险人姓名与材料中识别的姓名对比。"""
    insured_name = str(
        claim_info.get("Insured_And_Policy") or claim_info.get("Insured_Name") or ""
    ).strip()
    material_insured = str(
        ai_parsed.get("insured_name_in_materials") or ai_parsed.get("passenger_name") or ""
    ).strip()
    if insured_name and material_insured and material_insured.lower() not in ("unknown", ""):
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
    """航空公司官方行李记录替代托运行李牌的例外检查。"""
    has_airline_record = False
    baggage_record_info = {}

    if ai_parsed.get("has_airline_baggage_record") == "true":
        has_airline_record = True
        baggage_record_info = {
            "name": ai_parsed.get("airline_baggage_record_name", ""),
            "flight": ai_parsed.get("airline_baggage_record_flight", ""),
            "pieces": ai_parsed.get("airline_baggage_record_pieces", ""),
        }

    if not has_airline_record and vision_extract.get("has_airline_baggage_record") == "true":
        has_airline_record = True
        baggage_record_info = {
            "name": vision_extract.get("airline_baggage_record_name", ""),
            "flight": vision_extract.get("airline_baggage_record_flight", ""),
            "pieces": vision_extract.get("airline_baggage_record_pieces", ""),
        }

    if not has_airline_record:
        return False

    fellow_travelers = claim_info.get("Fellow_Travelers") or claim_info.get("Co_Applicants") or ""
    if str(fellow_travelers).strip().lower() not in ("", "none", "null", "无"):
        return False

    pieces = str(baggage_record_info.get("pieces") or "").strip().lower()
    if pieces not in ("1", "one", "壹", "1件"):
        return False

    record_name = str(baggage_record_info.get("name") or "").strip()
    insured_name = str(claim_info.get("Insured_And_Policy") or claim_info.get("Insured_Name") or "").strip()

    if record_name and insured_name:
        if record_name.upper().replace(" ", "") != insured_name.upper().replace(" ", ""):
            return False

    return True


def _check_exclusions(
    claim_info: Dict[str, Any],
    text_blob: str,
    parsed: Dict[str, Any],
) -> Optional[str]:
    """条款除外责任校验（委托 rules.flight.exclusions）。

    检查 Assessment_Remark（人工审核意见）+ Description_of_Accident（事故描述）+
    parsed 中的额外标注。被保险人的事故描述中自然会包含「行李未随」「未到达」等词语，
    这些是事故描述而非除外责任判定，不应触发拒赔。
    但「海关没收」「扣留」「恐怖活动」「战争」等属于客观事实描述，无论在哪个字段中
    出现都应触发除外责任。
    """
    assessment = str(claim_info.get('Assessment_Remark') or '')
    description = str(claim_info.get('Description_of_Accident') or '')

    # 先检查 Assessment_Remark（最权威，人工审核意见）
    result = _rules_check_exclusions(assessment, BAGGAGE_DELAY_EXCLUSIONS, extra_text=None)
    if not result.passed:
        return result.reason

    # 再检查 Description_of_Accident 中的"硬除外"关键词（海关没收/恐怖活动/战争）
    # 这些是客观事实，不论在哪个字段提及都应拒赔
    _HARD_EXCLUSIONS = [
        ("海关", "海关扣留/没收导致，属除外责任"),
        ("没收", "海关/政府部门没收导致，属除外责任"),
        ("扣留", "海关/政府部门扣留导致，属除外责任"),
        ("恐怖", "恐怖活动，属除外责任"),
        ("战争", "战争/军事冲突，属除外责任"),
        ("暴乱", "暴乱/武装叛乱，属除外责任"),
        ("罢工", "罢工导致，属除外责任"),
    ]
    desc_lower = description.lower()
    for keyword, reason in _HARD_EXCLUSIONS:
        if keyword in desc_lower:
            # 但排除"行李未随"等非实质性描述
            if keyword in ("海关", "扣留", "没收"):
                # 需要确认是海关/政府行为，而非一般安检
                context = description[max(0, description.index(keyword)-10):description.index(keyword)+20]
                if any(w in context for w in ["海关", "政府", "公安", "边防", "安检扣留", "没收"]):
                    return f"拒赔：{reason}"
            else:
                return f"拒赔：{reason}"

    # 【新增 2026-05-15】旅客通过非航空交通方式（铁路/地铁/自驾等）提前到达目的地并提取行李，
    # 不在行李延误险保障范围内。行李延误险保障的是航空托运导致的行李延误损失，
    # 若旅客自行选择其他交通方式离开机场并提取行李，属于旅客自主行为，非航司责任。
    # 场景：OUo3DIAT — 旅客通过铁路提前到达目的地并提取行李
    _EARLY_PICKUP_INDICATORS = [
        ("铁路", "railway"),
        ("火车", "train"),
        ("高铁", "high-speed rail"),
        ("地铁", "metro"),
        ("自驾", "self-drive"),
        ("自行提取", "collected by own means"),
        ("自行前往", "traveled independently"),
        ("非航空", "non-air transport"),
        ("提前到达", "arrived early"),
        ("提前离开", "left early"),
    ]
    for indicator_kw, _ in _EARLY_PICKUP_INDICATORS:
        if indicator_kw in desc_lower:
            # 需要上下文确认是"旅客自行通过XX方式离开"而非"行李通过XX转运"
            idx = desc_lower.index(indicator_kw)
            context = description[max(0, idx - 30):idx + 50]
            # 如果上下文中同时出现"旅客"/"客人"/"insured"/"passenger"+"提取"/"到达"/"离开"
            # 或出现"行李"+"未随"+"铁路/火车"的组合
            if any(w in context for w in ["旅客", "客人", "insured", "passenger", "被保险人"]) and \
               any(w in context for w in ["提取", "到达", "离开", "前往", "collected", "arrived", "left"]):
                return f"拒赔：旅客通过非航空交通方式提前到达目的地并提取行李，不在行李延误险保障范围内"
            # "行李未随"+"铁路/火车"也视为除外
            if any(w in context for w in ["行李未随", "行李未到达", "行李未到", "baggage not arrived"]) and \
               indicator_kw in ("铁路", "火车", "高铁"):
                return f"拒赔：旅客通过非航空交通方式提前到达目的地并提取行李，不在行李延误险保障范围内"

    return None


def _check_domestic_flight(
    vision_extract: Dict[str, Any],
    ai_parsed: Dict[str, Any],
    claim_info: Dict[str, Any] | None = None,
) -> Optional[str]:
    """纯国内航班检测：出发国和到达国均为中国时，不符合境外险承保范围。

    优先级策略：
    0. 检查 itinerary_segments 中是否有任何国际段（新增 2026-05-20）
    1. IATA 机场代码 + resolve_country() 确定性查询（最可靠）
    2. Vision/AI 提取的国家字符串（回退方案）
    3. 出入境记录兜底：存在出境记录直接豁免
    """
    from app.skills.airport import resolve_country

    # 出入境记录兜底：有出境记录说明含国际段，直接豁免
    exit_dt = vision_extract.get("exit_datetime") or (claim_info or {}).get("First_Exit_Date")
    if exit_dt and str(exit_dt).strip().lower() not in ("", "unknown"):
        return None

    # 港澳台机场代码和城市关键词
    hk_macau_tw_iata = {"HKG", "MFM", "TPE", "KHH", "RMQ", "TSA"}
    hk_macau_tw_city = {"香港", "澳门", "台湾", "台北", "高雄", "台中", "Hong Kong", "Macau", "Taiwan", "Taipei"}

    # 策略0: 检查 itinerary_segments 中是否有任何国际段（新增 2026-05-20）
    itinerary_segments = vision_extract.get("itinerary_segments") or []
    for seg in itinerary_segments:
        seg_dep = str(seg.get("original_dep_iata") or "").strip().upper()
        seg_arr = str(seg.get("original_arr_iata") or "").strip().upper()
        if seg_dep and seg_arr:
            # 检查是否涉及港澳台
            if seg_dep in hk_macau_tw_iata or seg_arr in hk_macau_tw_iata:
                return None
            seg_dep_info = resolve_country(seg_dep)
            seg_arr_info = resolve_country(seg_arr)
            if seg_dep_info.get("found") and seg_arr_info.get("found"):
                seg_dep_cc = seg_dep_info.get("country_code", "").upper()
                seg_arr_cc = seg_arr_info.get("country_code", "").upper()
                # 只要有一段不是纯国内（出发地或目的地有一个不是CN），就算国际段
                if seg_dep_cc != "CN" or seg_arr_cc != "CN":
                    return None

    # 策略1: IATA 机场代码确定性查询
    dep_iata = str(vision_extract.get("dep_iata") or ai_parsed.get("dep_iata") or "").strip().upper()
    arr_iata = str(vision_extract.get("arr_iata") or ai_parsed.get("arr_iata") or "").strip().upper()

    if dep_iata and arr_iata and len(dep_iata) == 3 and len(arr_iata) == 3:
        # 检查是否涉及港澳台
        if dep_iata in hk_macau_tw_iata or arr_iata in hk_macau_tw_iata:
            return None

        dep_info = resolve_country(dep_iata)
        arr_info = resolve_country(arr_iata)

        if dep_info.get("found") and arr_info.get("found"):
            dep_cc = dep_info.get("country_code", "").upper()
            arr_cc = arr_info.get("country_code", "").upper()
            if dep_cc == "CN" and arr_cc == "CN":
                dep_city = str(vision_extract.get("dep_city") or ai_parsed.get("dep_city") or dep_iata).strip()
                arr_city = str(vision_extract.get("arr_city") or ai_parsed.get("arr_city") or arr_iata).strip()
                return f"拒赔：航段为国内航班（{dep_city}→{arr_city}），不符合境外旅行险承保范围"
            # 非纯国内，放行
            return None

    # 策略2: 回退到 Vision/AI 提取的国家字符串
    dep_country = str(vision_extract.get("dep_country") or ai_parsed.get("dep_country") or "").strip()
    arr_country = str(vision_extract.get("arr_country") or ai_parsed.get("arr_country") or "").strip()

    if dep_country == "中国" and arr_country == "中国":
        dep_city = str(vision_extract.get("dep_city") or ai_parsed.get("dep_city") or "").strip()
        arr_city = str(vision_extract.get("arr_city") or ai_parsed.get("arr_city") or "").strip()
        # 再次检查城市名是否包含港澳台关键词
        if any(kw in dep_city for kw in hk_macau_tw_city) or any(kw in arr_city for kw in hk_macau_tw_city):
            return None

        return f"拒赔：航段为国内航班（{dep_city}→{arr_city}），不符合境外旅行险承保范围"

    return None


def _check_actual_arrival_vs_policy(
    claim_info: Dict[str, Any],
    ai_parsed: Dict[str, Any],
    debug: Dict[str, Any],
) -> Optional[str]:
    """检查实际到达时间是否超出保单有效期。

    使用 policy_validity 模块计算后的顺延日期（applied_eff/applied_exp），
    而非原始保单日期，确保与前置 _check_policy_validity 判定一致。
    """
    actual_arr_str = str(ai_parsed.get("flight_actual_arrival_time") or "").strip()
    if not actual_arr_str or actual_arr_str.lower() in ("unknown", ""):
        return None
    actual_arr = _parse_dt_flexible(actual_arr_str)
    if not actual_arr:
        return None

    # 优先使用 debug 中已存储的 policy_validity 顺延后日期
    validity_detail = debug.get("policy_validity") or {}
    applied_exp_str = validity_detail.get("applied_expiry")
    applied_eff_str = validity_detail.get("applied_effective")

    # 回退：直接从 claim_info 解析原始日期
    if not applied_exp_str:
        applied_exp_str = str(claim_info.get("Expiry_Date") or claim_info.get("Insurance_Period_To") or "").strip()
    if not applied_eff_str:
        applied_eff_str = str(claim_info.get("Effective_Date") or claim_info.get("Insurance_Period_From") or "").strip()

    exp_dt = _parse_dt_flexible(applied_exp_str) if applied_exp_str else None
    eff_dt = _parse_dt_flexible(applied_eff_str) if applied_eff_str else None

    if exp_dt and actual_arr > exp_dt:
        # 行李延误场景特殊处理：若事故日期已在保单 coverage 内，实际到达时间超出保单有效期
        # 不应直接拒赔（行李延误本身的触发点是事故日期，而非到达时间）
        coverage_by = validity_detail.get("coverage_hit_by")
        if coverage_by and actual_arr.date() > exp_dt.date():
            # 事故日期在保单内，但到达时间跨日超出有效期 — 放行到后续时长核算
            debug["actual_arrival_vs_policy"] = {
                "actual_arrival": str(actual_arr),
                "policy_expiry": str(exp_dt),
                "applied_expiry": validity_detail.get("used_extension", False),
                "exceeded": True,
                "overridden_for_baggage_delay": "事故日期在保单coverage内，到达时间跨日超出不拒赔，放行到时长核算",
            }
            return None
        debug["actual_arrival_vs_policy"] = {
            "actual_arrival": str(actual_arr),
            "policy_expiry": str(exp_dt),
            "applied_expiry": validity_detail.get("used_extension", False),
            "exceeded": True,
        }
        return f"拒赔：航班实际到达时间{actual_arr.strftime('%Y-%m-%d %H:%M')}超出保单有效期{exp_dt.strftime('%Y-%m-%d %H:%M')}"
    return None


async def _try_transfer_flight_receipt_time(
    ai_parsed: Dict[str, Any],
    vision_extract: Dict[str, Any],
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """当行李签收时间缺失但转运航班信息存在时，查询转运航班实际到达时间作为签收时间代理。

    注意：本函数会直接修改入参 ai_parsed 字典（设置 baggage_receipt_time 和 receipt_times 字段），
    调用方依赖此副作用来更新后续延误计算的输入数据。
    """
    debug: Dict[str, Any] = {
        "attempted": False,
        "flights_queried": [],
        "receipt_time_set": None,
        "source": None,
    }

    if not isinstance(ai_parsed, dict):
        debug["reason"] = "ai_parsed not dict"
        return debug

    existing_receipt = _parse_dt_flexible(ai_parsed.get("baggage_receipt_time"))
    if existing_receipt:
        debug["reason"] = "receipt_time already known"
        return debug

    main_arrival = _parse_dt_flexible(ai_parsed.get("flight_actual_arrival_time"))
    if not main_arrival:
        debug["reason"] = "main flight arrival unknown"
        return debug

    main_fn_raw = str(ai_parsed.get("flight_no") or "").strip()
    main_flight_nos: set = set()
    if main_fn_raw and main_fn_raw.lower() not in ("unknown", "未知", "δ֪", ""):
        for part in re.split(r"[,，;；]", main_fn_raw):
            fn = part.strip().upper()
            if fn and fn not in ("UNKNOWN", ""):
                main_flight_nos.add(fn)

    candidates: List[Dict[str, Any]] = []
    seen: set = set()

    all_flights = (
        vision_extract.get("all_flights_found")
        or ai_parsed.get("all_flights_found")
        or []
    )

    # 收集乘客本人行程中的所有航班号（联程航段），转运航班不得用乘客本人的航班作为签收时间代理
    _passenger_flight_nos: set = set()
    for _seg in (ai_parsed.get("itinerary_segments") or vision_extract.get("itinerary_segments") or []):
        if isinstance(_seg, dict):
            _ofn = str(_seg.get("original_flight_no") or "").strip().upper()
            if _ofn and _ofn not in ("UNKNOWN", ""):
                _passenger_flight_nos.add(_ofn)

    # 收集行李转运航班号（防止将其误当作行李转运签收时间代理）
    _BAGGAGE_FORWARDING_HINTS = [
        "行李装载", "行李装在", "行李装在今日", "行李转运", "行李已装载",
        "行李将搭乘", "行李运抵", "行李托运回", "行李送达", "行李将运",
        "您的行李将", "行李会搭乘", "行李后续",
        "baggage loaded", "baggage forwarded", "baggage will arrive",
        "baggage will travel", "luggage will arrive",
    ]
    _baggage_forwarding_nos: set = set()
    for _src in (ai_parsed, vision_extract):
        for _fl in (_src.get("all_flights_found") or []):
            if not isinstance(_fl, dict):
                continue
            _fn = str(_fl.get("flight_no") or "").strip()
            if not _fn:
                continue
            _role = str(_fl.get("role_hint") or "").strip()
            _src_f = str(_fl.get("source") or "").strip()
            if any(h in _role or h in _src_f for h in _BAGGAGE_FORWARDING_HINTS):
                _baggage_forwarding_nos.add(_fn.upper())

    for f in all_flights:
        if not isinstance(f, dict):
            continue
        fno = str(f.get("flight_no") or "").strip()
        if not fno or fno.lower() in ("unknown", "未知", "δ֪", ""):
            continue
        role_hint = str(f.get("role_hint") or "").strip()
        if "原航班" in role_hint:
            continue
        if fno.upper() in main_flight_nos:
            continue
        # 排除乘客本人的联程航班，防止将其误当作行李转运航班
        if fno.upper() in _passenger_flight_nos:
            continue
        # 排除行李转运航班，防止用转运航班到达时间覆盖真实签收时间
        if fno.upper() in _baggage_forwarding_nos:
            continue
        fdate = str(f.get("date") or "").strip()
        if not fdate or fdate.lower() in ("unknown", "未知", ""):
            continue
        date_norm = _extract_date_yyyy_mm_dd(fdate)
        if not date_norm:
            continue
        fsource = str(f.get("source") or "").strip()
        key = (fno.upper(), date_norm)
        if key not in seen:
            seen.add(key)
            candidates.append({"flight_no": fno.upper(), "date": date_norm, "source": fsource})

    alternate = ai_parsed.get("alternate") or {}
    if isinstance(alternate, dict):
        alt_fno = str(alternate.get("alt_flight_no") or "").strip().upper()
        if alt_fno and alt_fno not in ("UNKNOWN", "") and alt_fno not in main_flight_nos:
            # 若 alternate 航班是行李转运航班，跳过
            if alt_fno in _baggage_forwarding_nos:
                debug["alternate_skipped_baggage_forwarding"] = f"alternate航班{alt_fno}为行李转运航班，排除"
            else:
                alt_date_raw = str(alternate.get("alt_dep") or alternate.get("alt_arr") or "")
                if alt_date_raw and alt_date_raw.lower() not in ("unknown", "未知", ""):
                    alt_date = _extract_date_yyyy_mm_dd(alt_date_raw)
                else:
                    alt_date = _extract_date_yyyy_mm_dd(
                        ai_parsed.get("flight_date")
                    ) or _extract_date_yyyy_mm_dd(
                        ai_parsed.get("accident_date_in_materials")
                    )
                if alt_date:
                    key = (alt_fno, alt_date)
                    if key not in seen:
                        seen.add(key)
                        candidates.append({"flight_no": alt_fno, "date": alt_date, "source": "alternate_field"})

    if not candidates:
        debug["reason"] = "no transfer flight candidates"
        return debug

    skill = get_flight_lookup_skill()
    latest_arr_dt: Optional[datetime] = None
    debug["attempted"] = True

    for candidate in candidates[:3]:
        try:
            result = await skill.lookup_status(
                flight_no=candidate["flight_no"],
                date=candidate["date"],
                session=session,
            )
            flight_debug = {
                "flight_no": candidate["flight_no"],
                "date": candidate["date"],
                "success": result.get("success"),
                "actual_arr": result.get("actual_arr"),
            }
            debug["flights_queried"].append(flight_debug)

            if result.get("success") and result.get("actual_arr"):
                arr_dt = _parse_dt_flexible(result["actual_arr"])
                if arr_dt and arr_dt > main_arrival:
                    if latest_arr_dt is None or arr_dt > latest_arr_dt:
                        latest_arr_dt = arr_dt
        except Exception as e:
            debug["flights_queried"].append({
                "flight_no": candidate["flight_no"],
                "date": candidate["date"],
                "success": False,
                "error": str(e)[:100],
            })

    if latest_arr_dt:
        arr_str = latest_arr_dt.strftime("%Y-%m-%d %H:%M")
        ai_parsed["baggage_receipt_time"] = arr_str
        existing_list = ai_parsed.get("receipt_times") or []
        if isinstance(existing_list, list):
            existing_list.append(arr_str)
            ai_parsed["receipt_times"] = existing_list
        debug["receipt_time_set"] = arr_str
        debug["source"] = "transfer_flight_arrival"

    return debug
