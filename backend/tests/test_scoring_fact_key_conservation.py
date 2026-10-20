"""ScoringFactSet 事实身份键守恒防御测试（综合提升方案 阶段0.4, 缺口C）。

职责: 锁定"同名同分同卷不同页的评分事实不被 effective 投影合并"的守恒行为,
      与"同页同名同分观察事实合并证据引用"的既有语义。
不做: 不测解析、不测 LLM、不测落库。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_facts import build_scoring_fact_set  # noqa: E402

import pytest

pytestmark = pytest.mark.scoring


def _item(name: str, score: float, pages: list[int], desc: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        score_item_name=name,
        description=desc or f"{name}规则",
        score_weight=score,
        volume="technical",
        lot=None,
        volume_weight=None,
        volume_max=None,
        score_rules=[],
        deduction_rules=[],
        source_pages=pages,
    )


def test_same_name_score_on_different_pages_stay_separate() -> None:
    """样本8形态: 相邻两个业绩项均2分(不同页/不同证据位置), 满分必须都计入。"""
    fact_set = build_scoring_fact_set(
        [
            _item("企业业绩", 2.0, [3], desc="提供2022年以来业绩..."),
            _item("企业业绩", 2.0, [4], desc="提供2023年以来业绩..."),
        ],
        source_id="test:conservation",
    )
    assert len(fact_set.effective_fact_keys) == 2
    assert fact_set.total_score == 4.0


def test_same_page_duplicate_observation_merges_evidence() -> None:
    """同页同名同分(解析层重复观察)仍合并为单一有效事实并聚合证据引用。"""
    fact_set = build_scoring_fact_set(
        [
            _item("项目团队", 10.0, [5]),
            _item("项目团队", 10.0, [5]),
        ],
        source_id="test:merge",
    )
    assert len(fact_set.effective_fact_keys) == 1
    assert fact_set.total_score == 10.0
    assert len(fact_set.effective_facts[0].evidence_refs) == 2
    # 观察事实全保留(: 只关联不删除)
    assert len(fact_set.observed_facts) == 2
