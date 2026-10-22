import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.project import ProjectStatus
from app.services.writing_service import WritingService


def _reset_service_locks() -> None:
    WritingService._render_locks.clear()


@pytest.mark.asyncio
async def test_get_consistency_issues_returns_cached_without_recompute() -> None:
    _reset_service_locks()
    artifact = SimpleNamespace(
        project_id=7,
        render_version=3,
        consistency_render_version=3,
        consistency_issues_json=json.dumps([{"message": "cached"}], ensure_ascii=False),
    )
    service = WritingService(SimpleNamespace())
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=7, status=ProjectStatus.COMPOSED))  # type: ignore[method-assign]
    service._ensure_render_artifact = AsyncMock(return_value=artifact)  # type: ignore[method-assign]

    issues = await service.get_consistency_issues(7, SimpleNamespace())

    assert issues == [{"message": "cached"}]


@pytest.mark.asyncio
async def test_get_consistency_issues_uses_consistency_only_cache() -> None:
    _reset_service_locks()
    artifact = SimpleNamespace(
        project_id=8,
        render_version=2,
        preview_html=None,
        docx_blob=None,
        consistency_render_version=2,
        consistency_issues_json=json.dumps([{"message": "consistency-only"}], ensure_ascii=False),
    )
    execute_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: artifact),
    )
    fake_db = SimpleNamespace(execute=AsyncMock(return_value=execute_result))
    service = WritingService(fake_db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=8, status=ProjectStatus.COMPOSED))  # type: ignore[method-assign]
    service._ensure_render_artifact = AsyncMock()  # type: ignore[method-assign]

    issues = await service.get_consistency_issues(8, SimpleNamespace())

    assert issues == [{"message": "consistency-only"}]
    service._ensure_render_artifact.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_consistency_issues_rebuilds_artifact_when_cache_missing() -> None:
    _reset_service_locks()
    cached_artifact = SimpleNamespace(
        project_id=9,
        render_version=1,
        consistency_render_version=1,
        consistency_issues_json=json.dumps([{"message": "rebuilt"}], ensure_ascii=False),
    )
    service = WritingService(SimpleNamespace())
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=9, status=ProjectStatus.COMPOSED))  # type: ignore[method-assign]
    service._ensure_render_artifact = AsyncMock(side_effect=[
        SimpleNamespace(
            project_id=9,
            render_version=1,
            consistency_render_version=None,
            consistency_issues_json=None,
        ),
        cached_artifact,
    ])  # type: ignore[method-assign]

    issues = await service.get_consistency_issues(9, SimpleNamespace())

    assert issues == [{"message": "rebuilt"}]
    service._ensure_render_artifact.assert_any_call(9)
    service._ensure_render_artifact.assert_any_call(
        9,
        force_rebuild=True,
        explicit_render=False,
    )


@pytest.mark.asyncio
async def test_get_render_artifact_forces_populate_existing_to_avoid_stale_identity_map() -> None:
    """并发 preview/consistency 请求下，锁内二次查询必须绕开 session 旧对象缓存。"""
    captured = {}

    async def _execute(stmt):
        captured["options"] = stmt.get_execution_options()
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: None),
        )

    service = WritingService(SimpleNamespace(execute=AsyncMock(side_effect=_execute)))

    await service._get_render_artifact(1568)

    assert captured["options"].get("populate_existing") is True


@pytest.mark.asyncio
async def test_ensure_render_artifact_reuses_artifact_that_appears_while_waiting_for_lock() -> None:
    """并发请求等待锁后若已有完整快照，不应再次重建渲染。"""
    _reset_service_locks()
    stale = None
    fresh = SimpleNamespace(
        project_id=1568,
        render_version=1,
        preview_html="<html />",
        docx_blob=b"docx",
    )
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=ProjectStatus.COMPLETED)),
        commit=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_render_artifact = AsyncMock(side_effect=[stale, fresh])  # type: ignore[method-assign]
    service._build_render_snapshot = AsyncMock()  # type: ignore[method-assign]

    artifact = await service._ensure_render_artifact(1568)

    assert artifact is fresh
    service._build_render_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_consistency_cache_after_compose_writes_without_render(monkeypatch) -> None:
    from app.core.tasks.writing_compose_task import _refresh_consistency_cache_after_compose

    class _Checker:
        async def check(self, project_id: int) -> list[dict]:
            assert project_id == 31
            return [{"message": "early"}]

    monkeypatch.setattr(
        "app.core.writer.consistency_checker.get_consistency_checker",
        lambda **_: _Checker(),
    )
    execute_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: None),
    )
    fake_db = SimpleNamespace(
        execute=AsyncMock(return_value=execute_result),
        add=MagicMock(),
        commit=AsyncMock(),
    )

    issue_count = await _refresh_consistency_cache_after_compose(fake_db, 31)  # type: ignore[arg-type]

    assert issue_count == 1
    artifact = fake_db.add.call_args.args[0]
    assert artifact.preview_html is None
    assert artifact.docx_blob is None
    assert json.loads(artifact.consistency_issues_json) == [{"message": "early"}]
    assert artifact.consistency_render_version == artifact.render_version
    fake_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_render_artifact_force_rebuild_increments_version_and_clears_consistency() -> None:
    _reset_service_locks()
    artifact = SimpleNamespace(
        project_id=12,
        render_version=2,
        preview_html="<old/>",
        docx_blob=b"old",
        docx_size=3,
        rendered_at=None,
        consistency_issues_json='[{"message":"old"}]',
        consistency_checked_at="old",
        consistency_render_version=2,
    )
    project = SimpleNamespace(status=ProjectStatus.COMPOSED)
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=project),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
        add=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_render_artifact = AsyncMock(side_effect=[artifact, artifact])  # type: ignore[method-assign]
    service._build_render_snapshot = AsyncMock(return_value=("<html>new</html>", b"new-docx", [{"message": "new"}]))  # type: ignore[method-assign]

    rebuilt = await service._ensure_render_artifact(
        12,
        force_rebuild=True,
        explicit_render=True,
    )

    assert rebuilt is artifact
    assert artifact.render_version == 3
    assert artifact.preview_html == "<html>new</html>"
    assert artifact.docx_blob == b"new-docx"
    assert artifact.docx_size == len(b"new-docx")
    assert json.loads(artifact.consistency_issues_json) == [{"message": "new"}]
    assert artifact.consistency_checked_at is not None
    assert artifact.consistency_render_version == 3
    assert project.status == ProjectStatus.COMPLETED
    assert fake_db.commit.await_count == 3
    fake_db.refresh.assert_awaited_once_with(artifact)


@pytest.mark.asyncio
async def test_ensure_render_artifact_implicit_render_promotes_composed_to_completed() -> None:
    _reset_service_locks()
    artifact = SimpleNamespace(
        project_id=18,
        render_version=1,
        preview_html=None,
        docx_blob=None,
        docx_size=None,
        rendered_at=None,
        consistency_issues_json=None,
        consistency_checked_at=None,
        consistency_render_version=None,
    )
    project = SimpleNamespace(status=ProjectStatus.COMPOSED)
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=project),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
        add=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_render_artifact = AsyncMock(side_effect=[artifact, artifact])  # type: ignore[method-assign]
    service._build_render_snapshot = AsyncMock(return_value=("<html>auto</html>", b"auto-docx", [{"message": "auto"}]))  # type: ignore[method-assign]

    rebuilt = await service._ensure_render_artifact(18)

    assert rebuilt is artifact
    assert artifact.preview_html == "<html>auto</html>"
    assert artifact.docx_blob == b"auto-docx"
    assert project.status == ProjectStatus.COMPLETED
    assert fake_db.commit.await_count == 2
    fake_db.refresh.assert_awaited_once_with(artifact)


@pytest.mark.asyncio
async def test_ensure_render_artifact_cached_artifact_self_heals_composed_status() -> None:
    _reset_service_locks()
    artifact = SimpleNamespace(
        project_id=19,
        render_version=2,
        preview_html="<html />",
        docx_blob=b"cached-docx",
    )
    project = SimpleNamespace(status=ProjectStatus.COMPOSED)
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=project),
        commit=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_render_artifact = AsyncMock(return_value=artifact)  # type: ignore[method-assign]

    rebuilt = await service._ensure_render_artifact(19)

    assert rebuilt is artifact
    assert project.status == ProjectStatus.COMPLETED
    fake_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_rendered_docx_reuses_cached_artifact() -> None:
    _reset_service_locks()
    artifact = SimpleNamespace(
        project_id=15,
        render_version=1,
        preview_html="<html />",
        docx_blob=b"cached-docx",
    )
    service = WritingService(SimpleNamespace())
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=15, status=ProjectStatus.COMPOSED))  # type: ignore[method-assign]
    service._ensure_render_artifact = AsyncMock(return_value=artifact)  # type: ignore[method-assign]

    docx_bytes = await service.get_rendered_docx(15, SimpleNamespace())

    assert docx_bytes == b"cached-docx"


@pytest.mark.asyncio
async def test_get_rendered_docx_allows_fixed_template_high_risk_issue_after_user_ack() -> None:
    _reset_service_locks()
    artifact = SimpleNamespace(
        project_id=16,
        render_version=1,
        preview_html="<html />",
        docx_blob=b"cached-docx",
        consistency_issues_json=json.dumps([
            {
                "type": "fixed_template_field_missing",
                "severity": "high",
                "message": "固定模板字段缺失",
            }
        ], ensure_ascii=False),
        consistency_render_version=1,
    )
    service = WritingService(SimpleNamespace())
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=16, status=ProjectStatus.COMPOSED))  # type: ignore[method-assign]
    service._ensure_render_artifact = AsyncMock(return_value=artifact)  # type: ignore[method-assign]

    docx_bytes = await service.get_rendered_docx(
        16,
        SimpleNamespace(),
        allow_blocking_issues=True,
    )

    assert docx_bytes == b"cached-docx"


@pytest.mark.asyncio
async def test_invalidate_render_artifact_clears_cached_fields() -> None:
    _reset_service_locks()
    artifact = SimpleNamespace(
        preview_html="<html />",
        docx_blob=b"cached-docx",
        docx_size=123,
        rendered_at="old",
        consistency_issues_json='[{"message":"cached"}]',
        consistency_checked_at="old",
        consistency_render_version=4,
    )
    service = WritingService(SimpleNamespace())
    service._get_render_artifact = AsyncMock(return_value=artifact)  # type: ignore[method-assign]

    await service._invalidate_render_artifact(22)  # type: ignore[arg-type]

    assert artifact.preview_html is None
    assert artifact.docx_blob is None
    assert artifact.docx_size is None
    assert artifact.rendered_at is None
    assert artifact.consistency_issues_json is None
    assert artifact.consistency_checked_at is None
    assert artifact.consistency_render_version is None


@pytest.mark.asyncio
async def test_refresh_fixed_template_drafts_only_rewrites_changed_content() -> None:
    _reset_service_locks()

    class _ScalarResult:
        def __init__(self, nodes):
            self._nodes = nodes

        def scalars(self):
            return self

        def all(self):
            return self._nodes

    nodes = [
        SimpleNamespace(id=1, sort_order=0),
        SimpleNamespace(id=2, sort_order=1),
    ]
    fake_db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult(nodes)),
    )
    composer = SimpleNamespace(
        _fill_template=AsyncMock(side_effect=[
            SimpleNamespace(content="same", source_material_ids=[], source_clause_ids=[]),
            SimpleNamespace(content="new", source_material_ids=[7], source_clause_ids=[9]),
        ]),
        _load_current_draft=AsyncMock(side_effect=[
            SimpleNamespace(content="same"),
            SimpleNamespace(content="old"),
        ]),
        _save_draft=AsyncMock(),
    )
    service = WritingService(fake_db)

    await service._refresh_fixed_template_drafts(540, composer)

    assert composer._save_draft.await_count == 1
    args, kwargs = composer._save_draft.await_args
    assert args[0] is nodes[1]
    request = args[1]
    from app.core.writer.composer.draft_persistence import DraftWriteRequest

    assert isinstance(request, DraftWriteRequest)
    assert request.content == "new"
    assert request.source_material_ids == [7]
    assert request.source_clause_ids == [9]
    assert kwargs == {}
