"""
flight_delay stages — Vision 抽取结果合并到 parsed 数据。
"""

from __future__ import annotations

import re
from typing import Any, Dict

from .utils import _is_unknown, _truthy


def merge_vision_into_parsed(
    parsed: Dict[str, Any],
    vision_extract: Dict[str, Any],
) -> Dict[str, Any]:
    """将 Vision/OCR 抽取结果合并到 AI 解析数据中。"""
    if not vision_extract:
        return parsed

    # 0) 理赔焦点航段识别
    v_claim_focus = vision_extract.get("claim_focus") or {}
    if isinstance(v_claim_focus, dict):
        cf_node = parsed.setdefault("claim_focus", {})
        for k, v in v_claim_focus.items():
            v_str = str(v).strip() if v is not None else ""
            if not _is_unknown(v_str) and _is_unknown(cf_node.get(k)):
                cf_node[k] = v_str

    # 0.5) schedule_revision_chain
    v_chain = vision_extract.get("schedule_revision_chain") or []
    if isinstance(v_chain, list) and v_chain:
        parsed["schedule_revision_chain"] = v_chain
        first_rev = v_chain[0] if v_chain else {}
        if isinstance(first_rev, dict):
            sched_node = parsed.setdefault("schedule_local", {})
            rev_planned_dep = str(first_rev.get("planned_dep") or "").strip()
            rev_planned_arr = str(first_rev.get("planned_arr") or "").strip()
            rev_dep_tz = str(first_rev.get("dep_timezone_hint") or "").strip()
            rev_arr_tz = str(first_rev.get("arr_timezone_hint") or "").strip()
            if "/" in rev_planned_dep:
                rev_planned_dep = rev_planned_dep.split("/")[0].strip()
            if "/" in rev_planned_arr:
                rev_planned_arr = rev_planned_arr.split("/")[0].strip()
            if not _is_unknown(rev_planned_dep) and _is_unknown(sched_node.get("planned_dep")):
                sched_node["planned_dep"] = rev_planned_dep
            if not _is_unknown(rev_planned_arr) and _is_unknown(sched_node.get("planned_arr")):
                sched_node["planned_arr"] = rev_planned_arr
            if not _is_unknown(rev_dep_tz):
                sched_node["dep_timezone_hint"] = rev_dep_tz
            if not _is_unknown(rev_arr_tz):
                sched_node["arr_timezone_hint"] = rev_arr_tz

        # 联程场景：从 itinerary_segments 提取末程机场（用于延误计算的机场匹配）
        # 业务规则（2026-05-11明确）：联程多程只看末程的起飞地和到达地
        v_segments = vision_extract.get("itinerary_segments") or []
        if isinstance(v_segments, list) and len(v_segments) >= 2:
            last_seg = v_segments[-1]
            if isinstance(last_seg, dict):
                last_dep_iata = str(last_seg.get("original_dep_iata") or last_seg.get("dep_iata") or "").strip().upper()
                last_arr_iata = str(last_seg.get("original_arr_iata") or last_seg.get("arr_iata") or "").strip().upper()
                if last_dep_iata and not _is_unknown(last_dep_iata):
                    sched_node.setdefault("last_seg_dep_iata", last_dep_iata)
                if last_arr_iata and not _is_unknown(last_arr_iata):
                    sched_node.setdefault("last_seg_arr_iata", last_arr_iata)

        last_rev = v_chain[-1] if len(v_chain) > 1 else first_rev
        if isinstance(last_rev, dict):
            alt_node = parsed.setdefault("alternate_local", {})
            last_dep = str(last_rev.get("planned_dep") or "").strip()
            last_arr = str(last_rev.get("planned_arr") or "").strip()
            if not _is_unknown(last_dep) and _is_unknown(alt_node.get("alt_dep")):
                alt_node["alt_dep"] = last_dep
            if not _is_unknown(last_arr) and _is_unknown(alt_node.get("alt_arr")):
                alt_node["alt_arr"] = last_arr

    # 0.6) aviation_scheduled
    v_avi_sched = vision_extract.get("aviation_scheduled") or {}
    if isinstance(v_avi_sched, dict):
        avi_node = parsed.setdefault("aviation_scheduled", {})
        for k, v in v_avi_sched.items():
            v_str = str(v).strip() if v is not None else ""
            if not _is_unknown(v_str) and _is_unknown(avi_node.get(k)):
                avi_node[k] = v_str

    # 1) 航班号
    cf_flight = str((parsed.get("claim_focus") or {}).get("flight_no") or "").strip()
    if not _is_unknown(cf_flight):
        flight_node = parsed.setdefault("flight", {})
        flight_node["ticket_flight_no"] = cf_flight
    else:
        v_flight_no = str(vision_extract.get("flight_no") or "").strip()
        if not _is_unknown(v_flight_no):
            flight_node = parsed.setdefault("flight", {})
            if _is_unknown(flight_node.get("ticket_flight_no")):
                flight_node["ticket_flight_no"] = v_flight_no

    # 1.5) claim_focus dep/arr_iata
    cf_dep = str((parsed.get("claim_focus") or {}).get("dep_iata") or "").strip().upper()
    cf_arr = str((parsed.get("claim_focus") or {}).get("arr_iata") or "").strip().upper()
    route_node = parsed.setdefault("route", {})
    if not _is_unknown(cf_dep) and _is_unknown(route_node.get("dep_iata")):
        route_node["dep_iata"] = cf_dep
    if not _is_unknown(cf_arr) and _is_unknown(route_node.get("arr_iata")):
        route_node["arr_iata"] = cf_arr

    # 2) 计划起飞时间
    v_flight_date = str(vision_extract.get("flight_date") or "").strip()
    if not _is_unknown(v_flight_date):
        sched_node = parsed.setdefault("schedule_local", {})
        existing_dep = str(sched_node.get("planned_dep") or "").strip()
        if _is_unknown(existing_dep):
            sched_node["planned_dep"] = v_flight_date

    # 2.5) 机场三字码
    v_dep_iata = str(vision_extract.get("dep_iata") or "").strip().upper()
    v_arr_iata = str(vision_extract.get("arr_iata") or "").strip().upper()
    if not _is_unknown(v_dep_iata) and _is_unknown(route_node.get("dep_iata")):
        route_node["dep_iata"] = v_dep_iata
    if not _is_unknown(v_arr_iata) and _is_unknown(route_node.get("arr_iata")):
        route_node["arr_iata"] = v_arr_iata

    # 3) 替代航班时间
    v_alt = vision_extract.get("alternate") or {}
    if isinstance(v_alt, dict):
        alt_node = parsed.setdefault("alternate_local", {})
        for src_key, dst_key in [("alt_dep", "alt_dep"), ("alt_arr", "alt_arr"),
                                  ("alt_flight_no", "alt_flight_no"), ("alt_source", "alt_source")]:
            v_val = str(v_alt.get(src_key) or "").strip()
            if not _is_unknown(v_val) and _is_unknown(alt_node.get(dst_key)):
                alt_node[dst_key] = v_val

        # 日期一致性校验：若 parse 阶段填了 alt_dep 但日期与 Vision 不同，
        # 且 Vision 的日期不同于原航班日期，则优先采用 Vision（来自登机牌的实际日期）
        v_alt_dep = str(v_alt.get("alt_dep") or "").strip()
        p_alt_dep = str(alt_node.get("alt_dep") or "").strip()
        if not _is_unknown(v_alt_dep) and not _is_unknown(p_alt_dep):
            v_date = v_alt_dep[:10] if len(v_alt_dep) >= 10 else ""
            p_date = p_alt_dep[:10] if len(p_alt_dep) >= 10 else ""
            if v_date and p_date and v_date != p_date:
                sched_node = parsed.get("schedule_local") or {}
                sched_dep = str(sched_node.get("planned_dep") or "")[:10]
                if v_date != sched_dep:
                    alt_node["alt_dep"] = v_alt_dep
                    v_alt_arr = str(v_alt.get("alt_arr") or "").strip()
                    if not _is_unknown(v_alt_arr):
                        alt_node["alt_arr"] = v_alt_arr
        is_conn_booking = _truthy(v_alt.get("is_connecting_rebooking")) is True
        if is_conn_booking:
            v_alt_dep = str(v_alt.get("alt_dep") or "").strip()
            if not _is_unknown(v_alt_dep) and not _is_unknown(alt_node.get("alt_dep")):
                alt_node["alt_dep"] = v_alt_dep
        if _truthy(v_alt.get("is_connecting_missed")) is True:
            itin_node = parsed.setdefault("itinerary", {})
            itin_node["is_connecting_or_transit"] = "true"
            itin_node["mentions_missed_connection"] = "true"
        if is_conn_booking:
            # 校验：itinerary_segments 只有1段且替代航班与原航班同路线时，
            # 不标记联程改签（Vision 可能误判，如携程APP变动截图含后续行程段）
            v_segments = vision_extract.get("itinerary_segments") or []
            orig_dep = str(vision_extract.get("dep_iata") or "").strip().upper()
            orig_arr = str(vision_extract.get("arr_iata") or "").strip().upper()
            v_alt_dep_iata = str(v_alt.get("dep_iata") or "").strip().upper()
            v_alt_arr_iata = str(v_alt.get("arr_iata") or "").strip().upper()
            same_route = (
                not _is_unknown(v_alt_dep_iata) and not _is_unknown(v_alt_arr_iata)
                and not _is_unknown(orig_dep) and not _is_unknown(orig_arr)
                and v_alt_dep_iata == orig_dep and v_alt_arr_iata == orig_arr
            )
            single_segment = isinstance(v_segments, list) and len(v_segments) <= 1
            is_conn_booking_validated = is_conn_booking and not (same_route or single_segment)
            if is_conn_booking_validated:
                itin_node = parsed.setdefault("itinerary", {})
                itin_node["is_connecting_or_transit"] = "true"
                itin_node["is_connecting_rebooking"] = "true"

    # 4) evidence
    v_evidence = vision_extract.get("evidence") or {}
    if isinstance(v_evidence, dict):
        ev_node = parsed.setdefault("evidence", {})
        for k, v in v_evidence.items():
            v_str = str(v).strip() if v is not None else ""
            if not _is_unknown(v_str) and _is_unknown(ev_node.get(k)):
                ev_node[k] = v

    # 5) delay_proof_reason_text
    reason_text = str(v_evidence.get("delay_proof_reason_text") or "").strip()
    if not _is_unknown(reason_text):
        if _is_unknown(parsed.get("delay_reason")):
            parsed["delay_reason"] = reason_text
        if _is_unknown(parsed.get("delay_reason_is_external")):
            _INTERNAL_KEYWORDS = ["公司原因", "商业原因", "运力调整", "计划取消", "company reason"]
            is_internal = any(kw in reason_text.lower() for kw in _INTERNAL_KEYWORDS)
            parsed["delay_reason_is_external"] = "false" if is_internal else "true"

    # 6) delay_proof_planned_dep / delay_proof_actual_dep
    proof_planned = str(v_evidence.get("delay_proof_planned_dep") or "").strip()
    proof_actual = str(v_evidence.get("delay_proof_actual_dep") or "").strip()
    if not _is_unknown(proof_planned):
        sched_node = parsed.setdefault("schedule_local", {})
        if _is_unknown(sched_node.get("planned_dep")):
            sched_node["planned_dep"] = proof_planned
    if not _is_unknown(proof_actual):
        actual_node = parsed.setdefault("actual_local", {})
        if _is_unknown(actual_node.get("actual_dep")):
            actual_node["actual_dep"] = proof_actual

    # 6.5) delay_proof_planned_arr / delay_proof_actual_arr
    proof_planned_arr = str(v_evidence.get("delay_proof_planned_arr") or "").strip()
    proof_actual_arr = str(v_evidence.get("delay_proof_actual_arr") or "").strip()
    if not _is_unknown(proof_planned_arr):
        sched_node = parsed.setdefault("schedule_local", {})
        if _is_unknown(sched_node.get("planned_arr")):
            sched_node["planned_arr"] = proof_planned_arr
    if not _is_unknown(proof_actual_arr):
        actual_node = parsed.setdefault("actual_local", {})
        if _is_unknown(actual_node.get("actual_arr")):
            actual_node["actual_arr"] = proof_actual_arr

    # 7) boarding_pass_actual_dep — 被保险人实际乘坐的航班（业务规则 2026-05-11 明确）
    # 多次改签场景下，以登机牌上提取的航班为准（旅客实际乘坐的），
    # 而非 schedule_revision_chain 的最后一个元素。
    bp_actual = str(v_evidence.get("boarding_pass_actual_dep") or "").strip()
    bp_actual_arr = str(v_evidence.get("boarding_pass_actual_arr") or "").strip()
    bp_flight_no = str(v_evidence.get("boarding_pass_flight_no") or "").strip()
    if not _is_unknown(bp_actual):
        v_alt_fn = str(v_alt.get("alt_flight_no") or "").strip()
        has_alt_flight = not _is_unknown(v_alt_fn)
        has_chain = isinstance(v_chain, list) and len(v_chain) > 0
        avi_status = str(parsed.get("aviation_status") or "").strip()
        is_cancelled = avi_status in ("取消", "cancelled", "CANCELLED")
        is_rebooking = has_alt_flight or has_chain or is_cancelled
        if is_rebooking:
            # 改签场景：登机牌数据作为实际乘坐航班的权威来源，
            # 覆盖之前从 chain 中填充的 alternate_local 值
            alt_node = parsed.setdefault("alternate_local", {})
            alt_node["alt_dep"] = bp_actual
            if not _is_unknown(bp_actual_arr):
                alt_node["alt_arr"] = bp_actual_arr
            if not _is_unknown(bp_flight_no):
                alt_node["alt_flight_no"] = bp_flight_no
        else:
            actual_node = parsed.setdefault("actual_local", {})
            if _is_unknown(actual_node.get("actual_dep")):
                actual_node["actual_dep"] = bp_actual

    return parsed
