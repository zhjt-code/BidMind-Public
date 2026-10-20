""" 规范化材料键两级匹配。

覆盖风险点:
1. 强匹配放行（短义务名词命中文档中的完整材料名）;
2. 双方均有修饰语且零重叠时拒绝（防跨专业冒认）;
3. 同名异主体 / 异证号不互认;
4. 仅中心语一致为弱匹配, 不得当硬定谳。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.retriever.material_key import (
    MATCH_NONE,
    MATCH_STRONG,
    MATCH_WEAK,
    canonical_material_key,
    match_material_in_text,
    match_material_keys,
)


class TestCanonicalKey:
    def test_strips_generic_prefix_and_tail_noise(self):
        assert canonical_material_key("投标人准入证等材料") == "准入证"

    def test_nfkc_fullwidth_digits_and_spaces(self):
        assert canonical_material_key("授 权　委托书") == "授权委托书"


class TestStrongMatch:
    def test_short_key_contained_in_specific_document_name(self):
        assert (
            match_material_keys(
                "准入证",
                "河南省建设工程企业资质和资格管理系统专业准入证",
            )
            == MATCH_STRONG
        )

    def test_authorization_letter_exact(self):
        assert match_material_keys("授权委托书", "授权委托书") == MATCH_STRONG

    def test_social_insurance_morphological_variant(self):
        """同形态第三实体: 社保缴费证明 ≈ 社会保险缴费证明。"""
        assert match_material_keys("社保缴费证明", "社会保险缴费证明") == MATCH_STRONG


class TestModifierReject:
    def test_disjoint_modifiers_rejected(self):
        assert (
            match_material_keys("矿山开采专业准入证", "科研服务专业准入证")
            == MATCH_NONE
        )

    def test_disjoint_modifiers_not_bridged_by_same_identifier(self):
        assert (
            match_material_keys(
                "矿山开采专业准入证",
                "科研服务专业准入证",
                left_identifier="ABC-001",
                right_identifier="ABC-001",
            )
            == MATCH_NONE
        )


class TestSubjectAndIdentifier:
    def test_same_name_different_subject_not_mutual(self):
        assert (
            match_material_keys(
                "授权委托书",
                "授权委托书",
                left_subject="<ORG_23>",
                right_subject="<ORG_2>",
            )
            == MATCH_NONE
        )

    def test_same_name_different_identifier_not_mutual(self):
        assert (
            match_material_keys(
                "准入证",
                "准入证",
                left_identifier="YZ-1001",
                right_identifier="YZ-2002",
            )
            == MATCH_NONE
        )


class TestWeakMatch:
    def test_generic_two_char_head_is_weak(self):
        assert match_material_keys("证书", "建造师注册证书") == MATCH_WEAK

    def test_weak_never_upgraded_by_identifier(self):
        assert (
            match_material_keys(
                "证书",
                "建造师注册证书",
                left_identifier="X-1",
                right_identifier="X-1",
            )
            == MATCH_WEAK
        )


class TestMatchInText:
    def test_strong_when_specific_name_appears_in_ocr(self):
        text = "扫描件：河南省建设工程企业资质和资格管理系统专业准入证 编号YZ001"
        assert match_material_in_text("准入证", text) == MATCH_STRONG

    def test_none_when_other_specialty_in_text(self):
        text = "科研服务专业准入证 编号RS-9"
        assert match_material_in_text("矿山开采专业准入证", text) == MATCH_NONE
