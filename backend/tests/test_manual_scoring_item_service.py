"""手动补录评分项服务契约测试。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.schemas.requirement import ManualScoringItemCreate  # noqa: E402
from app.services.manual_scoring_item_service import (  # noqa: E402
    build_manual_scoring_requirement_attrs,
)

pytestmark = pytest.mark.scoring


def test_build_manual_scoring_requirement_attrs_sets_scoring_invariants():
    """手动评分项必须写入评分项全集所需的所有稳定字段。"""
    data = ManualScoringItemCreate(
        category="TECH",
        score_item_name="施工组织设计",
        description="施工组织设计完整、可行，最高得20分",
        score_weight=20,
        score_rules=["方案完整得20分", "缺少关键措施酌情扣分"],
        deduction_rules=["未提供不得分"],
        volume="technical",
        volume_label="技术部分",
        lot="采购包1",
        source_pages=[12, 13],
    )

    attrs = build_manual_scoring_requirement_attrs(
        project_id=1589,
        document_id=1441,
        data=data,
    )

    assert attrs["project_id"] == 1589
    assert attrs["document_id"] == 1441
    assert attrs["requirement_kind"] == "scoring_item"
    assert attrs["is_key_review"] == 1
    assert attrs["review_relevance"] == "actionable"
    assert attrs["is_mandatory"] == 0
    assert attrs["confirmed"] == 0
    assert attrs["score_weight"] == 20
    assert attrs["source_page"] == 12
    assert attrs["source_pages"] == "[12, 13]"
    assert attrs["keywords"] == '["施工组织设计"]'

    metadata = attrs["scoring_metadata"]
    assert metadata["source"] == "manual"
    assert metadata["max_score"] == 20
    assert metadata["score_item_name"] == "施工组织设计"
    assert metadata["score_rules"] == ["方案完整得20分", "缺少关键措施酌情扣分"]
    assert metadata["deduction_rules"] == ["未提供不得分"]
    assert metadata["volume"] == "technical"
    assert metadata["volume_label"] == "技术部分"
    assert metadata["lot"] == "采购包1"


def test_manual_scoring_item_requires_positive_score_weight():
    """手动评分项满分必须为正数，避免无分值项污染评分项全集。"""
    with pytest.raises(ValueError):
        ManualScoringItemCreate(
            category="TECH",
            score_item_name="施工组织设计",
            description="施工组织设计完整、可行，最高得20分",
            score_weight=0,
        )
