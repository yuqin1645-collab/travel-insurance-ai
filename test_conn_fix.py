#!/usr/bin/env python3
import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from app.modules.flight_delay.pipeline import _is_unknown, _has_timezone, _truthy

parsed = {
    "schedule_revision_chain": [
        {"planned_dep": "2026-02-19 08:20", "planned_arr": "2026-02-19 10:05"},
        {"original_flight_no": "KL1175", "original_date": "2026-02-19 11:03"},
        {"planned_dep": "2026-02-19 14:25", "planned_arr": "2026-02-19 15:05"},
    ],
    "alternate_local": {
        "alt_dep": "2026-02-19 14:25",  # 错误：被填成末班计划时间
        "alt_arr": "2026-02-19T14:53:00+01:00",
        "alt_flight_no": "SK4172",
    },
    "itinerary": {"is_connecting_rebooking": True},
    "flight": {"ticket_flight_no": "KL1163"},
    "route": {"dep_iata": "AMS", "arr_iata": "BGO"},
}
alt_local = parsed.get("alternate_local") or {}
alt_fn = str(alt_local.get("alt_flight_no") or "").strip()
alt_dep_raw = str(alt_local.get("alt_dep") or "").strip()
alt_dep_date = alt_dep_raw[:10] if alt_dep_raw and alt_dep_raw.lower() not in ("unknown", "") else ""

# 联程改签逻辑
is_conn_rebooking = _truthy((parsed.get("itinerary") or {}).get("is_connecting_rebooking")) is True
chain = (parsed or {}).get("schedule_revision_chain") or []
if is_conn_rebooking and isinstance(chain, list) and len(chain) >= 2:
    first_alt = chain[1]
    alt_fn_from_chain = str(first_alt.get("original_flight_no") or "").strip()
    alt_dep_from_chain = str(first_alt.get("original_date") or "").strip()
    print("联程改签检测：是")
    print(f"  修正 alt_fn: {alt_fn} → {alt_fn_from_chain}")
    print(f"  修正 alt_dep_date: {alt_dep_date} → {alt_dep_from_chain[:10]}")
    alt_fn = alt_fn_from_chain
    alt_dep_date = alt_dep_from_chain[:10]
else:
    print("联程改签检测：否")

print(f"最终查询航班: {alt_fn}, 日期: {alt_dep_date}")
