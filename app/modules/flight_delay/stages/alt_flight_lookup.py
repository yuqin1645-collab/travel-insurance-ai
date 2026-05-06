"""
flight_delay stages — 接驳/替代航班飞常准查询。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List

import aiohttp

from app.logging_utils import LOGGER, log_extra
from app.skills.flight_lookup import get_flight_lookup_skill

from .utils import _is_unknown, _truthy, _has_timezone


async def lookup_alt_flight_data(
    *,
    parsed: Dict[str, Any],
    vision_extract: Dict[str, Any],
    cf_candidates: List,
    forceid: str,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """接驳/替代航班飞常准查询，返回更新后的 parsed。"""
    is_conn_rebooking = _truthy((parsed.get("itinerary") or {}).get("is_connecting_rebooking")) is True
    chain = (parsed or {}).get("schedule_revision_chain") or []
    first_alt_flight_no = None
    first_alt_date = None
    if is_conn_rebooking and isinstance(chain, list) and len(chain) >= 2:
        first_alt = chain[1]
        first_alt_flight_no = str(first_alt.get("original_flight_no") or "").strip()
        first_alt_date = str(first_alt.get("original_date") or "").strip()
        if first_alt_date and first_alt_date.lower() not in ("unknown", ""):
            first_alt_date = first_alt_date[:10]

    alt_local = parsed.get("alternate_local") or {}
    alt_fn = str(alt_local.get("alt_flight_no") or "").strip()
    alt_dep_raw = str(alt_local.get("alt_dep") or "").strip()
    alt_dep_date = alt_dep_raw[:10] if alt_dep_raw and alt_dep_raw.lower() not in ("unknown", "") else ""

    _already_queried = [c[0].upper() for c in cf_candidates] if cf_candidates else []

    alt_results: Dict[str, Any] = {}

    if (
        is_conn_rebooking
        and first_alt_flight_no
        and first_alt_flight_no.lower() not in ("unknown", "null", "")
        and first_alt_date
        and first_alt_flight_no.upper() not in _already_queried
    ):
        try:
            skill = get_flight_lookup_skill()
            first_alt_aviation = await skill.lookup_status(
                flight_no=first_alt_flight_no,
                date=first_alt_date,
                dep_iata=None,
                arr_iata=None,
                session=session,
            )
            alt_results["first_alt_aviation_lookup"] = first_alt_aviation
            if first_alt_aviation.get("success"):
                LOGGER.info(
                    f"[{forceid}] 联程首班替代航班飞常准查询成功: {first_alt_flight_no} {first_alt_date} -> {first_alt_aviation.get('status')}",
                    extra=log_extra(forceid=forceid, stage="fd_first_alt_aviation_lookup", attempt=0),
                )
                first_actual_dep = first_alt_aviation.get("actual_dep")
                if first_actual_dep:
                    parsed.setdefault("alternate_local", {})["alt_dep"] = first_actual_dep
                    parsed.setdefault("actual_local", {})["actual_dep"] = first_actual_dep
                    LOGGER.info(
                        f"[{forceid}] 联程首班 alt_dep/actual_dep 已覆盖为: {first_actual_dep}",
                        extra=log_extra(forceid=forceid, stage="fd_first_alt_aviation_lookup", attempt=0),
                    )
        except Exception as _first_ae:
            LOGGER.warning(
                f"[{forceid}] 联程首班替代航班查询失败（降级）: {_first_ae}",
                extra=log_extra(forceid=forceid, stage="fd_first_alt_aviation_lookup", attempt=0),
            )
            first_alt_planned_dep = str(first_alt.get("planned_dep") or "").strip()
            if first_alt_planned_dep and first_alt_planned_dep.lower() not in ("unknown", ""):
                parsed.setdefault("alternate_local", {})["alt_dep"] = first_alt_planned_dep
                parsed.setdefault("actual_local", {})["actual_dep"] = first_alt_planned_dep
                LOGGER.info(
                    f"[{forceid}] 联程首班 alt_dep/actual_dep 已用 Vision 提取时间兜底: {first_alt_planned_dep}",
                    extra=log_extra(forceid=forceid, stage="fd_first_alt_aviation_lookup", attempt=0),
                )

    if (
        alt_fn and alt_fn.lower() not in ("unknown", "null", "")
        and alt_dep_date
        and alt_fn.upper() not in _already_queried
    ):
        try:
            skill = get_flight_lookup_skill()
            alt_aviation = await skill.lookup_status(
                flight_no=alt_fn,
                date=alt_dep_date,
                dep_iata=None,
                arr_iata=None,
                session=session,
            )
            alt_results["alt_aviation_lookup"] = alt_aviation
            if alt_aviation.get("success"):
                LOGGER.info(
                    f"[{forceid}] 接驳航班飞常准查询成功: {alt_fn} {alt_dep_date} -> {alt_aviation.get('status')}",
                    extra=log_extra(forceid=forceid, stage="fd_alt_aviation_lookup", attempt=0),
                )
                avi_dep_iata = str(alt_aviation.get("dep_iata") or "").strip().upper()
                avi_arr_iata = str(alt_aviation.get("arr_iata") or "").strip().upper()
                if not _is_unknown(avi_dep_iata):
                    parsed.setdefault("alternate_local", {})["alt_dep_iata"] = avi_dep_iata
                if not _is_unknown(avi_arr_iata):
                    parsed.setdefault("alternate_local", {})["alt_arr_iata"] = avi_arr_iata

                is_conn_rebooking = _truthy((parsed.get("itinerary") or {}).get("is_connecting_rebooking")) is True
                actual_arr = alt_aviation.get("actual_arr")
                alt_arr_current = str(alt_local.get("alt_arr") or "")
                alt_arr_needs_fill = (
                    _is_unknown(alt_local.get("alt_arr"))
                    or "unknown" in alt_arr_current.lower()
                    or not _has_timezone(alt_arr_current)
                )
                if actual_arr and alt_arr_needs_fill:
                    parsed.setdefault("alternate_local", {})["alt_arr"] = actual_arr
                    if is_conn_rebooking:
                        parsed.setdefault("actual_local", {})["actual_arr"] = actual_arr

                actual_dep = alt_aviation.get("actual_dep")
                alt_dep_current = str(alt_local.get("alt_dep") or "")
                alt_dep_is_text_extracted = bool(re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?![:+\-])", alt_dep_current))
                alt_dep_needs_fill = (
                    _is_unknown(alt_local.get("alt_dep"))
                    or "unknown" in alt_dep_current.lower()
                    or (not _has_timezone(alt_dep_current) and not alt_dep_is_text_extracted)
                )
                try:
                    alt_dep_dt = datetime.fromisoformat(alt_dep_current.replace(" ", "T"))
                    alt_arr_dt_str = str(alt_local.get("alt_arr") or "")
                    if alt_arr_dt_str and alt_arr_dt_str.lower() not in ("unknown", ""):
                        alt_arr_dt = datetime.fromisoformat(alt_arr_dt_str.replace(" ", "T"))
                        if alt_dep_dt > alt_arr_dt:
                            alt_dep_needs_fill = False
                except Exception:
                    pass
                alt_dep_to_fill = actual_dep or alt_aviation.get("planned_dep")
                if alt_dep_to_fill:
                    is_conn_rebooking = _truthy((parsed.get("itinerary") or {}).get("is_connecting_rebooking")) is True
                    if is_conn_rebooking:
                        pass
                    elif alt_dep_needs_fill:
                        parsed.setdefault("alternate_local", {})["alt_dep"] = alt_dep_to_fill
                        parsed.setdefault("actual_local", {})["actual_dep"] = alt_dep_to_fill
        except Exception as _alt_ae:
            LOGGER.warning(
                f"[{forceid}] 接驳航班查询异常（降级跳过）: {_alt_ae}",
                extra=log_extra(forceid=forceid, stage="fd_alt_aviation_lookup", attempt=0),
            )

    return {"parsed": parsed, "alt_results": alt_results}
