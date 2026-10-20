""": SectionPlan 外层 wait_for 不得短于云端单次 HTTP 预算。

覆盖:
- wait 时限下限 = max(配置值, CLOUD_TIMEOUT)
- 超时必须落 failed 并以带 elapsed/timeout 明细的 TimeoutError 退出
- 预算内完成不得降级、不得写 failed
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _FakePlanRepo:
    def __init__(self) -> None:
        self.saved_failures: list[tuple[str, str]] = []

    async def save_failed_plan(self, request, failure_code: str, failure_reason: str) -> int:
        self.saved_failures.append((failure_code, failure_reason))
        return 1


async def _hang(*args, **kwargs) -> None:
    await asyncio.Event().wait()


def test_section_plan_wait_timeout_floors_at_cloud_timeout(monkeypatch) -> None:
    from app.core.config import settings
    from app.core.writer.section_plan.feature_flags import (
        resolve_section_plan_wait_timeout_sec,
    )

    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_TIMEOUT_SEC", 60)
    monkeypatch.setattr(settings, "CLOUD_TIMEOUT", 300.0)

    assert resolve_section_plan_wait_timeout_sec() == 300.0


def test_section_plan_wait_timeout_keeps_larger_configured_value(monkeypatch) -> None:
    from app.core.config import settings
    from app.core.writer.section_plan.feature_flags import (
        resolve_section_plan_wait_timeout_sec,
    )

    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_TIMEOUT_SEC", 600)
    monkeypatch.setattr(settings, "CLOUD_TIMEOUT", 300.0)

    assert resolve_section_plan_wait_timeout_sec() == 600.0


@pytest.mark.asyncio
async def test_await_section_plan_timeout_persists_failed_and_raises_detail() -> None:
    from app.core.writer.composer.free_writing_route import (
        await_section_plan_within_budget,
    )

    repo = _FakePlanRepo()
    request = SimpleNamespace(outline_node_id=9)

    with pytest.raises(TimeoutError, match=r"wait_for exceeded .*elapsed_ms=") as caught:
        await await_section_plan_within_budget(
            _hang(),
            timeout_sec=0.05,
            request=request,
            repository=repo,
            node_id=9,
        )

    assert repo.saved_failures
    code, reason = repo.saved_failures[0]
    assert code == "planner_failed"
    assert "wait_for exceeded 0.05" in reason
    assert "elapsed_ms=" in str(caught.value)
    assert "timeout_sec=" in str(caught.value)


@pytest.mark.asyncio
async def test_await_section_plan_success_within_budget_does_not_persist() -> None:
    from app.core.writer.composer.free_writing_route import (
        await_section_plan_within_budget,
    )

    repo = _FakePlanRepo()

    async def ready() -> str:
        return "planned_new"

    result = await await_section_plan_within_budget(
        ready(),
        timeout_sec=1.0,
        request=SimpleNamespace(),
        repository=repo,
        node_id=3,
    )

    assert result == "planned_new"
    assert repo.saved_failures == []


@pytest.mark.asyncio
async def test_await_section_plan_persist_error_still_raises_timeout() -> None:
    from app.core.writer.composer.free_writing_route import (
        await_section_plan_within_budget,
    )

    class _BrokenRepo:
        async def save_failed_plan(self, *args, **kwargs) -> int:
            raise RuntimeError("db down")

    with pytest.raises(TimeoutError, match="wait_for exceeded"):
        await await_section_plan_within_budget(
            _hang(),
            timeout_sec=0.05,
            request=SimpleNamespace(),
            repository=_BrokenRepo(),
            node_id=1,
        )


@pytest.mark.asyncio
async def test_compose_free_wait_for_timeout_degrades_to_legacy(monkeypatch) -> None:
    from app.core.config import settings
    from app.core.writer.section_composer import SectionComposer
    from app.core.writer.section_plan.contracts import SectionPlanRequest

    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.0)
    monkeypatch.setattr(settings, "WRITING_DE_AI_REWRITE_ENABLED", False)
    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_ENABLED", True)
    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_RATIO", 1.0)
    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(settings, "CLOUD_TIMEOUT", 0.05)

    import app.core.writer.section_plan.repository as plan_repo
    import app.core.writer.section_plan.service as plan_service
    import app.core.writer.section_plan.source_loader as plan_source

    monkeypatch.setattr(plan_source, "load_section_plan_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(plan_service, "resolve_section_plan", _hang)

    captured = _FakePlanRepo()

    class _RepoFactory:
        def __init__(self) -> None:
            self.inner = captured

        async def get_ready_by_fingerprint(self, *args, **kwargs):
            return None

        async def save_failed_plan(self, request, failure_code: str, failure_reason: str) -> int:
            assert isinstance(request, SectionPlanRequest)
            return await captured.save_failed_plan(request, failure_code, failure_reason)

        async def save_ready_plan(self, *args, **kwargs) -> int:
            raise AssertionError("timeout path must not save ready")

    monkeypatch.setattr(plan_repo, "SessionScopedSectionPlanRepository", _RepoFactory)

    llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>自由论述输出</p>"))
    composer = SectionComposer(db=SimpleNamespace(), llm_client=llm_client, project_id=1)
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(
        id=3, title="服务方案", depth=1, target_word_count=2000, chapter_profile=None,
    )
    payload = await composer._compose_free(
        node,
        industry_profile=None,
        previous_summaries=[],
        number_map={3: "三、"},
    )

    assert "自由论述输出" in payload.content
    assert payload.planning_mode == "legacy_fallback"
    assert payload.planning_failure_code == "planner_failed"
    assert captured.saved_failures
    llm_client.chat.assert_awaited_once()
