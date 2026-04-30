"""
flight_delay stages — 延误时长计算（_compute_delay_minutes）及兜底增强。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.skills.airport import resolve_country

from .utils import (
    _is_unknown,
    _truthy,
    _parse_utc_dt,
    _parse_local_dt,
    _parse_local_dt_iana,
    _parse_tz_offset,
    _has_timezone,
    _parse_threshold_minutes,
    _extract_delay_minutes_from_text,
)


def _sanitize_date(s: str) -> str:
    """'2026-02-22 20:05/unknown' → '2026-02-22 20:05'"""
    if s and isinstance(s, str) and s.endswith("/unknown"):
        return s[: -len("/unknown")]
    return s


def _compute_delay_minutes(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """按规则"取长原则"计算延误分钟数。"""
    utc = (parsed or {}).get("utc") or {}
    schedule_local = (parsed or {}).get("schedule_local") or {}
    alternate_local = (parsed or {}).get("alternate_local") or {}
    actual_local = (parsed or {}).get("actual_local") or {}

    _route = (parsed or {}).get("route") or {}
    _dep_iata = str(_route.get("dep_iata") or "").strip().upper()
    _arr_iata = str(_route.get("arr_iata") or "").strip().upper()

    def _resolve_iana(iata: str) -> Optional[str]:
        if not iata or iata in ("UNKNOWN", "NULL", "NONE", ""):
            return None
        ap = resolve_country(iata)
        if ap.get("found") and str(ap.get("timezone") or "").lower() != "unknown":
            return str(ap["timezone"])
        return None

    _dep_iana = _resolve_iana(_dep_iata)
    _arr_iana = _resolve_iana(_arr_iata)

    chain = (parsed or {}).get("schedule_revision_chain") or []
    chain0 = chain[0] if chain and isinstance(chain[0], dict) else {}
    chain0_dep = _sanitize_date(str(chain0.get("planned_dep") or "").strip())
    chain0_arr = _sanitize_date(str(chain0.get("planned_arr") or "").strip())
    chain0_dep_tz = str(chain0.get("dep_timezone_hint") or "").strip()
    chain0_arr_tz = str(chain0.get("arr_timezone_hint") or "").strip()

    def _try_parse_utc(value: Any) -> Optional[datetime]:
        if not value or _is_unknown(str(value)):
            return None
        s = str(value).strip()
        if "/" in s:
            s = s.split("/")[0].strip()
        if not s or s.lower() == "unknown":
            return None
        return _parse_utc_dt(s)

    def _try_parse_local(
        value: Any, tz_hint: Optional[str], fallback_iana: Optional[str]
    ) -> Optional[datetime]:
        if not value or _is_unknown(str(value)):
            return None
        if fallback_iana:
            r = _parse_local_dt_iana(str(value), fallback_iana)
            if r:
                return r
        if tz_hint:
            tz = _parse_tz_offset(tz_hint)
            if tz:
                s = str(value).strip()
                if not _has_timezone(s):
                    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                        try:
                            dt = datetime.strptime(s[:19], fmt)
                            return dt.replace(tzinfo=tz).astimezone(timezone.utc)
                        except Exception:
                            continue
        return None

    first_planned_dep_utc = (
        _try_parse_utc(chain0_dep)
        or _try_parse_local(chain0_dep, chain0_dep_tz, _dep_iana)
    )
    first_planned_arr_utc = (
        _try_parse_utc(chain0_arr)
        or _try_parse_local(chain0_arr, chain0_arr_tz, _arr_iana)
    )

    sched_planned_dep = _sanitize_date(str(schedule_local.get("planned_dep") or "").strip())
    sched_planned_arr = _sanitize_date(str(schedule_local.get("planned_arr") or "").strip())
    sched_dep_tz = str(schedule_local.get("dep_timezone_hint") or "").strip()
    sched_arr_tz = str(schedule_local.get("arr_timezone_hint") or "").strip()

    sched_planned_dep_utc = (
        _try_parse_utc(sched_planned_dep)
        or _try_parse_local(sched_planned_dep, sched_dep_tz, _dep_iana)
    )
    sched_planned_arr_utc = (
        _try_parse_utc(sched_planned_arr)
        or _try_parse_local(sched_planned_arr, sched_arr_tz, _arr_iana)
    )

    alt_dep_raw = _sanitize_date(str(alternate_local.get("alt_dep") or "").strip())
    alt_arr_raw = _sanitize_date(str(alternate_local.get("alt_arr") or "").strip())
    alt_dep_utc = (
        _try_parse_utc(alt_dep_raw)
        or _try_parse_local(alt_dep_raw, chain0_dep_tz, _dep_iana)
    )
    alt_arr_utc = (
        _try_parse_utc(alt_arr_raw)
        or _try_parse_local(alt_arr_raw, chain0_arr_tz, _arr_iana)
    )

    actual_dep_raw = _sanitize_date(str(actual_local.get("actual_dep") or "").strip())
    actual_arr_raw = _sanitize_date(str(actual_local.get("actual_arr") or "").strip())
    actual_dep_utc = (
        _try_parse_utc(actual_dep_raw)
        or _parse_local_dt_iana(actual_dep_raw, _dep_iana)
    )
    actual_arr_utc = (
        _try_parse_utc(actual_arr_raw)
        or _parse_local_dt_iana(actual_arr_raw, _arr_iana)
    )

    a: Optional[int] = None
    b: Optional[int] = None
    missing: List[str] = []
    method = "unknown"

    # 口径1：有 chain → 旅客首版计划 → 飞常准实际
    # 联程/中转场景下 chain[0] 为首段航班，actual_local 为理赔焦点航班，
    # 两者描述不同航班，直接比较无意义，跳过口径1
    itinerary = (parsed or {}).get("itinerary") or {}
    is_connecting = _truthy(itinerary.get("is_connecting_or_transit"))
    if chain and not is_connecting:
        if first_planned_dep_utc and actual_dep_utc:
            delta = int((actual_dep_utc - first_planned_dep_utc).total_seconds() // 60)
            if delta >= 0:
                a = delta
        if first_planned_arr_utc and actual_arr_utc:
            delta = int((actual_arr_utc - first_planned_arr_utc).total_seconds() // 60)
            if delta >= 0:
                b = delta
        candidates_chain = [m for m in [a, b] if isinstance(m, int)]
        if candidates_chain:
            final_minutes = max(candidates_chain)
            method = f"旅客首版计划→飞常准实际（起飞{a or '?'}分/到达{b or '?'}分）"
            return {
                "a_minutes": a, "b_minutes": b,
                "final_minutes": final_minutes, "method": method, "missing": missing,
                "planned_dep_utc": first_planned_dep_utc.isoformat() if first_planned_dep_utc else None,
                "planned_arr_utc": first_planned_arr_utc.isoformat() if first_planned_arr_utc else None,
                "actual_dep_utc": actual_dep_utc.isoformat() if actual_dep_utc else None,
                "actual_arr_utc": actual_arr_utc.isoformat() if actual_arr_utc else None,
                "alt_dep_utc": alt_dep_utc.isoformat() if alt_dep_utc else None,
                "alt_arr_utc": alt_arr_utc.isoformat() if alt_arr_utc else None,
                "first_planned_dep_utc": first_planned_dep_utc.isoformat() if first_planned_dep_utc else None,
                "first_planned_arr_utc": first_planned_arr_utc.isoformat() if first_planned_arr_utc else None,
                "source": "schedule_revision_chain[0] → actual_local",
            }

    # 口径2：计划 → 替代航班 alt
    # 机场匹配：只计算原航班与替代航班在同一机场（出发/到达）对应的延误分量
    # alt_dep_iata/alt_arr_iata 由 pipeline stage1.4 从飞常准结果写入 alternate_local
    # 联程场景：用末段机场（last_seg_dep/arr_iata）而非首程的 route.dep/arr_iata 做匹配
    c: Optional[int] = None
    d: Optional[int] = None
    last_seg_dep_iata = str((schedule_local.get("last_seg_dep_iata") or "")).strip().upper()
    last_seg_arr_iata = str((schedule_local.get("last_seg_arr_iata") or "")).strip().upper()
    orig_dep_iata = last_seg_dep_iata or str(_dep_iata or "").upper()
    orig_arr_iata = last_seg_arr_iata or str(_arr_iata or "").upper()
    alt_dep_iata = str((alternate_local.get("alt_dep_iata") or "")).strip().upper()
    alt_arr_iata = str((alternate_local.get("alt_arr_iata") or "")).strip().upper()
    # 若无飞常准机场数据则视为匹配（兼容旧数据）
    dep_iata_match = (
        not alt_dep_iata or not orig_dep_iata
        or alt_dep_iata == orig_dep_iata
    )
    arr_iata_match = (
        not alt_arr_iata or not orig_arr_iata
        or alt_arr_iata == orig_arr_iata
    )
    if sched_planned_dep_utc and alt_dep_utc and dep_iata_match:
        delta = int((alt_dep_utc - sched_planned_dep_utc).total_seconds() // 60)
        if delta >= 0:
            c = delta
    else:
        if not sched_planned_dep_utc:
            missing.append("planned_dep(需可换算时区)")
        if not alt_dep_utc:
            missing.append("alt_dep(改签后实际起飞时间/需可换算时区)")

    if sched_planned_arr_utc and alt_arr_utc and arr_iata_match:
        delta = int((alt_arr_utc - sched_planned_arr_utc).total_seconds() // 60)
        if delta >= 0:
            d = delta
    else:
        if not sched_planned_arr_utc:
            missing.append("planned_arr(需可换算时区)")
        if not alt_arr_utc:
            missing.append("alt_arr(替代抵达原目的地时间/需可换算时区)")

    candidates_alt = [m for m in [c, d] if isinstance(m, int)]
    final_alt = max(candidates_alt) if candidates_alt else None

    # 口径3：计划 → 飞常准实际（兜底）
    e: Optional[int] = None
    f: Optional[int] = None
    if sched_planned_dep_utc and actual_dep_utc:
        delta = int((actual_dep_utc - sched_planned_dep_utc).total_seconds() // 60)
        if delta >= 0:
            e = delta
    if sched_planned_arr_utc and actual_arr_utc:
        delta = int((actual_arr_utc - sched_planned_arr_utc).total_seconds() // 60)
        if delta >= 0:
            f = delta

    candidates_actual = [m for m in [e, f] if isinstance(m, int)]
    final_actual = max(candidates_actual) if candidates_actual else None

    # 联程改签场景特殊处理
    is_conn_rebooking = _truthy(itinerary.get("is_connecting_rebooking")) is True
    connecting_rebooking_suspicion = (
        is_connecting
        and (
            is_conn_rebooking
            or (
                isinstance(c, int) and isinstance(d, int) and d > 0
                and c > d * 1.2
            )
        )
    )

    # 联程改签延误计算规则：
    # 原航班取消后改签为联程航班（如 KL1175+SK4172），
    # 延误 = max(联程首段实际起飞 - 原航班计划起飞, 联程末段实际到达 - 原航班计划到达)
    # alt_dep 已在 pipeline 中被覆盖为联程首段起飞时间，alt_arr 为末段到达时间
    # 所以口径2 (c, d) 已经是正确的联程延误值，直接取 max(c, d)
    if is_conn_rebooking or connecting_rebooking_suspicion:
        conn_candidates = [m for m in [c, d] if isinstance(m, int)]
        if conn_candidates:
            final_minutes = max(conn_candidates)
            method = f"联程改签-取max(起飞延误{c}分,到达延误{d}分): 联程首段起飞vs原计划起飞, 联程末段到达vs原计划到达"
        else:
            final_minutes = None
            method = "联程改签-无法计算：缺少改签航班实际时间数据"
    elif final_alt is not None and final_actual is not None:
        final_minutes = max(final_alt, final_actual)
        method = f"取长: alt(起飞{c}分/到达{d}分) vs 实际(起飞{e}分/到达{f}分)"
    elif final_alt is not None:
        final_minutes = final_alt
        method = f"alt口径(起飞{c}分/到达{d}分)"
    elif final_actual is not None:
        final_minutes = final_actual
        method = f"飞常准实际口径(起飞{e}分/到达{f}分)"
    else:
        final_minutes = None
        method = "无法计算：缺少时间数据"

    return {
        "a_minutes": a, "b_minutes": b,
        "final_minutes": final_minutes, "method": method, "missing": missing,
        "planned_dep_utc": sched_planned_dep_utc.isoformat() if sched_planned_dep_utc else None,
        "planned_arr_utc": sched_planned_arr_utc.isoformat() if sched_planned_arr_utc else None,
        "actual_dep_utc": actual_dep_utc.isoformat() if actual_dep_utc else None,
        "actual_arr_utc": actual_arr_utc.isoformat() if actual_arr_utc else None,
        "alt_dep_utc": alt_dep_utc.isoformat() if alt_dep_utc else None,
        "alt_arr_utc": alt_arr_utc.isoformat() if alt_arr_utc else None,
        "first_planned_dep_utc": first_planned_dep_utc.isoformat() if first_planned_dep_utc else None,
        "first_planned_arr_utc": first_planned_arr_utc.isoformat() if first_planned_arr_utc else None,
        "source": "computed",
    }


def _augment_with_computed_delay(
    *,
    parsed: Dict[str, Any],
    policy_terms_excerpt: str,
    free_text: str = "",
) -> Dict[str, Any]:
    """为 parsed 增加 computed_delay 信息。"""
    parsed = dict(parsed or {})
    computed = _compute_delay_minutes(parsed)
    threshold_minutes = _parse_threshold_minutes(policy_terms_excerpt) or 5 * 60

    if computed.get("final_minutes") is None and free_text:
        text_minutes = _extract_delay_minutes_from_text(free_text)
        if text_minutes is not None:
            computed["final_minutes"] = text_minutes
            computed["method"] = f"文本提取兜底: 从案件描述提取到{text_minutes}分钟"
            computed["source"] = "text_fallback"

    computed["threshold_minutes"] = threshold_minutes
    computed["threshold_source"] = "policy_terms_excerpt" if _parse_threshold_minutes(policy_terms_excerpt) else "default(5h)"
    computed["threshold_met"] = (
        isinstance(computed.get("final_minutes"), int) and computed["final_minutes"] >= threshold_minutes
    )
    parsed["computed_delay"] = computed
    return parsed
