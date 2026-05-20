"""赔付计算 Skill 单元测试"""
import pytest
from app.skills.compensation import (
    tier_lookup,
    calculate_payout,
    parse_tier_config_from_terms,
)
from app.rules.claim_types.flight_delay import FLIGHT_DELAY_TIERS
from app.rules.claim_types.baggage_delay import BAGGAGE_DELAY_TIERS


class TestTierLookup:
    """tier_lookup 档位匹配测试"""

    # ── 航班延误档位 ──

    def test_flight_5h_tier(self):
        """延误 5h (300 分钟)→ 命中 300 元档"""
        result = tier_lookup(300, FLIGHT_DELAY_TIERS)
        assert result["amount"] == 300
        assert result["matched_tier"]["min_hours"] == 5
        assert result["matched_tier"]["max_hours"] == 10

    def test_flight_10h_tier(self):
        """延误 10h → 命中 600 元档"""
        result = tier_lookup(600, FLIGHT_DELAY_TIERS)
        assert result["amount"] == 600
        assert result["matched_tier"]["min_hours"] == 10

    def test_flight_25h_max_tier(self):
        """延误 25h → 命中最高档 1200"""
        result = tier_lookup(1500, FLIGHT_DELAY_TIERS)
        assert result["amount"] == 1200
        assert result["matched_tier"]["max_hours"] is None

    def test_flight_below_threshold(self):
        """延误 3h (180 分钟) → 未达起赔门槛"""
        result = tier_lookup(180, FLIGHT_DELAY_TIERS)
        assert result["amount"] == 0
        assert result["matched_tier"] is None

    def test_flight_zero_minutes(self):
        """0 分钟 → amount=0"""
        result = tier_lookup(0, FLIGHT_DELAY_TIERS)
        assert result["amount"] == 0
        assert result["matched_tier"] is None

    # ── 行李延误档位 ──

    def test_baggage_6h_tier(self):
        """行李延误 6h → 500 元"""
        result = tier_lookup(360, BAGGAGE_DELAY_TIERS)
        assert result["amount"] == 500

    def test_baggage_12h_tier(self):
        """行李延误 12h → 1000 元"""
        result = tier_lookup(720, BAGGAGE_DELAY_TIERS)
        assert result["amount"] == 1000

    def test_baggage_18h_tier(self):
        """行李延误 18h → 1500 元"""
        result = tier_lookup(1080, BAGGAGE_DELAY_TIERS)
        assert result["amount"] == 1500

    def test_baggage_below_threshold(self):
        """行李延误 4h → 未达门槛"""
        result = tier_lookup(240, BAGGAGE_DELAY_TIERS)
        assert result["amount"] == 0

    # ── 默认档位兜底 ──

    def test_default_tier_config_fallback(self):
        """无 config 参数 → 使用默认航班延误档位"""
        result = tier_lookup(360, None)
        assert result["amount"] == 300  # 6h 命中默认第二档

    # ── 展示格式 ──

    def test_delay_hours_display_with_minutes(self):
        """390 分钟 → "6小时30分" """
        result = tier_lookup(390, FLIGHT_DELAY_TIERS)
        assert result["delay_hours_display"] == "6小时30分"

    def test_delay_hours_display_exact_hour(self):
        """300 分钟 → "5小时" """
        result = tier_lookup(300, FLIGHT_DELAY_TIERS)
        assert result["delay_hours_display"] == "5小时"


class TestCalculatePayout:
    """calculate_payout 限额比对测试"""

    def test_claim_greater_than_calculated(self):
        """索赔金额 > 计算金额 → 取计算金额"""
        result = calculate_payout(360, 800, 1200)
        # tier_lookup(360) → 300；300 < 800 → 取 300
        assert result["final_amount"] == 300

    def test_claim_less_than_calculated(self):
        """索赔金额 < 计算金额 → 取索赔金额"""
        result = calculate_payout(360, 200, 1200)
        # tier_lookup(360) → 300；200 < 300 → 取 200
        assert result["final_amount"] == 200

    def test_exceeds_insured_amount(self):
        """超保单限额 → 截断为限额"""
        result = calculate_payout(360, 500, 250)
        # tier_lookup(360) → 300；min(300, 500, 250) → 250
        assert result["final_amount"] == 250

    def test_exceeds_remaining_coverage(self):
        """超剩余保额 → 截断"""
        result = calculate_payout(360, 500, 1200, remaining_coverage=200)
        # tier_lookup(360) → 300；min(300, 500, 200) → 200
        assert result["final_amount"] == 200

    def test_claim_none_no_cap(self):
        """无索赔金额无保额——用 tier 金额兜底"""
        result = calculate_payout(360, None, None)
        assert result["final_amount"] == 300

    def test_below_threshold_zero_payout(self):
        """未达门槛 → final_amount=0"""
        result = calculate_payout(60, 500, 1200)
        assert result["final_amount"] == 0


class TestParseTierConfig:
    """parse_tier_config_from_terms 条款解析测试"""

    def test_single_tier(self):
        """单档位标准格式"""
        tiers = parse_tier_config_from_terms("延误满6小时赔付500元")
        assert tiers is not None
        assert len(tiers) == 1
        assert tiers[0]["min_hours"] == 6
        assert tiers[0]["amount"] == 500

    def test_multi_tier(self):
        """多档位 + max_hours 衔接"""
        tiers = parse_tier_config_from_terms("延误满6小时赔付500元，延误满12小时赔付1000元")
        assert tiers is not None
        assert len(tiers) == 2
        assert tiers[0]["max_hours"] == 12
        assert tiers[1]["max_hours"] is None

    def test_empty_text(self):
        """空文本 → None"""
        assert parse_tier_config_from_terms("") is None

    def test_no_match(self):
        """不含档位格式 → None"""
        assert parse_tier_config_from_terms("本险种为旅行延误险") is None