"""保单有效期判定规则 单元测试"""
import pytest
from datetime import datetime, timedelta
from app.rules.common.policy_validity import check, _parse_dt


class TestPolicyValidity:
    """保单有效期判定规则测试"""

    def _base_claim(self, **overrides):
        """构造基础案件信息"""
        base = {
            "PolicyStatus": "active",
            "Effective_Date": "2026-01-01",
            "Expiry_Date": "2026-12-31",
            "Date_of_Accident": "2026-06-15",
        }
        base.update(overrides)
        return base

    # ── 主险状态 ──

    def test_main_policy_terminated(self):
        """主险合同已终止 → 拒赔"""
        claim = self._base_claim(PolicyStatus="terminated")
        result = check(claim)
        assert result.passed is False
        assert result.action == "reject"
        assert "终止" in result.reason

    def test_main_policy_expired(self):
        """主险合同已过期 → 拒赔"""
        claim = self._base_claim(PolicyStatus="expired")
        result = check(claim)
        assert result.passed is False
        assert result.action == "reject"

    # ── 事故日期在有效期内 ──

    def test_accident_date_in_coverage(self):
        """事故日期在保单有效期内 → 通过"""
        claim = self._base_claim()
        result = check(claim)
        assert result.passed is True
        assert result.action == "continue"

    def test_accident_date_outside_coverage(self):
        """事故日期超出有效期 → 拒赔"""
        claim = self._base_claim(
            Date_of_Accident="2025-12-31 23:59:59",
        )
        result = check(claim)
        assert result.passed is False
        assert result.action == "reject"

    # ── 缺失字段不拦截 ──

    def test_missing_effective_dates_not_blocked(self):
        """生效/失效日期缺失 → 不拦截，交 AI 判断"""
        claim = {
            "PolicyStatus": "active",
            "Date_of_Accident": "2026-06-15",
        }
        result = check(claim)
        assert result.passed is True
        assert result.action == "continue"

    # ── 安联顺延规则 ──

    def test_allianz_extension_within_15_days(self):
        """安联出境晚于生效日 10 天 → 顺延并通过"""
        claim = self._base_claim(
            Insurer="安联保险",
            Effective_Date="2026-01-01",
            Expiry_Date="2026-01-31",
            First_Exit_Date="2026-01-11",
            Date_of_Accident="2026-01-25",  # 顺延后落在窗口内
        )
        result = check(claim)
        assert result.passed is True
        assert result.action == "continue"
        assert result.detail.get("used_extension") is True

    def test_allianz_extension_exceeds_15_days(self):
        """安联出境晚于生效日 20 天 → 不顺延（事故日期仍可通过 first_exit_date 命中窗口）"""
        claim = self._base_claim(
            Insurer="安联保险",
            Effective_Date="2026-01-01",
            Expiry_Date="2026-01-25",
            First_Exit_Date="2026-01-21",
            Date_of_Accident="2026-01-28",
        )
        result = check(claim)
        # 出境超15天不顺延，但 First_Exit_Date=2026-01-21 落在 [01-01, 01-25] 内
        assert result.passed is True
        assert result.detail.get("used_extension") is False
        assert result.detail.get("coverage_hit_by") == "first_exit_date"

    def test_allianz_negative_extension_not_shrink(self):
        """出境早于生效日 → 不缩小保障窗口（P2 修复）"""
        claim = self._base_claim(
            Insurer="安联保险",
            Effective_Date="2026-01-10",
            Expiry_Date="2026-12-31",
            First_Exit_Date="2026-01-05",
            Date_of_Accident="2026-01-08",  # 在原始窗口内但不在缩小后的
        )
        result = check(claim)
        # 负顺延不应缩小窗口，事故日期虽在保单开始后但在出境后
        # 原始窗口 [1-10, 12-31]，事故 1-8 早于生效日
        assert result.passed is False
        assert result.detail.get("used_extension") is True
        assert result.detail.get("allianz_extension_days") == -5

    # ── flight_date 作为命中时间点 ──

    def test_flight_date_hits_coverage(self):
        """flight_date 落在有效期内 → 通过（即使事故日期不在）"""
        claim = self._base_claim(
            Date_of_Accident="2025-01-01",
            Flight_Date="2026-03-15",
        )
        result = check(claim)
        assert result.passed is True
        assert result.action == "continue"
        assert result.detail.get("coverage_hit_by") == "flight_date"

    def test_flight_date_no_year_accepted(self):
        """登机牌格式（仅月/日无年份）→ 不被接受为有效 flight_date"""
        claim = self._base_claim(
            Date_of_Accident="2025-01-01",
            Flight_Date="02-19",  # 登机牌仅有月/日
        )
        result = check(claim)
        # 无有效 flight_date，无其他时间点命中，但事故日期超期
        assert result.passed is False

    def test_no_flight_date_all_points_out(self):
        """无完整航班日期且所有时间点超期 → 拒赔"""
        claim = self._base_claim(
            Date_of_Accident="2025-01-01",
            Effective_Date="2026-01-01",
            Expiry_Date="2026-12-31",
        )
        result = check(claim)
        assert result.passed is False
        assert result.action == "reject"

    # ── 日期解析 ──

    def test_parse_datetime_formats(self):
        """日期解析器支持多种格式"""
        assert _parse_dt("2026-01-15") == datetime(2026, 1, 15)
        assert _parse_dt("2026/01/15") == datetime(2026, 1, 15)
        assert _parse_dt("20260115") == datetime(2026, 1, 15)
        assert _parse_dt("2026-01-15 10:30:00") == datetime(2026, 1, 15, 10, 30, 0)
        assert _parse_dt("20260115103000") == datetime(2026, 1, 15, 10, 30, 0)
        assert _parse_dt("") is None
        assert _parse_dt(None) is None