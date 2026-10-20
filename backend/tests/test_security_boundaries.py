"""权限边界防御测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.api.routers import files as files_router
from app.services.writing_service import WritingService


@pytest.mark.asyncio
async def test_process_document_rejects_document_outside_project() -> None:
    """父项目权限通过时, 仍必须拒绝不属于该项目的 document_id。"""
    document_service = Mock()
    document_service.get_document_by_id_and_project = AsyncMock(return_value=None)

    with (
        patch.object(files_router, "_get_project_with_auth", new=AsyncMock()),
        patch.object(files_router, "DocumentService", return_value=document_service),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await files_router.process_document(
                project_id=1,
                document_id=99,
                current_user=SimpleNamespace(id=1),
                db=SimpleNamespace(),
            )

    assert exc_info.value.status_code == 404
    assert "不属于该项目" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_draft_query_binds_node_to_project() -> None:
    """草稿查询必须 join outline node 并绑定 project_id。"""
    execute_result = Mock()
    execute_result.scalars.return_value.first.return_value = None
    db = SimpleNamespace(execute=AsyncMock(return_value=execute_result))
    service = WritingService(db)  # type: ignore[arg-type]
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=7))  # type: ignore[method-assign]

    with pytest.raises(Exception):
        await service.get_draft(project_id=7, node_id=42, user=SimpleNamespace(id=1))

    statement = db.execute.call_args.args[0]
    compiled = str(statement)
    assert "JOIN writing_outline_nodes" in compiled
    assert "writing_outline_nodes.project_id" in compiled


@pytest.mark.asyncio
async def test_list_node_images_checks_project_access_and_filters_bindings() -> None:
    """节点图片列表必须先校验项目访问权限, 再按 project_id 过滤绑定。"""
    from app.api.routers import writing as writing_router

    execute_result = Mock()
    execute_result.all.return_value = []
    db = SimpleNamespace(execute=AsyncMock(return_value=execute_result))
    service = SimpleNamespace(_get_project_or_raise=AsyncMock(return_value=SimpleNamespace(id=7)))

    with patch.object(writing_router, "_svc", return_value=service):
        result = await writing_router.list_node_images(
            project_id=7,
            node_id=42,
            current_user=SimpleNamespace(id=1),
            db=db,
        )

    assert result == []
    service._get_project_or_raise.assert_awaited_once()
    statement = db.execute.call_args.args[0]
    assert "writing_bindings.project_id" in str(statement)
