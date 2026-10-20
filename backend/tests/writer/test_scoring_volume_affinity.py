"""评分项分卷亲和性防御测试。

映射风险: 目录生成按分卷筛选评分项时若只看正文关键词, 会把带有"技术"字样的价格/
商务评分项误塞进技术卷；权威评分项应优先消费 metadata.volume。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.writer.outline_generator import OutlineGenerator  # noqa: E402
from app.core.writer.scoring_volume_affinity import (  # noqa: E402
    volume_category_from_metadata,
)

import pytest

pytestmark = pytest.mark.scoring


def _clause(sub_type="scoring_criterion", content="技术报价评分", *, volume=None):
    metadata = {"volume": volume} if volume is not None else None
    return SimpleNamespace(
        sub_type=sub_type,
        content=content,
        metadata_json=metadata,
    )


def test_volume_category_from_metadata_maps_known_scoring_volumes() -> None:
    assert volume_category_from_metadata(_clause(volume="technical")) == "technical"
    assert volume_category_from_metadata(_clause(volume="commercial")) == "commercial"
    assert volume_category_from_metadata(_clause(volume="qualification")) == "qualification"
    assert volume_category_from_metadata(_clause(volume="price")) == "commercial"
    assert volume_category_from_metadata(_clause(volume="other")) is None
    assert volume_category_from_metadata(_clause(volume=None)) is None


def test_volume_category_documents_unrouted_known_volumes() -> None:
    assert volume_category_from_metadata(_clause(volume="staffing")) is None
    assert volume_category_from_metadata(_clause(volume="performance")) is None
    assert volume_category_from_metadata(_clause(volume="unspecified")) is None


def test_outline_volume_filter_prefers_scoring_metadata_volume() -> None:
    generator = object.__new__(OutlineGenerator)
    price_scoring = _clause(content="技术报价评分公式", volume="price")
    tech_scoring = _clause(content="技术方案完整性", volume="technical")
    plain_content = _clause("content_required", "技术方案正文要求")

    technical = generator._classify_clauses_for_volume(
        [price_scoring, tech_scoring, plain_content],
        "technical",
    )
    commercial = generator._classify_clauses_for_volume(
        [price_scoring, tech_scoring, plain_content],
        "commercial",
    )

    assert price_scoring not in technical
    assert tech_scoring in technical
    assert plain_content in technical
    assert price_scoring in commercial
    assert tech_scoring not in commercial


def test_outline_volume_filter_falls_back_to_keywords_without_metadata_volume() -> None:
    generator = object.__new__(OutlineGenerator)
    scoring_without_meta = _clause(content="技术方案评分要求", volume=None)

    technical = generator._classify_clauses_for_volume([scoring_without_meta], "technical")
    commercial = generator._classify_clauses_for_volume([scoring_without_meta], "commercial")

    assert scoring_without_meta in technical
    assert scoring_without_meta not in commercial
