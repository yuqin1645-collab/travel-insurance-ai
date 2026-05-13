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

# 模块级常量
FLIGHT_DELAY_DEFAULT_THRESHOLD_MINUTES = 300  # 5小时
CONNECTING_REBOOKING_RATIO = 1.2  # 联程改签判定系数：起飞延误/到达延误 > 此值视为联程改签


def _sanitize_date(s: str) -> str:
    """'2026-02-22 20:05/unknown' → '2026-02-22 20:05'；含 HH:MM 占位符 → 返回空字符串"""
    if s and isinstance(s, str):
        if "HH:MM" in s:
            return ""
        if s.endswith("/unknown"):
            return s[: -len("/unknown")]
    return s


def _resolve_iana(iata: str) -> Optional[str]:
    """根据 IATA 机场代码解析对应时区（IANA 格式）。"""
    if not iata or iata in ("UNKNOWN", "NULL", "NONE", ""):
        return None
    ap = resolve_country(iata)
    if ap.get("found") and str(ap.get("timezone") or "").lower() != "unknown":
        return str(ap["timezone"])
    return None


def _try_parse_utc(value: Any) -> Optional[datetime]:
    """尝试将值解析为 UTC 时间。"""
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
    """尝试将值解析为本地时间并转换为 UTC。"""
    if not value or _is_unknown(str(value)):
        return None
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
    if fallback_iana:
        r = _parse_local_dt_iana(str(value), fallback_iana)
        if r:
            return r
    return None


def _compute_delay_minutes(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """按规则"取长原则"计算延误分钟数。"""
    utc = (parsed or {}).get("utc") or {}
    schedule_local = (parsed or {}).get("schedule_local") or {}
    alternate_local = (parsed or {}).get("alternate_local") or {}
    actual_local = (parsed or {}).get("actual_local") or {}

    _route = (parsed or {}).get("route") or {}
    _dep_iata = str(_route.get("dep_iata") or "").strip().upper()
    _arr_iata = str(_route.get("arr_iata") or "").strip().upper()

    _dep_iana = _resolve_iana(_dep_iata)
    _arr_iana = _resolve_iana(_arr_iata)

    chain = (parsed or {}).get("schedule_revision_chain") or []
    chain0 = chain[0] if chain and isinstance(chain[0], dict) else {}
    chain0_dep = _sanitize_date(str(chain0.get("planned_dep") or "").strip())
    chain0_arr = _sanitize_date(str(chain0.get("planned_arr") or "").strip())
    chain0_dep_tz = str(chain0.get("dep_timezone_hint") or "").strip()
    chain0_arr_tz = str(chain0.get("arr_timezone_hint") or "").strip()

    missing: List[str] = []

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

    # 基准时间选择：当存在 schedule_revision_chain 时，口径2/3 必须用 chain[0]
    # 的原始计划时间（旅客购票时的计划），而非 aviation_scheduled（可能已被航司多次调整）。
    # 场景：原航班 07:30 取消 → 航司调整 schedule 为 08:28 → 改签 MU553 23:30
    # 若用 aviation_scheduled=08:28 为基准，延误=902分钟（偏低）
    # 若用 chain[0]=07:30 为基准，延误=960分钟（正确）
    baseline_dep_utc = first_planned_dep_utc if first_planned_dep_utc else sched_planned_dep_utc
    baseline_arr_utc = first_planned_arr_utc if first_planned_arr_utc else sched_planned_arr_utc

    alt_dep_raw = _sanitize_date(str(alternate_local.get("alt_dep") or "").strip())
    alt_arr_raw = _sanitize_date(str(alternate_local.get("alt_arr") or "").strip())

    # 构建 alt 时间解析的时区 fallback 链（按优先级）：
    # 1) chain0 时区提示  2) schedule_local 时区提示  3) alternate_local 自身时区提示
    # 4) alt 出发/到达机场 IATA→IANA  5) route 出发/到达机场 IATA→IANA
    _alt_dep_tz_chain: List[tuple] = []
    _alt_arr_tz_chain: List[tuple] = []
    for _tz_src, _iana_src in [
        (chain0_dep_tz, _dep_iana),
        (sched_dep_tz, _dep_iana),
        (str(alternate_local.get("timezone_hint") or "").strip(), _dep_iana),
    ]:
        if _tz_src and not _is_unknown(_tz_src):
            _alt_dep_tz_chain.append((_tz_src, _iana_src))
    for _tz_src, _iana_src in [
        (chain0_arr_tz, _arr_iana),
        (sched_arr_tz, _arr_iana),
        (str(alternate_local.get("timezone_hint") or "").strip(), _arr_iana),
    ]:
        if _tz_src and not _is_unknown(_tz_src):
            _alt_arr_tz_chain.append((_tz_src, _iana_src))
    # 从 alt 机场 IATA 反查 IANA
    _alt_dep_iata_for_tz = str(alternate_local.get("alt_dep_iata") or "").strip().upper()
    _alt_arr_iata_for_tz = str(alternate_local.get("alt_arr_iata") or "").strip().upper()
    if _alt_dep_iata_for_tz and not _is_unknown(_alt_dep_iata_for_tz):
        _alt_dep_iana = _resolve_iana(_alt_dep_iata_for_tz)
        if _alt_dep_iana:
            _alt_dep_tz_chain.append(("", _alt_dep_iana))
    if _alt_arr_iata_for_tz and not _is_unknown(_alt_arr_iata_for_tz):
        _alt_arr_iana = _resolve_iana(_alt_arr_iata_for_tz)
        if _alt_arr_iana:
            _alt_arr_tz_chain.append(("", _alt_arr_iana))
    # route IANA 兜底
    if _dep_iana:
        _alt_dep_tz_chain.append(("", _dep_iana))
    if _arr_iana:
        _alt_arr_tz_chain.append(("", _arr_iana))

    alt_dep_utc = _try_parse_utc(alt_dep_raw)
    if not alt_dep_utc:
        for _tz_hint, _iana in _alt_dep_tz_chain:
            alt_dep_utc = _try_parse_local(alt_dep_raw, _tz_hint, _iana)
            if alt_dep_utc:
                break
    alt_arr_utc = _try_parse_utc(alt_arr_raw)
    if not alt_arr_utc:
        for _tz_hint, _iana in _alt_arr_tz_chain:
            alt_arr_utc = _try_parse_local(alt_arr_raw, _tz_hint, _iana)
            if alt_arr_utc:
                break

    # alt 时间合理性校验：若 alt 时间与原航班计划时间差距超过 7 天，
    # 说明 Vision 可能将无关登机牌误识别为改签航班，标记异常并置空 alt 时间
    if alt_dep_utc and sched_planned_dep_utc:
        _alt_gap_days = abs((alt_dep_utc - sched_planned_dep_utc).total_seconds()) / 86400
        if _alt_gap_days > 7:
            missing.append(f"alt_dep(与原航班计划差距{_alt_gap_days:.0f}天，疑似无关登机牌)")
            alt_dep_utc = None
            alt_arr_utc = None

    actual_dep_raw = _sanitize_date(str(actual_local.get("actual_dep") or "").strip())
    actual_arr_raw = _sanitize_date(str(actual_local.get("actual_arr") or "").strip())
    actual_dep_utc = (
        _try_parse_utc(actual_dep_raw)
        or _try_parse_local(actual_dep_raw, chain0_dep_tz, _dep_iana)
    )
    actual_arr_utc = (
        _try_parse_utc(actual_arr_raw)
        or _try_parse_local(actual_arr_raw, chain0_arr_tz, _arr_iana)
    )

    chain_dep_delay: Optional[int] = None
    chain_arr_delay: Optional[int] = None
    method = "unknown"

    # 取消航班兜底：飞常准标记为"取消"且无实际时间时，AI 解析的 actual 不可信
    # （AI 可能将 planned 复制到 actual，导致 planned=actual → 延误=0）
    avi_status = str((parsed or {}).get("aviation_status") or "").strip()
    if avi_status == "取消":
        avi_has_actual = (
            not _is_unknown(str(actual_local.get("actual_dep") or ""))
            or not _is_unknown(str(actual_local.get("actual_arr") or ""))
        )
        if avi_has_actual:
            # 检查 actual 是否实际来自飞常准（飞常准返回的才有 ISO 时区格式）
            actual_from_aviation = (
                bool(_try_parse_utc(actual_dep_raw)) or bool(_try_parse_utc(actual_arr_raw))
            )
            if not actual_from_aviation:
                # AI 解析的 actual 不可信，置空强制走 alt 或文本兜底
                actual_dep_utc = None
                actual_arr_utc = None
                missing.append("actual_dep/actual_arr(航班取消，AI解析时间不可靠)")

    # 口径1：有 chain → 旅客首版计划 → 飞常准实际
    # 联程/中转场景下 chain[0] 为首段航班，actual_local 为理赔焦点航班，
    # 两者描述不同航班，直接比较无意义，跳过口径1
    # 取消航班场景：原航班已取消，actual 时间反映的是空机起飞/取消时刻，
    # 与旅客实际出行无关，应跳过口径1走口径2（计划→改签实际）
    itinerary = (parsed or {}).get("itinerary") or {}
    is_connecting = _truthy(itinerary.get("is_connecting_or_transit"))
    _skip_k1 = is_connecting or (avi_status == "取消" and alt_dep_utc is not None)
    if chain and not _skip_k1:
        if first_planned_dep_utc and actual_dep_utc:
            delta = int((actual_dep_utc - first_planned_dep_utc).total_seconds() // 60)
            if delta >= 0:
                chain_dep_delay = delta
        if first_planned_arr_utc and actual_arr_utc:
            delta = int((actual_arr_utc - first_planned_arr_utc).total_seconds() // 60)
            if delta >= 0:
                chain_arr_delay = delta
        candidates_chain = [m for m in [chain_dep_delay, chain_arr_delay] if isinstance(m, int)]
        # 口径1返回0或极小值（<30分钟）时，若存在有效改签航班且时间与计划差距显著，
        # 不回传，而是继续走口径2（计划→改签实际）。
        # 场景1：原航班准点但旅客被改签到更晚航班（chain=0, alt=630）
        # 场景2：原航班小幅延误但改签后大幅延误（chain=13, alt=1080）
        chain_max = max(candidates_chain) if candidates_chain else None
        chain_small_but_alt_big = (
            chain_max is not None and chain_max < 30
            and alt_dep_utc is not None
            and sched_planned_dep_utc is not None
            and abs((alt_dep_utc - sched_planned_dep_utc).total_seconds()) > 3600  # alt与计划差距>1小时
        )
        if candidates_chain and not chain_small_but_alt_big:
            final_minutes = max(candidates_chain)
            method = f"旅客首版计划→飞常准实际（起飞{chain_dep_delay or '?'}分/到达{chain_arr_delay or '?'}分）"
            return {
                "chain_dep_delay": chain_dep_delay, "chain_arr_delay": chain_arr_delay,
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
    # 业务规则（2026-05-11明确）：不管机场是否匹配都要计算延误时长。
    # 原航班单程、改签后多程场景：用原航班计划时间 vs 改签航班实际时间，
    # 不因机场不同而跳过计算。
    # 机场匹配信息仅用于记录，不作为计算阻断条件。
    alt_dep_delay: Optional[int] = None
    alt_arr_delay: Optional[int] = None
    last_seg_dep_iata = str((schedule_local.get("last_seg_dep_iata") or "")).strip().upper()
    last_seg_arr_iata = str((schedule_local.get("last_seg_arr_iata") or "")).strip().upper()
    orig_dep_iata = last_seg_dep_iata or str(_dep_iata or "").upper()
    orig_arr_iata = last_seg_arr_iata or str(_arr_iata or "").upper()
    alt_dep_iata = str((alternate_local.get("alt_dep_iata") or "")).strip().upper()
    alt_arr_iata = str((alternate_local.get("alt_arr_iata") or "")).strip().upper()

    # 记录机场匹配情况（用于 debug），但不阻断计算
    dep_iata_match = (
        not alt_dep_iata or not orig_dep_iata
        or alt_dep_iata == orig_dep_iata
    )
    arr_iata_match = (
        not alt_arr_iata or not orig_arr_iata
        or alt_arr_iata == orig_arr_iata
    )
    if baseline_dep_utc and alt_dep_utc:
        delta = int((alt_dep_utc - baseline_dep_utc).total_seconds() // 60)
        if delta >= 0:
            alt_dep_delay = delta
            if not dep_iata_match:
                missing.append(f"alt_dep机场不匹配({orig_dep_iata}→{alt_dep_iata})，但仍计入延误")
    else:
        if not baseline_dep_utc:
            missing.append("planned_dep(需可换算时区)")
        if not alt_dep_utc:
            missing.append("alt_dep(改签后实际起飞时间/需可换算时区)")

    if baseline_arr_utc and alt_arr_utc:
        delta = int((alt_arr_utc - baseline_arr_utc).total_seconds() // 60)
        if delta >= 0:
            alt_arr_delay = delta
            if not arr_iata_match:
                missing.append(f"alt_arr机场不匹配({orig_arr_iata}→{alt_arr_iata})，但仍计入延误")
    else:
        if not baseline_arr_utc:
            missing.append("planned_arr(需可换算时区)")
        if not alt_arr_utc:
            missing.append("alt_arr(替代抵达原目的地时间/需可换算时区)")

    candidates_alt = [m for m in [alt_dep_delay, alt_arr_delay] if isinstance(m, int)]
    final_alt = max(candidates_alt) if candidates_alt else None

    # 口径3：计划 → 飞常准实际（兜底）
    actual_dep_delay: Optional[int] = None
    actual_arr_delay: Optional[int] = None
    if baseline_dep_utc and actual_dep_utc:
        delta = int((actual_dep_utc - baseline_dep_utc).total_seconds() // 60)
        if delta >= 0:
            actual_dep_delay = delta
    if baseline_arr_utc and actual_arr_utc:
        delta = int((actual_arr_utc - baseline_arr_utc).total_seconds() // 60)
        if delta >= 0:
            actual_arr_delay = delta

    candidates_actual = [m for m in [actual_dep_delay, actual_arr_delay] if isinstance(m, int)]
    final_actual = max(candidates_actual) if candidates_actual else None

    # 联程改签场景特殊处理
    is_conn_rebooking = _truthy(itinerary.get("is_connecting_rebooking")) is True
    connecting_rebooking_suspicion = (
        is_connecting
        and (
            is_conn_rebooking
            or (
                isinstance(alt_dep_delay, int) and isinstance(alt_arr_delay, int) and alt_arr_delay > 0
                and alt_dep_delay > alt_arr_delay * CONNECTING_REBOOKING_RATIO  # 起飞延误显著大于到达延误，判定为联程改签场景（首段起飞延误被放大）
            )
        )
    )

    # 联程改签延误计算规则（修正 2026-05-07）：
    # 1. 先看末段实际延误：末段实际出发/到达 vs 末段计划出发/到达
    # 2. 如果前序导致（missed_connection）→ 追溯计算：
    #    延误 = max(首段实际出发-原计划出发, 末段实际到达-原计划到达)
    # 3. 否则 → 仅按末段实际延误计算
    if is_conn_rebooking or connecting_rebooking_suspicion:
        missed_connection = _truthy(itinerary.get("mentions_missed_connection"))

        # 末段机场 IANA（优先用 alternate_local 的机场，降级用 route 的机场）
        _last_dep_iata = str((alternate_local.get("alt_dep_iata") or "")).strip().upper()
        _last_arr_iata = str((alternate_local.get("alt_arr_iata") or "")).strip().upper()
        _last_dep_iana = _resolve_iana(_last_dep_iata) if _last_dep_iata else None
        _last_arr_iana = _resolve_iana(_last_arr_iata) if _last_arr_iata else None

        # 末段计划时间（从 schedule_revision_chain 最后一项取）
        chain_last = chain[-1] if chain else {}
        last_planned_dep_str = _sanitize_date(str(chain_last.get("planned_dep") or "").strip())
        last_planned_arr_str = _sanitize_date(str(chain_last.get("planned_arr") or "").strip())
        last_planned_dep_tz = str(chain_last.get("dep_timezone_hint") or "").strip()
        last_planned_arr_tz = str(chain_last.get("arr_timezone_hint") or "").strip()

        last_planned_dep_utc = (
            _try_parse_utc(last_planned_dep_str)
            or _try_parse_local(last_planned_dep_str, last_planned_dep_tz, _last_dep_iana or _dep_iana)
        )
        last_planned_arr_utc = (
            _try_parse_utc(last_planned_arr_str)
            or _try_parse_local(last_planned_arr_str, last_planned_arr_tz, _last_arr_iana or _arr_iana)
        )

        # 末段实际时间即 alt_dep_utc / alt_arr_utc（已被 alt_flight_lookup 覆盖为末段实际）
        last_seg_dep_delay: Optional[int] = None
        last_seg_arr_delay: Optional[int] = None
        if last_planned_dep_utc and alt_dep_utc:
            delta = int((alt_dep_utc - last_planned_dep_utc).total_seconds() // 60)
            if delta >= 0:
                last_seg_dep_delay = delta
        if last_planned_arr_utc and alt_arr_utc:
            delta = int((alt_arr_utc - last_planned_arr_utc).total_seconds() // 60)
            if delta >= 0:
                last_seg_arr_delay = delta

        if missed_connection:
            # 前序导致：追溯计算，从原计划到末段实际
            conn_candidates = [m for m in [alt_dep_delay, alt_arr_delay] if isinstance(m, int)]
            if conn_candidates:
                final_minutes = max(conn_candidates)
                method = f"联程改签(前序导致)-取max(起飞延误{alt_dep_delay}分,到达延误{alt_arr_delay}分): 首段实际出发vs原计划出发, 末段实际到达vs原计划到达"
            else:
                final_minutes = None
                method = "联程改签(前序导致)-无法计算：缺少改签航班实际时间数据"
        else:
            # 非前序导致：仅按末段实际延误计算
            last_seg_candidates = [m for m in [last_seg_dep_delay, last_seg_arr_delay] if isinstance(m, int) and m > 0]
            # 兜底：末段延误很小但完整追溯延误很大（≥5倍）→ 实际是简单改签（同航线改次日），
            # 非真正的联程改签，应使用完整追溯（原计划→改签实际）
            conn_candidates = [m for m in [alt_dep_delay, alt_arr_delay] if isinstance(m, int)]
            if last_seg_candidates and conn_candidates:
                last_seg_max = max(last_seg_candidates)
                conn_max = max(conn_candidates)
                if conn_max > last_seg_max * 5:
                    final_minutes = conn_max
                    method = f"联程改签-降级追溯(起飞延误{alt_dep_delay}分,到达延误{alt_arr_delay}分): 末段延误({last_seg_max}分)远小于完整追溯({conn_max}分)，判定为简单改签"
                else:
                    final_minutes = last_seg_max
                    method = f"联程改签-末段延误(起飞{last_seg_dep_delay}分/到达{last_seg_arr_delay}分): 末段实际vs末段计划"
            elif last_seg_candidates:
                final_minutes = max(last_seg_candidates)
                method = f"联程改签-末段延误(起飞{last_seg_dep_delay}分/到达{last_seg_arr_delay}分): 末段实际vs末段计划"
            else:
                # 末段无法计算，降级使用完整追溯
                if conn_candidates:
                    final_minutes = max(conn_candidates)
                    method = f"联程改签-降级追溯(起飞延误{alt_dep_delay}分,到达延误{alt_arr_delay}分): 末段计划时间缺失"
                else:
                    final_minutes = None
                    method = "联程改签-无法计算：缺少时间数据"
    elif final_alt is not None and final_actual is not None:
        final_minutes = max(final_alt, final_actual)
        method = f"取长: alt(起飞{alt_dep_delay}分/到达{alt_arr_delay}分) vs 实际(起飞{actual_dep_delay}分/到达{actual_arr_delay}分)"
    elif final_alt is not None:
        final_minutes = final_alt
        method = f"alt口径(起飞{alt_dep_delay}分/到达{alt_arr_delay}分)"
    elif final_actual is not None:
        final_minutes = final_actual
        method = f"飞常准实际口径(起飞{actual_dep_delay}分/到达{actual_arr_delay}分)"
    else:
        final_minutes = None
        method = "无法计算：缺少时间数据"

    return {
        "chain_dep_delay": chain_dep_delay, "chain_arr_delay": chain_arr_delay,
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
    threshold_minutes = _parse_threshold_minutes(policy_terms_excerpt) or FLIGHT_DELAY_DEFAULT_THRESHOLD_MINUTES

    if computed.get("final_minutes") is None and free_text and computed.get("missing"):
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
