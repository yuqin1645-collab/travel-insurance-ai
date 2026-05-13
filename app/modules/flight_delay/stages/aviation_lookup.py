"""
flight_delay stages — 飞常准航班权威数据查询（并行）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import aiohttp

from app.logging_utils import LOGGER, log_extra
from app.skills.flight_lookup import get_flight_lookup_skill, _expand_flight_number_aliases

from .utils import _is_unknown, _merge_aviation_into_parsed


def _is_duplicate_candidate(flight_no: str, existing_candidates: List) -> bool:
    """检查航班号是否与已有候选重复（含航空公司代码别名）。"""
    aliases = _expand_flight_number_aliases(flight_no)
    existing_upper = [c[0].upper() for c in existing_candidates]
    for a in aliases:
        if a.upper() in existing_upper:
            return True
    return False


async def _query_one_candidate(
    candidate_fn: str,
    candidate_date: str,
    candidate_dep: str,
    candidate_arr: str,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """查询单个候选航班，异常时返回错误结果。"""
    try:
        skill = get_flight_lookup_skill()
        return await skill.lookup_status(
            flight_no=candidate_fn,
            date=candidate_date,
            dep_iata=candidate_dep if candidate_dep and candidate_dep.lower() != "unknown" else None,
            arr_iata=candidate_arr if candidate_arr and candidate_arr.lower() != "unknown" else None,
            session=session,
        )
    except Exception as e:
        return {"success": False, "error": str(e), "_exception": True}


async def lookup_aviation_data(
    *,
    parsed: Dict[str, Any],
    vision_extract: Dict[str, Any],
    forceid: str,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """飞常准航班权威数据查询（并行），返回 aviation_result 等。"""
    all_flights = (vision_extract.get("all_flights_found") or [])
    claim_focus = (parsed.get("claim_focus") or {})
    cf_fn = str(claim_focus.get("flight_no") or "").strip()
    cf_dep = str(claim_focus.get("dep_iata") or "").strip().upper()
    cf_arr = str(claim_focus.get("arr_iata") or "").strip().upper()

    chain = (parsed.get("schedule_revision_chain") or [])
    chain_date = ""
    if chain and isinstance(chain[0], dict):
        chain_dep_raw = str(chain[0].get("planned_dep") or "").strip()
        if chain_dep_raw and chain_dep_raw.lower() not in ("unknown", ""):
            chain_date = chain_dep_raw[:10]

    v_flight_date = str(vision_extract.get("flight_date") or "").strip()
    v_flight_date = v_flight_date[:10] if v_flight_date and v_flight_date.lower() not in ("unknown", "") else ""

    planned_dep_raw = str((parsed.get("schedule_local") or {}).get("planned_dep") or "").strip()
    planned_dep_date = planned_dep_raw[:10] if planned_dep_raw and planned_dep_raw.lower() not in ("unknown", "") else ""

    flight_date = chain_date or v_flight_date or planned_dep_date

    cf_candidates = []
    if cf_fn and cf_fn.lower() not in ("unknown", ""):
        cf_candidates.append((cf_fn, cf_dep, cf_arr, flight_date))

    ticket_fn = str((parsed.get("flight") or {}).get("ticket_flight_no") or "").strip()
    if ticket_fn and ticket_fn.lower() not in ("unknown", "") and not _is_duplicate_candidate(ticket_fn, cf_candidates):
        cf_candidates.append((ticket_fn, "", "", flight_date))

    if len(cf_candidates) < 2 and all_flights and isinstance(all_flights, list):
        for fl in all_flights:
            if isinstance(fl, dict):
                fn = str(fl.get("flight_no") or "").strip()
                dep = str(fl.get("dep_iata") or "").strip().upper()
                arr = str(fl.get("arr_iata") or "").strip().upper()
                dt_raw = str(fl.get("date") or "").strip()
                dt = dt_raw[:10] if dt_raw and dt_raw.lower() not in ("unknown", "") else ""
                if fn and fn.lower() not in ("unknown", "") and not _is_duplicate_candidate(fn, cf_candidates):
                    cf_candidates.append((fn, dep, arr, dt or flight_date))
                    if len(cf_candidates) >= 2:
                        break

    route_dep_iata = str((parsed.get("route") or {}).get("dep_iata") or "").strip().upper()
    route_arr_iata = str((parsed.get("route") or {}).get("arr_iata") or "").strip().upper()

    # 过滤无效候选
    valid_candidates = [(fn, dep, arr, dt) for fn, dep, arr, dt in cf_candidates if fn and dt]

    # 并行查询所有候选
    if valid_candidates:
        tasks = [
            _query_one_candidate(fn, dt, dep, arr, session)
            for fn, dep, arr, dt in valid_candidates
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        raw_results = []

    # 收集结果，保持顺序
    aviation_results_all: List[Dict[str, Any]] = []
    for (candidate_fn, candidate_date, candidate_dep, candidate_arr), raw in zip(valid_candidates, raw_results):
        if isinstance(raw, Exception):
            one_result = {"success": False, "error": str(raw), "_exception": True}
        else:
            one_result = raw
        aviation_results_all.append({
            "candidate": (candidate_fn, candidate_date, candidate_dep, candidate_arr),
            "result": one_result,
        })

    # 按优先级选择：第一个 route_match 的成功结果 > 第一个成功结果
    aviation_result: Dict[str, Any] = {}
    first_success_idx = -1
    route_match_idx = -1

    for i, entry in enumerate(aviation_results_all):
        one_result = entry["result"]
        candidate_fn, candidate_date, candidate_dep, candidate_arr = entry["candidate"]
        if one_result.get("success"):
            if first_success_idx < 0:
                first_success_idx = i
            avi_dep = str(one_result.get("dep_iata") or "").strip().upper()
            avi_arr = str(one_result.get("arr_iata") or "").strip().upper()
            route_match = (
                not route_dep_iata or not route_arr_iata
                or not avi_dep or not avi_arr
                or (avi_dep == route_dep_iata and avi_arr == route_arr_iata)
            )
            LOGGER.info(
                f"[{forceid}] 飞常准查询成功（候选={candidate_fn} {candidate_date}）: -> {one_result.get('status')} [{avi_dep}->{avi_arr}] route_match={route_match}",
                extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
            )
            if route_match and route_match_idx < 0:
                route_match_idx = i
        else:
            LOGGER.info(
                f"[{forceid}] 飞常准候选未返回数据: {candidate_fn} {candidate_date}, error={one_result.get('error', '')}",
                extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
            )

    # 选择最佳结果
    chosen_idx = route_match_idx if route_match_idx >= 0 else first_success_idx
    if chosen_idx >= 0:
        entry = aviation_results_all[chosen_idx]
        one_result = entry["result"]
        candidate_fn, candidate_date, candidate_dep, candidate_arr = entry["candidate"]
        avi_dep = str(one_result.get("dep_iata") or "").strip().upper()
        avi_arr = str(one_result.get("arr_iata") or "").strip().upper()

        parsed = _merge_aviation_into_parsed(parsed, one_result)
        parsed.setdefault("evidence", {})
        if isinstance(parsed["evidence"], dict):
            parsed["evidence"]["aviation_delay_proof"] = True
            parsed["evidence"]["aviation_delay_proof_source"] = f"飞常准: {one_result.get('status','')} {one_result.get('source','')}"

        # 联程场景：飞常准查到了末段航班
        arr_match = route_arr_iata and avi_arr and avi_arr == route_arr_iata
        dep_mismatch = route_dep_iata and avi_dep and avi_dep != route_dep_iata
        if arr_match and dep_mismatch:
            sched_node = parsed.setdefault("schedule_local", {})
            last_planned_arr = one_result.get("planned_arr")
            if last_planned_arr and not _is_unknown(str(last_planned_arr)):
                sched_node["planned_arr"] = str(last_planned_arr)
            last_actual_arr = one_result.get("actual_arr")
            if last_actual_arr and not _is_unknown(str(last_actual_arr)):
                parsed.setdefault("actual_local", {})["actual_arr"] = str(last_actual_arr)
            sched_node["last_seg_dep_iata"] = avi_dep
            sched_node["last_seg_arr_iata"] = avi_arr

        aviation_result = one_result

    # 联程场景：前程/前序航段填充（遍历所有候选，不仅限于被选中的）
    # 条件1：出发机场匹配路线出发，但到达机场不匹配路线到达 → 如PEK→MUC(联程第一程) vs 解析路线PEK→CDG
    # 条件2：到达机场等于路线出发机场 → 如EVE→OSL衔接OSL→LHR路线，前序航段
    for entry in aviation_results_all:
        r = entry["result"]
        if not r.get("success"):
            continue
        cand_fn, cand_date, cand_dep, cand_arr = entry["candidate"]
        avi_dep = str(r.get("dep_iata") or "").strip().upper()
        avi_arr = str(r.get("arr_iata") or "").strip().upper()
        dep_matches_route = route_dep_iata and avi_dep and avi_dep == route_dep_iata
        arr_is_transit = route_arr_iata and avi_arr and avi_arr != route_arr_iata
        arr_matches_route_dep = route_dep_iata and avi_arr and avi_arr == route_dep_iata
        if (dep_matches_route and arr_is_transit) or arr_matches_route_dep:
            seg_entry = {
                "flight_no": r.get("flight_no"),
                "dep_iata": avi_dep,
                "arr_iata": avi_arr,
                "planned_dep": r.get("planned_dep"),
                "planned_arr": r.get("planned_arr"),
                "actual_dep": r.get("actual_dep"),
                "actual_arr": r.get("actual_arr"),
                "status": r.get("status"),
            }
            parsed.setdefault("connecting_segments_data", []).append(seg_entry)
            LOGGER.info(
                f"[{forceid}] 检测到联程前序航段: {cand_fn} [{avi_dep}->{avi_arr}], "
                f"route={route_dep_iata}->{route_arr_iata}",
                extra=log_extra(forceid=forceid, stage="fd_aviation_lookup", attempt=0),
            )

    # 原航班/替代航班颠倒检测（在 merge 之后执行，避免修正值被 _force_fill 覆盖）
    # 当多个候选同路线，一个"已到达"一个"取消"，
    # 且取消航班的计划时间早于已到达航班的计划时间 → Vision 可能把实际乘坐的航班当成了"原航班"
    # 用取消航班的计划时间修正 schedule_local（它才是真正被取消的原航班）
    cancelled_entry = None
    arrived_entry = None
    for entry in aviation_results_all:
        r = entry["result"]
        if not r.get("success"):
            continue
        status = str(r.get("status") or "").strip()
        if status == "取消" and not _is_unknown(str(r.get("planned_dep"))):
            cancelled_entry = entry
        if status == "已到达" and not _is_unknown(str(r.get("actual_dep"))):
            arrived_entry = entry

    if cancelled_entry and arrived_entry:
        cancelled_result = cancelled_entry["result"]
        arrived_result = arrived_entry["result"]
        c_dep_iata = str(cancelled_result.get("dep_iata") or "").strip().upper()
        c_arr_iata = str(cancelled_result.get("arr_iata") or "").strip().upper()
        a_dep_iata = str(arrived_result.get("dep_iata") or "").strip().upper()
        a_arr_iata = str(arrived_result.get("arr_iata") or "").strip().upper()
        # 路线相同才比较
        if c_dep_iata == a_dep_iata and c_arr_iata == a_arr_iata:
            c_planned_dep = cancelled_result.get("planned_dep")
            c_planned_arr = cancelled_result.get("planned_arr")
            sched = parsed.setdefault("schedule_local", {})
            cur_planned_dep = str(sched.get("planned_dep") or "").strip()
            # 当前计划时间比取消航班的计划时间晚 → 说明当前用的是已到达航班的计划时间（错误）
            if c_planned_dep and cur_planned_dep:
                try:
                    from datetime import datetime as _dt
                    c_dt_str = str(c_planned_dep).replace(" ", "T")[:16]
                    cur_dt_str = cur_planned_dep.replace(" ", "T")[:16]
                    c_dt = _dt.fromisoformat(c_dt_str)
                    cur_dt = _dt.fromisoformat(cur_dt_str)
                    if cur_dt > c_dt:
                        # 修正 schedule_local（直接覆盖，merge 已完成不会再次触发）
                        if not _is_unknown(str(c_planned_dep)):
                            sched["planned_dep"] = str(c_planned_dep)
                        if not _is_unknown(str(c_planned_arr)):
                            sched["planned_arr"] = str(c_planned_arr)
                        if not _is_unknown(c_dep_iata):
                            from app.skills.airport import resolve_country as _resolve_ap
                            _dep_ap = _resolve_ap(c_dep_iata)
                            if _dep_ap.get("found") and str(_dep_ap.get("timezone") or "").lower() != "unknown":
                                sched["dep_timezone_hint"] = str(_dep_ap["timezone"])
                        if not _is_unknown(c_arr_iata):
                            from app.skills.airport import resolve_country as _resolve_ap2
                            _arr_ap = _resolve_ap2(c_arr_iata)
                            if _arr_ap.get("found") and str(_arr_ap.get("timezone") or "").lower() != "unknown":
                                sched["arr_timezone_hint"] = str(_arr_ap["timezone"])
                        # 清除错误的 alternate 时间和 chain
                        parsed.get("alternate_local", {}).pop("alt_dep", None)
                        parsed.get("alternate_local", {}).pop("alt_arr", None)
                        parsed.pop("schedule_revision_chain", None)
                        LOGGER.info(
                            f"[{forceid}] 检测到原航班/替代航班颠倒（{arrived_result.get('flight_no')}已到达计划较晚，"
                            f"{cancelled_result.get('flight_no')}取消计划较早），已修正schedule_local.planned_dep={sched.get('planned_dep')}",
                            extra=log_extra(forceid=forceid, stage="fd_aviation_swap_detect", attempt=0),
                        )
                except (ValueError, TypeError):
                    pass

    return {
        "aviation_result": aviation_result,
        "aviation_results_all": aviation_results_all,
        "parsed": parsed,
        "cf_candidates": cf_candidates,
    }
