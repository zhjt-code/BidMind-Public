"""P0 A4 防御测试: 评分项 LLM 兜底忠实性护栏（scoring_fidelity）。

映射真实问题: LLM 兜底提取评分项时可能改写/压缩原文，导致落库的 description/score_rules
与评分表 chunk 原文存在语义漂移，审查人员难以溯源核实。本模块通过字符 bigram 包含率
检测并标记低忠实性条目，使 scoring_metadata 携带可展示的审查标记。

覆盖场景:
- 高重合率原文忠实提取 → 不打 rewritten 标记（flag='ok'）
- 低重合率改写/压缩 → flag='rewritten' 并附 note
- 空 description / 空 source_text → 保守标记 needs_review
- 空 score_rules 不崩溃
- annotate_scoring_fidelity 批量接口正确处理 items 列表
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from app.core.extractor.scoring.scoring_fidelity import (  # noqa: E402
    FidelityAnnotation,
    annotate_scoring_fidelity,
    build_fidelity_key,
    check_item_fidelity,
)

pytestmark = pytest.mark.scoring

# ---------------------------------------------------------------------------
# 测试用评分表原文 chunk（模拟真实评分表内容）
# ---------------------------------------------------------------------------

_SOURCE_TECH = (
    "技术方案 满分20分 方案完善可行得20分 方案基本可行得10分 方案不可行得0分 "
    "项目经理 满分10分 高级职称且5年以上经验得10分 中级职称得5分 初级得0分"
)

_SOURCE_QUAL = (
    "资质条件 满分10分 具备一级资质得10分 具备二级资质得5分 不具备得0分"
)


# ---------------------------------------------------------------------------
# check_item_fidelity: 单条评分项忠实性检查
# ---------------------------------------------------------------------------


class TestCheckItemFidelity:
    """check_item_fidelity 纯函数行为。"""

    def test_faithful_description_returns_ok(self):
        """原文原句提取的 description 与 source 高重合 → flag='ok'。"""
        description = "技术方案，完善可行得20分，基本可行得10分，不可行得0分"
        annotation = check_item_fidelity(description, [], _SOURCE_TECH)
        assert annotation.flag == "ok", (
            f"高重合率应为 ok，实际 flag={annotation.flag}, overlap={annotation.overlap_score:.3f}"
        )
        assert annotation.overlap_score > 0.3

    def test_faithful_with_score_rules_returns_ok(self):
        """description + score_rules 合并后与原文高重合 → flag='ok'。"""
        description = "项目经理配置"
        score_rules = ["高级职称且5年以上经验得10分", "中级职称得5分", "初级得0分"]
        annotation = check_item_fidelity(description, score_rules, _SOURCE_TECH)
        assert annotation.flag == "ok", (
            f"合并 score_rules 后应为 ok，实际 flag={annotation.flag}, overlap={annotation.overlap_score:.3f}"
        )

    def test_rewritten_description_returns_rewritten(self):
        """LLM 改写/压缩后的描述与原文低重合 → flag='rewritten' 且 note 非空。"""
        # 使用完全不相关/重写后的文本
        description = "综合评定施工组织与技术实施能力的整体水平"
        annotation = check_item_fidelity(description, [], _SOURCE_TECH)
        assert annotation.flag == "rewritten", (
            f"低重合率应为 rewritten，实际 flag={annotation.flag}, overlap={annotation.overlap_score:.3f}"
        )
        assert annotation.note, "rewritten 标记必须附 note"
        assert annotation.overlap_score < 0.3

    def test_empty_description_returns_needs_review(self):
        """空 description → 保守标记 needs_review，不崩溃。"""
        annotation = check_item_fidelity("", [], _SOURCE_TECH)
        assert annotation.flag == "needs_review"

    def test_empty_source_text_returns_needs_review(self):
        """空 source_text → 保守标记 needs_review，不崩溃。"""
        annotation = check_item_fidelity("技术方案满分20分", [], "")
        assert annotation.flag == "needs_review"

    def test_none_equivalent_source_returns_needs_review(self):
        """source_text 仅含空白 → needs_review，不崩溃。"""
        annotation = check_item_fidelity("技术方案满分20分", [], "   ")
        assert annotation.flag == "needs_review"

    def test_empty_score_rules_does_not_crash(self):
        """空 score_rules 列表不应引发异常。"""
        result = check_item_fidelity("资质条件，一级资质得10分", [], _SOURCE_QUAL)
        assert result.flag in ("ok", "rewritten", "needs_review")

    def test_returns_fidelity_annotation_type(self):
        """返回值为 FidelityAnnotation dataclass。"""
        result = check_item_fidelity("技术方案", [], _SOURCE_TECH)
        assert isinstance(result, FidelityAnnotation)
        assert result.flag in ("ok", "rewritten", "needs_review")
        assert isinstance(result.overlap_score, float)
        assert 0.0 <= result.overlap_score <= 1.0

    def test_very_short_description_returns_needs_review(self):
        """极短 description（单字/空白）→ needs_review，不崩溃。"""
        annotation = check_item_fidelity("技", [], _SOURCE_TECH)
        assert annotation.flag == "needs_review"

    def test_overlap_score_bounded(self):
        """overlap_score 始终在 [0, 1] 范围内。"""
        cases = [
            ("技术方案满分20分方案完善可行得20分", [], _SOURCE_TECH),
            ("完全无关的新词汇", [], _SOURCE_TECH),
            ("", [], ""),
            ("test", ["abc"], ""),
        ]
        for desc, rules, src in cases:
            ann = check_item_fidelity(desc, rules, src)
            assert 0.0 <= ann.overlap_score <= 1.0, f"overlap_score 超界: {ann.overlap_score}"

    def test_faithful_qualification_item(self):
        """资质类评分项原文忠实提取 → ok。"""
        description = "具备一级资质得10分，具备二级资质得5分，不具备得0分"
        annotation = check_item_fidelity(description, [], _SOURCE_QUAL)
        assert annotation.flag == "ok", (
            f"flag={annotation.flag}, overlap={annotation.overlap_score:.3f}"
        )


# ---------------------------------------------------------------------------
# annotate_scoring_fidelity: 批量标注接口
# ---------------------------------------------------------------------------


class TestAnnotateScoringFidelity:
    """annotate_scoring_fidelity 批量接口行为。"""

    def _make_item(self, description: str, score_rules: list[str] | None = None):
        return SimpleNamespace(
            score_item_name="测试项",
            description=description,
            score_rules=score_rules or [],
        )

    def test_returns_dict(self):
        """批量标注返回 dict。"""
        items = [self._make_item("技术方案完善可行得20分")]
        result = annotate_scoring_fidelity(items, _SOURCE_TECH)
        assert isinstance(result, dict)

    def test_faithful_item_in_batch_is_ok(self):
        """高重合率的批量项标记为 ok。"""
        item = self._make_item("技术方案完善可行得20分，基本可行得10分")
        result = annotate_scoring_fidelity([item], _SOURCE_TECH)
        # 至少一条记录且 flag 正确
        assert len(result) == 1
        annotation = next(iter(result.values()))
        assert annotation.flag == "ok"

    def test_rewritten_item_in_batch_is_flagged(self):
        """低重合率批量项被标记 rewritten。"""
        item = self._make_item("综合衡量投标人的整体履约能力与实施水平")
        result = annotate_scoring_fidelity([item], _SOURCE_TECH)
        assert len(result) == 1
        annotation = next(iter(result.values()))
        assert annotation.flag == "rewritten"

    def test_empty_items_returns_empty_dict(self):
        """空 items 列表 → 空字典，不崩溃。"""
        result = annotate_scoring_fidelity([], _SOURCE_TECH)
        assert result == {}

    def test_mixed_fidelity_batch(self):
        """混合高/低重合率批量项 → 各自独立标注。"""
        faithful = self._make_item("技术方案完善可行得20分")
        rewritten = self._make_item("综合履约能力综合评定满分值")
        result = annotate_scoring_fidelity([faithful, rewritten], _SOURCE_TECH)
        assert len(result) == 2
        flags = list(r.flag for r in result.values())
        assert "ok" in flags
        assert "rewritten" in flags

    def test_item_with_empty_description_in_batch_is_needs_review(self):
        """批量中的空 description 项标记为 needs_review，不影响其他项。"""
        empty_item = self._make_item("")
        normal_item = self._make_item("技术方案完善可行得20分")
        result = annotate_scoring_fidelity([empty_item, normal_item], _SOURCE_TECH)
        assert len(result) >= 1  # 空description可能与normal合并key
        # 批量中至少包含一个 needs_review
        flags = {ann.flag for ann in result.values()}
        assert "needs_review" in flags

    def test_empty_source_text_all_needs_review(self):
        """空 source_text → 所有项标记 needs_review。"""
        items = [
            self._make_item("技术方案完善可行"),
            self._make_item("项目经理高级职称"),
        ]
        result = annotate_scoring_fidelity(items, "")
        for ann in result.values():
            assert ann.flag == "needs_review"

    def test_sidecar_key_uses_structural_fields_not_description_prefix_only(self):
        """sidecar 键包含名称/分值/完整描述，避免跨 chunk 同前缀描述错挂。"""
        item_a = self._make_item("业绩要求" + "甲" * 80)
        item_a.score_item_name = "业绩金额"
        item_a.score_weight = 20.0
        item_b = self._make_item("业绩要求" + "甲" * 80)
        item_b.score_item_name = "业绩数量"
        item_b.score_weight = 10.0

        assert build_fidelity_key(item_a) != build_fidelity_key(item_b)
        result = annotate_scoring_fidelity([item_a, item_b], _SOURCE_TECH)

        assert set(result.keys()) == {build_fidelity_key(item_a), build_fidelity_key(item_b)}

    def test_same_description_prefix_different_tail_does_not_collide(self):
        """完整 description 参与 sidecar 键，前60字符相同但尾部不同也不覆盖。"""
        prefix = "技术方案评分标准" + "甲" * 50
        item_a = self._make_item(prefix + "A")
        item_a.score_weight = 20.0
        item_b = self._make_item(prefix + "B")
        item_b.score_weight = 20.0

        result = annotate_scoring_fidelity([item_a, item_b], _SOURCE_TECH)

        assert len(result) == 2
