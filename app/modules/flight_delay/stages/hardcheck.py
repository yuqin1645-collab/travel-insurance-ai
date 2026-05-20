"""
flight_delay stages — 硬校验集合（_run_hardcheck）及可预见因素欺诈检测。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
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

from .utils import _truthy, _is_unknown, _iata, _parse_date_str, _parse_date_any, _parse_tz_offset
from .validators import (
    _check_inheritance_scenario,
    _check_legal_capacity,
    _check_name_match,
    _check_same_day_policy,
    _check_coverage_area_text,
    _check_guardian_materials,
)

# 模块级常量
FRAUD_SUSPECT_DAYS_THRESHOLD = 3  # 投保/订票时间距事故日期≤3天，触发可预见因素欺诈嫌疑
BEIJING_TZ = timezone(timedelta(hours=8))  # 北京时间 UTC+8


def _normalize_to_beijing(time_str: str, tz_hint: Optional[str] = None) -> str:
    """将时间字符串归一化到北京时间（UTC+8），返回 'YYYY-MM-DD HH:MM' 格式。
    安联是国内保险公司，保单有效期以北京时间为准。
    若无法解析时区则返回原字符串。
    """
    from .utils import _parse_utc_dt, _parse_local_dt

    if not time_str or _is_unknown(time_str):
        return time_str

    dt_utc = _parse_utc_dt(time_str)
    if dt_utc is None and tz_hint:
        dt_utc = _parse_local_dt(time_str, tz_hint)
    if dt_utc is None:
        return time_str
    return dt_utc.astimezone(BEIJING_TZ).strftime('%Y-%m-%d %H:%M')


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

        # 新增：航班取消早于投保检测（既存条件免责）
        # 当飞常准确认航班取消且投保时取消已存在 → 触发既存条件免责
        try:
            avi_status = str((parsed or {}).get("aviation_status") or "").strip()
            avi_status_raw = str((parsed or {}).get("aviation_status_raw") or "").strip()
            is_cancelled = avi_status == "取消" or "取消" in avi_status_raw

            if is_cancelled:
                date_of_insurance_raw = str(claim_info.get("Date_of_Insurance") or claim_info.get("date_of_insurance") or "").strip()
                insurance_dt = _parse_date_any(date_of_insurance_raw)

                if insurance_dt:
                    # 从 aviation_lookup 获取计划起飞时间
                    planned_dep_raw = str((parsed or {}).get("aviation_scheduled", {}).get("planned_dep") or "").strip()
                    if not planned_dep_raw:
                        planned_dep_raw = str((parsed or {}).get("schedule_local", {}).get("planned_dep") or "").strip()
                    planned_dep_dt = _parse_date_any(planned_dep_raw)

                    if planned_dep_dt:
                        # 投保日期在计划起飞日期之前 → 正常投保行为
                        # 但航班已取消 → 需要判断取消是否早于投保
                        # 对于"提前取消"，取消必然在计划起飞前很久就已宣布
                        # 如果投保日期距计划起飞日期 >= 7天，且航班状态为"提前取消"
                        # → 高度疑似投保时已知航班取消
                        days_before_departure = (planned_dep_dt.date() - insurance_dt.date()).days

                        if "提前取消" in avi_status_raw:
                            # 提前取消 + 投保在计划起飞前 >= 7天
                            # → 取消公告很可能在投保前已发布
                            fn = str((parsed or {}).get("flight", {}).get("ticket_flight_no") or (parsed or {}).get("flight", {}).get("operating_flight_no") or "").strip()
                            route = (parsed or {}).get("route") or {}
                            dep = str(route.get("dep_iata") or "").strip()
                            arr = str(route.get("arr_iata") or "").strip()
                            result["fraud_suspected"] = True
                            result["fraud_level"] = "confirmed"
                            result["reason"] = (
                                f"既存条件：航班{fn or 'unknown'}（{dep or ''}->{arr or ''}）"
                                f"被飞常准确认为提前取消，"
                                f"投保时间（{date_of_insurance_raw}）距计划起飞（{planned_dep_raw[:16]}）尚有{days_before_departure}天，"
                                f"提前取消的公告通常在起飞前多日发布，投保时航班取消已成既定事实"
                            )
                            result["note"] = "命中既存条件免责：航班取消早于投保，不予赔付"
                            return result
                        elif days_before_departure <= 1:
                            # 投保在计划起飞前1天内，航班已取消
                            # → 取消公告可能刚发布
                            fn = str((parsed or {}).get("flight", {}).get("ticket_flight_no") or (parsed or {}).get("flight", {}).get("operating_flight_no") or "").strip()
                            result["fraud_suspected"] = True
                            result["fraud_level"] = "suspect"
                            result["reason"] = (
                                f"既存条件嫌疑：航班{fn or 'unknown'}已取消，"
                                f"投保时间（{date_of_insurance_raw}）距计划起飞（{planned_dep_raw[:16]}）仅{days_before_departure}天，"
                                f"投保时航班可能已被取消，需人工复核"
                            )
                            result["note"] = "投保时航班取消状态可能已存在，建议人工复核"
        except Exception:
            pass

        if not reason_is_foreseeable:
            result["note"] = "延误原因非典型可预见因素（天气/罢工等），跳过欺诈检测"
            return result

        try:
            route = (parsed or {}).get("route") or {}

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
            if days_before <= FRAUD_SUSPECT_DAYS_THRESHOLD:
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
    vision_extract: Optional[Dict[str, Any]] = None,
    claim_folder: Optional[Path] = None,
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

        dep_iata = _iata(route.get("dep_iata"))
        arr_iata = _iata(route.get("arr_iata"))
        transit_iata = _iata(route.get("transit_iata")) or _iata(itinerary.get("transit_iata"))

        accident_date_raw = str(claim_info.get("Date_of_Accident") or claim_info.get("date_of_accident") or "").strip()

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

            # 时区提示映射（用于北京时间归一化，安联保单有效期以北京时间为准）
            _tz_hint_dep = str(sched_local.get("dep_timezone_hint") or "").strip()
            _tz_hint_arr = str(sched_local.get("arr_timezone_hint") or "").strip()
            alt_local = (parsed or {}).get("alternate_local") or {}
            actual_local = (parsed or {}).get("actual_local") or {}
            _tz_hint_alt = str(alt_local.get("timezone_hint") or "").strip()
            _tz_hint_act = str(actual_local.get("timezone_hint") or "").strip()
            _label_to_tz = {
                "计划起飞时间": _tz_hint_dep,
                "实际出发时间": _tz_hint_act or _tz_hint_dep,
                "替代航班起飞时间": _tz_hint_alt or _tz_hint_dep,
                "实际到达时间": _tz_hint_act or _tz_hint_arr,
                "替代航班到达时间": _tz_hint_alt or _tz_hint_arr,
            }

            _date_of_insurance_raw = str(claim_info.get("Date_of_Insurance") or claim_info.get("date_of_insurance") or "").strip()
            # 投保时间仅作参考展示，不作为正向判定依据（投保时间在有效期之前是正常的）
            _insurance_ref: Optional[tuple] = None
            if _date_of_insurance_raw and _date_of_insurance_raw.lower() not in ("unknown", "null", "none", ""):
                _insurance_ref = ("投保时间", _date_of_insurance_raw)

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

            # 实际起飞/到达时间（延误事件的真实发生时间，优先级最高）
            actual_local = (parsed or {}).get("actual_local") or {}
            actual_dep_raw = str(actual_local.get("actual_dep") or "").strip()
            if actual_dep_raw and actual_dep_raw.lower() not in ("unknown", "null", "none", ""):
                time_points.append(("实际出发时间", actual_dep_raw))
            actual_arr_raw = str(actual_local.get("actual_arr") or "").strip()
            if actual_arr_raw and actual_arr_raw.lower() not in ("unknown", "null", "none", ""):
                time_points.append(("实际到达时间", actual_arr_raw))

            alt_local = (parsed or {}).get("alternate_local") or {}
            alt_dep_raw = str(alt_local.get("alt_dep") or "").strip()
            if alt_dep_raw and alt_dep_raw.lower() not in ("unknown", "null", "none", ""):
                time_points.append(("替代航班起飞时间", alt_dep_raw))
            alt_arr_raw = str(alt_local.get("alt_arr") or "").strip()
            if alt_arr_raw and alt_arr_raw.lower() not in ("unknown", "null", "none", ""):
                time_points.append(("替代航班到达时间", alt_arr_raw))

            # 区分"实际时间"和"计划/参考时间"
            actual_time_labels = {"实际出发时间", "实际到达时间", "替代航班起飞时间", "替代航班到达时间"}
            has_actual_times = any(label in actual_time_labels for label, _ in time_points)

            in_coverage = None
            passed_times = []
            failed_times = []
            final_basis = ""
            final_check_result = None

            for _label, _time_str in time_points:
                # 安联保单有效期以北京时间为准，将带时区的时间归一化到北京时间
                _tz_hint = _label_to_tz.get(_label, "")
                _time_beijing = _normalize_to_beijing(_time_str, _tz_hint) if _tz_hint else _time_str

                # 时间合理性校验：若"实际时间"与计划起飞日期偏差超过7天，视为AI解析错误，不参与判定
                _time_unreliable = False
                if _label in actual_time_labels and planned_dep_raw and not _is_unknown(planned_dep_raw):
                    _planned_date = (_normalize_to_beijing(planned_dep_raw, _tz_hint_dep) if _tz_hint_dep else planned_dep_raw)[:10]
                    _actual_date = _time_beijing[:10]
                    try:
                        _pd = datetime.strptime(_planned_date, "%Y-%m-%d").date()
                        _ad = datetime.strptime(_actual_date, "%Y-%m-%d").date()
                        if abs((_ad - _pd).days) > 7:
                            _time_unreliable = True
                    except Exception:
                        pass

                if _time_unreliable:
                    continue

                _cov = check_delay_in_coverage(
                    delay_time=_time_beijing,
                    effective_from=effective_from,
                    effective_to=effective_to,
                    is_allianz=is_allianz,
                    first_exit_date=first_exit_date,
                    time_basis_label=_label,
                )
                _all_checked_times.append((_label, _time_beijing, _cov.get("in_coverage"), _cov.get("note", "")))
                if _cov.get("in_coverage") is True:
                    passed_times.append((_label, _time_beijing))
                elif _cov.get("in_coverage") is False:
                    failed_times.append((_label, _time_beijing))

            # 有效期判定逻辑（修正 2026-05-08）：
            # - 如果有实际时间（实际出发/到达、替代航班起飞/到达），以实际时间为准：
            #   至少一个实际时间在有效期内 → 通过；全部实际时间超出 → 拒绝
            # - 如果没有实际时间，降级使用计划/参考时间（OR逻辑）
            if has_actual_times:
                actual_passed = [t for t in passed_times if t[0] in actual_time_labels]
                actual_failed = [t for t in failed_times if t[0] in actual_time_labels]
                if actual_passed:
                    in_coverage = True
                    final_check_result = check_delay_in_coverage(
                        delay_time=actual_passed[0][1],
                        effective_from=effective_from,
                        effective_to=effective_to,
                        is_allianz=is_allianz,
                        first_exit_date=first_exit_date,
                        time_basis_label=actual_passed[0][0],
                    )
                    final_basis = f"{actual_passed[0][0]}: {actual_passed[0][1]}"
                elif actual_failed:
                    # 所有实际时间都超出有效期 → 拒绝
                    in_coverage = False
                    final_check_result = {
                        "in_coverage": False,
                        "applied_from": effective_from or "unknown",
                        "applied_to": effective_to or "unknown",
                        "used_extension": False,
                        "note": "所有实际航班时间均超出保单有效期",
                        "basis": f"实际时间全部超出: {', '.join(f'{l}({t})' for l, t in actual_failed)}",
                    }
                    final_basis = f"实际时间全部超出有效期"
                else:
                    # 实际时间全部无法判定 → 降级OR
                    if passed_times:
                        in_coverage = True
                        final_check_result = check_delay_in_coverage(
                            delay_time=passed_times[0][1],
                            effective_from=effective_from,
                            effective_to=effective_to,
                            is_allianz=is_allianz,
                            first_exit_date=first_exit_date,
                            time_basis_label=passed_times[0][0],
                        )
                        final_basis = f"{passed_times[0][0]}: {passed_times[0][1]}"
            else:
                # 无实际时间，使用计划/参考时间（OR逻辑）
                if passed_times:
                    in_coverage = True
                    final_check_result = check_delay_in_coverage(
                        delay_time=passed_times[0][1],
                        effective_from=effective_from,
                        effective_to=effective_to,
                        is_allianz=is_allianz,
                        first_exit_date=first_exit_date,
                        time_basis_label=passed_times[0][0],
                    )
                    final_basis = f"{passed_times[0][0]}: {passed_times[0][1]}"

            # 投保时间加入展示（仅参考，不影响判定）
            if _insurance_ref:
                _ins_label, _ins_time = _insurance_ref
                _ins_cov = check_delay_in_coverage(
                    delay_time=_ins_time,
                    effective_from=effective_from,
                    effective_to=effective_to,
                    is_allianz=is_allianz,
                    first_exit_date=first_exit_date,
                    time_basis_label=_ins_label,
                )
                _all_checked_times.insert(0, (_ins_label, _ins_time, _ins_cov.get("in_coverage"), _ins_cov.get("note", "")))

            if final_check_result:
                cov_check = final_check_result
                _summary_parts = []
                for _lp, _tp, _ic, _note in _all_checked_times:
                    _summary_parts.append(f"{_lp}({_tp}): {'✓在有效期' if _ic is True else ('✗超出有效期' if _ic is False else '?无法判定')}")
                logic_desc = "实际时间优先" if has_actual_times else "计划时间OR逻辑"
                cov_check["note"] = f"有效期校验（{logic_desc}）: {'; '.join(_summary_parts)}"
                cov_check["basis"] = f"判定依据: {final_basis}"
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
            for kw in [
                "missed their connecting", "misconnection", "connecting flight",
                "接驳", "误机后续", "错过后续", "未能搭乘后续", "错过接驳",
                # 新增：更多中转接驳相关表述
                "前序航班", "前段航班", "前序延误", "前段延误",
                "转机", "中转", "中转延误", "联程延误",
                "衔接不上", "赶不上", "来不及",
                "missed connection", "missed transit", "transit delay",
                "connection missed", "unable to connect",
            ]:
                for m in re.finditer(re.escape(kw), t):
                    prefix = t[max(0, m.start()-15):m.start()]
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
        vision_confirms_missed = (vision_is_connecting_missed == "true")

        is_missed_connection = (
            mention_missed_connection is True
            or (is_connecting_flight is True and reason_suggests_missed)
            or vision_confirms_missed
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
        prev_seg_arrived_ok = False  # 前程正常到达（飞常准确认）
        causal_check_available = False  # 因果检查是否执行

        # 业务规则（2026-05-11明确）：不管延误时长够不够，
        # 都要检查是否前序航班延误导致到达中转站时间延后，造成赶不上后续航班。
        # 因果检查优先于改签豁免：先判断前序是否延误，再决定是否豁免。

        # ── 因果检查：前程 actual_arr vs 末段 planned_dep ──
        connecting_segments = (parsed or {}).get("connecting_segments_data") or []
        last_seg_dep_iata_val = str((parsed.get("schedule_local") or {}).get("last_seg_dep_iata") or "").strip()
        if connecting_segments and last_seg_dep_iata_val:
            causal_check_available = True
            for seg in connecting_segments:
                if str(seg.get("arr_iata") or "").strip().upper() == last_seg_dep_iata_val.upper():
                    prev_actual_arr_raw = str(seg.get("actual_arr") or "").strip()
                    try:
                        if prev_actual_arr_raw and prev_actual_arr_raw.lower() not in ("", "unknown", "none"):
                            prev_actual_arr_dt = datetime.fromisoformat(prev_actual_arr_raw)
                            v_segs = (vision_extract or {}).get("itinerary_segments") or []
                            last_seg_planned_dep_raw = ""
                            for vs in v_segs:
                                if str(vs.get("original_dep_iata") or "").strip().upper() == last_seg_dep_iata_val.upper():
                                    last_seg_planned_dep_raw = str(vs.get("original_date") or "").strip()
                                    break
                            if last_seg_planned_dep_raw:
                                last_seg_tz = prev_actual_arr_dt.utcoffset()
                                if last_seg_tz is not None:
                                    from datetime import timezone as _tz2, timedelta as _td2
                                    date_part = last_seg_planned_dep_raw[:16]
                                    last_seg_dep_dt = datetime.strptime(date_part, "%Y-%m-%d %H:%M").replace(tzinfo=_tz2(last_seg_tz))
                                    if prev_actual_arr_dt <= last_seg_dep_dt:
                                        prev_seg_arrived_ok = True
                    except Exception:
                        pass

        # 因果检查确认：前序航班延误导致误机（actual_arr > last_seg planned_dep）
        # → 主动触发误机免责，不管初始判定结果如何
        if causal_check_available and not prev_seg_arrived_ok:
            is_missed_connection = True

        # 若前程正常到达（非前序延误导致），末段是独立事件，不触发误机免责
        if is_missed_connection and causal_check_available and prev_seg_arrived_ok:
            is_missed_connection = False

        # 改签豁免：仅在因果检查未执行或确认前程正常到达时才适用
        # 如果因果检查确认前序延误导致了误机，即使有改签也不豁免
        if is_missed_connection and has_rebooking and not causal_check_available:
            # 无法确认前序是否延误 → 默认适用改签豁免
            is_missed_connection = False
            rebooking_override = True

        LOGGER.info(
            f"[missed_conn_check_line658] after has_rebooking check: is_missed={is_missed_connection}, rebovr={rebooking_override}",
            extra=log_extra(forceid=str((claim_info or {}).get("forceid", "unknown")), stage="fd_hardcheck", attempt=0),
        )

        if is_missed_connection and avi_status == "取消" and has_rebooking and not is_conn_rebooking_flag and not causal_check_available:
            is_missed_connection = False
            rebooking_override = True

        # 联程改签场景豁免：仅在因果检查未执行时才适用
        LOGGER.info(
            f"[missed_conn_check_line668] before is_conn_rebooking check: is_missed={is_missed_connection}, is_conn_rebooking={is_conn_rebooking_flag}, causal_avail={causal_check_available}",
            extra=log_extra(forceid=str((claim_info or {}).get("forceid", "unknown")), stage="fd_hardcheck", attempt=0),
        )
        if is_missed_connection and is_conn_rebooking_flag and not causal_check_available:
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

        # 中转接驳豁免结果调试日志（用于排查 P2 未触发原因）
        LOGGER.info(
            f"[missed_conn_debug] is_missed={is_missed_connection}, "
            f"has_rebooking={has_rebooking}(alt_dep={alt_dep_val!r}, alt_fn={alt_flight_no!r}), "
            f"is_conn_rebooking={is_conn_rebooking_flag}, causal_avail={causal_check_available}, "
            f"avi_status={avi_status!r}, "
            f"rebovr={rebooking_override}, prev_arr_ok={prev_seg_arrived_ok}",
            extra=log_extra(forceid=str((claim_info or {}).get("forceid", "unknown")), stage="fd_hardcheck", attempt=0),
        )

        result["missed_connection_check"] = {
            "is_missed_connection": is_missed_connection,
            "mention_missed_connection": mention_missed_connection,
            "is_connecting_flight": is_connecting_flight,
            "reason_suggests_missed": reason_suggests_missed,
            "vision_confirms_missed": vision_confirms_missed,
            "aviation_delay_proof_override": aviation_delay_proof_override,
            "overbooking_override": overbooking_override,
            "rebooking_override": rebooking_override,
            "prev_seg_arrived_ok": prev_seg_arrived_ok,
            "note": (
                "前序航班延误导致无法搭乘后续接驳航班，属于免责情形4，不予赔付" if is_missed_connection
                else (
                    "原航班取消后承运人整体改签，旅客未乘坐原联程航班，不适用中转接驳免责"
                    if rebooking_override
                    else (
                        "飞常准确认前程正常到达中转机场，末段独立取消/延误，不属于前程延误导致的误机，不适用联程免责"
                        if prev_seg_arrived_ok
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
        # 预计算国际航班判定（出入境兜底和护照兜底共用）
        route_dep_cc = str(dep_info.get("country_code") or "").strip().upper()
        route_arr_cc = str(arr_info.get("country_code") or "").strip().upper()
        dep_found = dep_info.get("found", False)
        arr_found = arr_info.get("found", False)
        is_international = (
            (route_dep_cc and route_arr_cc and (route_dep_cc != "CN" or route_arr_cc != "CN"))
            or (route_dep_cc and route_dep_cc != "CN")
            or (route_arr_cc and route_arr_cc != "CN")
        )
        airport_unknown = (not dep_found or not arr_found) and (dep_iata and arr_iata)
        # 兜底：国际航班确认 + 任一旅行证件 → 推断已出入境
        if has_exit_entry_record is not True:
            has_any_travel_doc = has_passport is True or has_boarding_pass is True or has_id_proof is True
            if (is_international or airport_unknown) and has_any_travel_doc:
                has_exit_entry_record = True
                result["debug_notes"].append("出入境记录兜底：国际航班/机场未知+旅行证件齐全，推断出入境记录已满足")
        # 额外兜底：有飞常准延误证明 → 推断已出入境（官方数据含出入境信息）
        if has_exit_entry_record is not True:
            if _truthy(evidence.get("aviation_delay_proof")) is True:
                has_exit_entry_record = True
                result["debug_notes"].append("出入境记录兜底：飞常准延误证明已确认，推断出入境记录已满足")
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
        except Exception as e:
            result["debug_notes"].append(f"has_application_form/id_proof兜底校验降级: {e}")

        try:
            if has_delay_proof is not True:
                if _truthy(evidence.get("aviation_delay_proof")) is True:
                    has_delay_proof = True
        except Exception as e:
            result["debug_notes"].append(f"aviation_delay_proof兜底校验降级: {e}")

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
                        has_delay_proof = True
                        result["debug_notes"].append("delay_proof文本兜底：描述文本含航班号+延误关键词，推断延误证明已满足")
            # 额外兜底：改签场景 + 飞常准查到改签后航班 → 推断延误证明已满足
            if has_delay_proof is not True:
                alternate = (parsed or {}).get("alternate") or {}
                if isinstance(alternate, dict) and alternate.get("alt_flight_no"):
                    alt_fn = str(alternate.get("alt_flight_no") or "").strip()
                    if alt_fn and alt_fn.lower() not in ("unknown", ""):
                        has_delay_proof = True
                        result["debug_notes"].append("delay_proof兜底：存在改签航班信息，推断延误/改签证明已满足")
        except Exception as e:
            result["debug_notes"].append(f"delay_proof文本兜底校验降级: {e}")

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
        except Exception as e:
            result["debug_notes"].append(f"insurance_certificate兜底校验降级: {e}")

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
            # 兜底：有延误证明 + 有身份证明 → 登机牌非必须（延误证明已含航班信息）
            if has_delay_proof is True and has_id_proof is True:
                has_boarding_pass = True
                result["debug_notes"].append("登机牌兜底：延误证明+身份证明齐全，推断登机牌已满足")
            # 兜底：Vision提取到有效航班数据 → 推断登机牌/行程单已提供
            elif vision_extract and vision_extract.get("flight_no") and not _is_unknown(str(vision_extract.get("flight_no") or "")):
                has_boarding_pass = True
                result["debug_notes"].append("登机牌兜底：Vision已提取到航班号，推断登机牌/行程单已提供")
            else:
                missing_required.append("登机牌或电子客票行程单")
        if is_id_card_policy:
            if has_exit_entry_record is True and has_passport is False:
                # 兜底：身份证保单但确认国际旅行 → 护照必然存在
                if is_international:
                    has_passport = True
                    result["debug_notes"].append("护照照片页兜底：身份证保单+国际航班确认，推断护照已提供")
                else:
                    missing_required.append("被保险人护照照片页")
        else:
            if has_passport is False:
                # 兜底：证件类型为护照 → 推断护照照片页已提供
                id_type_lower = id_type_text.lower()
                if "护照" in id_type_text or "passport" in id_type_lower:
                    has_passport = True
                    result["debug_notes"].append("护照照片页兜底：证件类型为护照，推断护照已提供")
                else:
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

        # 检查监护人材料是否已提供
        # 从 claim_folder 中获取实际的文件名（优先使用本地文件名，因为可能包含原始文件名）
        file_names = []
        if claim_folder:
            try:
                for f in claim_folder.iterdir():
                    if f.is_file() and f.name != "claim_info.json":
                        file_names.append(f.name)
            except Exception:
                pass

        # 如果本地文件名获取失败，回退到 FileList URL 中的文件名
        if not file_names:
            file_list = claim_info.get("FileList") or []
            for item in file_list:
                if isinstance(item, dict):
                    url = item.get("FileUrl") or item.get("fileUrl") or item.get("url") or ""
                    if url:
                        fname = url.split("/")[-1].split("?")[0] if "/" in url else url
                        if fname:
                            file_names.append(fname)
                elif isinstance(item, str):
                    file_names.append(item)

        result["guardian_materials_check"] = _check_guardian_materials(
            claim_info=claim_info,
            vision_extract=vision_extract or {},
            file_names=file_names,
            claim_folder=claim_folder,
        )

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
