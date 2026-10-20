"""防御测试: scoring_prompt_block — 评分项评审标准提示块与检索扩展文本构造

覆盖:
- build_scoring_context: scoring_item / is_key_review+score_weight → 生成标准块
- build_scoring_context: authoritative_description 优先出现
- build_scoring_context: 普通 normal+非 is_key_review → 返回空
- build_query_extension: 包含 score_item_name/score_rules 关键词, 去重, 长度受控
- build_query_extension: 非评分项 → 返回空
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.scoring_prompt_block import (
    build_scoring_context,
    build_query_extension,
)
from app.core.ai.prompts.scoring_extract import (  # noqa: E402
    SCORING_EXTRACT_SYSTEM,
    SCORING_EXTRACT_USER,
)
from app.core.ai.prompts.scoring_repair import (  # noqa: E402
    SCORING_REPAIR_SYSTEM,
    SCORING_REPAIR_USER,
)

pytestmark = pytest.mark.scoring


def test_scoring_extract_prompt_requires_group_volume_priority_and_original_label():
    """: LLM兜底路径必须对齐确定性路径的分组优先与原文卷名字段。"""
    combined = f"{SCORING_EXTRACT_SYSTEM}\n{SCORING_EXTRACT_USER}"
    assert "表内分组" in combined
    assert "不得覆盖分组" in combined
    assert "volume_label" in combined


def test_scoring_repair_prompt_requires_group_volume_priority_and_original_label():
    """: LLM修补路径必须输出原文卷名, 且项名关键词不得覆盖分组归属。"""
    combined = f"{SCORING_REPAIR_SYSTEM}\n{SCORING_REPAIR_USER}"
    assert "表内分组" in combined
    assert "不得覆盖分组" in combined
    assert "volume_label" in combined


# ---------------------------------------------------------------------------
# 辅助: 构造 mock Requirement 对象
# ---------------------------------------------------------------------------


def _make_req(
    requirement_kind: str = "normal",
    is_key_review: int = 0,
    score_weight: float | None = None,
    scoring_metadata: dict | None = None,
    description: str = "投标人须具有建筑工程施工总承包一级资质",
):
    req = MagicMock()
    req.requirement_kind = requirement_kind
    req.is_key_review = is_key_review
    req.score_weight = score_weight
    req.scoring_metadata = scoring_metadata
    req.description = description
    return req


# ---------------------------------------------------------------------------
# build_scoring_context
# ---------------------------------------------------------------------------


class TestBuildScoringContext:

    def test_scoring_item_generates_block_with_required_keywords(self):
        """scoring_item 类型应生成包含 '评分项''满分''评分/分档规则' 关键词的块"""
        req = _make_req(
            requirement_kind="scoring_item",
            score_weight=10.0,
            scoring_metadata={
                "score_item_name": "技术方案",
                "max_score": 10.0,
                "score_rules": ["优秀10分，良好8分，一般6分"],
                "deduction_rules": [],
            },
        )
        block = build_scoring_context(req)

        assert block, "评分项应返回非空块"
        assert "评分项" in block
        assert "满分" in block
        assert "技术方案" in block
        assert "10" in block
        assert "优秀10分" in block
        # score_rules 应体现"评分"相关
        assert "评分" in block or "分档" in block

    def test_is_key_review_with_score_weight_also_generates_block(self):
        """is_key_review=1 且 score_weight 非空时也应生成评分块（非 scoring_item）"""
        req = _make_req(
            requirement_kind="normal",
            is_key_review=1,
            score_weight=5.0,
            scoring_metadata={
                "score_item_name": "业绩评分",
                "max_score": 5.0,
                "score_rules": ["有类似业绩得满分5分，无得0分"],
                "deduction_rules": [],
            },
        )
        block = build_scoring_context(req)

        assert block, "重点评审项应返回非空块"
        assert "业绩评分" in block
        assert "5" in block

    def test_authoritative_description_appears_in_block(self):
        """authoritative_description 应出现在块中"""
        req = _make_req(
            requirement_kind="scoring_item",
            score_weight=8.0,
            scoring_metadata={
                "score_item_name": "质量管理",
                "max_score": 8.0,
                "score_rules": ["通过ISO9001认证得8分"],
                "deduction_rules": [],
                "authoritative_description": "投标人须提供有效的ISO9001质量管理体系认证证书",
            },
        )
        block = build_scoring_context(req)

        assert "ISO9001质量管理体系认证证书" in block

    def test_normal_non_key_review_returns_empty(self):
        """普通 normal 且 is_key_review=0 → 返回空字符串"""
        req = _make_req(
            requirement_kind="normal",
            is_key_review=0,
            score_weight=None,
        )
        block = build_scoring_context(req)

        assert block == ""

    def test_is_key_review_without_score_weight_returns_empty(self):
        """is_key_review=1 但 score_weight=None → 返回空（不是真正评分项）"""
        req = _make_req(
            requirement_kind="normal",
            is_key_review=1,
            score_weight=None,
        )
        block = build_scoring_context(req)

        assert block == ""

    def test_scoring_item_with_null_metadata_does_not_raise(self):
        """scoring_item 但 scoring_metadata=None 时应优雅处理，不抛异常"""
        req = _make_req(
            requirement_kind="scoring_item",
            score_weight=3.0,
            scoring_metadata=None,
        )
        block = build_scoring_context(req)

        assert isinstance(block, str)

    def test_deduction_rules_appear_when_present(self):
        """存在 deduction_rules 时应出现在块中"""
        req = _make_req(
            requirement_kind="scoring_item",
            score_weight=10.0,
            scoring_metadata={
                "score_item_name": "项目经理",
                "max_score": 10.0,
                "score_rules": ["正高级职称得10分"],
                "deduction_rules": ["每缺一份材料扣2分"],
            },
        )
        block = build_scoring_context(req)

        assert "每缺一份材料扣2分" in block


# ---------------------------------------------------------------------------
# build_query_extension
# ---------------------------------------------------------------------------


class TestBuildQueryExtension:

    def test_scoring_item_extension_contains_score_item_name(self):
        """评分项扩展文本应包含 score_item_name（当 description 中没有时）"""
        req = _make_req(
            requirement_kind="scoring_item",
            score_weight=10.0,
            scoring_metadata={
                "score_item_name": "信息化建设平台",
                "max_score": 10.0,
                "score_rules": ["有系统平台得满分10分"],
                "deduction_rules": [],
            },
            description="请提供相关证明材料",
        )
        ext = build_query_extension(req)

        assert ext, "评分项应返回非空扩展文本"
        assert "信息化建设平台" in ext

    def test_extension_deduplicates_with_description(self):
        """score_item_name 已在 description 中时不重复追加"""
        req = _make_req(
            requirement_kind="scoring_item",
            score_weight=5.0,
            scoring_metadata={
                "score_item_name": "技术方案",
                "score_rules": ["完整详尽得5分"],
            },
            description="技术方案的完整性与可行性评审",
        )
        ext = build_query_extension(req)

        # "技术方案" 已在 description 中，不应再出现在扩展中
        assert "技术方案" not in ext

    def test_extension_length_controlled(self):
        """扩展文本总长度不超过 120 字符"""
        req = _make_req(
            requirement_kind="scoring_item",
            score_weight=10.0,
            scoring_metadata={
                "score_item_name": "A" * 50,
                "score_rules": ["B" * 50, "C" * 50, "D" * 50, "E" * 50, "F" * 50],
            },
        )
        ext = build_query_extension(req)

        assert len(ext) <= 120, f"扩展文本超过120字符: len={len(ext)}"

    def test_normal_non_scoring_returns_empty(self):
        """普通 normal 且 is_key_review=0 → 返回空字符串"""
        req = _make_req(
            requirement_kind="normal",
            is_key_review=0,
            score_weight=None,
        )
        ext = build_query_extension(req)

        assert ext == ""

    def test_is_key_review_without_score_weight_returns_empty(self):
        """is_key_review=1 但 score_weight=None → 返回空"""
        req = _make_req(
            requirement_kind="normal",
            is_key_review=1,
            score_weight=None,
        )
        ext = build_query_extension(req)

        assert ext == ""

    def test_score_rules_snippet_appended_when_not_in_description(self):
        """description 中未出现的 score_rules 片段应追加到扩展文本"""
        req = _make_req(
            requirement_kind="scoring_item",
            score_weight=10.0,
            scoring_metadata={
                "score_item_name": "综合实力",
                "score_rules": ["特级资质得满分"],
                "deduction_rules": [],
            },
            description="请提供资质证书",
        )
        ext = build_query_extension(req)

        assert "综合实力" in ext or "特级资质" in ext, (
            f"扩展文本应包含 score_item_name 或 score_rules 片段，实际: {ext!r}"
        )
