"""
baggage_delay stages — 统一 re-export。
"""

from .utils import (
    _safe_float,
    _parse_date,
    _extract_delay_hours,
    _extract_delay_hours_from_parsed,
    _parse_dt_flexible,
    _extract_date_yyyy_mm_dd,
    _collect_receipt_times,
    _classify_aviation_failure,
    _extract_file_names,
    _result,
)

from .handlers import (
    _check_policy_validity,
    _material_gate,
    _check_special_materials,
    _check_info_consistency,
    _check_airline_baggage_record_exception,
    _check_exclusions,
    _check_domestic_flight,
    _check_actual_arrival_vs_policy,
    _try_transfer_flight_receipt_time,
)

from .calculator import (
    _compute_delay_hours_by_rule,
    _compute_payout_with_rules,
    _compute_tier_amount,
)

from .vision_merge import _merge_vision_to_parsed

from .aviation_lookup import run_aviation_lookup
from .accident_validation import validate_accident_type
from .material_gate import run_material_gate
from .post_process import run_post_process

__all__ = [
    # utils
    "_safe_float", "_parse_date", "_extract_delay_hours",
    "_extract_delay_hours_from_parsed", "_parse_dt_flexible",
    "_extract_date_yyyy_mm_dd", "_collect_receipt_times",
    "_classify_aviation_failure", "_extract_file_names", "_result",
    # handlers
    "_check_policy_validity", "_material_gate", "_check_special_materials",
    "_check_info_consistency", "_check_airline_baggage_record_exception",
    "_check_exclusions", "_check_domestic_flight",
    "_check_actual_arrival_vs_policy", "_try_transfer_flight_receipt_time",
    # calculator
    "_compute_delay_hours_by_rule", "_compute_payout_with_rules",
    "_compute_tier_amount",
    # vision_merge
    "_merge_vision_to_parsed",
    # aviation_lookup
    "run_aviation_lookup",
    # accident_validation
    "validate_accident_type",
    # material_gate
    "run_material_gate",
    # post_process
    "run_post_process",
]