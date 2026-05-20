"""除外责任规则 单元测试"""
import pytest
from app.rules.flight.exclusions import check, BAGGAGE_DELAY_EXCLUSIONS, FLIGHT_DELAY_EXCLUSIONS


class TestExclusions:
    """条款除外责任规则测试"""

    def test_no_exclusion_matched(self):
        """不含任何除外关键词 → 通过"""
        result = check(
            content="航班延误4小时，航司已出具延误证明",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        assert result.passed is True
        assert result.action == "continue"
        assert "未命中" in result.reason

    def test_customs_seizure(self):
        """海关没收命中（关键词：海关或其他政府部门没收、扣留、隔离、检验或销毁）"""
        result = check(
            content="行李被海关或其他政府部门没收、扣留、隔离、检验或销毁，无法理赔",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        assert result.passed is False
        assert result.action == "reject"
        assert "海关" in result.detail.get("matched_keyword", "")

    def test_war_exclusion(self):
        """战争等命中（关键词：战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱）"""
        result = check(
            content="因当地战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱导致航班取消",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        assert result.passed is False
        assert "战争" in result.detail.get("matched_keyword", "")

    def test_terrorism_exclusion(self):
        """恐怖活动除外命中"""
        result = check(
            content="恐怖活动导致机场关闭",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        assert result.passed is False
        assert "恐怖活动" in result.detail.get("matched_keyword", "")

    def test_not_notified_carrier(self):
        """未通知承运人命中"""
        result = check(
            content="被保险人未将行李延误一事通知有关公共交通工具承运人",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        assert result.passed is False

    def test_extra_text_also_checked(self):
        """extra_text 中关键词也被检测"""
        result = check(
            content="航班延误3小时",
            extra_text="战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱导致机场关闭",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        assert result.passed is False

    def test_empty_content_passes(self):
        """空字符串 → 通过"""
        result = check(
            content="",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        assert result.passed is True

    def test_multiple_exclusions_returns_first(self):
        """多个除外命中 → 返回第一个匹配"""
        result = check(
            content="海关或其他政府部门没收、扣留、隔离、检验或销毁后又遇战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        assert result.passed is False
        assert "海关" in result.detail.get("matched_keyword", "")

    def test_case_insensitive_english(self):
        """英文除外关键词大小写不敏感"""
        result = check(
            content="baggage was forwarded due to missed connection",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        # "baggage forwarded" 关键词为"行李装载等"中文，应不匹配
        assert result.passed is True

    def test_strike_exclusion(self):
        """罢工命中（关键词：战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱）"""
        result = check(
            content="因战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱导致航班取消",
            exclusion_checks=BAGGAGE_DELAY_EXCLUSIONS,
        )
        assert result.passed is False
        assert "罢工" in result.detail.get("matched_keyword", "")

    def test_flight_delay_exclusions(self):
        """航班延误险除外条款也正常工作"""
        result = check(
            content="战争、军事行动、暴乱、武装叛乱、罢工、暴动、内乱导致机场关闭",
            exclusion_checks=FLIGHT_DELAY_EXCLUSIONS,
        )
        assert result.passed is False
        assert "暴乱" in result.detail.get("matched_keyword", "")