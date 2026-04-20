#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从本地 review_results/flight_delay/ 的 JSON 文件提取联程航段数据，
写入 ai_review_segments 子表，并同步更新 ai_review_result 的5个联程标量字段。

时间字段口径说明：
  planned_dep/arr  ← itinerary_segments[n].original_date（本地时间字符串，无时区）
                     存储时直接作为 naive datetime，时区以 dep/arr_iata 所在地为准。
  actual_dep/arr   ← aviation_lookup.actual_dep/arr（飞常准返回带时区的 ISO 字符串）
                     去掉时区信息后存储，保留飞常准报告的本地时间值（如 2026-02-25 12:15）。
  delay_min        ← actual_dep - planned_dep（两者均为本地时间，同一时区，直接相减）
                     注意：仅在飞常准查到的是触发航段时才有意义。
"""

import sys
import json
import asyncio
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.database import get_review_segment_dao, get_review_result_dao
from app.db.models import ReviewSegment


RESULTS_DIR = Path("review_results/flight_delay")


def _parse_dt(value: Any) -> Optional[datetime]:
    """解析时间字符串为 naive datetime（去掉时区）"""
    if not value:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("unknown", "null", "none", ""):
        return None
    # 处理 ISO 格式（可能带时区偏移 +HH:MM 或 Z）
    try:
        # 替换 Z 为 +00:00
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        # 去掉时区信息，保留本地时间值（飞常准返回的是起飞地本地时间+偏移）
        return dt.replace(tzinfo=None)
    except Exception:
        pass
    # 尝试常见格式
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt)].replace("T", " "), fmt)
        except Exception:
            continue
    # 从字符串中提取 YYYY-MM-DD HH:MM 部分
    m = re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", s)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        except Exception:
            pass
    return None


def _truthy(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "是")


def extract_segments(forceid: str, debug_info: Dict[str, Any]) -> List[ReviewSegment]:
    """
    从 DebugInfo 提取联程航段列表，构建 ReviewSegment 对象。

    数据来源：
      - itinerary_segments（vision 提取）：原始航段列表，含 flight_no、dep/arr_iata、original_date
      - aviation_lookup：飞常准查到的那段的 planned/actual 时间和状态（通常是触发延误的那段）
      - parse_enriched.itinerary：联程标志位
    """
    enriched = debug_info.get("flight_delay_parse_enriched") or {}
    av = debug_info.get("flight_delay_aviation_lookup") or {}
    itinerary = enriched.get("itinerary") or {}

    # 找 itinerary_segments（vision 输出）
    vision_extract = debug_info.get("flight_delay_vision_extract") or {}
    raw_segs: List[Dict] = vision_extract.get("itinerary_segments") or []

    # 联程标量
    is_connecting = _truthy(itinerary.get("is_connecting_or_transit"))
    missed_connection = _truthy(itinerary.get("mentions_missed_connection"))
    transit_iata = str(itinerary.get("transit_iata") or "").strip() or None

    total_segments = len(raw_segs) if raw_segs else (2 if is_connecting else 1)

    # 从 aviation_lookup 提取飞常准数据（触发延误的那段）
    av_dep_iata = str(av.get("dep_iata") or "").strip().upper() or None
    av_arr_iata = str(av.get("arr_iata") or "").strip().upper() or None
    av_flight_no = str(av.get("flight_no") or "").strip().upper() or None
    av_planned_dep = _parse_dt(av.get("planned_dep"))
    av_planned_arr = _parse_dt(av.get("planned_arr"))
    av_actual_dep = _parse_dt(av.get("actual_dep"))
    av_actual_arr = _parse_dt(av.get("actual_arr"))
    av_status = str(av.get("status") or "").strip() or None

    # 全程始发/目的地
    # 若有 itinerary_segments，首段 dep_iata = origin，末段 arr_iata = destination
    if raw_segs:
        first = raw_segs[0]
        last = raw_segs[-1]
        origin_iata = str(first.get("original_dep_iata") or "").strip().upper() or None
        destination_iata = str(last.get("original_arr_iata") or "").strip().upper() or None
        if origin_iata in ("UNKNOWN", ""):
            origin_iata = None
        if destination_iata in ("UNKNOWN", ""):
            destination_iata = None
    else:
        # 直飞：从 enriched 取
        route = enriched.get("route") or enriched.get("flight") or {}
        origin_iata = str(route.get("dep_iata") or enriched.get("dep_iata") or "").strip().upper() or None
        destination_iata = str(route.get("arr_iata") or enriched.get("arr_iata") or "").strip().upper() or None

    segments: List[ReviewSegment] = []

    if not raw_segs:
        # 直飞：从 aviation_lookup 补一条
        if av_dep_iata or av_flight_no:
            delay_min = None
            if av_actual_dep and av_planned_dep:
                delta = (av_actual_dep - av_planned_dep).total_seconds() / 60
                delay_min = int(delta) if delta > -60 else None  # 超早起飞忽略负值

            seg = ReviewSegment(
                forceid=forceid,
                segment_no=1,
                flight_no=av_flight_no,
                dep_iata=av_dep_iata,
                arr_iata=av_arr_iata,
                origin_iata=origin_iata or av_dep_iata,
                destination_iata=destination_iata or av_arr_iata,
                planned_dep=av_planned_dep,
                planned_arr=av_planned_arr,
                actual_dep=av_actual_dep,
                actual_arr=av_actual_arr,
                delay_min=delay_min,
                avi_status=av_status,
                is_triggered=True,
                is_connecting=False,
                missed_connect=False,
            )
            segments.append(seg)
        return segments

    # 联程：逐段构建，先找 flight_no 精确匹配的触发段，没有再用 dep/arr 匹配
    # 两次遍历：先确定哪段是触发段，再构建对象
    triggered_seg_no: Optional[int] = None
    if av_flight_no:
        for raw in raw_segs:
            fn = str(raw.get("original_flight_no") or "").strip().upper()
            # 去掉空格后比较（vision 有时会保留空格如 "AA 4387"）
            if fn and fn.replace(" ", "") == av_flight_no.replace(" ", ""):
                triggered_seg_no = int(raw.get("segment_no") or 1)
                break
    if triggered_seg_no is None and av_dep_iata and av_arr_iata:
        for raw in raw_segs:
            dep = str(raw.get("original_dep_iata") or "").strip().upper()
            arr = str(raw.get("original_arr_iata") or "").strip().upper()
            if dep and arr and dep not in ("UNKNOWN", "") and arr not in ("UNKNOWN", ""):
                if dep == av_dep_iata and arr == av_arr_iata:
                    triggered_seg_no = int(raw.get("segment_no") or 1)
                    break

    # 确定触发段之后的接驳失误段序号（missed_connection 为真时，触发段之后的后续段才标误机）
    triggered_idx = triggered_seg_no  # 段号（1-based）

    for raw in raw_segs:
        seg_no = int(raw.get("segment_no") or 1)
        flight_no = str(raw.get("original_flight_no") or "").strip().upper() or None
        dep_iata = str(raw.get("original_dep_iata") or "").strip().upper() or None
        arr_iata = str(raw.get("original_arr_iata") or "").strip().upper() or None
        if dep_iata in ("UNKNOWN", ""):
            dep_iata = None
        if arr_iata in ("UNKNOWN", ""):
            arr_iata = None

        # 若 vision 未识别到 dep/arr，但飞常准有对应数据，用飞常准补全
        if dep_iata is None and flight_no and av_flight_no and flight_no.replace(" ", "") == av_flight_no.replace(" ", ""):
            dep_iata = av_dep_iata
        if arr_iata is None and flight_no and av_flight_no and flight_no.replace(" ", "") == av_flight_no.replace(" ", ""):
            arr_iata = av_arr_iata

        # 计划时间：来自 itinerary_segments.original_date（本地时间字符串）
        planned_dep = _parse_dt(raw.get("original_date"))
        planned_arr = None  # vision 未提取到达时间，留 None

        # 判断本段是否是飞常准查到的那段（触发延误的段）
        is_triggered = (triggered_seg_no is not None and seg_no == triggered_seg_no)
        actual_dep = None
        actual_arr = None
        delay_min = None
        avi_status_seg = None

        if is_triggered:
            actual_dep = av_actual_dep
            actual_arr = av_actual_arr
            avi_status_seg = av_status
            # planned_dep 优先用飞常准的（更精确），若 vision 只给了日期则用飞常准
            if av_planned_dep:
                planned_dep = av_planned_dep
            if av_planned_arr:
                planned_arr = av_planned_arr
            if actual_dep and planned_dep:
                delta = (actual_dep - planned_dep).total_seconds() / 60
                delay_min = int(delta) if delta > -60 else None

        # 本段是否误机：联程、有 missed_connection 标志，且本段在触发段之后（后续衔接段）
        # 触发段本身不标误机（误机指"因前段延误而赶不上的那段"）
        missed_connect = False
        if (is_connecting and missed_connection and not is_triggered
                and triggered_idx is not None and seg_no > triggered_idx):
            missed_connect = True

        seg = ReviewSegment(
            forceid=forceid,
            segment_no=seg_no,
            flight_no=flight_no,
            dep_iata=dep_iata,
            arr_iata=arr_iata,
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            planned_dep=planned_dep,
            planned_arr=planned_arr,
            actual_dep=actual_dep,
            actual_arr=actual_arr,
            delay_min=delay_min,
            avi_status=avi_status_seg,
            is_triggered=is_triggered,
            is_connecting=is_connecting if is_connecting is not None else False,
            missed_connect=missed_connect,
        )
        segments.append(seg)

    return segments


async def main(dry_run: bool = False):
    result_files = sorted(RESULTS_DIR.glob("*_ai_review.json"))
    print(f"找到 {len(result_files)} 个航班延误审核结果\n")

    seg_dao = get_review_segment_dao()
    total_inserted = 0
    skipped = 0
    errors = 0

    for fpath in result_files:
        try:
            d = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [ERR] 读取失败 {fpath.name}: {e}")
            errors += 1
            continue

        forceid = str(d.get("forceid") or "").strip()
        if not forceid:
            skipped += 1
            continue

        di = d.get("DebugInfo") or {}
        segments = extract_segments(forceid, di)

        if not segments:
            print(f"  [SKIP] {forceid}  无航段数据（直飞且无飞常准数据）")
            skipped += 1
            continue

        # 打印摘要
        enriched = di.get("flight_delay_parse_enriched") or {}
        itinerary = enriched.get("itinerary") or {}
        is_conn = _truthy(itinerary.get("is_connecting_or_transit"))
        print(f"  {'联程' if is_conn else '直飞'} {forceid}  {len(segments)} 段")
        for s in segments:
            triggered_mark = " ★触发" if s.is_triggered else ""
            missed_mark = " ⚡误机" if s.missed_connect else ""
            print(f"    段{s.segment_no}: {s.flight_no or '?'} "
                  f"{s.dep_iata or '?'}→{s.arr_iata or '?'} "
                  f"计划起飞={s.planned_dep.strftime('%Y-%m-%d %H:%M') if s.planned_dep else 'None'} "
                  f"实际起飞={s.actual_dep.strftime('%Y-%m-%d %H:%M') if s.actual_dep else 'None'} "
                  f"延误={s.delay_min}min{triggered_mark}{missed_mark}")

        if not dry_run:
            n = await seg_dao.upsert_segments(forceid, segments)
            total_inserted += n

    print(f"\n{'[DRY RUN] ' if dry_run else ''}完成：写入 {total_inserted} 行，跳过 {skipped} 个，错误 {errors} 个")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印不写库")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
