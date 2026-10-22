from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.tasks.writing_outline_task import run_writing_outline_task
from app.core.writer.outline_generator import OutlineGenerator
from app.models.project import ProjectStatus, ProjectType
from app.schemas.writing import BidDirectorySpec, BidStructureAnalysis


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_generate_from_spec_emits_progressive_callback(monkeypatch) -> None:
    events: list[tuple[int, int, str]] = []
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "technical",
                "volume_title": "技术标",
                "items": [
                    {"order": 1, "title": "技术方案"},
                    {"order": 2, "title": "人员配置"},
                ],
            }
        ],
    )

    async def _fake_enrich(node, *_args, **_kwargs):
        node.children = []

    async def _fake_save_tree(_project_id, nodes):
        return nodes

    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_enrich_skeleton_node", _fake_enrich)
    monkeypatch.setattr(generator, "_save_tree", _fake_save_tree)
    monkeypatch.setattr(
        "app.core.writer.outline_generator.attach_scope_titles_to_outline",
        AsyncMock(return_value=0),
    )

    await generator._generate_from_spec(
        7,
        spec,
        30000,
        progress_callback=lambda current, total, message: events.append(
            (current, total, message)
        ),
    )

    assert events[0] == (0, 3, "骨架丰富启动")
    assert any("骨架丰富 1/2" in m for _, _, m in events)
    assert (2, 3, "实质内容标题挂载") in events
    attach_idx = next(i for i, (_, _, m) in enumerate(events) if m == "实质内容标题挂载")
    assert all(current < total for current, total, _ in events[: attach_idx + 1])
    assert any(message == "写入目录" for _, _, message in events)
    assert events[-1][:2] == (3, 3)


@pytest.mark.asyncio
async def test_generate_from_spec_keeps_generating_open_during_scope_attach(
    monkeypatch,
) -> None:
    """单叶子丰富结束后不得 current=total，否则目录生成步会提前打满。"""
    events: list[tuple[int, int, str]] = []
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "technical",
                "volume_title": "技术标",
                "items": [{"order": 1, "title": "技术方案"}],
            }
        ],
    )

    async def _fake_enrich(node, *_args, **_kwargs):
        node.children = []

    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_enrich_skeleton_node", _fake_enrich)
    monkeypatch.setattr(generator, "_save_tree", AsyncMock(side_effect=lambda _pid, nodes: nodes))
    monkeypatch.setattr(
        "app.core.writer.outline_generator.attach_scope_titles_to_outline",
        AsyncMock(return_value=7),
    )

    await generator._generate_from_spec(
        7,
        spec,
        30000,
        progress_callback=lambda current, total, message: events.append(
            (current, total, message)
        ),
    )

    enrich_done = [e for e in events if "骨架丰富 1/1" in e[2]]
    assert enrich_done
    assert enrich_done[0][0] < enrich_done[0][1]
    assert (1, 2, "实质内容标题挂载") in events
    assert events[-1] == (2, 2, "写入目录")


@pytest.mark.asyncio
async def test_generate_self_composed_emits_progressive_callback(monkeypatch) -> None:
    events: list[tuple[int, int, str]] = []
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    structure = BidStructureAnalysis(
        structure_mode="tech_comm",
        has_technical=True,
        has_commercial=True,
    )

    async def _fake_fill(volume_node, *_args, **_kwargs):
        volume_node.children = []

    async def _fake_save_tree(_project_id, nodes):
        return nodes

    monkeypatch.setattr(generator, "_build_context", AsyncMock(return_value={"industry_profile": ""}))
    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_fill_volume", _fake_fill)
    monkeypatch.setattr(generator, "_save_tree", _fake_save_tree)
    monkeypatch.setattr(
        "app.core.writer.outline_generator.attach_scope_titles_to_outline",
        AsyncMock(return_value=0),
    )

    await generator._generate_self_composed(
        7,
        structure,
        30000,
        progress_callback=lambda current, total, message: events.append(
            (current, total, message)
        ),
    )

    assert events[0] == (0, 3, "分卷填充启动")
    assert [current for current, total, _ in events if total == 3] == [0, 1, 2, 2, 3]
    assert any("分卷填充 1/2" in message for _, _, message in events)
    assert (2, 3, "实质内容标题挂载") in events
    assert any(message == "写入目录" for _, _, message in events)


@pytest.mark.asyncio
async def test_generate_skips_explicit_directory_without_items(monkeypatch) -> None:
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    empty_spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {"volume_type": "commercial", "volume_title": "商务部分格式", "items": []},
            {"volume_type": "technical", "volume_title": "技术部分格式", "items": []},
            {"volume_type": "pricing", "volume_title": "价格部分格式", "items": []},
        ],
    )
    structure = BidStructureAnalysis(
        structure_mode="tech_comm",
        has_technical=True,
        has_commercial=True,
    )

    async def _unexpected_from_spec(*_args, **_kwargs):
        raise AssertionError("空显式目录不应进入照搬模式")

    async def _fake_self_composed(*_args, **_kwargs):
        return ["self-composed"]

    monkeypatch.setattr(generator, "_load_catalog_template_keys", AsyncMock())
    monkeypatch.setattr(generator, "_load_bid_directory_spec", AsyncMock(return_value=empty_spec))
    monkeypatch.setattr(generator, "_load_bid_structure", AsyncMock(return_value=structure))
    monkeypatch.setattr(generator, "_generate_from_spec", _unexpected_from_spec)
    monkeypatch.setattr(generator, "_generate_self_composed", _fake_self_composed)

    result = await generator.generate(7)

    assert result == ["self-composed"]


@pytest.mark.asyncio
async def test_writing_outline_task_generating_heartbeat_is_progressive(monkeypatch) -> None:
    heartbeats: list[tuple[str, str]] = []
    project = SimpleNamespace(
        id=7,
        project_type=ProjectType.BID_WRITING,
        status=ProjectStatus.EXTRACTED,
        error_message=None,
    )
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=project),
        execute=AsyncMock(return_value=_ScalarResult([])),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    class _FakeRetryService:
        NODE_WRITING_OUTLINE = "writing_outline"

        def __init__(self, _db):
            pass

        async def mark_task_running(self, *_args, **_kwargs):
            return None

        async def mark_task_completed(self, *_args, **_kwargs):
            return None

        async def mark_task_failed(self, *_args, **_kwargs):
            return None

    class _FakeGenerator:
        async def generate(self, project_id, progress_callback=None):
            assert project_id == 7
            assert progress_callback is not None
            progress_callback(0, 3, "骨架丰富启动")
            progress_callback(1, 3, "骨架丰富 1/2: 技术方案")
            progress_callback(2, 3, "实质内容标题挂载")
            progress_callback(3, 3, "写入目录")
            return [object(), object()]

        async def generate_chapter_profiles(self, *_args, **_kwargs):
            return 0

    def _capture_heartbeat(_task_name, stage="", progress="", extra=None):
        heartbeats.append((stage, progress))

    import app.core.ai as ai_module
    import app.core.arq_runtime as arq_runtime
    import app.core.tasks as tasks_module
    import app.core.tasks.writing_outline_task as outline_task_module
    import app.core.writer.outline_generator as outline_generator_module
    import app.services.retry_service as retry_service_module

    monkeypatch.setattr(outline_task_module, "async_session_factory", lambda: _FakeSessionContext(fake_db))
    monkeypatch.setattr(tasks_module, "update_heartbeat", _capture_heartbeat)
    monkeypatch.setattr(arq_runtime, "is_project_cancelled", lambda _project_id: False)
    monkeypatch.setattr(retry_service_module, "RetryService", _FakeRetryService)
    monkeypatch.setattr(outline_generator_module, "get_outline_generator", lambda **_kwargs: _FakeGenerator())
    monkeypatch.setattr(ai_module, "release_project_binding", AsyncMock())

    await run_writing_outline_task(7)

    generating_pcts = [
        int(progress.split("%", 1)[0])
        for stage, progress in heartbeats
        if stage == "generating" and progress.split("%", 1)[0].isdigit()
    ]
    assert sorted(set(generating_pcts)) == [10, 36, 63, 90]
    assert generating_pcts == sorted(generating_pcts)
