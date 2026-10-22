"""Judge 批处理生命周期：drain、超时、取消无悬挂。"""

from __future__ import annotations

import asyncio

import pytest

from app.core.writer.composer.judge_batch import JudgeJobSnapshot, WriterJudgeBatch


def _snapshot(node_id: int) -> JudgeJobSnapshot:
    return JudgeJobSnapshot(
        project_id=1,
        node_id=node_id,
        node_title=f"n{node_id}",
        node_type="free_writing",
        content="正文",
        source_material_ids=(1,),
        source_clause_ids=(2,),
        is_high_risk=False,
    )


@pytest.mark.asyncio
async def test_drain_completes_all_jobs_before_return() -> None:
    batch = WriterJudgeBatch()
    seen: list[int] = []

    async def runner(snapshot: JudgeJobSnapshot) -> None:
        await asyncio.sleep(0.01)
        seen.append(snapshot.node_id)

    batch.enqueue_judge(_snapshot(1))
    batch.enqueue_judge(_snapshot(2))
    await batch.drain_judges(runner)
    assert sorted(seen) == [1, 2]
    assert batch.pending_count == 0
    assert batch.inflight_count == 0


@pytest.mark.asyncio
async def test_drain_timeout_cancels_inflight_jobs() -> None:
    batch = WriterJudgeBatch(drain_timeout_seconds=0.02)
    cancelled = {"n": 0}

    async def runner(_snapshot: JudgeJobSnapshot) -> None:
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            cancelled["n"] += 1
            raise

    batch.enqueue_judge(_snapshot(9))
    await batch.drain_judges(runner)
    assert cancelled["n"] == 1
    assert batch.inflight_count == 0


@pytest.mark.asyncio
async def test_group_exception_then_cancel_leaves_no_hanging_tasks() -> None:
    batch = WriterJudgeBatch()
    started = asyncio.Event()

    async def runner(snapshot: JudgeJobSnapshot) -> None:
        if snapshot.node_id == 1:
            raise RuntimeError("group boom")
        started.set()
        await asyncio.sleep(2)

    batch.enqueue_judge(_snapshot(1))
    batch.enqueue_judge(_snapshot(2))
    await batch.drain_judges(runner)
    await batch.cancel_judges()
    assert batch.inflight_count == 0
    assert not [task for task in asyncio.all_tasks() if not task.done() and task.get_name().startswith("Judge")]


@pytest.mark.asyncio
async def test_cancel_without_drain_drops_queue() -> None:
    batch = WriterJudgeBatch()
    batch.enqueue_judge(_snapshot(3))
    await batch.cancel_judges()
    assert batch.pending_count == 0
    assert batch.inflight_count == 0


def test_snapshot_from_compose_uses_enum_value_and_source_ids() -> None:
    from types import SimpleNamespace

    from app.core.writer.composer.judge_batch import snapshot_from_compose
    from app.models.writing_outline_node import OutlineNodeType

    snapshot = snapshot_from_compose(
        project_id=9,
        node=SimpleNamespace(id=4, title="技术方案", node_type=OutlineNodeType.free_writing),
        payload=SimpleNamespace(content="承诺工期30天", source_material_ids=[8], source_clause_ids=[1]),
        is_high_risk=True,
    )
    assert snapshot.node_type == "free_writing"
    assert snapshot.source_material_ids == (8,)
    assert snapshot.source_clause_ids == (1,)
    assert snapshot.is_high_risk is True


@pytest.mark.asyncio
async def test_run_writer_judge_from_snapshot_keeps_enqueued_high_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.core.writer.composer.gate_runner import run_writer_judge_from_snapshot

    seen: dict[str, object] = {}

    async def _fake_judge(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(
        "app.core.ai.critic.judge_sampler.maybe_run_judge",
        _fake_judge,
    )
    composer = SimpleNamespace(
        _build_writer_judge_evidence=AsyncMock(return_value={"bound_materials": []}),
    )
    await run_writer_judge_from_snapshot(
        composer,
        JudgeJobSnapshot(
            project_id=1,
            node_id=7,
            node_title="n7",
            node_type="free_writing",
            content="正文",
            source_material_ids=(1,),
            source_clause_ids=(2,),
            is_high_risk=True,
        ),
    )
    assert seen["is_high_risk"] is True
    assert seen["sample_key"] == "1:7"
    composer._build_writer_judge_evidence.assert_awaited_once()


def _pipeline_node(*, node_id: int, title: str, node_type, sort_order: int):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=node_id,
        parent_id=None,
        title=title,
        node_type=node_type,
        sort_order=sort_order,
        status="pending",
        depth=1,
    )


def _pipeline_payload(content: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        content=content,
        source_material_ids=[8],
        source_clause_ids=[1],
        gate_flags=None,
        section_plan_id=None,
        planning_mode="planned_new",
        planning_failure_code=None,
    )


class _DeferComposer:
    def __init__(self, nodes) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        self.nodes = nodes
        self.inline_judge_calls = 0
        self.db = SimpleNamespace(commit=AsyncMock(), execute=None)
        self._db_lock = AsyncMock()

    async def _load_all_outline_nodes(self, _project_id: int):
        return list(self.nodes)

    def _sort_by_reading_order(self, nodes):
        return nodes

    async def _load_industry_profile(self, _project_id: int):
        return None

    async def _ensure_material_pool_size(self, _project_id: int):
        return None

    async def _load_current_draft(self, _node_id: int):
        return None

    async def _fill_template(self, _node):
        return _pipeline_payload("fixed")

    async def _fill_data(self, _node, *_args, **_kwargs):
        return _pipeline_payload("data")

    async def _compose_free(self, _node, *_args, **_kwargs):
        return _pipeline_payload("free")

    def _run_writer_post_llm_gate(self, *_args, **_kwargs):
        return []

    def _aggregate_gate_flags(self, _verdicts):
        return None

    async def _maybe_run_writer_cove(self, *_args, **_kwargs):
        return None

    def _merge_gate_flags(self, left, _right):
        return left

    def _is_writer_critical_commitment(self, _node, _payload):
        return True, "承诺"

    async def _maybe_run_writer_judge(self, *_args, **_kwargs):
        self.inline_judge_calls += 1

    async def _save_draft(self, node, *_args, **_kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(id=node.id, word_count=8, summary=None)

    async def _generate_summary(self, title, _content):
        return f"{title}-摘要"

    def _progress(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_defer_enqueues_instead_of_inline_and_drains_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    from app.core.writer.composer.compose_pipeline import (
        ComposePipeline,
        ComposeSectionsRequest,
    )
    from app.models.writing_outline_node import OutlineNodeType

    monkeypatch.setattr(settings, "WRITING_JUDGE_DEFER_ENABLED", True)
    drained: list[int] = []

    async def _fake_run(_composer, snapshot: JudgeJobSnapshot) -> None:
        drained.append(snapshot.node_id)

    monkeypatch.setattr(
        "app.core.writer.composer.gate_runner.run_writer_judge_from_snapshot",
        _fake_run,
    )
    data_node = _pipeline_node(
        node_id=3, title="业绩表", node_type=OutlineNodeType.data_fill, sort_order=1
    )
    free_node = _pipeline_node(
        node_id=4, title="技术方案", node_type=OutlineNodeType.free_writing, sort_order=2
    )
    composer = _DeferComposer([data_node, free_node])
    await ComposePipeline(composer).compose_sections(ComposeSectionsRequest(project_id=9))
    assert composer.inline_judge_calls == 0
    assert sorted(drained) == [3, 4]


@pytest.mark.asyncio
async def test_defer_disabled_keeps_inline_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.core.writer.composer.compose_pipeline import (
        ComposePipeline,
        ComposeSectionsRequest,
    )
    from app.models.writing_outline_node import OutlineNodeType

    monkeypatch.setattr(settings, "WRITING_JUDGE_DEFER_ENABLED", False)
    drained: list[int] = []

    async def _fake_run(_composer, snapshot: JudgeJobSnapshot) -> None:
        drained.append(snapshot.node_id)

    monkeypatch.setattr(
        "app.core.writer.composer.gate_runner.run_writer_judge_from_snapshot",
        _fake_run,
    )
    free_node = _pipeline_node(
        node_id=4, title="技术方案", node_type=OutlineNodeType.free_writing, sort_order=1
    )
    composer = _DeferComposer([free_node])
    await ComposePipeline(composer).compose_sections(ComposeSectionsRequest(project_id=9))
    assert composer.inline_judge_calls == 1
    assert drained == []


@pytest.mark.asyncio
async def test_compose_error_cancels_deferred_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.core.writer.composer.compose_pipeline import (
        ComposePipeline,
        ComposeSectionsRequest,
    )
    from app.models.writing_outline_node import OutlineNodeType

    monkeypatch.setattr(settings, "WRITING_JUDGE_DEFER_ENABLED", True)
    drained: list[int] = []

    async def _fake_run(_composer, snapshot: JudgeJobSnapshot) -> None:
        drained.append(snapshot.node_id)

    monkeypatch.setattr(
        "app.core.writer.composer.gate_runner.run_writer_judge_from_snapshot",
        _fake_run,
    )
    first = _pipeline_node(
        node_id=4, title="章一", node_type=OutlineNodeType.free_writing, sort_order=1
    )
    second = _pipeline_node(
        node_id=5, title="章二", node_type=OutlineNodeType.free_writing, sort_order=2
    )
    composer = _DeferComposer([first, second])

    async def _save_draft(node, *_args, **_kwargs):
        from types import SimpleNamespace

        if node.id == 5:
            raise RuntimeError("compose boom")
        return SimpleNamespace(id=node.id, word_count=8, summary=None)

    composer._save_draft = _save_draft  # type: ignore[method-assign]
    pipeline = ComposePipeline(composer)
    with pytest.raises(RuntimeError, match="compose boom"):
        await pipeline.compose_sections(ComposeSectionsRequest(project_id=9))
    assert drained == []
    assert composer.inline_judge_calls == 0
