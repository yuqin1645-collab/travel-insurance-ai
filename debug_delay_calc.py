#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from app.modules.flight_delay.pipeline import _compute_delay_minutes

parsed = {
  "flight_delay_parse_enriched": {
    "schedule_local": {"planned_dep": "2026-02-19 08:20", "planned_arr": "2026-02-19 10:05"},
    "alternate_local": {"alt_dep": "2026-02-19 11:03", "alt_arr": "2026-02-19T14:53:00+01:00"},
    "actual_local": {"actual_dep": "2026-02-19 14:25", "actual_arr": "2026-02-19T14:53:00+01:00"},
    "route": {"dep_iata": "AMS", "arr_iata": "BGO"},
    "schedule_revision_chain": [
      {"planned_dep": "2026-02-19 08:20", "planned_arr": "2026-02-19 10:05"},
      {"planned_dep": "2026-02-19 11:03", "planned_arr": "2026-02-19 12:33"},
      {"planned_dep": "2026-02-19 14:25", "planned_arr": "2026-02-19 15:05"},
    ],
  }
}

result = _compute_delay_minutes(parsed["flight_delay_parse_enriched"])
print("a_minutes (dep):", result.get("a_minutes"))
print("b_minutes (arr):", result.get("b_minutes"))
print("final_minutes:", result.get("final_minutes"))
print("method:", result.get("method"))
