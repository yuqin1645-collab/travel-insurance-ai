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
            info["Date_of_Accident"] = accident_dt
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
    """条款除外责任校验（委托 rules.flight.exclusions）。"""
    content = f"{str(claim_info.get('Description_of_Accident') or '')} {str(claim_info.get('Assessment_Remark') or '')} {text_blob}"
    extra = str(parsed.get("notes") or "")
    result = _rules_check_exclusions(content, BAGGAGE_DELAY_EXCLUSIONS, extra_text=extra)
    if not result.passed:
        return result.reason
    return None


async def _try_transfer_flight_receipt_time(
    ai_parsed: Dict[str, Any],
    vision_extract: Dict[str, Any],
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """当行李签收时间缺失但转运航班信息存在时，查询转运航班实际到达时间作为签收时间代理。"""
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
