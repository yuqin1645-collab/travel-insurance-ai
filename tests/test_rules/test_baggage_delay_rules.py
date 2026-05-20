"""行李延误险规则 单元测试"""
import pytest
from app.rules.claim_types.baggage_delay import compute_payout


class TestBaggageDelayComputePayout:
    """行李延误赔付金额核算测试"""

    # ── 基础赔付 ──

    def test_7h_payout_500(self):
        """延误 7h → 500 元"""
        result = compute_payout(7, 500, 1000)
        assert result.passed is True
        assert result.action == "approve"
        assert result.detail["payout"] == 500

    def test_13h_payout_1000(self):
        """延误 13h → 1000 元"""
        result = compute_payout(13, 2000, 2000)
        assert result.detail["payout"] == 1000

    def test_18h_payout_1500(self):
        """延误 18h → 1500 元（最高档）"""
        result = compute_payout(18, 2000, 2000)
        assert result.detail["payout"] == 1500

    def test_25h_payout_still_1500(self):
        """延误 25h → 仍然 1500（最高无上限）"""
        result = compute_payout(25, 2000, 2000)
        assert result.passed is True
        assert result.detail["payout"] == 1500

    # ── 门槛 ──

    def test_5h_below_threshold(self):
        """延误 5h < 6h → 拒赔"""
        result = compute_payout(5, 500, 1200)
        assert result.passed is False
        assert result.action == "reject"
        assert result.detail["payout"] == 0
        assert "未达到" in result.reason

    def test_2h_below_threshold(self):
        """延误 2h → 拒赔"""
        result = compute_payout(2, 500, 1200)
        assert result.passed is False

    # ── 索赔金额校准 ──

    def test_claim_less_than_tier(self):
        """索赔金额 < 档位 → 取较小值"""
        result = compute_payout(7, 300, 1000)
        assert result.detail["payout"] == 300

    def test_claim_greater_than_tier(self):
        """索赔金额 > 档位 → 取档位金额"""
        result = compute_payout(7, 800, 1000)
        assert result.detail["payout"] == 500

    # ── 保额上限 ──

    def test_cap_limit_truncates(self):
        """cap=400 → 截断为 400"""
        result = compute_payout(7, 500, 400)
        assert result.detail["payout"] == 400

    def test_cap_none_unlimited(self):
        """cap=None → 不受限"""
        result = compute_payout(7, 500, None)
        assert result.detail["payout"] == 500

    def test_cap_zero(self):
        """cap=0 → 赔付截断为 0"""
        result = compute_payout(7, 500, 0)
        assert result.detail["payout"] == 0

    # ── 责任竞合 ──

    def test_concurrency_personal_effect_higher(self):
        """随身财产 800 > 行李延误 500 → 取 800（再被 claim_amount=1000 截断）"""
        result = compute_payout(7, 1000, 1200, personal_effect_claim=800)
        # max(500, 800) = 800, min(800, 1000) = 800
        assert result.detail["payout"] == 800

    def test_concurrency_baggage_delay_higher(self):
        """行李延误 1500 > 随身财产 500 → 仍取 1500"""
        result = compute_payout(20, 1500, 2000, personal_effect_claim=500)
        assert result.detail["payout"] == 1500

    def test_concurrency_with_cap(self):
        """竞合取高后仍受 cap 约束"""
        result = compute_payout(7, 500, 600, personal_effect_claim=800)
        # max(500, 800) = 800, min(800, 500, 600) = 500
        assert result.detail["payout"] == 500