"""SectionPlan 预取：指纹一致、命中免重调、失败不阻塞。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.writer.composer.plan_prefetch import (
    await_node_plan_prefetch,
    build_plan_request_for_node,
    cancel_plan_prefetch,
    start_section_plan_prefetch,
)
from app.models.writing_outline_node import OutlineNodeType


def _node(*, node_id: int = 2, title: str = "技术方案"):
    return SimpleNamespace(
        id=node_id,
        title=title,
        target_word_count=1200,
        chapter_profile=None,
        parent_id=None,
        sort_order=1,
        status="pending",
        depth=1,
        node_type=OutlineNodeType.free_writing,
    )


def _material():
    return SimpleNamespace(id=9, content="合同金额100万元", title="合同扫描件")


def _clause():
    return SimpleNamespace(
        id=11,
        sub_type="content_required",
        content="必须响应工期要求",
        score_weight=None,
        metadata_json=None,
    )


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _composer(*, project_id: int | None = 9):
    return SimpleNamespace(
        project_id=project_id,
        db=SimpleNamespace(),
        llm_client=SimpleNamespace(chat_with_validation=AsyncMock()),
        _parse_profile=lambda _node: None,
        _load_bound_materials=AsyncMock(return_value=[_material()]),
        _load_bound_clauses=AsyncMock(return_value=[_clause()]),
        _db_lock=_Lock(),
        _compose_cache=None,
    )


@pytest.mark.asyncio
async def test_prefetch_and_compose_builder_share_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.writer.section_plan import source_loader

    async def _passthrough(_composer, project_id, bound_clauses):
        return list(bound_clauses)

    monkeypatch.setattr(source_loader, "load_section_plan_clauses", _passthrough)
    composer = _composer()
    node = _node()
    materials = [_material()]
    clauses = [_clause()]
    first = await build_plan_request_for_node(
        composer,
        project_id=9,
        node=node,
        industry_profile="市政工程",
        materials=materials,
        clauses=clauses,
    )
    second = await build_plan_request_for_node(
        composer,
        project_id=9,
        node=node,
        industry_profile="市政工程",
        materials=None,
        clauses=None,
    )
    assert first.request.input_fingerprint == second.request.input_fingerprint
    assert first.request.input_fingerprint
    assert [getattr(item, "id", None) for item in first.planning_clauses] == [11]


@pytest.mark.asyncio
async def test_prefetch_hit_skips_second_plan_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.writer.composer.plan_prefetch import prefetch_one_section_plan
    from app.core.writer.section_plan import source_loader
    from app.core.writer.section_plan.contracts import SectionPlanResolution

    monkeypatch.setattr(
        source_loader,
        "load_section_plan_clauses",
        AsyncMock(return_value=[_clause()]),
    )
    llm_calls = {"n": 0}

    class _Repo:
        def __init__(self) -> None:
            self.ready = None

        async def get_ready_by_fingerprint(self, outline_node_id: int, input_fingerprint: str):
            return self.ready

        async def save_ready_plan(self, request, plan_payload):
            self.ready = SectionPlanResolution(
                mode="planned_reused",
                section_plan_id=7,
                plan=plan_payload,
            )
            return 7

        async def save_failed_plan(self, request, failure_code: str, failure_reason: str):
            return 0

    repo = _Repo()

    async def _resolve(request, llm_client, repository):
        ready = await repository.get_ready_by_fingerprint(
            request.outline_node_id,
            request.input_fingerprint,
        )
        if ready is not None:
            return ready
        llm_calls["n"] += 1
        plan_id = await repository.save_ready_plan(request, {"section_goal": "预取计划"})
        return SectionPlanResolution(
            mode="planned_new",
            section_plan_id=plan_id,
            plan={"section_goal": "预取计划"},
        )

    monkeypatch.setattr(
        "app.core.writer.section_plan.service.resolve_section_plan",
        _resolve,
    )
    monkeypatch.setattr(
        "app.core.writer.section_plan.repository.SessionScopedSectionPlanRepository",
        lambda: repo,
    )
    composer = _composer()
    node = _node()
    first = await prefetch_one_section_plan(
        composer, project_id=9, node=node, industry_profile="市政工程"
    )
    second = await prefetch_one_section_plan(
        composer, project_id=9, node=node, industry_profile="市政工程"
    )
    assert llm_calls["n"] == 1
    assert first.mode == "planned_new"
    assert second.mode == "planned_reused"
    assert second.section_plan_id == 7


@pytest.mark.asyncio
async def test_prefetch_failure_does_not_block_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.core.writer.composer.compose_pipeline import (
        ComposePipeline,
        ComposeSectionsRequest,
    )

    monkeypatch.setattr(settings, "WRITING_PLAN_PREFETCH_ENABLED", True)
    monkeypatch.setattr(settings, "WRITING_JUDGE_DEFER_ENABLED", False)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("prefetch boom")

    monkeypatch.setattr(
        "app.core.writer.composer.plan_prefetch.prefetch_one_section_plan",
        _boom,
    )
    node = _node(node_id=4)
    composer = _DeferLikeComposer([node], project_id=9)
    drafts = await ComposePipeline(composer).compose_sections(
        ComposeSectionsRequest(project_id=9)
    )
    assert [item.id for item in drafts] == [4]
    assert composer.compose_free_calls == 1


@pytest.mark.asyncio
async def test_prefetch_flag_off_skips_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "WRITING_PLAN_PREFETCH_ENABLED", False)
    ctx = SimpleNamespace(free_nodes=[_node()], industry_profile="市政工程")
    tasks = start_section_plan_prefetch(_composer(), ctx)
    assert tasks == {}


@pytest.mark.asyncio
async def test_await_prefetch_swallows_task_error() -> None:
    async def _boom():
        raise RuntimeError("x")

    tasks = {2: __import__("asyncio").create_task(_boom())}
    await await_node_plan_prefetch(tasks, 2)


@pytest.mark.asyncio
async def test_cancel_plan_prefetch_drops_inflight() -> None:
    import asyncio

    started = asyncio.Event()

    async def _hang():
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(_hang())
    await started.wait()
    await cancel_plan_prefetch({1: task})
    assert task.cancelled() or task.done()


class _DeferLikeComposer:
    def __init__(self, nodes, project_id: int) -> None:
        self.nodes = nodes
        self.project_id = project_id
        self.db = SimpleNamespace(commit=AsyncMock(), execute=None)
        self._db_lock = _Lock()
        self.compose_free_calls = 0
        self.inline_judge_calls = 0

    async def _load_all_outline_nodes(self, _project_id: int):
        return list(self.nodes)

    def _sort_by_reading_order(self, nodes):
        return nodes

    async def _load_industry_profile(self, _project_id: int):
        return "市政工程"

    async def _ensure_material_pool_size(self, _project_id: int):
        return None

    async def _load_current_draft(self, _node_id: int):
        return None

    async def _compose_free(self, node, *_args, **_kwargs):
        self.compose_free_calls += 1
        return SimpleNamespace(
            content="free",
            source_material_ids=[8],
            source_clause_ids=[1],
            gate_flags=None,
            section_plan_id=None,
            planning_mode="planned_reused",
            planning_failure_code=None,
        )

    def _run_writer_post_llm_gate(self, *_args, **_kwargs):
        return []

    def _aggregate_gate_flags(self, _verdicts):
        return None

    async def _maybe_run_writer_cove(self, *_args, **_kwargs):
        return None

    def _merge_gate_flags(self, left, _right):
        return left

    def _is_writer_critical_commitment(self, _node, _payload):
        return False, "关键承诺"

    async def _maybe_run_writer_judge(self, *_args, **_kwargs):
        self.inline_judge_calls += 1

    async def _save_draft(self, node, *_args, **_kwargs):
        return SimpleNamespace(id=node.id, word_count=8, summary=None)

    async def _generate_summary(self, title, _content):
        return f"{title}-摘要"

    def _progress(self, *_args, **_kwargs):
        return None
