"""身份一致性校验规则 单元测试"""
import pytest
from app.rules.common.identity_check import check


class TestIdentityCheck:
    """申请人与保单权益人身份一致性校验测试"""

    def _base_claim(self, **overrides):
        base = {
            "Claimant_Name": "张三",
            "Insured_Name": "张三",
            "Claimant_IDNumber": "110101199001011234",
            "Insured_IDNumber": "110101199001011234",
        }
        base.update(overrides)
        return base

    # ── 姓名一致性 ──

    def test_names_match_passes(self):
        """姓名一致 → 通过"""
        result = check(self._base_claim())
        assert result.passed is True
        assert result.action == "continue"

    def test_names_mismatch_no_relationship(self):
        """姓名不一致且无监护关系 → 拒赔"""
        claim = self._base_claim(
            Claimant_Name="李四",
            Insured_Name="张三",
        )
        result = check(claim)
        assert result.passed is False
        assert result.action == "reject"
        assert "不匹配" in result.reason

    def test_names_mismatch_with_guardian_relationship(self):
        """姓名不一致但有监护关系 → 豁免通过"""
        claim = self._base_claim(
            Claimant_Name="李四",
            Insured_Name="张三",
            Relationship="监护人",
        )
        result = check(claim)
        assert result.passed is True
        assert "relationship_exemption" in result.detail

    def test_names_mismatch_with_parent_relationship(self):
        """英文 parent 关系 → 豁免通过"""
        claim = self._base_claim(
            Claimant_Name="李四",
            Insured_Name="张三",
            Relationship="parent",
        )
        result = check(claim)
        assert result.passed is True

    # ── 证件号一致性 ──

    def test_id_numbers_mismatch(self):
        """证件号不一致 → 拒赔"""
        claim = self._base_claim(
            Claimant_IDNumber="110101199001019999",
        )
        result = check(claim)
        assert result.passed is False
        assert "证件号" in result.reason

    def test_both_name_and_id_mismatch(self):
        """姓名和证件号均不一致 → 拒赔（姓名优先触发）"""
        claim = self._base_claim(
            Claimant_Name="李四",
            Insured_Name="张三",
            Claimant_IDNumber="110101199001019999",
        )
        result = check(claim)
        assert result.passed is False
        assert "姓名" not in result.reason  # 无关系豁免时姓名最先触发
        assert "不匹配" in result.reason

    # ── 空字段跳过 ──

    def test_empty_claimant_name_skipped(self):
        """Claimant_Name 为空 → 字段缺失不拦截"""
        claim = self._base_claim(Claimant_Name="")
        result = check(claim)
        assert result.passed is True

    def test_empty_both_names_skipped(self):
        """双方姓名均为空 → 不拦截"""
        claim = self._base_claim(
            Claimant_Name="",
            Insured_Name="",
        )
        result = check(claim)
        assert result.passed is True

    # ── 证件号细节 ──

    def test_id_suffix_in_detail(self):
        """证件号后缀记录在 detail 中（不泄露完整号码）"""
        claim = self._base_claim(Claimant_IDNumber="110101199001011234")
        result = check(claim)
        assert result.detail.get("claimant_id_suffix") == "1234"
        assert result.detail.get("insured_id_suffix") == "1234"