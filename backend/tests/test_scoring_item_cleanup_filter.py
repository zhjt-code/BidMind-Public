"""防御测试: 自动评分项清理过滤条件的 NULL 安全性。

职责: 锁定"排除手工评分项"过滤在 JSON 缺 source 键(SQL NULL)时仍命中自动评分项。
不做: 不连接真实 PostgreSQL、不执行 LLM 提取、不测试完整重提取流程。
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///")
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models.requirement import (  # noqa: E402
    Requirement,
    RequirementCategory,
    non_manual_scoring_metadata_filter,
)

import pytest

pytestmark = pytest.mark.scoring


def _scoring_row(metadata: dict | None) -> Requirement:
    return Requirement(
        project_id=1,
        document_id=1,
        category=RequirementCategory.TECH,
        description="测试评分项描述",
        requirement_kind="scoring_item",
        confirmed=0,
        scoring_metadata=metadata,
    )


def test_filter_matches_auto_items_without_source_key_and_spares_manual():
    """缺 source 键(NULL)/无 metadata 的自动评分项必须命中, 手工项必须豁免。: 裸 `!= 'manual'` 在三值逻辑下对 NULL 不为 TRUE, 导致重提取后
 旧自动评分项永不被清理, 新旧结果叠加(1934 实测 116 = 旧16 + 新100)。
 """
    engine = create_engine("sqlite://")
    # 只建 requirements 表: 全量 metadata 含 PGVector 列, SQLite 无法创建
    Requirement.__table__.create(engine)

    with Session(engine) as db:
        auto_no_source_key = _scoring_row({"score_item_name": "管理体系认证", "max_score": 1.0})
        auto_null_metadata = _scoring_row(None)
        manual_item = _scoring_row({"source": "manual", "score_item_name": "手工项", "max_score": 5.0})
        db.add_all([auto_no_source_key, auto_null_metadata, manual_item])
        db.commit()

        matched_ids = set(
            db.execute(
                select(Requirement.id).where(
                    Requirement.requirement_kind == "scoring_item",
                    non_manual_scoring_metadata_filter(),
                )
            ).scalars()
        )

        assert auto_no_source_key.id in matched_ids, "缺 source 键的自动评分项必须命中清理条件"
        assert auto_null_metadata.id in matched_ids, "无 metadata 的自动评分项必须命中清理条件"
        assert manual_item.id not in matched_ids, "手工评分项不得命中清理条件"

    engine.dispose()


def test_both_cleanup_sites_use_shared_null_safe_filter():
    """评分项清理契约(+):

 1. 提取幂等清理(scoring_extractor)仍必须使用共享 NULL 安全过滤;
 2. 重提取预删必须按项目删除全部 Requirement，不得再排除 scoring_item;
 3. 任何清理点禁止残留裸 != 'manual' 三值逻辑漏洞。
 """
    from app.core.extractor.scoring import scoring_extractor
    from app.services import requirement_reextract_service, requirement_reextract_swap

    extractor_source = inspect.getsource(scoring_extractor)
    assert "non_manual_scoring_metadata_filter" in extractor_source

    swap_source = inspect.getsource(requirement_reextract_swap)
    assert 'requirement_kind != "scoring_item"' not in swap_source
    assert "clear_extracted_artifacts" in swap_source

    for module in (requirement_reextract_service, requirement_reextract_swap, scoring_extractor):
        source = inspect.getsource(module)
        assert 'as_string() != "manual"' not in source, module.__name__
