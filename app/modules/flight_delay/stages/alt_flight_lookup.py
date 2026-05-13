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


def _is_duplicate_candidate(flight_no: str, existing_candidates: List) -> bool:
    """检查航班号是否与已有候选重复（仅精确匹配，不用别名展开）。

    别名展开是飞常准查询时的事情，去重不应将不同航班（如 U25270 和 EJU5270）视为相同。
    """
    target = flight_no.strip().upper().replace(" ", "")
    existing_upper = [c[0].strip().upper().replace(" ", "") for c in existing_candidates]
    return target in existing_upper


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
    last_alt_flight_no = None
    last_alt_date = None
    if is_conn_rebooking and isinstance(chain, list) and len(chain) >= 2:
        first_alt = chain[1]
        first_alt_flight_no = str(first_alt.get("original_flight_no") or "").strip()
        first_alt_date = str(first_alt.get("original_date") or "").strip()
        if first_alt_date and first_alt_date.lower() not in ("unknown", ""):
            first_alt_date = first_alt_date[:10]
        # 联程改签：chain 中偶数索引为原始航班，奇数索引为替代航班
        # chain长度>=4 表示有末段替代航班（如 chain[3]）
        if len(chain) >= 4:
            last_alt = chain[3]
            last_alt_flight_no = str(last_alt.get("original_flight_no") or "").strip()
            last_alt_date = str(last_alt.get("original_date") or "").strip()
            if last_alt_date and last_alt_date.lower() not in ("unknown", ""):
                last_alt_date = last_alt_date[:10]

    alt_local = parsed.get("alternate_local") or {}
    alt_fn = str(alt_local.get("alt_flight_no") or "").strip()
    alt_dep_raw = str(alt_local.get("alt_dep") or "").strip()
    alt_dep_date = alt_dep_raw[:10] if alt_dep_raw and alt_dep_raw.lower() not in ("unknown", "") else ""

    alt_results: Dict[str, Any] = {}

    if (
        is_conn_rebooking
        and first_alt_flight_no
        and first_alt_flight_no.lower() not in ("unknown", "null", "")
        and first_alt_date
        and not _is_duplicate_candidate(first_alt_flight_no, cf_candidates)
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
                    LOGGER.info(
                        f"[{forceid}] 联程首班 alt_dep 已覆盖为: {first_actual_dep}",
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
                LOGGER.info(
                    f"[{forceid}] 联程首班 alt_dep 已用 Vision 提取时间兜底: {first_alt_planned_dep}",
                    extra=log_extra(forceid=forceid, stage="fd_first_alt_aviation_lookup", attempt=0),
                )

    # 联程改签末段替代航班查询：获取最终目的地实际到达时间
    if (
        is_conn_rebooking
        and last_alt_flight_no
        and last_alt_flight_no.lower() not in ("unknown", "null", "")
        and last_alt_date
        and not _is_duplicate_candidate(last_alt_flight_no, cf_candidates)
    ):
        try:
            skill = get_flight_lookup_skill()
            last_alt_aviation = await skill.lookup_status(
                flight_no=last_alt_flight_no,
                date=last_alt_date,
                dep_iata=None,
                arr_iata=None,
                session=session,
            )
            alt_results["last_alt_aviation_lookup"] = last_alt_aviation
            if last_alt_aviation.get("success"):
                LOGGER.info(
                    f"[{forceid}] 联程末段替代航班飞常准查询成功: {last_alt_flight_no} {last_alt_date} -> {last_alt_aviation.get('status')}",
                    extra=log_extra(forceid=forceid, stage="fd_last_alt_aviation_lookup", attempt=0),
                )
                last_actual_arr = last_alt_aviation.get("actual_arr")
                if last_actual_arr and not _is_unknown(str(last_actual_arr)):
                    parsed.setdefault("alternate_local", {})["alt_arr"] = str(last_actual_arr)
                    LOGGER.info(
                        f"[{forceid}] 联程末段 alt_arr 已覆盖为: {last_actual_arr}",
                        extra=log_extra(forceid=forceid, stage="fd_last_alt_aviation_lookup", attempt=0),
                    )
        except Exception as _last_ae:
            LOGGER.warning(
                f"[{forceid}] 联程末段替代航班查询失败（降级）: {_last_ae}",
                extra=log_extra(forceid=forceid, stage="fd_last_alt_aviation_lookup", attempt=0),
            )

    # 联程改签末段不变场景：末段航班号不变但时刻可能调整（如 LH2452 08:45→LH2452 10:15），
    # chain 中只有末段信息（无首段），需从 all_flights_found 中找末段航班号并查询飞常准
    if is_conn_rebooking and (
        not last_alt_flight_no or last_alt_flight_no.lower() in ("unknown", "null", "")
    ):
        sched = parsed.get("schedule_local") or {}
        route = parsed.get("route") or {}
        last_seg_dep = (str(sched.get("last_seg_dep_iata") or "").strip().upper()
                        or str(route.get("dep_iata") or "").strip().upper())
        last_seg_arr = (str(sched.get("last_seg_arr_iata") or "").strip().upper()
                        or str(route.get("arr_iata") or "").strip().upper())
        # 从 all_flights_found 中找末段航班号：匹配机场 + 非首段改签航班
        all_flights = (vision_extract or {}).get("all_flights_found") or []
        # 先尝试精确匹配末段机场
        for af in all_flights:
            af_dep = str(af.get("dep_iata") or "").strip().upper()
            af_arr = str(af.get("arr_iata") or "").strip().upper()
            af_role = str(af.get("role_hint") or "")
            # 跳过已确认为首段改签的航班（与 alt_flight_no 相同）
            if alt_fn and str(af.get("flight_no") or "").strip().upper() == alt_fn.strip().upper():
                continue
            if (
                (not last_seg_dep or not af_dep or af_dep == last_seg_dep)
                and (not last_seg_arr or not af_arr or af_arr == last_seg_arr)
            ):
                _last_fn = str(af.get("flight_no") or "").strip()
                _last_date = str(af.get("date") or "").strip()
                if _last_fn and _last_fn.lower() not in ("unknown", "null", "") and _last_date:
                    _last_date = _last_date[:10]
                    if not _is_duplicate_candidate(_last_fn, cf_candidates):
                        try:
                            skill = get_flight_lookup_skill()
                            _last_avi = await skill.lookup_status(
                                flight_no=_last_fn,
                                date=_last_date,
                                dep_iata=af_dep if not _is_unknown(af_dep) else None,
                                arr_iata=af_arr if not _is_unknown(af_arr) else None,
                                session=session,
                            )
                            alt_results["last_seg_aviation_lookup"] = _last_avi
                            if _last_avi.get("success"):
                                LOGGER.info(
                                    f"[{forceid}] 联程末段(不变)飞常准查询成功: {_last_fn} {_last_date} -> {_last_avi.get('status')}",
                                    extra=log_extra(forceid=forceid, stage="fd_last_seg_aviation_lookup", attempt=0),
                                )
                                _last_actual_arr = _last_avi.get("actual_arr")
                                if _last_actual_arr and not _is_unknown(str(_last_actual_arr)):
                                    parsed.setdefault("alternate_local", {})["alt_arr"] = str(_last_actual_arr)
                                _last_actual_dep = _last_avi.get("actual_dep")
                                if _last_actual_dep and not _is_unknown(str(_last_actual_dep)):
                                    parsed.setdefault("alternate_local", {})["alt_dep"] = str(_last_actual_dep)
                                _avi_dep_iata = str(_last_avi.get("dep_iata") or "").strip().upper()
                                _avi_arr_iata = str(_last_avi.get("arr_iata") or "").strip().upper()
                                if not _is_unknown(_avi_dep_iata):
                                    parsed.setdefault("alternate_local", {})["alt_dep_iata"] = _avi_dep_iata
                                if not _is_unknown(_avi_arr_iata):
                                    parsed.setdefault("alternate_local", {})["alt_arr_iata"] = _avi_arr_iata
                        except Exception as _last_seg_ae:
                            LOGGER.warning(
                                f"[{forceid}] 联程末段(不变)查询失败: {_last_seg_ae}",
                                extra=log_extra(forceid=forceid, stage="fd_last_seg_aviation_lookup", attempt=0),
                            )
                break

    if (
        alt_fn and alt_fn.lower() not in ("unknown", "null", "")
        and alt_dep_date
        and not _is_duplicate_candidate(alt_fn, cf_candidates)
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
                alt_status = alt_aviation.get("status")
                if alt_status and not _is_unknown(str(alt_status)):
                    parsed.setdefault("alternate_local", {})["alt_aviation_status"] = str(alt_status)

                # 检测 Vision 将原航班与替代航班颠倒的情况
                # 场景：alt航班飞常准返回"取消" + 主航班返回"已到达"有实际时间
                # 说明 Vision 把实际乘坐的航班当成了"原航班"，把真正取消的原航班当成了"替代航班"
                avi_status_main = str((parsed or {}).get("aviation_status") or "").strip()
                if alt_status == "取消" and avi_status_main == "已到达":
                    sched = parsed.setdefault("schedule_local", {})
                    alt_node = parsed.setdefault("alternate_local", {})
                    # 优先使用飞常准返回的带时区的计划时间，降级使用 Vision 提取的时间
                    alt_avi_planned_dep = alt_aviation.get("planned_dep")
                    alt_avi_planned_arr = alt_aviation.get("planned_arr")
                    alt_planned_dep = (
                        alt_avi_planned_dep if not _is_unknown(str(alt_avi_planned_dep or ""))
                        else alt_node.get("alt_dep")
                    )
                    alt_planned_arr = (
                        alt_avi_planned_arr if not _is_unknown(str(alt_avi_planned_arr or ""))
                        else alt_node.get("alt_arr")
                    )
                    # 将真正原航班（被取消的）的计划时间写回 schedule_local
                    if not _is_unknown(alt_planned_dep):
                        sched["planned_dep"] = alt_planned_dep
                    if not _is_unknown(alt_planned_arr):
                        sched["planned_arr"] = alt_planned_arr
                    # 同步修正时区提示：替代航班（真正原航班）的起降机场时区
                    if not _is_unknown(avi_dep_iata):
                        from app.skills.airport import resolve_country
                        _dep_ap = resolve_country(avi_dep_iata)
                        if _dep_ap.get("found") and str(_dep_ap.get("timezone") or "").lower() != "unknown":
                            sched["dep_timezone_hint"] = str(_dep_ap["timezone"])
                    if not _is_unknown(avi_arr_iata):
                        from app.skills.airport import resolve_country
                        _arr_ap = resolve_country(avi_arr_iata)
                        if _arr_ap.get("found") and str(_arr_ap.get("timezone") or "").lower() != "unknown":
                            sched["arr_timezone_hint"] = str(_arr_ap["timezone"])
                    # 清除错误的 alternate 时间（它们是被取消航班的计划时间，不是实际改签航班）
                    alt_node.pop("alt_dep", None)
                    alt_node.pop("alt_arr", None)
                    # 清除错误的 schedule_revision_chain（Vision 将原航班与替代航班顺序颠倒，
                    # chain[0] 是实际乘坐的航班而非真正原航班，口径1 会用错误数据算出错误延误）
                    parsed.pop("schedule_revision_chain", None)
                    LOGGER.info(
                        f"[{forceid}] 检测到原航班/替代航班颠倒（alt={alt_fn}取消，主航班已到达），"
                        f"已交换计划时间并清除chain，schedule_local.planned_dep={sched.get('planned_dep')}",
                        extra=log_extra(forceid=forceid, stage="fd_alt_aviation_lookup", attempt=0),
                    )

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
                        parsed.setdefault("alternate_local", {})["alt_dep"] = alt_dep_to_fill
                    elif alt_dep_needs_fill:
                        parsed.setdefault("alternate_local", {})["alt_dep"] = alt_dep_to_fill
                        parsed.setdefault("actual_local", {})["actual_dep"] = alt_dep_to_fill
        except Exception as _alt_ae:
            LOGGER.warning(
                f"[{forceid}] 接驳航班查询异常（降级跳过）: {_alt_ae}",
                extra=log_extra(forceid=forceid, stage="fd_alt_aviation_lookup", attempt=0),
            )

    return {"parsed": parsed, "alt_results": alt_results}
