"""材料门禁规则 单元测试"""
import pytest
from app.rules.common.material_gate import check, FLIGHT_DELAY_KEYWORDS, BAGGAGE_DELAY_KEYWORDS


class TestMaterialGate:
    """材料门禁关键词语法匹配测试"""

    def test_all_materials_present(self):
        """全部关键词命中 → 通过"""
        result = check(
            text_blob="航班延误证明信函 delay proof",
            file_names=["理赔申请书.pdf", "身份证.jpg", "登机牌.png"],
            keyword_map=FLIGHT_DELAY_KEYWORDS,
        )
        assert result.passed is True
        assert result.action == "continue"

    def test_missing_multiple_materials(self):
        """缺少多种材料 → 列出所有缺失项"""
        result = check(
            text_blob="",
            file_names=["身份证.jpg"],
            keyword_map=FLIGHT_DELAY_KEYWORDS,
        )
        assert result.passed is False
        assert result.action == "supplement"
        missing = result.detail.get("missing", [])
        assert len(missing) > 1
        assert "被保险人身份证" not in missing  # 已提供

    def test_empty_file_list(self):
        """空文件列表 → 补充全部材料"""
        result = check(
            text_blob="",
            file_names=[],
            keyword_map=FLIGHT_DELAY_KEYWORDS,
        )
        assert result.passed is False
        assert result.detail.get("no_files") is True

    def test_keyword_in_text_blob_only(self):
        """关键词在 text_blob 中，不在文件名中 → 仍然通过"""
        result = check(
            text_blob="延误证明 delay 登机牌 boarding ticket 身份证",
            file_names=["理赔申请书.doc"],
            keyword_map=FLIGHT_DELAY_KEYWORDS,
        )
        assert result.passed is True

    def test_keyword_in_filename_only(self):
        """关键词仅在文件名中 → 不一定全部命中"""
        result = check(
            text_blob="",
            file_names=["延误证明信函.pdf"],
            keyword_map=FLIGHT_DELAY_KEYWORDS,
        )
        # 仅靠文件名匹配"延误证明"，不一定全部命中（还有"理赔申请书"等）
        missing = result.detail.get("missing", [])
        assert "延误证明（含延误时间及原因）" not in missing

    def test_single_keyword_sufficient(self):
        """每种材料只需命中一个关键词"""
        result = check(
            text_blob="claim form boarding",
            file_names=["身份证正反面.jpg"],
            keyword_map=FLIGHT_DELAY_KEYWORDS,
        )
        # "claim form" 命中理赔申请书，"boarding" 命中交通票据
        # 还缺 "延误证明"
        missing = result.detail.get("missing", [])
        assert len(missing) == 1

    def test_missing_claim_form_in_detail(self):
        """缺理赔申请书 → detail 中明确列出"""
        result = check(
            text_blob="delay 延误证明 身份证 ticket 登机牌",
            file_names=[],
            keyword_map=FLIGHT_DELAY_KEYWORDS,
        )
        missing = result.detail.get("missing", [])
        assert "理赔申请书" in missing

    def test_case_insensitive_matching(self):
        """文件名英文关键词匹配（"claim form" 需含空格）"""
        result = check(
            text_blob="",
            file_names=["claim form.PDF", "IDENTITY.JPG", "BOARDING.PNG", "delay proof.delay"],
            keyword_map=FLIGHT_DELAY_KEYWORDS,
        )
        assert result.passed is True

    def test_baggage_delay_keywords(self):
        """行李延误关键词映射正常工作"""
        result = check(
            text_blob="pir baggage delay 签收 receipt 护照 passport 委托书",
            file_names=["理赔申请书.pdf", "身份证.jpg", "银行卡.jpg", "登机牌.png"],
            keyword_map=BAGGAGE_DELAY_KEYWORDS,
        )
        assert result.passed is True

    def test_baggage_delay_missing_bank_card(self):
        """行李延误缺银行卡 → 补件"""
        result = check(
            text_blob="pir 签收 护照 身份证 机票 理赔申请",
            file_names=[],
            keyword_map=BAGGAGE_DELAY_KEYWORDS,
        )
        missing = result.detail.get("missing", [])
        assert any("银行卡" in m for m in missing)