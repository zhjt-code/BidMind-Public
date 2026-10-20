"""P1 集成：current-draft 预载、commit 次数、DraftWriteRequest、图片缓存。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.writer.composer.compose_cache import ComposeTaskCache
from app.core.writer.composer.compose_pipeline import (
    ComposePipeline,
    ComposeSectionsRequest,
)
from app.core.writer.composer.draft_persistence import DraftWriteRequest
from app.models.writing_outline_node import OutlineNodeType


def _payload(content: str) -> object:
    return SimpleNamespace(
        content=content,
        source_material_ids=[8],
        source_clause_ids=[1],
        gate_flags=None,
        section_plan_id=None,
        planning_mode="planned_new",
        planning_failure_code=None,
        failure_state="completed",
    )


def _node(*, node_id: int, title: str, node_type, sort_order: int):
    return SimpleNamespace(
        id=node_id,
        parent_id=None,
        title=title,
        node_type=node_type,
        sort_order=sort_order,
        status="pending",
        depth=1,
    )


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _DraftResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _CountingDb:
    def __init__(self, rows):
        self.rows = rows
        self.execute_calls = 0
        self.commits = 0

    async def execute(self, *_args, **_kwargs):
        self.execute_calls += 1
        return _DraftResult(self.rows)

    async def commit(self):
        self.commits += 1


class _CountingComposer:
    def __init__(self, nodes, db, drafts_by_id=None) -> None:
        self.nodes = nodes
        self.db = db
        self._db_lock = _Lock()
        self.drafts_by_id = drafts_by_id or {}
        self.draft_loads = 0
        self.save_requests: list[object] = []
        self.progress: list[tuple] = []
        self.fingerprints: list[dict] = []

    async def _load_all_outline_nodes(self, _project_id: int):
        return list(self.nodes)

    def _sort_by_reading_order(self, nodes):
        return nodes

    async def _load_industry_profile(self, _project_id: int):
        return {"industry": "demo"}

    async def _ensure_material_pool_size(self, _project_id: int):
        return None

    async def _load_current_draft(self, node_id: int):
        self.draft_loads += 1
        return self.drafts_by_id.get(node_id)

    async def _fill_template(self, _node):
        return _payload("fixed")

    async def _fill_data(self, _node, *_args, **_kwargs):
        return _payload("data")

    async def _compose_free(self, _node, _profile, _summaries, _number_map):
        return _payload("free")

    def _run_writer_post_llm_gate(self, *_args, **_kwargs):
        return []

    def _aggregate_gate_flags(self, _verdicts):
        return None

    async def _maybe_run_writer_cove(self, *_args, **_kwargs):
        return None

    def _merge_gate_flags(self, left, _right):
        return left

    async def _maybe_run_writer_judge(self, *_args, **_kwargs):
        return None

    async def _save_draft(self, node, content, **_kwargs):
        self.save_requests.append(content)
        cache = getattr(self, "_compose_cache", None)
        fingerprint = cache.source_fingerprint(content) if cache is not None else {}
        self.fingerprints.append(fingerprint)
        return SimpleNamespace(
            id=node.id,
            word_count=8,
            summary=None,
            source_clause_ids=getattr(content, "source_clause_ids", None),
            source_material_ids=getattr(content, "source_material_ids", None),
            gate_flags=getattr(content, "gate_flags", None),
            planning_mode=getattr(content, "planning_mode", None),
            failure_state="completed",
        )

    async def _generate_summary(self, title, _content):
        return f"{title}-摘要"

    def _progress(self, _cb, stage, current, total, message):
        self.progress.append((stage, current, total, message))


@pytest.mark.asyncio
async def test_current_draft_classification_uses_single_preload_query() -> None:
    skip_node = _node(
        node_id=1,
        title="已有草稿",
        node_type=OutlineNodeType.free_writing,
        sort_order=1,
    )
    fixed_node = _node(
        node_id=2,
        title="封面",
        node_type=OutlineNodeType.fixed_template,
        sort_order=2,
    )
    data_node = _node(
        node_id=3,
        title="业绩表",
        node_type=OutlineNodeType.data_fill,
        sort_order=3,
    )
    free_node = _node(
        node_id=4,
        title="技术方案",
        node_type=OutlineNodeType.free_writing,
        sort_order=4,
    )
    skip_draft = SimpleNamespace(
        outline_node_id=1,
        summary="旧摘要",
        word_count=10,
        is_locked=1,
        is_user_edited=0,
    )
    db = _CountingDb([skip_draft])
    composer = _CountingComposer(
        [skip_node, fixed_node, data_node, free_node],
        db,
        drafts_by_id={1: skip_draft},
    )
    drafts = await ComposePipeline(composer).compose_sections(
        ComposeSectionsRequest(project_id=9)
    )
    assert [item.id for item in drafts] == [2, 3, 4]
    assert db.execute_calls == 1
    assert composer.draft_loads == 0
    assert db.commits == 1
    assert all(isinstance(item, DraftWriteRequest) for item in composer.save_requests)
    assert composer.save_requests[0].source_clause_ids == [1]
    assert composer.save_requests[0].source_material_ids == [8]
    assert composer.save_requests[0].planning_mode == "planned_new"
    assert composer.progress[-1][0] == "completed"


@pytest.mark.asyncio
async def test_fallback_without_execute_loads_each_node_once() -> None:
    skip_node = _node(
        node_id=1,
        title="已有草稿",
        node_type=OutlineNodeType.free_writing,
        sort_order=1,
    )
    free_node = _node(
        node_id=4,
        title="技术方案",
        node_type=OutlineNodeType.free_writing,
        sort_order=4,
    )
    db = SimpleNamespace(commit=AsyncMock())
    composer = _CountingComposer(
        [skip_node, free_node],
        db,
        drafts_by_id={
            1: SimpleNamespace(summary="旧摘要", is_locked=1, is_user_edited=0),
        },
    )
    await ComposePipeline(composer).compose_sections(ComposeSectionsRequest(project_id=9))
    assert composer.draft_loads == 2
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_cache_on_off_source_fields_identical() -> None:
    node = _node(
        node_id=2,
        title="封面",
        node_type=OutlineNodeType.fixed_template,
        sort_order=1,
    )
    db = _CountingDb([])
    composer = _CountingComposer([node], db)
    await ComposePipeline(composer).compose_sections(ComposeSectionsRequest(project_id=3))
    on_fp = composer.fingerprints[0]
    off_cache = ComposeTaskCache(project_id=3)
    off_fp = off_cache.source_fingerprint(composer.save_requests[0])
    assert on_fp == off_fp
    assert on_fp["source_clause_ids"] == [1]
    assert on_fp["source_material_ids"] == [8]
    assert on_fp["planning_mode"] == "planned_new"
    assert on_fp["failure_state"] is None


@pytest.mark.asyncio
async def test_material_image_forwarder_reuses_task_cache() -> None:
    from app.core.writer.section_composer import SectionComposer

    composer = SectionComposer.__new__(SectionComposer)
    composer._compose_cache = ComposeTaskCache(project_id=1)
    img = SimpleNamespace(material_id=7, image_index=0)
    calls = {"n": 0}

    async def fake_load(_composer, materials):
        calls["n"] += 1
        assert [item.id for item in materials] == [7]
        return [img]

    with patch(
        "app.core.writer.section_composer.material_context.load_material_images",
        fake_load,
    ):
        material = SimpleNamespace(id=7)
        first = await composer._load_material_images([material])
        second = await composer._load_material_images([material])
    assert calls["n"] == 1
    assert first == second == [img]
