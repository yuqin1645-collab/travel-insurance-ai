"""行李延误 calculator 单元测试"""
import pytest
from app.modules.baggage_delay.stages.calculator import (
    _compute_delay_hours_by_rule,
    _compute_tier_amount,
)


class TestComputeDelayHours:
    """_compute_delay_hours_by_rule 延误时长计算测试"""

    # ── 标准到达-签收差值 ──

    def test_standard_delta_6_5h(self):
        """到达 10:00，签收 16:30 → 6.5h"""
        parsed = {
            "flight_actual_arrival_time": "2026-01-15T10:00:00",
            "baggage_receipt_time": "2026-01-15T16:30:00",
        }
        result = _compute_delay_hours_by_rule(parsed, "")
        assert result["delay_hours"] == 6.5
        assert result["method"] == "arrival_receipt_delta"

    def test_standard_delta_12h(self):
        """到达 08:00，签收 20:00 → 12h"""
        parsed = {
            "flight_actual_arrival_time": "2026-01-15T08:00:00",
            "baggage_receipt_time": "2026-01-15T20:00:00",
        }
        result = _compute_delay_hours_by_rule(parsed, "")
        assert result["delay_hours"] == 12.0

    def test_receipt_earlier_than_arrival(self):
        """签收时间早于到达时间 → delay_hours=None"""
        parsed = {
            "flight_actual_arrival_time": "2026-01-15T10:00:00",
            "baggage_receipt_time": "2026-01-15T09:00:00",
        }
        result = _compute_delay_hours_by_rule(parsed, "")
        assert result["delay_hours"] is None

    # ── 文本回退 ──

    def test_text_fallback_rejected_without_reliable_receipt(self):
        """无可靠签收时间 → 文本提取"延误8小时"被拒绝（防误判）"""
        parsed = {
            "flight_actual_arrival_time": "2026-01-15T10:00:00",
            "baggage_receipt_time_source": "unknown",
        }
        text_blob = "行李延误8小时，到达时间为2026-01-15 10:00"
        result = _compute_delay_hours_by_rule(parsed, text_blob)
        # 无可靠签收时间时，文本提取不可信
        assert result["delay_hours"] is None
        assert result["text_fallback_rejected"] is not None

    def test_text_fallback_accepted_with_reliable_receipt(self):
        """有可靠签收时间 + 无到达时间 → 文本提取被接受"""
        parsed = {
            "baggage_receipt_time": "2026-01-15T16:30:00",
            "baggage_receipt_time_source": "actual_receipt",
            "receipt_times": ["2026-01-15T16:30:00"],
        }
        text_blob = "行李延误8小时"
        result = _compute_delay_hours_by_rule(parsed, text_blob)
        # 无到达时间，但有可靠签收时间 → 文本提取被接受
        assert result["delay_hours"] == 8
        assert result["method"] == "text_fallback"

    def test_no_data_returns_none(self):
        """无到达时间无文本 → None"""
        result = _compute_delay_hours_by_rule({}, "")
        assert result["delay_hours"] is None

    # ── 联程/改签场景 ──

    def test_connecting_rebooking_uses_alternate(self):
        """改签场景 → 使用 alternate 到达时间"""
        parsed = {
            "flight_actual_arrival_time": "2026-01-15T10:00:00",
            "baggage_receipt_time": "2026-01-15T18:00:00",
            "alternate": {
                "is_connecting_rebooking": "true",
                "alt_flight_no": "CA1234",
                "alt_arr": "2026-01-15T16:00:00",
            },
        }
        result = _compute_delay_hours_by_rule(parsed, "")
        # alternate 到达 16:00 → 签收 18:00，而非原航班 10:00→18:00
        assert result["arrival_time_source"] == "alternate_arrival_rebooking"
        assert result["delay_hours"] == 2.0

    def test_rebooking_same_time_zero_delay(self):
        """改签到达=签收时间 → 0 延误"""
        parsed = {
            "flight_actual_arrival_time": "2026-01-14T10:00:00",
            "baggage_receipt_time": "2026-01-15T16:00:00",
            "alternate": {
                "is_connecting_rebooking": "true",
                "alt_flight_no": "MU5678",
                "alt_arr": "2026-01-15T16:00:00",
            },
        }
        result = _compute_delay_hours_by_rule(parsed, "")
        assert result["delay_hours"] == 0
        assert result["method"] == "rebooking_normal_arrival"

    # ── receipt_times 列表 ──

    def test_receipt_times_list_uses_max(self):
        """receipt_times 列表 → 取最晚时间"""
        parsed = {
            "flight_actual_arrival_time": "2026-01-15T10:00:00",
            "receipt_times": [
                "2026-01-15T15:00:00",
                "2026-01-15T18:00:00",
                "2026-01-15T14:00:00",
            ],
        }
        result = _compute_delay_hours_by_rule(parsed, "")
        assert result["delay_hours"] == 8.0  # 18:00 - 10:00


class TestComputeTierAmount:
    """_compute_tier_amount 档位金额计算测试"""

    def test_7h_500(self):
        assert _compute_tier_amount(7) == 500

    def test_12h_1000(self):
        assert _compute_tier_amount(12) == 1000

    def test_15h_1000(self):
        """15h 仍在 12-18 档 → 1000"""
        assert _compute_tier_amount(15) == 1000

    def test_18h_1500(self):
        assert _compute_tier_amount(18) == 1500

    def test_20h_1500(self):
        assert _compute_tier_amount(20) == 1500

    def test_3h_0(self):
        assert _compute_tier_amount(3) == 0