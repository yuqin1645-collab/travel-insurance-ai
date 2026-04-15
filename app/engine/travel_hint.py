from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from app.logging_utils import LOGGER, log_extra


@dataclass(frozen=True)
class TravelHint:
    earliest_travel_date: Optional[str]
    effective_date: Optional[str]
    recorded: bool


def record_travel_vs_effective_hint(
    *,
    reviewer: Any,
    claim_info: Dict[str, Any],
    ocr_results: Dict[str, Any],
    forceid: str,
    index: int,
    total: int,
    ctx: Dict[str, Any],
) -> TravelHint:
    """
    从 OCR 里提取“最早出行日期”并与保单生效日做对比。
    仅记录提示，不作为自动拒赔条件（避免误判）。
    """
    try:
        earliest_travel = reviewer._extract_earliest_travel_date_from_ocr(ocr_results)
        if not earliest_travel:
            return TravelHint(None, None, False)

        accident_date = datetime.strptime(claim_info.get("Date_of_Accident", ""), "%Y-%m-%d").date()
        effective_dt = datetime.strptime(claim_info.get("Effective_Date", ""), "%Y%m%d%H%M%S")
        effective_date = effective_dt.date()
        travel_date = earliest_travel.date()

        days_before_effective = (effective_date - travel_date).days
        is_same_trip_window = 1 <= days_before_effective <= 60

        if travel_date <= accident_date and travel_date < effective_date and is_same_trip_window:
            msg = (
                f"[{index}/{total}] 提示: 识别到最早出行日期为 {earliest_travel.strftime('%Y-%m-%d')} "
                f"(早于保单生效日期 {effective_date.strftime('%Y-%m-%d')})，仅记录供后续参考，不自动拒赔"
            )
            LOGGER.info(msg, extra=log_extra(forceid=forceid, stage="ocr", attempt=0))
            try:
                ctx.setdefault("debug", []).append(
                    {
                        "stage": "ocr",
                        "attempt": 0,
                        "travel_hint": msg[:200],
                    }
                )
            except Exception:
                pass
            return TravelHint(earliest_travel.strftime("%Y-%m-%d"), effective_date.strftime("%Y-%m-%d"), True)

        return TravelHint(earliest_travel.strftime("%Y-%m-%d"), effective_date.strftime("%Y-%m-%d"), False)
    except Exception as e:
        LOGGER.warning(
            f"[{index}/{total}] 出行时间预检查失败: {e}",
            extra=log_extra(forceid=forceid, stage="ocr", attempt=0),
        )
        return TravelHint(None, None, False)

