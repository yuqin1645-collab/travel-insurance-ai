"""Pipeline 共享工具 单元测试"""
import pytest
from app.engine.pipeline_shared import (
    is_system_failure_reason,
    build_stage_error_return_from_reason,
)


class TestIsSystemFailureReason:
    """is_system_failure_reason 系统异常判定测试"""

    def test_timeout_is_system_error(self):
        assert is_system_failure_reason("timeout") is True

    def test_api_failure_is_system_error(self):
        assert is_system_failure_reason("API调用失败") is True

    def test_http_429_is_system_error(self):
        assert is_system_failure_reason("HTTP 429 Too Many Requests") is True

    def test_http_503_is_system_error(self):
        assert is_system_failure_reason("503 Service Unavailable") is True

    def test_cannot_connect_is_system_error(self):
        assert is_system_failure_reason("Cannot connect to host") is True

    def test_timed_out_is_system_error(self):
        assert is_system_failure_reason("Connection timed out") is True

    def test_normal_business_reject_is_not_system_error(self):
        """正常业务拒赔 → False"""
        assert is_system_failure_reason("拒赔：延误时长未达到6小时赔付门槛") is False

    def test_empty_string_is_not_system_error(self):
        assert is_system_failure_reason("") is False

    def test_none_is_not_system_error(self):
        assert is_system_failure_reason(None) is False


class TestBuildStageErrorReturnFromReason:
    """build_stage_error_return_from_reason 错误回包构建测试"""

    def test_basic_output_format(self):
        result = build_stage_error_return_from_reason(
            forceid="test_forceid",
            checkpoint="coverage_check",
            reason="网络超时",
            ctx={"debug": []},
        )
        assert result["forceid"] == "test_forceid"
        assert result["IsAdditional"] == "Y"
        assert result["KeyConclusions"][0]["checkpoint"] == "coverage_check"
        assert "coverage_check" in result["Remark"]
        assert "网络超时" in result["Remark"]

    def test_long_reason_truncated(self):
        """超过 200 字符的 reason → 截断"""
        long_reason = "X" * 300
        result = build_stage_error_return_from_reason(
            forceid="test",
            checkpoint="test_checkpoint",
            reason=long_reason,
            ctx={},
        )
        # Remark 中不应出现超过 200 字符的 reason
        assert len(result["KeyConclusions"][0]["Remark"]) <= 300  # remark 有前缀

    def test_debug_info_preserved(self):
        """ctx 内容保留在 DebugInfo"""
        ctx = {"debug": [{"stage": "test", "attempt": 1, "error": "xxx"}]}
        result = build_stage_error_return_from_reason(
            forceid="test",
            checkpoint="test_checkpoint",
            reason="reason",
            ctx=ctx,
        )
        assert result["DebugInfo"] == ctx