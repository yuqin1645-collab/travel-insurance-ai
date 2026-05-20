"""
baggage_delay stages — 官方航班数据查询与联程/改签修正。
从 pipeline.py 提取，保持原逻辑不变。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict

import aiohttp

from app.skills.flight_lookup import get_flight_lookup_skill

from .utils import _extract_date_yyyy_mm_dd

_FLIGHT_NO_PATTERN = re.compile(r'^[A-Za-z]{2}\d{1,4}$')

_BAGGAGE_FORWARDING_HINTS = [
    "行李装载", "行李装在", "行李装在今日", "行李转运", "行李已装载",
    "行李将搭乘", "行李运抵", "行李托运回", "行李送达", "行李将运",
    "您的行李将", "行李会搭乘", "行李后续",
    "baggage loaded", "baggage forwarded", "baggage will arrive",
    "baggage will travel", "luggage will arrive", "luggage will be",
    "baggage will be forwarded", "baggage will be delivered",
]


async def run_aviation_lookup(
    ai_parsed: Dict[str, Any],
    vision_extract: Dict[str, Any],
    claim_info: Dict[str, Any],
    debug: Dict[str, Any],
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """官方航班数据补强查询。返回 aviation_lookup dict。

    会直接修改 ai_parsed（同步 flight_no 等修正结果）。
    """
    aviation_lookup: Dict[str, Any] = {}
    if not isinstance(ai_parsed, dict):
        debug["arrival_source"] = "no_parsed_data"
        return aviation_lookup

    flight_no = str(ai_parsed.get("flight_no") or "").strip()
    dep_iata = str(ai_parsed.get("dep_iata") or "").strip().upper()
    arr_iata = str(ai_parsed.get("arr_iata") or "").strip().upper()
    flight_date = (
        _extract_date_yyyy_mm_dd(ai_parsed.get("flight_date"))
        or _extract_date_yyyy_mm_dd(claim_info.get("Date_of_Accident"))
    )

    _valid_flight_no = bool(_FLIGHT_NO_PATTERN.match(flight_no)) if flight_no else False

    # 第一步：从 all_flights_found 中预先识别行李转运航班号
    _baggage_forwarding_nos: set = set()
    for _src in (ai_parsed, vision_extract):
        for _fl in (_src.get("all_flights_found") or []):
            _fn = str(_fl.get("flight_no") or "").strip()
            if not _fn or not _FLIGHT_NO_PATTERN.match(_fn):
                continue
            _role = str(_fl.get("role_hint") or "").strip()
            _src_field = str(_fl.get("source") or "").strip()
            if any(h in _role or h in _src_field for h in _BAGGAGE_FORWARDING_HINTS):
                _baggage_forwarding_nos.add(_fn.upper())

    # 第二步：若主 flight_no 命中行李转运航班，拒绝并标记
    if _valid_flight_no and flight_no.upper() in _baggage_forwarding_nos:
        debug["flight_no_baggage_forwarding_rejected"] = (
            f"{flight_no} 为行李转运航班（非乘客航班），拒绝作为主航班查询，从 all_flights_found 回退"
        )
        _valid_flight_no = False

    # P1 修复：联程航班场景
    _itinerary_segments = (ai_parsed.get("itinerary_segments") or vision_extract.get("itinerary_segments") or [])
    if _valid_flight_no and len(_itinerary_segments) > 1:
        _alternate = ai_parsed.get("alternate") or vision_extract.get("alternate") or {}
        _is_rebooking = str(_alternate.get("is_connecting_rebooking") or "").strip().lower() == "true"
        if _is_rebooking:
            _alt_fn = str(_alternate.get("alt_flight_no") or "").strip().upper()
            _alt_date = _extract_date_yyyy_mm_dd(_alternate.get("alt_dep") or _alternate.get("alt_arr"))
            if _alt_fn and _FLIGHT_NO_PATTERN.match(_alt_fn) and _alt_fn != flight_no.upper():
                debug["flight_no_corrected_for_connecting"] = (
                    f"改签场景，目标航班从 {flight_no} 修正为改签航班 {_alt_fn}（{_alt_date}）"
                )
                flight_no = _alt_fn
                flight_date = _alt_date or flight_date
                _valid_flight_no = True
        else:
            _accident_date = _extract_date_yyyy_mm_dd(claim_info.get("Date_of_Accident"))
            _outbound_segments = []
            for _seg in _itinerary_segments:
                if not isinstance(_seg, dict):
                    continue
                _seg_date = _extract_date_yyyy_mm_dd(_seg.get("original_date"))
                if _seg_date and _accident_date:
                    try:
                        from datetime import datetime as _dt
                        _diff = abs((_dt.strptime(_seg_date, "%Y-%m-%d") - _dt.strptime(_accident_date, "%Y-%m-%d")).days)
                        if _diff <= 3:
                            _outbound_segments.append(_seg)
                    except ValueError:
                        _outbound_segments.append(_seg)
                else:
                    _outbound_segments.append(_seg)

            if len(_outbound_segments) > 1:
                _last_segment = None
                for _seg in _outbound_segments:
                    _seg_no = _seg.get("segment_no") or 0
                    if _last_segment is None or _seg_no > _last_segment.get("segment_no", 0):
                        _last_segment = _seg
                if _last_segment and isinstance(_last_segment, dict):
                    _last_fn = str(_last_segment.get("original_flight_no") or "").strip().upper()
                    _last_date = _extract_date_yyyy_mm_dd(_last_segment.get("original_date")) or flight_date
                    _last_arr = ""
                    for _fl in (ai_parsed.get("all_flights_found") or vision_extract.get("all_flights_found") or []):
                        if isinstance(_fl, dict) and str(_fl.get("flight_no") or "").strip().upper() == _last_fn:
                            _last_arr = str(_fl.get("arr_iata") or "").strip().upper()
                            break
                    if _last_fn and _last_fn != flight_no.upper():
                        debug["flight_no_corrected_for_connecting"] = (
                            f"联程航班 {len(_outbound_segments)} 段（去程），目标航班从 {flight_no} 修正为末程 {_last_fn}"
                        )
                        flight_no = _last_fn
                        flight_date = _last_date
                        arr_iata = _last_arr
                        _valid_flight_no = bool(_FLIGHT_NO_PATTERN.match(flight_no)) if flight_no else False

    # 第三步：收集 all_flights_found 中的候选航班号（去重保序，过滤行李转运）
    _candidates: list = []
    _seen = set()
    if _valid_flight_no:
        _candidates.append((flight_no, flight_date, dep_iata, arr_iata))
        _seen.add(flight_no.upper())
    for _src in (ai_parsed, vision_extract):
        for _fl in (_src.get("all_flights_found") or []):
            _fn = str(_fl.get("flight_no") or "").strip()
            if not _fn or not _FLIGHT_NO_PATTERN.match(_fn) or _fn.upper() in _seen:
                continue
            if _fn.upper() in _baggage_forwarding_nos:
                debug.setdefault("baggage_forwarding_filtered", []).append(
                    f"{_fn}（role_hint={_fl.get('role_hint')}, source={_fl.get('source')}）")
                continue
            _fd = _extract_date_yyyy_mm_dd(_fl.get("date")) or flight_date
            _dep = str(_fl.get("dep_iata") or "").strip().upper()
            _arr = str(_fl.get("arr_iata") or "").strip().upper()
            _candidates.append((_fn, _fd, _dep if _dep != "UNKNOWN" else "", _arr if _arr != "UNKNOWN" else ""))
            _seen.add(_fn.upper())

    if not _valid_flight_no and _candidates:
        _best_idx = 0
        for _i, (_fn, _fd, _dep, _arr) in enumerate(_candidates):
            if _fd and _candidates[_best_idx][1] and _fd > _candidates[_best_idx][1]:
                _best_idx = _i
        debug["flight_no_corrected"] = f"{flight_no} -> {_candidates[_best_idx][0]}（从 all_flights_found 回退，优先末段航班）"
        flight_no = _candidates[_best_idx][0]
        flight_date = _candidates[_best_idx][1]
        dep_iata = _candidates[_best_idx][2]
        arr_iata = _candidates[_best_idx][3]
        ai_parsed["flight_no"] = flight_no
        if flight_date:
            ai_parsed["flight_date"] = flight_date

    _target_flight_no = flight_no.upper() if flight_no else None

    _valid_candidates = [(_fn, _fd, _dep, _arr) for _fn, _fd, _dep, _arr in _candidates[:5] if _fn and _fd]
    if _valid_candidates:
        skill = get_flight_lookup_skill()
        tasks = [
            skill.lookup_status(
                flight_no=_fn,
                date=_fd,
                dep_iata=_dep if _dep and _dep != "UNKNOWN" else None,
                arr_iata=_arr if _arr and _arr != "UNKNOWN" else None,
                session=session,
            )
            for _fn, _fd, _dep, _arr in _valid_candidates
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        _target_result = None
        _fallback_result = None
        for (_fn, _fd, _dep, _arr), raw in zip(_valid_candidates, raw_results):
            if isinstance(raw, Exception):
                one_result = {"success": False, "error": str(raw), "_exception": True}
            else:
                one_result = raw
            if one_result.get("success") and one_result.get("actual_arr"):
                if _fn.upper() == _target_flight_no:
                    _target_result = (_fn, one_result)
                    break
                elif _fallback_result is None:
                    _fallback_result = (_fn, one_result)
            else:
                debug.setdefault("aviation_candidates_tried", []).append(
                    {"flight_no": _fn, "date": _fd, "success": False,
                     "error": str(one_result.get("error") or "")[:80]}
                )
        _chosen = _target_result or _fallback_result
        if _chosen:
            _fn_chosen, _result_chosen = _chosen
            ai_parsed["flight_actual_arrival_time"] = _result_chosen["actual_arr"]
            debug["arrival_source"] = "variflight_actual_arr"
            aviation_lookup = _result_chosen
            if _target_result is None and _fallback_result is not None:
                debug["aviation_used_fallback"] = f"目标航班{_target_flight_no}未查到，使用{_fallback_result[0]}"

    if not aviation_lookup.get("success"):
        debug["arrival_source"] = "material_or_llm_fallback"

    return aviation_lookup