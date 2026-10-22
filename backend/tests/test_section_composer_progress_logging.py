"""Plan Step 5 防御测试：SectionComposer._progress 异常应记录 warning，而非静默吞。

背景: writer 域内 ``ClauseExtractor`` 进度回调失败会 ``logger.warning(...)``，
但 ``SectionComposer._progress`` 仍走 ``except Exception: pass`` 静默路径，
导致排障时无法发现回调链断裂。本测试固定 "记录 warning + 不中断主流程" 行为。
"""

from __future__ import annotations

import logging
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")


def _section_composer_instance():
    """构造不依赖 DB 的 SectionComposer 实例，仅用于纯函数测试。"""
    from app.core.writer.section_composer import SectionComposer

    return SectionComposer.__new__(SectionComposer)


def test_progress_logs_warning_when_callback_raises(caplog):
    """回调抛错时应记录 warning 并继续，不允许向上抛出。"""
    import loguru

    composer = _section_composer_instance()

    def bad_callback(stage, current, total, message):
        raise RuntimeError("callback boom")

    caplog.set_level(logging.WARNING)
    sink_id = loguru.logger.add(caplog.handler, level="WARNING", format="{message}")
    try:
        composer._progress(bad_callback, "compose_all", 1, 2, "正在生成")
    finally:
        loguru.logger.remove(sink_id)

    log_blob = " ".join(rec.message for rec in caplog.records)
    assert "进度回调失败" in log_blob, (
        f"应记录 '进度回调失败' warning；当前 caplog 内容: {log_blob!r}"
    )


def test_progress_no_callback_returns_silently():
    """回调为 None 时 _progress 不应抛出，亦无 warning 输出。"""
    composer = _section_composer_instance()
    composer._progress(None, "compose_all", 0, 1, "")


def test_progress_normal_callback_invoked_with_args():
    """正常回调应被以期望参数调用一次。"""
    composer = _section_composer_instance()
    captured: list[tuple] = []

    def ok_callback(stage, current, total, message):
        captured.append((stage, current, total, message))

    composer._progress(ok_callback, "phase_x", 3, 5, "ok")
    assert captured == [("phase_x", 3, 5, "ok")]


def _payload(content: str) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        content=content,
        source_material_ids=[],
        source_clause_ids=[],
        gate_flags=None,
        section_plan_id=None,
        planning_mode=None,
        planning_failure_code=None,
    )


def _node(*, node_id: int, title: str, node_type, sort_order: int):
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


class _FakeComposer:
    def __init__(self, nodes, drafts_by_id=None) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        self.nodes = nodes
        self.drafts_by_id = drafts_by_id or {}
        self.saved_titles: list[str] = []
        self.progress: list[tuple] = []
        self.free_summaries: list[list] = []
        self._db_lock = AsyncMock()
        self.db = SimpleNamespace(commit=AsyncMock())

    async def _load_all_outline_nodes(self, _project_id: int):
        return list(self.nodes)

    def _sort_by_reading_order(self, nodes):
        return nodes

    async def _load_industry_profile(self, _project_id: int):
        return {"industry": "demo"}

    async def _ensure_material_pool_size(self, _project_id: int):
        return None

    async def _load_current_draft(self, node_id: int):
        return self.drafts_by_id.get(node_id)

    async def _fill_template(self, _node):
        return _payload("fixed")

    async def _fill_data(self, _node, *_args, **_kwargs):
        return _payload("data")

    async def _compose_free(self, _node, _profile, local_summaries, _number_map):
        self.free_summaries.append(list(local_summaries))
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

    async def _save_draft(self, node, *_args, **_kwargs):
        from types import SimpleNamespace

        self.saved_titles.append(node.title)
        return SimpleNamespace(id=node.id, word_count=8, summary=None)

    async def _generate_summary(self, title, _content):
        return f"{title}-摘要"

    def _progress(self, _cb, stage, current, total, message):
        self.progress.append((stage, current, total, message))


@pytest.mark.asyncio
async def test_compose_pipeline_progress_is_monotonic_and_skips_summaries():
    """拆分后仍按 skip -> fixed -> data -> free 记账，skip 章进入摘要链。"""
    from types import SimpleNamespace

    from app.core.writer.composer.compose_pipeline import (
        ComposePipeline,
        ComposeSectionsRequest,
    )
    from app.models.writing_outline_node import OutlineNodeType

    skip_node = _node(node_id=1, title="已有草稿", node_type=OutlineNodeType.free_writing, sort_order=1)
    fixed_node = _node(node_id=2, title="封面", node_type=OutlineNodeType.fixed_template, sort_order=2)
    data_node = _node(node_id=3, title="业绩表", node_type=OutlineNodeType.data_fill, sort_order=3)
    free_node = _node(node_id=4, title="技术方案", node_type=OutlineNodeType.free_writing, sort_order=4)
    composer = _FakeComposer(
        [skip_node, fixed_node, data_node, free_node],
        drafts_by_id={1: SimpleNamespace(summary="旧摘要", word_count=10, is_locked=1)},
    )
    drafts = await ComposePipeline(composer).compose_sections(
        ComposeSectionsRequest(project_id=9, skip_existing_drafts=False)
    )
    assert [item.id for item in drafts] == [2, 3, 4]
    assert composer.saved_titles == ["封面", "业绩表", "技术方案"]
    currents = [item[1] for item in composer.progress if item[0] == "composing"]
    assert currents == sorted(currents)
    assert composer.progress[-1][0] == "completed"
    assert composer.free_summaries
    assert composer.free_summaries[0][0]["summary"] == "旧摘要"


@pytest.mark.asyncio
async def test_compose_pipeline_cancels_outstanding_data_tasks_on_error():
    import asyncio

    from app.core.writer.composer.compose_pipeline import (
        ComposePipeline,
        ComposeSectionsRequest,
    )
    from app.models.writing_outline_node import OutlineNodeType

    n1 = _node(node_id=11, title="表1", node_type=OutlineNodeType.data_fill, sort_order=1)
    n2 = _node(node_id=12, title="表2", node_type=OutlineNodeType.data_fill, sort_order=2)
    composer = _FakeComposer([n1, n2])
    cancelled = {"n": 0}

    async def _fill_data(node, *_args, **_kwargs):
        if node.id == 11:
            raise RuntimeError("data boom")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled["n"] += 1
            raise
        return _payload("x")

    composer._fill_data = _fill_data  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="data boom"):
        await ComposePipeline(composer).compose_sections(ComposeSectionsRequest(project_id=3))
    assert cancelled["n"] >= 1
