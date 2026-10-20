"""material_matcher 候选 volume_type 守门测试。

覆盖:
- fixed_template 节点 volume=technical → 仅纳入 snapshot.volume_type=technical 的 clause
- fixed_template 节点 volume=commercial → 仅纳入 commercial
- 节点 volume_type 为空 → 不强制过滤 (向后兼容旧项目)
- clause snapshot.volume_type 为空 → 视为通用模板, 任意 volume 节点都接受
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.writer.material_matcher import MaterialMatcher
from app.models.writing_outline_node import OutlineNodeType


def _node(node_id: int, title: str, volume_type: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=node_id,
        title=title,
        node_type=OutlineNodeType.fixed_template,
        volume_type=volume_type,
        embedding=None,
        chapter_profile=None,
        depth=2,
        parent_id=None,
    )


def _clause(
    clause_id: int,
    sub_type: str,
    volume_type: str | None,
    template_key: str = "bid_letter",
) -> SimpleNamespace:
    snapshot = {
        "title": "投标函",
        "template_key": template_key,
        "volume_type": volume_type,
        "is_complete_candidate": True,
        "raw_text_from_chunks": "致:招标人\n投标人: (盖章)\n日期: 年 月 日",
    }
    return SimpleNamespace(
        id=clause_id,
        sub_type=sub_type,
        content="投标函固定模板",
        raw_text="致:招标人",
        embedding=None,
        metadata_json={
            "template_key": template_key,
            "target_section_hint": "投标函",
            "fixed_template_snapshot": snapshot,
        },
    )


def _make_matcher() -> MaterialMatcher:
    matcher = MaterialMatcher.__new__(MaterialMatcher)
    matcher.db = MagicMock()
    matcher.llm_client = MagicMock()
    matcher._judge_retry = MagicMock()
    return matcher


def test_candidates_filtered_by_technical_volume() -> None:
    matcher = _make_matcher()
    chapter = _node(5460, "投标函", "technical")
    clauses = [
        _clause(10599, "fixed_template", "technical"),
        _clause(10600, "fixed_template", "commercial"),
    ]

    candidates = matcher._retrieve_candidates(chapter, clauses, scope_map={}, node_map={})

    ids = {c.id for c in candidates}
    assert 10599 in ids
    assert 10600 not in ids


def test_candidates_filtered_by_commercial_volume() -> None:
    matcher = _make_matcher()
    chapter = _node(5515, "投标函", "commercial")
    clauses = [
        _clause(10599, "fixed_template", "technical"),
        _clause(10600, "fixed_template", "commercial"),
    ]

    candidates = matcher._retrieve_candidates(chapter, clauses, scope_map={}, node_map={})

    ids = {c.id for c in candidates}
    assert 10600 in ids
    assert 10599 not in ids


def test_candidates_no_filter_when_node_volume_is_empty() -> None:
    """旧项目数据节点 volume_type 为空时维持原行为, 不过滤。"""
    matcher = _make_matcher()
    chapter = _node(5460, "投标函", None)
    clauses = [
        _clause(10599, "fixed_template", "technical"),
        _clause(10600, "fixed_template", "commercial"),
    ]

    candidates = matcher._retrieve_candidates(chapter, clauses, scope_map={}, node_map={})

    ids = {c.id for c in candidates}
    assert ids == {10599, 10600}


def test_candidates_keep_clause_when_clause_volume_is_empty() -> None:
    """clause 没有 volume_type 视作通用模板, 任意 volume 节点都接受。"""
    matcher = _make_matcher()
    chapter = _node(5460, "投标函", "technical")
    clauses = [
        _clause(10599, "fixed_template", None),
        _clause(10600, "fixed_template", "commercial"),
    ]

    candidates = matcher._retrieve_candidates(chapter, clauses, scope_map={}, node_map={})

    ids = {c.id for c in candidates}
    assert 10599 in ids
    assert 10600 not in ids
