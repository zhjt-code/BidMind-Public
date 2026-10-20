""" 防御测试: 评分事实集合必须在后续处理前保持可守恒。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_facts import (  # noqa: E402
    ScoringFact,
    ScoringScopeKey,
    build_scoring_fact_set,
)
from app.core.extractor.scoring.scoring_integrity import (  # noqa: E402
    ScoringIntegrityError,
    build_integrity_snapshot,
    compare_integrity_snapshots,
)
from app.schemas.requirement import ExtractedScoringItem  # noqa: E402

pytestmark = pytest.mark.scoring


def _item(
    name: str,
    score: float,
    *,
    lot: str | None = None,
    volume: str | None = "technical",
    pages: list[int] | None = None,
    description: str | None = None,
) -> ExtractedScoringItem:
    return ExtractedScoringItem(
        category="TECH",
        score_item_name=name,
        description=description or f"{name} 满分{score:g}分",
        score_weight=score,
        lot=lot,
        volume=volume,
        source_pages=pages or [1],
        score_rules=[f"{name}最高得{score:g}分"],
    )


def test_fact_set_preserves_two_lots_each_100() -> None:
    """1590同类结构: 两个采购包各100, 顶层200不是异常。"""
    fact_set = build_scoring_fact_set(
        [
            _item("技术项", 70, lot="采购包1", pages=[36]),
            _item("报价评分", 30, lot="采购包1", volume="price", pages=[34]),
            _item("技术项", 90, lot="采购包2", pages=[41]),
            _item("报价评分", 10, lot="采购包2", volume="price", pages=[40]),
        ],
        source_id="doc-1590",
    )

    assert fact_set.totals_by_lot() == {"采购包1": 100.0, "采购包2": 100.0}
    assert fact_set.total_score == 200.0
    assert {fact.scope.lot for fact in fact_set.effective_facts} == {"采购包1", "采购包2"}


def test_single_package_uses_explicit_scope_key() -> None:
    """单包与未识别归属必须区分, 不再共用 None。"""
    single = build_scoring_fact_set([_item("技术项", 100)], source_id="single")
    unassigned = build_scoring_fact_set(
        [_item("技术项", 100, lot=ScoringScopeKey.UNASSIGNED_LOT)],
        source_id="unassigned",
    )

    assert single.effective_facts[0].scope.lot == ScoringScopeKey.SINGLE_LOT
    assert unassigned.effective_facts[0].scope.lot == ScoringScopeKey.UNASSIGNED_LOT
    assert single.effective_facts[0].scope != unassigned.effective_facts[0].scope


def test_integrity_rejects_fact_deletion() -> None:
    before = build_scoring_fact_set(
        [
            _item("总监理工程师资质", 3, lot="采购包2", pages=[45]),
            _item("人员实力1", 3, lot="采购包2", pages=[45]),
            _item("人员实力2", 3, lot="采购包2", pages=[45]),
        ],
        source_id="before",
    )
    after = build_scoring_fact_set(
        [_item("总监理工程师资质", 3, lot="采购包2", pages=[45])],
        source_id="after",
    )

    with pytest.raises(ScoringIntegrityError, match="事实数量减少"):
        compare_integrity_snapshots(
            build_integrity_snapshot(before),
            build_integrity_snapshot(after),
        )


def test_integrity_rejects_deterministic_score_update() -> None:
    before = build_scoring_fact_set(
        [_item("报价评分", 30, lot="采购包1", volume="price", pages=[34])],
        source_id="before",
    )
    changed_fact = ScoringFact(
        **{
            **before.effective_facts[0].model_dump(),
            "score_weight": 20.0,
        }
    )
    after = before.model_copy(
        update={"observed_facts": [changed_fact], "effective_fact_keys": [changed_fact.fact_key]}
    )

    with pytest.raises(ScoringIntegrityError, match="分值集合变化"):
        compare_integrity_snapshots(
            build_integrity_snapshot(before),
            build_integrity_snapshot(after),
        )


def test_exact_duplicate_keeps_observed_evidence_but_single_effective_fact() -> None:
    fact_set = build_scoring_fact_set(
        [
            _item("技术方案", 10, lot="采购包1", pages=[10]),
            _item("技术方案", 10, lot="采购包1", pages=[10]),
        ],
        source_id="duplicates",
    )

    assert len(fact_set.observed_facts) == 2
    assert len(fact_set.effective_facts) == 1
    assert len(fact_set.effective_facts[0].evidence_refs) == 2
