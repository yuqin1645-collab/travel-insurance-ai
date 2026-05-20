"""行李延误 utils 单元测试"""
import pytest
from datetime import datetime
from app.modules.baggage_delay.stages.utils import (
    _parse_dt_flexible,
    _extract_date_yyyy_mm_dd,
    _classify_aviation_failure,
    _safe_float,
)


class TestParseDtFlexible:
    """_parse_dt_flexible 日期解析测试"""

    def test_standard_format(self):
        """标准格式 2026-01-15 10:30"""
        result = _parse_dt_flexible("2026-01-15 10:30")
        assert result == datetime(2026, 1, 15, 10, 30)

    def test_iso_with_timezone(self):
        """含时区 ISO 格式"""
        result = _parse_dt_flexible("2026-01-15T10:30:00+08:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 15

    def test_iso_with_z(self):
        """Z 结尾的 ISO 格式"""
        result = _parse_dt_flexible("2026-01-15T02:30:00Z")
        assert result is not None

    def test_unknown_returns_none(self):
        """'unknown' → None"""
        assert _parse_dt_flexible("unknown") is None

    def test_empty_returns_none(self):
        assert _parse_dt_flexible("") is None

    def test_none_returns_none(self):
        assert _parse_dt_flexible(None) is None

    def test_slash_format(self):
        """斜杠分隔 2026/01/15 10:30"""
        result = _parse_dt_flexible("2026/01/15 10:30")
        assert result == datetime(2026, 1, 15, 10, 30)

    def test_compact_format(self):
        """紧凑格式 20260115103000"""
        result = _parse_dt_flexible("20260115103000")
        assert result == datetime(2026, 1, 15, 10, 30, 0)


class TestExtractDate:
    """_extract_date_yyyy_mm_dd 日期提取测试"""

    def test_extract_from_text(self):
        assert _extract_date_yyyy_mm_dd("航班日期2026-03-15起飞") == "2026-03-15"

    def test_slash_format(self):
        assert _extract_date_yyyy_mm_dd("2026/03/15") == "2026-03-15"

    def test_no_date(self):
        assert _extract_date_yyyy_mm_dd("无日期文本") == ""

    def test_empty(self):
        assert _extract_date_yyyy_mm_dd("") == ""


class TestClassifyAviationFailure:
    """_classify_aviation_failure 航班查询失败分类测试"""

    def test_success_is_none(self):
        assert _classify_aviation_failure({"success": True}) == "none"

    def test_empty_dict_is_none(self):
        assert _classify_aviation_failure({}) == "none"

    def test_timeout_is_system_error(self):
        assert _classify_aviation_failure({"success": False, "error": "timeout"}) == "system_error"

    def test_network_is_system_error(self):
        assert _classify_aviation_failure({"success": False, "error": "network error"}) == "system_error"

    def test_not_found_is_evidence_gap(self):
        assert _classify_aviation_failure({"success": False, "error": "未找到航班"}) == "evidence_gap"

    def test_query_failed_is_evidence_gap(self):
        assert _classify_aviation_failure({"success": False, "error": "查询失败"}) == "evidence_gap"


class TestSafeFloat:
    """_safe_float 安全浮点转换测试"""

    def test_integer_string(self):
        assert _safe_float("123") == 123.0

    def test_decimal_string(self):
        assert _safe_float("123.45") == 123.45

    def test_comma_format(self):
        """千分位逗号格式"""
        assert _safe_float("1,234.56") == 1234.56

    def test_non_numeric(self):
        assert _safe_float("abc") is None

    def test_none(self):
        assert _safe_float(None) is None

    def test_empty(self):
        assert _safe_float("") is None