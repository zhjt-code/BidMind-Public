""" 防御测试: 写标并发生成不得把 composer.db 交给计划/图示仓储。

职责: 锁定 free_writing 传入 scoped 短 Session 仓储, 且每次方法调用开闭独立 session。
不做: 不连真实库、不跑 LLM、不验证计划/图示业务内容。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeResult:
    def scalar(self):
        return 0

    def scalars(self):
        return self

    def first(self):
        return None

    def all(self):
        return []


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False
        self.execute = AsyncMock(return_value=_FakeResult())
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock(side_effect=self._assign_id)
        self.expunge_all = MagicMock()

    @staticmethod
    def _assign_id(obj: object) -> None:
        setattr(obj, "id", 91)

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


def _plan_request():
    from app.core.writer.section_plan.contracts import SectionPlanRequest

    return SectionPlanRequest(
        project_id=1,
        outline_node_id=2,
        node_title="技术方案",
        target_word_count=1000,
        constraints=[],
        evidence_catalog=[],
        prompt_snapshot={"planner_prompt_sha256": "b" * 64, "source": "code"},
        input_fingerprint="c" * 64,
    )


def _make_composer():
    from app.core.writer.section_composer import SectionComposer

    composer = SectionComposer(
        db=SimpleNamespace(marker="composer-db"),
        llm_client=SimpleNamespace(chat=AsyncMock(return_value="<p>正文</p>")),
        project_id=1,
    )
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return composer


@pytest.fixture
def compose_settings(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.0)
    monkeypatch.setattr(settings, "WRITING_DE_AI_REWRITE_ENABLED", False)
    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_ENABLED", True)
    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_RATIO", 1.0)
    monkeypatch.setattr(settings, "WRITING_SECTION_DIAGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "WRITING_SECTION_DIAGRAM_RATIO", 1.0)
    return settings


def _plan_with_visuals() -> dict:
    return {
        "section_goal": "阐明施工组织方案",
        "arguments": [],
        "measures": [
            {
                "measure_id": "measure_1",
                "action": "组建现场施工管理团队",
                "steps": ["确定项目经理", "配置专职安全员", "明确岗位职责"],
                "verification": "查验任命文件",
                "argument_ids": [],
                "project_fact_ids": [],
            },
        ],
        "acceptance_criteria": [],
        "visuals": [
            {
                "visual_id": "vis_1",
                "diagram_type": "flowchart",
                "title": "施工管理组织流程图",
                "purpose": "展示组建流程",
                "measure_ids": ["measure_1"],
                "placement_hint": None,
            }
        ],
    }


@pytest.mark.asyncio
async def test_compose_free_does_not_pass_composer_db_to_plan_repository(
    monkeypatch: pytest.MonkeyPatch,
    compose_settings,
) -> None:
    import app.core.writer.section_plan.context as plan_context
    import app.core.writer.section_plan.service as plan_service
    import app.core.writer.section_plan.source_loader as plan_source_loader
    from app.core.writer.section_plan.contracts import SectionPlanResolution
    from app.core.writer.section_plan.repository import SessionScopedSectionPlanRepository

    captured: dict[str, object] = {}

    async def _capture_resolve(request, llm_client, repository):
        captured["repo"] = repository
        return SectionPlanResolution(
            mode="planned_new",
            section_plan_id=42,
            plan=_plan_with_visuals(),
        )

    monkeypatch.setattr(plan_source_loader, "load_section_plan_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        plan_context,
        "build_section_plan_request",
        lambda **kwargs: SimpleNamespace(outline_node_id=3, input_fingerprint="f" * 64),
    )
    monkeypatch.setattr(plan_service, "resolve_section_plan", _capture_resolve)

    composer = _make_composer()
    node = SimpleNamespace(
        id=3, title="施工组织方案", depth=1, target_word_count=2000, chapter_profile=None,
    )
    await composer._compose_free(node, None, [], {3: "三、"})

    repo = captured["repo"]
    assert isinstance(repo, SessionScopedSectionPlanRepository)
    assert getattr(repo, "db", None) is not composer.db


@pytest.mark.asyncio
async def test_compose_free_does_not_pass_composer_db_to_diagram_repository(
    monkeypatch: pytest.MonkeyPatch,
    compose_settings,
) -> None:
    import app.core.writer.section_plan.context as plan_context
    import app.core.writer.section_plan.service as plan_service
    import app.core.writer.section_plan.source_loader as plan_source_loader
    import app.core.writer.visuals.materializer as materializer_module
    from app.core.writer.section_plan.contracts import SectionPlanResolution
    from app.core.writer.visuals.repository import SessionScopedSectionDiagramRepository

    captured: dict[str, object] = {}

    async def _capture_materialize(repository, **kwargs):
        captured["repo"] = repository
        return []

    monkeypatch.setattr(plan_source_loader, "load_section_plan_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        plan_context,
        "build_section_plan_request",
        lambda **kwargs: SimpleNamespace(outline_node_id=3, input_fingerprint="f" * 64),
    )
    monkeypatch.setattr(
        plan_service,
        "resolve_section_plan",
        AsyncMock(return_value=SectionPlanResolution(
            mode="planned_new",
            section_plan_id=42,
            plan=_plan_with_visuals(),
        )),
    )
    monkeypatch.setattr(
        materializer_module,
        "materialize_section_diagrams",
        _capture_materialize,
    )

    composer = _make_composer()
    node = SimpleNamespace(
        id=3, title="施工组织方案", depth=1, target_word_count=2000, chapter_profile=None,
    )
    await composer._compose_free(node, None, [], {3: "三、"})

    repo = captured["repo"]
    assert isinstance(repo, SessionScopedSectionDiagramRepository)
    assert getattr(repo, "db", None) is not composer.db


@pytest.mark.asyncio
async def test_scoped_plan_repository_opens_fresh_session_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.writer.section_plan.repository as plan_repo_mod
    from app.core.writer.section_plan.repository import SessionScopedSectionPlanRepository

    sessions: list[_FakeAsyncSession] = []

    def _factory() -> _FakeAsyncSession:
        session = _FakeAsyncSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(plan_repo_mod, "async_session_factory", _factory)
    repo = SessionScopedSectionPlanRepository()

    await repo.get_ready_by_fingerprint(2, "c" * 64)
    await repo.save_ready_plan(_plan_request(), {"section_goal": "目标"})
    await repo.save_failed_plan(_plan_request(), "planner_failed", "slow")

    assert len(sessions) == 3
    assert all(item.entered and item.exited for item in sessions)
    assert sessions[0] is not sessions[1]
    assert sessions[1] is not sessions[2]


@pytest.mark.asyncio
async def test_scoped_diagram_repository_opens_fresh_session_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.writer.visuals.repository as diagram_repo_mod
    from app.core.writer.visuals.repository import SessionScopedSectionDiagramRepository

    sessions: list[_FakeAsyncSession] = []

    def _factory() -> _FakeAsyncSession:
        session = _FakeAsyncSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(diagram_repo_mod, "async_session_factory", _factory)
    repo = SessionScopedSectionDiagramRepository()

    await repo.get_ready_by_project(1)
    await repo.get_by_plan_visual(42, "vis_1")
    await repo.save_failed_diagram(
        project_id=1,
        outline_node_id=3,
        section_plan_id=42,
        visual_id="vis_1",
        diagram_type="flowchart",
        title="施工管理组织流程图",
        failure_code="visual_policy_rejected",
    )

    assert len(sessions) == 3
    assert all(item.entered and item.exited for item in sessions)
    assert sessions[0] is not sessions[1]
