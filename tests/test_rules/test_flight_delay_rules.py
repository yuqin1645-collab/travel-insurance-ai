"""航班延误险规则 单元测试"""
import pytest
from app.rules.claim_types.flight_delay import (
    check_rebooking_scenario,
    FLIGHT_DELAY_TIERS,
)


class TestFlightDelayRebooking:
    """改签场景延误时长判定测试"""

    def test_normal_rebooking_dep_longer(self):
        """dep 延误比 arr 延误长 → 取 dep 差值"""
        result = check_rebooking_scenario(
            "2026-01-15T10:00:00",
            "2026-01-15T12:00:00",
            "2026-01-15T15:00:00",
            "2026-01-15T16:30:00",
        )
        # dep差=(15:00-10:00)=300min, arr差=(16:30-12:00)=270min → 取300
        assert result.detail["delay_minutes"] == 300
        assert result.passed is True

    def test_rebooking_arr_longer(self):
        """arr 延误更长 → 取长原则"""
        result = check_rebooking_scenario(
            "2026-01-15T10:00", "2026-01-15T12:00",
            "2026-01-15T13:00", "2026-01-15T19:00",
        )
        # dep差=180min, arr差=420min → 取420
        assert result.detail["delay_minutes"] == 420

    def test_space_separated_format(self):
        """空格分隔的时间格式也能解析"""
        result = check_rebooking_scenario(
            "2026-01-15 10:00", "2026-01-15 12:00",
            "2026-01-15 15:00", "2026-01-15 16:30",
        )
        assert result.detail["delay_minutes"] == 300

    def test_missing_planned_dep(self):
        """缺少 planned_dep，只有 arr 可算"""
        result = check_rebooking_scenario(
            None, "2026-01-15T12:00:00",
            None, "2026-01-15T16:30:00",
        )
        # 只能算 arr 差 = 270
        assert result.detail["delay_minutes"] == 270
        assert result.passed is True

    def test_all_none(self):
        """全部 None → 补充材料"""
        result = check_rebooking_scenario(None, None, None, None)
        assert result.passed is False
        assert result.action == "supplement"


class TestFlightDelayTiers:
    """航班延误档位常量校验"""

    def test_tier_count(self):
        """4 个赔付档位"""
        assert len(FLIGHT_DELAY_TIERS) == 4

    def test_tiers_ordered_by_min_hours(self):
        """档位按 min_hours 升序"""
        hours = [t["min_hours"] for t in FLIGHT_DELAY_TIERS]
        assert hours == sorted(hours)

    def test_max_hours_chaining(self):
        """每档 max_hours 等于下一档 min_hours"""
        for i in range(len(FLIGHT_DELAY_TIERS) - 1):
            assert FLIGHT_DELAY_TIERS[i]["max_hours"] == FLIGHT_DELAY_TIERS[i + 1]["min_hours"]

    def test_last_tier_unbounded(self):
        """最后一档 max_hours=None（无上限）"""
        assert FLIGHT_DELAY_TIERS[-1]["max_hours"] is None

    def test_min_tier_hours(self):
        """起赔门槛 = 5h"""
        assert FLIGHT_DELAY_TIERS[0]["min_hours"] == 5