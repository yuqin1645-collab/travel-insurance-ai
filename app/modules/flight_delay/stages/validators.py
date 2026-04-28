"""
flight_delay stages — 校验函数（遗产继承、行为能力、姓名匹配、同天投保、承保区域文本、硬免责检查）。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, Optional

from app.skills.policy_booking import _parse_datetime_str

from .utils import _is_unknown


def _check_inheritance_scenario(claim_info: Dict[str, Any]) -> Dict[str, Any]:
    """遗产继承场景检测（已禁用）。"""
    return {"is_inheritance_suspected": False, "note": "继承检测已禁用"}


def _check_legal_capacity(claim_info: Dict[str, Any]) -> Dict[str, Any]:
    """未成年/限制民事行为能力人检测。"""
    id_type = str(claim_info.get("ID_Type") or claim_info.get("id_type") or "").strip()
    id_number = str(claim_info.get("ID_Number") or claim_info.get("id_number") or "").strip()

    if not id_number:
        return {
            "needs_guardian": None, "age": None, "id_type": id_type or "unknown",
            "id_number": "unknown", "note": "证件号缺失，无法判断是否为未成年人",
        }

    is_cn_id = (
        re.fullmatch(r"\d{17}[\dXx]", id_number)
        and (not id_type or any(kw in id_type for kw in ["身份证", "ID", "居民"]))
    )

    if is_cn_id:
        try:
            birth_str = id_number[6:14]
            birth_date = datetime.strptime(birth_str, "%Y%m%d").date()
            today = date.today()
            age = today.year - birth_date.year - (
                (today.month, today.day) < (birth_date.month, birth_date.day)
            )
            if age < 18:
                return {
                    "needs_guardian": True, "age": age, "id_type": id_type or "身份证",
                    "id_number": id_number,
                    "note": f"被保险人年龄{age}岁，为未成年人，需补充监护人身份证明及监护关系证明",
                }
            elif age < 0 or age > 120:
                return {
                    "needs_guardian": None, "age": None, "id_type": id_type or "身份证",
                    "id_number": id_number,
                    "note": f"从证件号解析的年龄({age}岁)异常，建议人工核查",
                }
            else:
                return {
                    "needs_guardian": False, "age": age, "id_type": id_type or "身份证",
                    "id_number": id_number,
                    "note": f"被保险人年龄{age}岁，具备完全民事行为能力",
                }
        except Exception:
            pass

    return {
        "needs_guardian": None, "age": None, "id_type": id_type or "unknown",
        "id_number": id_number,
        "note": f"证件类型({id_type or '未知'})无法从证件号提取年龄，如申请人为未成年人请人工核查",
    }


def _check_name_match(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    vision_extract: Dict[str, Any],
) -> Dict[str, Any]:
    """校验登机牌/延误证明上的乘客姓名与保单被保险人姓名是否一致。"""
    material_name = ""
    v_passenger = vision_extract.get("passenger_name") or ""
    if not _is_unknown(str(v_passenger).strip()):
        material_name = str(v_passenger).strip()
    if not material_name:
        p_passenger = (parsed or {}).get("passenger") or {}
        p_name = str(p_passenger.get("name") or p_passenger.get("passenger_name") or "").strip()
        if not _is_unknown(p_name):
            material_name = p_name

    policy_name = ""
    for field in ("Insured_And_Policy", "Insured_Name", "insured_name", "BeneficiaryName"):
        v = str(claim_info.get(field) or "").strip()
        if v and not _is_unknown(v):
            policy_name = v
            break

    if not material_name or not policy_name:
        return {
            "match_result": "unknown",
            "material_name": material_name or "unknown",
            "policy_name": policy_name or "unknown",
            "note": "姓名信息不足（材料侧或保单侧姓名未知），无法比对，建议人工复核",
        }

    def _normalize(name: str) -> str:
        return re.sub(r"[\s\-·•/]", "", name).upper()

    m_norm = _normalize(material_name)
    p_norm = _normalize(policy_name)

    if m_norm == p_norm:
        return {
            "match_result": "match", "material_name": material_name, "policy_name": policy_name,
            "note": f"姓名一致：材料={material_name}，保单={policy_name}",
        }

    def _name_tokens(name: str) -> set:
        tokens = set(re.split(r"[\s\-·•/,]+", name.upper()))
        return {t for t in tokens if len(t) > 1}

    m_tokens = _name_tokens(material_name)
    p_tokens = _name_tokens(policy_name)

    if m_tokens and p_tokens:
        if m_tokens <= p_tokens or p_tokens <= m_tokens or m_tokens == p_tokens:
            return {
                "match_result": "match", "material_name": material_name, "policy_name": policy_name,
                "note": f"姓名宽松匹配一致：材料={material_name}，保单={policy_name}",
            }
        if len(m_tokens & p_tokens) >= 1 and (len(m_tokens) <= 2 or len(p_tokens) <= 2):
            return {
                "match_result": "match", "material_name": material_name, "policy_name": policy_name,
                "note": f"姓名部分匹配（含姓或名）：材料={material_name}，保单={policy_name}，建议人工确认",
            }

    return {
        "match_result": "mismatch", "material_name": material_name, "policy_name": policy_name,
        "note": f"姓名不符：材料上乘客姓名={material_name}，保单被保险人姓名={policy_name}，请核实是否为同一人",
    }


def _check_same_day_policy(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
) -> Dict[str, Any]:
    """同天投保时刻校验。"""
    effective_raw = str(
        claim_info.get("Effective_Date")
        or claim_info.get("effective_date")
        or claim_info.get("Insurance_Period_From")
        or claim_info.get("Policy_Start_Date")
        or ""
    ).strip()

    sched_local = (parsed or {}).get("schedule_local") or {}
    planned_dep_raw = str(sched_local.get("planned_dep") or "").strip()

    ef_dt = _parse_datetime_str(effective_raw)
    dep_dt = _parse_datetime_str(planned_dep_raw)

    if ef_dt is None or dep_dt is None:
        return {
            "is_denied": None,
            "effective_datetime": effective_raw or "unknown",
            "planned_dep": planned_dep_raw or "unknown",
            "same_day": None,
            "note": "投保时刻或计划起飞时刻缺失/无法解析，无法判定同天投保，建议人工复核",
        }

    same_day = (ef_dt.date() == dep_dt.date())

    if not same_day:
        return {
            "is_denied": False,
            "effective_datetime": ef_dt.isoformat(),
            "planned_dep": dep_dt.isoformat(),
            "same_day": False,
            "note": f"投保日({ef_dt.date()})与计划起飞日({dep_dt.date()})不同天，无需同天投保校验",
        }

    ef_has_time = len(effective_raw.replace("-", "").replace("/", "").replace(" ", "").replace("T", "")) > 8
    if not ef_has_time:
        return {
            "is_denied": None,
            "effective_datetime": ef_dt.isoformat(),
            "planned_dep": dep_dt.isoformat(),
            "same_day": True,
            "note": f"投保日与计划起飞日同为{ef_dt.date()}，但投保时刻精度不足（仅含日期），无法判定时刻先后，建议人工复核",
        }

    is_denied = ef_dt >= dep_dt

    if is_denied:
        note = (
            f"出境当天投保：投保时刻({ef_dt.strftime('%Y-%m-%d %H:%M:%S')}) "
            f">= 计划起飞时刻({dep_dt.strftime('%Y-%m-%d %H:%M:%S')})，"
            "出境当天投保的航班延误不属于保障责任"
        )
    else:
        note = (
            f"同天投保但投保在先：投保时刻({ef_dt.strftime('%Y-%m-%d %H:%M:%S')}) "
            f"< 计划起飞时刻({dep_dt.strftime('%Y-%m-%d %H:%M:%S')})，属于保障责任"
        )

    return {
        "is_denied": is_denied,
        "effective_datetime": ef_dt.isoformat(),
        "planned_dep": dep_dt.isoformat(),
        "same_day": True,
        "note": note,
    }


def _check_coverage_area_text(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    dep_iata: str,
    arr_iata: str,
    dep_info: Dict[str, Any],
    arr_info: Dict[str, Any],
) -> Dict[str, Any]:
    """出境地区与保险计划文本兜底匹配。"""
    text_sources = [
        str(claim_info.get("Product_Name") or ""),
        str(claim_info.get("BenefitName") or ""),
        str(claim_info.get("Coverage_Area") or ""),
        str(claim_info.get("coverage_area") or ""),
        str(claim_info.get("Plan_Name") or ""),
        str(claim_info.get("plan_name") or ""),
        str(claim_info.get("Insurance_Company") or ""),
    ]
    combined = " ".join(text_sources).lower()

    dep_cc = str(dep_info.get("country_code") or "").upper()
    arr_cc = str(arr_info.get("country_code") or "").upper()
    dep_found = dep_info.get("found", False)
    arr_found = arr_info.get("found", False)

    _ASIA_CC = {
        "CN", "JP", "KR", "TH", "SG", "MY", "ID", "PH", "VN", "IN",
        "HK", "MO", "TW", "MM", "KH", "LA", "BD", "NP", "LK", "PK",
        "MN", "KZ", "UZ", "AZ", "GE", "AM", "TJ", "TM", "KG",
        "AE", "SA", "QA", "KW", "BH", "OM", "JO", "IL", "TR", "IR", "IQ",
    }
    _EUROPE_CC = {
        "GB", "FR", "DE", "IT", "ES", "NL", "BE", "CH", "AT", "SE",
        "NO", "DK", "FI", "PT", "GR", "PL", "CZ", "HU", "RO", "BG",
        "HR", "SK", "SI", "EE", "LV", "LT", "IE", "LU", "MT", "CY",
        "IS", "AL", "BA", "ME", "MK", "RS", "UA", "BY", "MD", "RU", "AZ",
    }
    _AMERICA_CC = {
        "US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "VE", "EC",
        "BO", "PY", "UY", "GY", "SR", "CU", "DO", "JM", "HT", "TT",
        "PA", "CR", "GT", "HN", "SV", "NI", "BZ",
    }
    _AFRICA_CC = {
        "ZA", "EG", "NG", "KE", "ET", "GH", "TZ", "UG", "MZ", "ZM",
        "ZW", "AO", "CM", "CI", "SN", "MG", "TN", "MA", "DZ", "LY",
    }
    _OCEANIA_CC = {"AU", "NZ", "FJ", "PG", "SB", "VU", "WS", "TO", "KI", "FM"}

    def _country_in_region(cc: str, region_set: set) -> bool:
        return cc in region_set

    if any(kw in combined for kw in ["全球", "global", "worldwide", "全世界"]):
        return {"in_coverage": True, "region_hint": "全球", "dep_country": dep_cc, "arr_country": arr_cc, "note": "保险计划为全球覆盖，出境地区在承保范围内"}

    if any(kw in combined for kw in ["亚洲", "asia", "亚太", "asia pacific", "apac"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _ASIA_CC) or _country_in_region(arr_cc, _ASIA_CC)
            return {
                "in_coverage": in_cov, "region_hint": "亚洲/亚太", "dep_country": dep_cc, "arr_country": arr_cc,
                "note": f"保险计划承保亚洲/亚太，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})" + ("在承保区域内" if in_cov else "均不在亚洲/亚太承保区域内"),
            }

    if any(kw in combined for kw in ["欧洲", "europe", "欧盟", "schengen", "申根"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _EUROPE_CC) or _country_in_region(arr_cc, _EUROPE_CC)
            return {
                "in_coverage": in_cov, "region_hint": "欧洲", "dep_country": dep_cc, "arr_country": arr_cc,
                "note": f"保险计划承保欧洲，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})" + ("在承保区域内" if in_cov else "均不在欧洲承保区域内"),
            }

    if any(kw in combined for kw in ["美洲", "america", "北美", "north america", "南美", "south america"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _AMERICA_CC) or _country_in_region(arr_cc, _AMERICA_CC)
            return {
                "in_coverage": in_cov, "region_hint": "美洲", "dep_country": dep_cc, "arr_country": arr_cc,
                "note": f"保险计划承保美洲，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})" + ("在承保区域内" if in_cov else "均不在美洲承保区域内"),
            }

    if any(kw in combined for kw in ["非洲", "africa"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _AFRICA_CC) or _country_in_region(arr_cc, _AFRICA_CC)
            return {
                "in_coverage": in_cov, "region_hint": "非洲", "dep_country": dep_cc, "arr_country": arr_cc,
                "note": f"保险计划承保非洲，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})" + ("在承保区域内" if in_cov else "均不在非洲承保区域内"),
            }

    if any(kw in combined for kw in ["大洋洲", "oceania", "澳洲", "australia", "新西兰"]):
        if dep_found and arr_found:
            in_cov = _country_in_region(dep_cc, _OCEANIA_CC) or _country_in_region(arr_cc, _OCEANIA_CC)
            return {
                "in_coverage": in_cov, "region_hint": "大洋洲", "dep_country": dep_cc, "arr_country": arr_cc,
                "note": f"保险计划承保大洋洲，出发地({dep_iata}/{dep_cc})或目的地({arr_iata}/{arr_cc})" + ("在承保区域内" if in_cov else "均不在大洋洲承保区域内"),
            }

    return {
        "in_coverage": None, "region_hint": "unknown", "dep_country": dep_cc, "arr_country": arr_cc,
        "note": "无法从保险计划名称/描述文本判断承保区域，建议人工确认出境地区是否符合保险计划",
    }


def _check_hardcheck_exclusion(hardcheck: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """检查 hardcheck 结果中是否命中硬免责条款。"""
    if not hardcheck:
        return None

    def _make_denial(reason: str, flag: str) -> Dict[str, Any]:
        return {
            "audit_result": "拒绝", "confidence_score": 1.0, "key_data": {},
            "logic_check": {"exclusion_triggered": True, flag: True},
            "payout_suggestion": {"currency": "CNY", "amount": 0, "basis": "免责条款命中"},
            "explanation": reason,
        }

    policy_cov = hardcheck.get("policy_coverage_check") or {}
    if policy_cov.get("in_coverage") is False:
        note = str(policy_cov.get("note") or "原出发航班计划起飞时间不在保单有效期内")
        return _make_denial(f"【超出有效期】{note}", "policy_coverage_out_triggered")

    domestic_check = hardcheck.get("domestic_flight_check") or {}
    if domestic_check.get("is_pure_domestic_cn") is True:
        dep = domestic_check.get("dep_iata", "")
        arr = domestic_check.get("arr_iata", "")
        return _make_denial(f"【纯国内航班不赔】出发地 {dep} 和目的地 {arr} 均在中国大陆，本保险仅承保含国际/境外段的航班", "pure_domestic_cn_triggered")

    war_risk = hardcheck.get("war_risk") or {}
    if war_risk.get("is_war_risk"):
        note = war_risk.get("note", "命中战争/冲突风险维护表")
        return _make_denial(f"【战争因素免责】{note}", "war_risk_triggered")

    coverage = hardcheck.get("coverage_area") or {}
    if coverage.get("in_coverage") is False:
        note = str(coverage.get("note") or "超出承保区域，不予赔付")
        return _make_denial(f"【承保区域不符】{note}", "coverage_out_of_area_triggered")

    coverage_text = hardcheck.get("coverage_area_text_check") or {}
    if coverage_text.get("in_coverage") is False:
        note = str(coverage_text.get("note") or "出境地区不在保险计划承保范围内")
        return _make_denial(f"【承保区域不符】{note}", "coverage_text_out_of_area_triggered")

    transit = hardcheck.get("transit_check") or {}
    if transit.get("is_domestic_cn") is True:
        iata = transit.get("iata", "")
        return _make_denial(f"【境内中转免责】中转地 {iata} 在境内，不予赔付", "transit_domestic_triggered")

    missed_conn = hardcheck.get("missed_connection_check") or {}
    if missed_conn.get("is_missed_connection") is True:
        return _make_denial("【中转接驳免责】前序航班延误导致无法搭乘后续接驳航班，不予赔付", "missed_connection_triggered")

    passenger_check = hardcheck.get("passenger_civil_check") or {}
    if passenger_check.get("is_passenger_civil") is False:
        fn = passenger_check.get("flight_no", "")
        return _make_denial(f"【非客运航班】航班 {fn} 非民航客运班机，不在赔付范围内", "non_passenger_civil_triggered")

    same_day_check = hardcheck.get("same_day_policy_check") or {}
    if same_day_check.get("is_denied") is True:
        note = str(same_day_check.get("note") or "出境当天投保，投保时刻不早于计划起飞时刻，不属于保障责任")
        return _make_denial(f"【同天投保免责】{note}", "same_day_policy_triggered")

    name_check = hardcheck.get("name_match_check") or {}
    if name_check.get("match_result") == "mismatch":
        note = str(name_check.get("note") or "登机牌/延误证明上的乘客姓名与保单被保险人姓名不符")
        return _make_denial(f"【姓名不符】{note}", "name_mismatch_triggered")

    return None
