"""S1.3 防御测试: 写标评分项权威提取 scoring_source。

映射风险: 写标评分项从"LLM 猜分值"切换为"评分表结构化权威提取"。必须保证:
- 权威条目字段映射正确(metadata 评分项名/满分/卷权重, 页锚点从 source_pages 还原)。
- 无评分项时回退 None, 不误删 LLM 结果。
- 替换幂等: 仅删未确认(confirmed=0)且非人工(is_manual=0)的评分约束, 保护人工资产。
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.extractor.scoring.scoring_extractor import ScoringStructureOutcome  # noqa: E402
from app.core.writer.scoring_constants import SCORING_AUTHORITY_SOURCE  # noqa: E402
from app.schemas.requirement import ExtractedScoringItem  # noqa: E402
import app.core.writer.scoring_source as scoring_source  # noqa: E402

import pytest

pytestmark = pytest.mark.scoring


def _item(**kw) -> ExtractedScoringItem:
    base = dict(
        category="TECH",
        score_item_name="技术方案完整性",
        description="方案覆盖全部技术指标得10分",
        score_weight=10.0,
        source_pages=[47, 48],
        score_rules=["覆盖全部得10分"],
        deduction_rules=["缺漏每项扣2分"],
        volume="technical",
        volume_weight=0.4,
        volume_max=100.0,
    )
    base.update(kw)
    return ExtractedScoringItem(**base)


class _FakeResult:
    def __init__(self, scalar_value=None, rows=None):
        self._scalar = scalar_value
        self._rows = list(rows or [])

    def scalar(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _FakeSession:
    """记录 execute/add/commit 的轻量异步会话替身(无真实 I/O)。"""

    def __init__(self, max_sort=None):
        self.executed: list = []
        self.added: list = []
        self.committed = False
        self._max_sort = max_sort

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(scalar_value=self._max_sort)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        return None


def _patch_extractor(monkeypatch, outcome: ScoringStructureOutcome):
    class _FakeExtractor:
        def __init__(self, db=None, project_id=None):
            pass

        async def collect_scoring_structure(self, document_id):
            return outcome

    monkeypatch.setattr(scoring_source, "ScoringExtractor", _FakeExtractor)


def _outcome(items, needs_review=False, probe=None) -> ScoringStructureOutcome:
    return ScoringStructureOutcome(
        items=items,
        volume_defs={},
        report=SimpleNamespace(needs_review=needs_review),
        chunk_count=1 if items else 0,
        verdict_probe=probe,
    )


def _writer_probe(**extra) -> dict:
    payload = {
        "trigger_verdict": "amber",
        "rescue_adopted": False,
        "final_verdict": "amber",
        "at": "2026-08-21T00:00:00+00:00",
    }
    payload.update(extra)
    return payload


def test_to_scoring_clause_maps_metadata_and_pages():
    clause = scoring_source._to_scoring_clause(
        project_id=7,
        document_id=11,
        item=_item(),
        sort_order=3,
        totals_needs_review=True,
    )
    assert clause.sub_type == "scoring_criterion"
    assert clause.layer.value == "generation_guide"
    assert clause.content == "方案覆盖全部技术指标得10分"
    assert clause.score_weight == 10.0
    # 页锚点从 source_pages 首末还原
    assert clause.source_page == 47
    assert clause.source_page_start == 47
    assert clause.source_page_end == 48
    meta = clause.metadata_json
    assert meta["score_item_name"] == "技术方案完整性"
    assert meta["max_score"] == 10.0
    assert meta["volume"] == "technical"
    assert meta["volume_weight"] == 0.4
    assert meta["source"] == SCORING_AUTHORITY_SOURCE
    assert meta["totals_needs_review"] is True


def test_to_scoring_clause_zero_weight_normalized_to_none():
    # 无有效分值(符合性说明项)满分归一为 None, 避免误导性"0分"
    clause = scoring_source._to_scoring_clause(
        project_id=1, document_id=1, item=_item(score_weight=0.0),
        sort_order=0, totals_needs_review=False,
    )
    assert clause.score_weight is None
    assert clause.metadata_json["max_score"] is None


def test_to_scoring_clause_preserves_lot_for_multi_package(monkeypatch):
    """: 多包文档评分项 lot 必须落库到 metadata, 否则写标核计横幅
 把两包分值混算成 200/100 误报"重复提取"。"""
    clause = scoring_source._to_scoring_clause(
        project_id=1, document_id=1, item=_item(lot="采购包2"),
        sort_order=0, totals_needs_review=False,
    )
    assert clause.metadata_json["lot"] == "采购包2"
    # 单包文档 lot=None 原样保留, 不虚构归属
    clause_single = scoring_source._to_scoring_clause(
        project_id=1, document_id=1, item=_item(),
        sort_order=0, totals_needs_review=False,
    )
    assert clause_single.metadata_json["lot"] is None


def test_replace_returns_none_when_no_items(monkeypatch):
    _patch_extractor(monkeypatch, _outcome(items=[], probe=_writer_probe()))
    session = _FakeSession()
    result = asyncio.run(
        scoring_source.replace_scoring_clauses_with_authority(session, 5, 9)
    )
    assert result is None
    # 无评分项时不得删/落条款, 但必须落裁决探针(与审查零提取同为样本)
    assert session.added == []
    assert session.committed is False
    assert session.executed, "零项也必须 stamp scoring_verdict_probe"
    compiled = " ".join(str(stmt.compile()).lower() for stmt in session.executed)
    assert "scoring_verdict_probe" in compiled
    assert "delete" not in compiled
    probe_params = [
        value for stmt in session.executed
        for value in stmt.compile().params.values()
        if isinstance(value, dict)
    ]
    assert probe_params and probe_params[0].get("flow") == "writer"


def test_replace_stamps_writer_probe_when_items_saved(monkeypatch):
    """: 写标权威替换成功也必须把探针写入 documents, 供看板覆盖写标样本。"""
    items = [_item(score_item_name="方案A")]
    probe = _writer_probe(rescue_adopted=True, final_verdict="green")
    _patch_extractor(monkeypatch, _outcome(items=items, probe=probe))
    session = _FakeSession(max_sort=0)
    result = asyncio.run(
        scoring_source.replace_scoring_clauses_with_authority(session, 5, 9)
    )
    assert result is not None
    compiled = " ".join(str(stmt.compile()).lower() for stmt in session.executed)
    assert "scoring_verdict_probe" in compiled
    probe_params = [
        value for stmt in session.executed
        for value in stmt.compile().params.values()
        if isinstance(value, dict)
    ]
    assert probe_params and probe_params[0].get("flow") == "writer"


def test_replace_inserts_authoritative_and_deletes_unconfirmed(monkeypatch):
    items = [_item(score_item_name="方案A"), _item(score_item_name="方案B", source_pages=[50])]
    _patch_extractor(monkeypatch, _outcome(items=items, needs_review=False))
    session = _FakeSession(max_sort=12)
    result = asyncio.run(
        scoring_source.replace_scoring_clauses_with_authority(session, 5, 9)
    )
    assert result is not None
    assert len(result) == 2
    assert session.committed is True
    assert len(session.added) == 2
    # 新条目 sort_order 接在既有最大值(12)之后
    assert [c.sort_order for c in session.added] == [13, 14]
    # 第一条 execute 为 DELETE, 且 WHERE 含 confirmed / is_manual 幂等保护(不误删人工/已确认)
    delete_sql = str(
        session.executed[0].compile(compile_kwargs={"literal_binds": True})
    ).lower()
    assert "delete" in delete_sql
    assert "confirmed" in delete_sql
    assert "is_manual" in delete_sql
    assert "scoring_criterion" in delete_sql


def test_writing_extract_task_wires_scoring_authority_replacement():
    """源码契约: 写标提取 6.5 段必须保留权威评分替换接线。"""
    from app.core.tasks.writing_extract_task import run_writing_extract_task

    source = inspect.getsource(run_writing_extract_task)
    assert "WRITING_SCORING_AUTHORITY_ENABLED" in source
    assert "replace_scoring_clauses_with_authority" in source
    assert "评分项权威替换完成" in source
