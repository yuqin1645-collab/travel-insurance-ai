"""
flight_delay stages — 统一 re-export。
"""

from .utils import (
    _policy_excerpt_or_default,
    _is_unknown,
    _merge_vision_into_parsed,
    _merge_aviation_into_parsed,
    _truthy,
    _has_timezone,
    _parse_utc_dt,
    _parse_tz_offset,
    _parse_local_dt,
    _parse_local_dt_iana,
    _parse_threshold_minutes,
    _extract_delay_minutes_from_text,
)

from .hardcheck import _check_foreseeability_fraud, _run_hardcheck
from .payout import _run_payout_calc
from .delay_calc import _compute_delay_minutes, _augment_with_computed_delay
from .postprocess import _postprocess_audit_result
from .duplicate import _is_concluded_status, _is_same_event, _check_duplicate_claim
from .validators import (
    _check_inheritance_scenario,
    _check_legal_capacity,
    _check_name_match,
    _check_same_day_policy,
    _check_coverage_area_text,
    _check_hardcheck_exclusion,
)

__all__ = [
    # utils
    "_policy_excerpt_or_default", "_is_unknown", "_merge_vision_into_parsed",
    "_merge_aviation_into_parsed", "_truthy", "_has_timezone", "_parse_utc_dt",
    "_parse_tz_offset", "_parse_local_dt", "_parse_local_dt_iana",
    "_parse_threshold_minutes", "_extract_delay_minutes_from_text",
    # hardcheck
    "_check_foreseeability_fraud", "_run_hardcheck",
    # payout
    "_run_payout_calc",
    # delay_calc
    "_compute_delay_minutes", "_augment_with_computed_delay",
    # postprocess
    "_postprocess_audit_result",
    # duplicate
    "_is_concluded_status", "_is_same_event", "_check_duplicate_claim",
    # validators
    "_check_inheritance_scenario", "_check_legal_capacity", "_check_name_match",
    "_check_same_day_policy", "_check_coverage_area_text", "_check_hardcheck_exclusion",
]
