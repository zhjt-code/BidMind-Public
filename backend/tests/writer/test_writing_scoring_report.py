"""P0 防御测试: 写标评分核计只以"有分值评分项"为准。

映射真实场景(项目1532): LLM 回退提取把评标办法总述/格式/保证金/报价规则等无分值告知类
混入 scoring_criterion，旧口径全部计入 missing → 误报"N 项分值缺失"。修正后:
- 无分值告知类不计入 missing、不报缺失。
- 全无分值 → 提示"未识别到有效评分项"(item_count=0)而非"N 项缺失"。
- 有分值项合计=100 即通过(无分值项不干扰); 不足才提示。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from app.services.writing.scoring_report import build_scoring_report_payload  # noqa: E402

pytestmark = pytest.mark.scoring


def _clause(
    sub_type="scoring_criterion",
    *,
    score_weight=None,
    volume=None,
    source=None,
):
    meta = {}
    if volume:
        meta["volume"] = volume
    if source:
        meta["source"] = source
    return SimpleNamespace(
        sub_type=sub_type,
        score_weight=score_weight,
        metadata_json=meta or None,
    )


def test_no_scoring_clauses_returns_none():
    assert build_scoring_report_payload([_clause("content_required")]) is None
    assert build_scoring_report_payload([]) is None


def test_all_unscored_reports_no_valued_not_missing():
    # 项目1532: 8 条无分值告知类 → 提示"未识别到有效评分项", 不报"N 项分值缺失"
    clauses = [_clause(score_weight=None) for _ in range(8)]
    payload = build_scoring_report_payload(clauses)
    assert payload is not None
    assert payload["item_count"] == 0
    assert payload["needs_review"] is True
    assert payload["missing_score_count"] == 0  # 关键: 不再把无分值项计入缺失
    assert "未识别到带分值的有效评分项" in payload["note"]
    assert "分值缺失" not in payload["note"]


def test_valued_items_sum_100_passes_despite_unscored_present():
    # 有分值项合计=100 → 通过; 混入的无分值告知类不影响、不报缺失
    clauses = [
        _clause(score_weight=60.0),
        _clause(score_weight=40.0),
        _clause(score_weight=None),  # 告知类
        _clause(score_weight=0),     # 告知类
    ]
    payload = build_scoring_report_payload(clauses)
    assert payload is not None
    assert payload["item_count"] == 2  # 仅有分值项计入
    assert payload["needs_review"] is False
    assert payload["missing_score_count"] == 0


def test_scoring_report_counts_authority_and_llm_sources():
    clauses = [
        _clause(score_weight=60.0, source="scoring_table"),
        _clause(score_weight=40.0),
        _clause(score_weight=None, source="scoring_table"),
    ]
    payload = build_scoring_report_payload(clauses)
    assert payload is not None
    assert payload["authority_count"] == 2
    assert payload["llm_count"] == 1


def test_unvalued_scoring_report_also_counts_sources():
    clauses = [
        _clause(score_weight=None, source="scoring_table"),
        _clause(score_weight=None),
    ]
    payload = build_scoring_report_payload(clauses)
    assert payload is not None
    assert payload["item_count"] == 0
    assert payload["authority_count"] == 1
    assert payload["llm_count"] == 1


def test_valued_items_undershoot_flags_review():
    # 有分值项合计=80<100 → 提示不足(真·漏提分值场景), 与无分值告知类无关
    clauses = [
        _clause(score_weight=50.0),
        _clause(score_weight=30.0),
        _clause(score_weight=None),
    ]
    payload = build_scoring_report_payload(clauses)
    assert payload is not None
    assert payload["item_count"] == 2
    assert payload["needs_review"] is True
