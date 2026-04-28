"""
flight_delay stages — 硬校验集合（_run_hardcheck）及可预见因素欺诈检测。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.logging_utils import LOGGER, log_extra
from app.skills.airport import resolve_country, check_transit_domestic
from app.skills.war_risk import check_war_table
from app.skills.weather import lookup_alerts_table, check_foreseeability
from app.skills.policy_booking import (
    lookup_effective_window,
    check_delay_in_coverage,
    lookup_coverage_area,
    check_delay_in_coverage_area,
)

from .utils import _truthy, _is_unknown
from .validators import (
    _check_inheritance_scenario,
    _check_legal_capacity,
    _check_name_match,
    _check_same_day_policy,
    _check_coverage_area_text,
)


def _check_foreseeability_fraud(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
) -> Dict[str, Any]:
    """情形6：可预见因素/欺诈检测。"""
    result: Dict[str, Any] = {
        "fraud_suspected": False,
        "fraud_level": "none",
        "reason": "",
        "note": "",
    }

    try:
        invest_date_raw = str(
            claim_info.get("Policy_Start_Date")
            or claim_info.get("policy_start_date")
            or claim_info.get("Insurance_Period_From")
            or claim_info.get("insurance_period_from")
            or claim_info.get("effective_from")
            or claim_info.get("Effective_Date")
            or claim_info.get("effective_date")
            or ""
        ).strip()
        accident_date_raw = str(claim_info.get("Date_of_Accident") or claim_info.get("date_of_accident") or "").strip()
        delay_reason = str((parsed or {}).get("delay_reason") or "").lower()

        def _parse_date_any(s: str) -> Optional[datetime]:
            ss = str(s or "").strip()
            if not ss or ss.lower() in ("unknown", "null", "none"):
                return None
            if re.fullmatch(r"\d{14}", ss):
                return datetime.strptime(ss, "%Y%m%d%H%M%S")
            if re.fullmatch(r"\d{8}", ss):
                return datetime.strptime(ss, "%Y%m%d")
            if "-" in ss or "/" in ss:
                s0 = ss[:10]
                for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
                    try:
                        return datetime.strptime(s0, fmt)
                    except Exception:
                        continue
            try:
                return datetime.fromisoformat(ss)
            except Exception:
                return None

        invest_dt = _parse_date_any(invest_date_raw)
        accident_dt = _parse_date_any(accident_date_raw)
        invest_date = invest_dt.date() if invest_dt else None
        accident_date = accident_dt.date() if accident_dt else None
        action_time_iso = invest_date.isoformat() if invest_date else ""

        fraud_flag = _truthy((parsed or {}).get("foreseeability_fraud"))
        if fraud_flag is True:
            result["fraud_suspected"] = True
            result["fraud_level"] = "suspect"
            result["note"] = "AI解析阶段已标注可预见因素欺诈嫌疑，需人工复核"
            return result

        foreseeable_keywords = [
            "台风", "typhoon", "hurricane", "飓风",
            "罢工", "strike",
            "暴风", "blizzard", "snowstorm",
            "洪水", "flood",
        ]
        reason_is_foreseeable = any(kw in delay_reason for kw in foreseeable_keywords)

        if not reason_is_foreseeable:
            result["note"] = "延误原因非典型可预见因素（天气/罢工等），跳过欺诈检测"
            return result

        try:
            route = (parsed or {}).get("route") or {}

            def _iata(val: Any) -> str:
                s = str(val or "").strip().upper()
                return s if s and s not in ("UNKNOWN", "NULL", "NONE") else ""

            dep_iata = _iata(route.get("dep_iata"))
            arr_iata = _iata(route.get("arr_iata"))
            airport_iata = arr_iata or dep_iata
            if airport_iata and action_time_iso and accident_date:
                alerts = lookup_alerts_table(airport_iata=airport_iata, check_date=accident_date)
            else:
                alerts = []

            if alerts:
                for alert in alerts:
                    published_at = str(alert.get("published_at") or "").strip()
                    if not published_at:
                        continue
                    fore_res = check_foreseeability(
                        published_at=published_at,
                        action_time=action_time_iso,
                        action_type="投保/订票",
                    )
                    if fore_res.get("is_foreseeable") is True:
                        result["fraud_suspected"] = True
                        result["fraud_level"] = "confirmed"
                        result["reason"] = (
                            f"可预见因素时间线命中：预警发布时间({published_at})早于投保/订票时间({invest_date_raw})"
                        )
                        result["note"] = "命中可预见因素时间线 => 拒赔"
                        return result
        except Exception:
            pass

        if invest_date and accident_date:
            days_before = (accident_date - invest_date).days
            if days_before <= 3:
                result["fraud_suspected"] = True
                result["fraud_level"] = "suspect"
                result["reason"] = (
                    f"延误原因为可预见因素（{delay_reason[:30]}），"
                    f"投保日（{invest_date.isoformat()}）距事故日（{accident_date.isoformat()}）仅{days_before}天，"
                    "疑似在已知延误因素后投保，需人工核查"
                )
                result["note"] = "命中情形6：投保时延误因素已可预见，建议人工复核欺诈嫌疑"
            else:
                result["note"] = f"投保日距事故日{days_before}天，未达欺诈判定阈值（≤3天）"
        else:
            result["note"] = f"延误原因含可预见因素（{delay_reason[:30]}），但日期信息不足，无法自动判定，建议人工关注"

    except Exception as e:
        result["note"] = f"欺诈检测异常: {e}"

    return result


def _run_hardcheck(
    parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    policy_excerpt: str,
    free_text: str = "",
    vision_extract: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """代码侧硬校验集合（不依赖AI，确定性判定）。"""
    result: Dict[str, Any] = {
        "dep_airport": {},
        "arr_airport": {},
        "transit_check": {},
        "war_risk": {},
        "policy_window": {},
        "coverage_area": {},
        "evidence_check": {},
        "passenger_civil_check": {},
        "missed_connection_check": {},
        "required_materials_check": {},
        "fraud_foreseeability_check": {},
        "policy_coverage_check": {},
        "debug_notes": [],
    }

    try:
        route = (parsed or {}).get("route") or {}
        itinerary = (parsed or {}).get("itinerary") or {}

        def _iata(val: Any) -> str:
            s = str(val or "").strip().upper()
            return s if s and s not in ("UNKNOWN", "NULL", "NONE") else ""

        dep_iata = _iata(route.get("dep_iata"))
        arr_iata = _iata(route.get("arr_iata"))
        transit_iata = _iata(route.get("transit_iata")) or _iata(itinerary.get("transit_iata"))

        accident_date_raw = str(claim_info.get("Date_of_Accident") or claim_info.get("date_of_accident") or "").strip()

        def _parse_date_str(s: str) -> Optional[Any]:
            if not s:
                return None
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
                try:
                    return datetime.strptime(s[:10], fmt).date()
                except Exception:
                    continue
            return None

        check_date = _parse_date_str(accident_date_raw)

        if dep_iata:
            result["dep_airport"] = resolve_country(dep_iata)
        if arr_iata:
            result["arr_airport"] = resolve_country(arr_iata)

        if transit_iata:
            result["transit_check"] = check_transit_domestic(transit_iata)
        elif dep_iata and arr_iata:
            result["transit_check"] = {"iata": dep_iata, "is_domestic_cn": None, "note": "非联程中转，无需境内中转免责判定"}

        dep_info = result.get("dep_airport") or {}
        arr_info = result.get("arr_airport") or {}
        dep_cc = dep_info.get("country_code", "")
        arr_cc = arr_info.get("country_code", "")
        dep_found = dep_info.get("found", False)
        arr_found = arr_info.get("found", False)

        if dep_iata and arr_iata and dep_found and arr_found:
            is_pure_domestic_cn = (dep_cc == "CN" and arr_cc == "CN")
            result["domestic_flight_check"] = {
                "is_pure_domestic_cn": is_pure_domestic_cn,
                "dep_iata": dep_iata,
                "arr_iata": arr_iata,
                "dep_country": dep_cc,
                "arr_country": arr_cc,
                "note": (
                    f"纯中国大陆国内航班（{dep_iata}→{arr_iata}），不在承保范围内"
                    if is_pure_domestic_cn
                    else f"含国际/境外段（{dep_iata}[{dep_cc}]→{arr_iata}[{arr_cc}]），在承保范围内"
                ),
            }
        else:
            result["domestic_flight_check"] = {
                "is_pure_domestic_cn": None,
                "dep_iata": dep_iata,
                "arr_iata": arr_iata,
                "note": "出发地或目的地机场未知，无法判定是否纯国内航班",
            }

        war_checks = []
        airports_to_check = [result.get("dep_airport"), result.get("arr_airport")]
        if transit_iata:
            transit_info = resolve_country(transit_iata)
            airports_to_check.append(transit_info)
        for airport_info in airports_to_check:
            cc = (airport_info or {}).get("country_code", "")
            if cc and cc != "unknown":
                war_result = check_war_table(cc, check_date=check_date)
                if war_result.get("is_war_risk"):
                    war_checks.append(war_result)
        if war_checks:
            result["war_risk"] = {
                **war_checks[0],
                "note": "；".join(w.get("note", "") for w in war_checks),
                "affected_locations": [w.get("country_code", "") for w in war_checks],
            }
        else:
            result["war_risk"] = {"is_war_risk": False if (dep_iata or arr_iata) else None, "note": "未命中战争风险维护表"}

        result["policy_window"] = lookup_effective_window(claim_info)

        try:
            policy_window = result["policy_window"]
            effective_from = policy_window.get("effective_from")
            effective_to = policy_window.get("effective_to")
            is_allianz = bool(policy_window.get("is_allianz"))
            first_exit_date = str(claim_info.get("First_Exit_Date") or claim_info.get("first_exit_date") or "").strip() or None
            sched_local = (parsed or {}).get("schedule_local") or {}
            planned_dep_raw = str(sched_local.get("planned_dep") or "").strip()

            time_points: List[tuple] = []
            _all_checked_times: List[tuple] = []

            _date_of_insurance_raw = str(claim_info.get("Date_of_Insurance") or claim_info.get("date_of_insurance") or "").strip()
            if _date_of_insurance_raw and _date_of_insurance_raw.lower() not in ("unknown", "null", "none", ""):
                time_points.append(("投保时间", _date_of_insurance_raw))

            _exit_datetime_raw = str((vision_extract or {}).get("evidence", {}).get("exit_datetime") or "").strip()
            if _exit_datetime_raw and _exit_datetime_raw.lower() not in ("unknown", "null", "none", ""):
                time_points.append(("出境时间", _exit_datetime_raw))

            if planned_dep_raw and planned_dep_raw.lower() not in ("unknown", "null", "none", ""):
                time_points.append(("计划起飞时间", planned_dep_raw))

            _all_flights = (vision_extract or {}).get("all_flights_found") or []
            for _fl in _all_flights:
                _role = str(_fl.get("role_hint") or "").strip()
                if "原航班" in _role:
                    _fl_date = str(_fl.get("date") or "").strip()
                    if _fl_date and _fl_date.lower() not in ("unknown", "null", "none", ""):
                        _fl_date_first = _fl_date.replace("/", "-").replace(" ", "-").split("-")[0]
                        if len(_fl_date_first) == 4 and _fl_date_first.isdigit():
                            time_points.append(("航班日期", _fl_date))
                    break

            if accident_date_raw:
                time_points.append(("事故发生时间", accident_date_raw))

            alt_local = (parsed or {}).get("alternate_local") or {}
            alt_dep_raw = str(alt_local.get("alt_dep") or "").strip()
            if alt_dep_raw and alt_dep_raw.lower() not in ("unknown", "null", "none", ""):
                time_points.append(("联程延误发生时间", alt_dep_raw))

            in_coverage = None
            passed_times = []
            failed_times = []
            final_basis = ""
            final_check_result = None

            for _label, _time_str in time_points:
                _cov = check_delay_in_coverage(
                    delay_time=_time_str,
                    effective_from=effective_from,
                    effective_to=effective_to,
                    is_allianz=is_allianz,
                    first_exit_date=first_exit_date,
                    time_basis_label=_label,
                )
                _all_checked_times.append((_label, _time_str, _cov.get("in_coverage"), _cov.get("note", "")))
                if _cov.get("in_coverage") is True:
                    passed_times.append((_label, _time_str))
                    if in_coverage is None:
                        in_coverage = True
                        final_check_result = _cov
                        final_basis = f"{_label}: {_time_str}"

            if final_check_result:
                cov_check = final_check_result
                _summary_parts = []
                for _lp, _tp, _ic, _note in _all_checked_times:
                    _summary_parts.append(f"{_lp}({_tp}): {'✓在有效期' if _ic is True else ('✗超出有效期' if _ic is False else '?无法判定')}")
                cov_check["note"] = f"有效期校验（OR逻辑）: {'; '.join(_summary_parts)}"
                cov_check["basis"] = f"任一时间点在有效期内: {final_basis}"
                cov_check["checked_times"] = [
                    {"label": l, "time": t, "in_coverage": c} for l, t, c, _ in _all_checked_times
                ]
            else:
                cov_check = {
                    "in_coverage": None,
                    "applied_from": effective_from or "unknown",
                    "applied_to": effective_to or "unknown",
                    "used_extension": False,
                    "note": "所有时间点均无法判定，需补材/人工复核",
                    "basis": "unknown（无可用时间基准）",
                    "checked_times": [
                        {"label": l, "time": t, "in_coverage": c} for l, t, c, _ in _all_checked_times
                    ],
                }

            result["policy_coverage_check"] = cov_check
        except Exception as e:
            result["policy_coverage_check"] = {"in_coverage": None, "note": f"有效期校验异常: {e}"}
            result["debug_notes"].append(f"policy_coverage_check异常: {e}")

        coverage_info = lookup_coverage_area(claim_info)
        delay_iata = arr_iata or dep_iata
        area_check = check_delay_in_coverage_area(delay_iata, coverage_info)
        result["coverage_area"] = {**coverage_info, **area_check}

        flight_info = (parsed or {}).get("flight") or {}
        is_passenger_civil = _truthy(flight_info.get("is_passenger_civil"))
        result["passenger_civil_check"] = {
            "is_passenger_civil": is_passenger_civil,
            "flight_no": flight_info.get("ticket_flight_no", "unknown"),
            "note": (
                "航班属性未知，无法判断是否为民航客运班机" if is_passenger_civil is None
                else ("确认为民航客运班机" if is_passenger_civil else "非民航客运班机（货运/私人），不予赔付")
            ),
        }

        # 中转接驳延误检测
        itinerary = (parsed or {}).get("itinerary") or {}
        mention_missed_connection = _truthy(itinerary.get("mentions_missed_connection"))
        is_connecting_flight = _truthy(itinerary.get("is_connecting_or_transit"))
        aviation_delay_proof = _truthy((parsed or {}).get("evidence", {}).get("aviation_delay_proof"))

        def _has_connecting_keyword(text: str) -> bool:
            t = text.lower()
            for kw in ["missed their connecting", "misconnection", "connecting flight", "接驳", "误机后续", "错过后续", "未能搭乘后续", "错过接驳"]:
                for m in re.finditer(re.escape(kw), t):
                    prefix = t[max(0, m.start()-10):m.start()]
                    if any(neg in prefix for neg in ["未见", "未发现", "未检测", "无", "not ", "no ", "未提及", "不涉及"]):
                        continue
                    return True
            return False

        explanation_text = str((parsed or {}).get("explanation") or "")
        extraction_notes = str((vision_extract or {}).get("extraction_notes") or "")
        free_text_lower = (free_text or "")
        if (
            _has_connecting_keyword(explanation_text)
            or _has_connecting_keyword(extraction_notes)
            or _has_connecting_keyword(free_text_lower)
        ):
            mention_missed_connection = True

        delay_reason = str((parsed or {}).get("delay_reason") or "").lower()
        missed_connection_keywords = ["前序", "接驳", "误机", "missed connection", "connecting", "transit delay"]
        reason_suggests_missed = any(kw in delay_reason for kw in missed_connection_keywords)

        vision_alt = (vision_extract or {}).get("alternate") or {}
        vision_is_connecting_missed = str(vision_alt.get("is_connecting_missed") or "").strip().lower()
        vision_denies_missed = (vision_is_connecting_missed == "false")

        is_missed_connection = (
            (mention_missed_connection is True and not vision_denies_missed)
            or (is_connecting_flight is True and reason_suggests_missed and not vision_denies_missed)
        )

        avi_status = str((parsed or {}).get("aviation_status") or "").strip()
        alt_dep_val = str((parsed or {}).get("alternate_local", {}).get("alt_dep") or "").strip()
        alt_flight_no = str((parsed or {}).get("alternate_local", {}).get("alt_flight_no") or "").strip()
        has_rebooking = (
            not _is_unknown(alt_dep_val)
            or (not _is_unknown(alt_flight_no) and alt_flight_no != "")
        )
        vision_itinerary = (vision_extract or {}).get("itinerary_segments") or []
        vision_alt_cr = str((vision_extract or {}).get("alternate", {}).get("is_connecting_rebooking") or "").strip().lower()
        is_conn_rebooking_flag = vision_alt_cr == "true"
        if not is_conn_rebooking_flag and isinstance(vision_itinerary, list):
            for seg in vision_itinerary:
                if str(seg.get("is_connecting_rebooking") or "").strip().lower() == "true":
                    is_conn_rebooking_flag = True
                    break
        rebooking_override = False
        overbooking_override = False
        aviation_delay_proof_override = False

        if is_missed_connection and (avi_status == "取消" or is_conn_rebooking_flag) and has_rebooking:
            is_missed_connection = False
            rebooking_override = True

        _overbooking_keywords = ["超售", "overbooking", "overbooked", "denied boarding", "denied_boarding", "拒绝登机"]
        _all_texts = " ".join([
            str((parsed or {}).get("delay_reason") or ""),
            str((parsed or {}).get("explanation") or ""),
            str((vision_extract or {}).get("extraction_notes") or ""),
            str(free_text or ""),
        ]).lower()
        if is_missed_connection and any(kw in _all_texts for kw in _overbooking_keywords):
            is_missed_connection = False
            overbooking_override = True

        result["missed_connection_check"] = {
            "is_missed_connection": is_missed_connection,
            "mention_missed_connection": mention_missed_connection,
            "is_connecting_flight": is_connecting_flight,
            "reason_suggests_missed": reason_suggests_missed,
            "vision_denies_missed": vision_denies_missed,
            "aviation_delay_proof_override": aviation_delay_proof_override,
            "overbooking_override": overbooking_override,
            "rebooking_override": rebooking_override,
            "note": (
                "前序航班延误导致无法搭乘后续接驳航班，属于免责情形4，不予赔付" if is_missed_connection
                else (
                    "原航班取消后承运人整体改签，旅客未乘坐原联程航班，不适用中转接驳免责"
                    if rebooking_override
                    else (
                        "飞常准已确认被保险航班自身延误/取消，理赔事由明确，豁免中转接驳免责判定"
                        if aviation_delay_proof_override
                        else (
                            "超售/拒绝登机属于外部原因，豁免中转接驳免责判定"
                            if overbooking_override
                            else "未检测到中转接驳延误特征"
                        )
                    )
                )
            ),
        }

        # 必备材料清单硬检查
        evidence = (parsed or {}).get("evidence") or {}
        has_application_form = _truthy(evidence.get("has_application_form"))
        has_insurance_certificate = _truthy(evidence.get("has_insurance_certificate"))
        has_id_proof = _truthy(evidence.get("has_id_proof"))
        has_delay_proof = _truthy(evidence.get("has_delay_proof"))
        has_boarding_pass = _truthy(evidence.get("has_boarding_pass"))
        has_passport = _truthy(evidence.get("has_passport"))
        has_exit_entry_record = _truthy(evidence.get("has_exit_entry_record"))
        exit_dt = str(evidence.get("exit_datetime") or "").strip()
        if has_exit_entry_record is not True and not _is_unknown(exit_dt):
            has_exit_entry_record = True
        id_type_text = str(claim_info.get("ID_Type") or claim_info.get("id_type") or "").strip()
        is_id_card_policy = "身份证" in id_type_text

        vision_result_is_empty = not vision_extract or not any(
            vision_extract.get(k) for k in (
                "all_flights_found", "flight_no", "flight_date",
                "dep_iata", "arr_iata", "alternate", "evidence"
            )
        )

        try:
            if has_application_form is False:
                if (
                    str(claim_info.get("PolicyNo") or "").strip()
                    and str(claim_info.get("Applicant_Name") or "").strip()
                    and str(claim_info.get("Insurance_Company") or "").strip()
                    and str(claim_info.get("Description_of_Accident") or "").strip()
                ):
                    has_application_form = True
            if has_id_proof is False:
                if str(claim_info.get("ID_Type") or "").strip() and str(claim_info.get("ID_Number") or "").strip():
                    has_id_proof = True
        except Exception:
            pass

        try:
            if has_delay_proof is not True:
                if _truthy(evidence.get("aviation_delay_proof")) is True:
                    has_delay_proof = True
        except Exception:
            pass

        try:
            if has_delay_proof is False:
                desc = str(claim_info.get("Description_of_Accident") or "").strip().lower()
                flight_no = str(
                    (parsed or {}).get("flight", {}).get("ticket_flight_no")
                    or (parsed or {}).get("flight", {}).get("operating_flight_no")
                    or ""
                ).strip().upper().replace(" ", "")
                if not flight_no and desc:
                    m = re.search(r"\b([A-Z]{2}\d{1,5})\b", desc.upper())
                    if m:
                        flight_no = str(m.group(1)).strip().upper()
                keywords = ["取消", "延误", "罢工", "cancel", "delay", "strike"]
                has_keyword = any(k in desc for k in keywords)
                if flight_no and has_keyword:
                    if flight_no in desc.upper() or flight_no in desc:
                        if has_boarding_pass is True or has_application_form is True or has_id_proof is True:
                            has_delay_proof = True
        except Exception:
            pass

        try:
            claim_policy_no = str(claim_info.get("PolicyNo") or "").strip()
            claim_id_no = str(claim_info.get("ID_Number") or "").strip()
            parsed_policy_no = str((parsed or {}).get("policy_hint", {}).get("policy_no") or "").strip()
            parsed_id_no = str((parsed or {}).get("passenger", {}).get("id_number") or "").strip()
            if has_insurance_certificate is False:
                if (
                    claim_policy_no and parsed_policy_no and claim_policy_no == parsed_policy_no
                    and claim_id_no and parsed_id_no and claim_id_no == parsed_id_no
                    and has_id_proof is True
                ):
                    has_insurance_certificate = True
        except Exception:
            pass

        scan_stats = (vision_extract or {}).get("_vision_scan_stats") or {}
        scanned_all_attachments = bool(scan_stats.get("scanned_all_attachments") is True)

        missing_required = []
        if has_application_form is False:
            missing_required.append("权益补偿给付申请书")
        if has_insurance_certificate is False:
            missing_required.append("保险凭证/会员权益卡")
        if has_id_proof is False:
            missing_required.append("申请人身份证明（身份证/护照）")
        if has_delay_proof is False:
            missing_required.append("承运人延误书面证明")
        if has_boarding_pass is not True and not (_truthy(evidence.get("aviation_delay_proof")) is True):
            missing_required.append("登机牌或电子客票行程单")
        if is_id_card_policy:
            if has_exit_entry_record is True and has_passport is False:
                missing_required.append("被保险人护照照片页")
        else:
            if has_passport is False:
                missing_required.append("被保险人护照照片页")
        if has_exit_entry_record is False:
            missing_required.append("中国海关出入境盖章页或电子出入境记录")

        effective_missing_required = missing_required if scanned_all_attachments else []

        result["required_materials_check"] = {
            "missing_required": effective_missing_required,
            "has_application_form": has_application_form,
            "has_insurance_certificate": has_insurance_certificate,
            "has_id_proof": has_id_proof,
            "has_delay_proof": has_delay_proof,
            "has_boarding_pass": has_boarding_pass,
            "has_passport": has_passport,
            "has_exit_entry_record": has_exit_entry_record,
            "is_id_card_policy": is_id_card_policy,
            "scanned_all_attachments": scanned_all_attachments,
            "vision_result_is_empty": vision_result_is_empty,
            "note": (
                "材料未全量扫描完成，本轮不输出缺必备材料结论"
                if not scanned_all_attachments
                else (
                    f"缺少必备材料：{'、'.join(effective_missing_required)}"
                    if effective_missing_required
                    else ("Vision提取结果为空，需要人工复核" if vision_result_is_empty else "必备材料齐全")
                )
            ),
        }

        fraud_check = _check_foreseeability_fraud(parsed=parsed, claim_info=claim_info)
        result["fraud_foreseeability_check"] = fraud_check
        result["inheritance_check"] = _check_inheritance_scenario(claim_info=claim_info)
        result["capacity_check"] = _check_legal_capacity(claim_info=claim_info)
        result["name_match_check"] = _check_name_match(
            parsed=parsed, claim_info=claim_info, vision_extract=vision_extract or {},
        )
        result["same_day_policy_check"] = _check_same_day_policy(parsed=parsed, claim_info=claim_info)
        result["coverage_area_text_check"] = _check_coverage_area_text(
            parsed=parsed, claim_info=claim_info,
            dep_iata=dep_iata, arr_iata=arr_iata,
            dep_info=result.get("dep_airport") or {},
            arr_info=result.get("arr_airport") or {},
        )

    except Exception as e:
        result["debug_notes"].append(f"hardcheck异常: {e}")
        LOGGER.warning(f"[_run_hardcheck] 硬校验异常: {e}", extra=log_extra(stage="fd_hardcheck", attempt=0))

    return result
