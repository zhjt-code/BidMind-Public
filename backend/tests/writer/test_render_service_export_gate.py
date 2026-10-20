"""写标导出门禁测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ServiceError
from app.services.writing.render_service import RenderService


@pytest.mark.asyncio
async def test_explicit_render_allows_high_fixed_template_consistency_issue() -> None:
    """重新渲染只刷新预览/Word快照, 不应被 fixed_template high 问题阻断。"""
    artifact = SimpleNamespace(
        docx_blob=b"docx",
        render_version=1,
        consistency_render_version=1,
        consistency_issues_json=json.dumps([
            {
                "type": "fixed_template_field_missing",
                "severity": "high",
                "message": "固定模板缺少签章字段",
            }
        ], ensure_ascii=False),
    )
    service = RenderService(
        db=SimpleNamespace(),
        project_resolver=AsyncMock(return_value=SimpleNamespace(status="completed")),
        ensure_preview_ready=lambda _project: None,
        ensure_render_artifact=AsyncMock(return_value=artifact),
    )

    docx_bytes = await service.render_docx(1566, SimpleNamespace())

    assert docx_bytes == b"docx"
    service._ensure_render_artifact.assert_awaited_once_with(
        1566,
        force_rebuild=True,
        explicit_render=True,
    )


@pytest.mark.asyncio
async def test_download_blocks_high_fixed_template_consistency_issue() -> None:
    """高风险固定模板一致性问题存在且未确认时, 后端下载入口必须阻断。"""
    artifact = SimpleNamespace(
        docx_blob=b"docx",
        render_version=1,
        consistency_render_version=1,
        consistency_issues_json=json.dumps([
            {
                "type": "fixed_template_field_missing",
                "severity": "high",
                "message": "固定模板缺少签章字段",
            }
        ], ensure_ascii=False),
    )
    service = RenderService(
        db=SimpleNamespace(),
        project_resolver=AsyncMock(return_value=SimpleNamespace(status="completed")),
        ensure_preview_ready=lambda _project: None,
        ensure_render_artifact=AsyncMock(return_value=artifact),
    )

    with pytest.raises(ServiceError, match="固定模板"):
        await service.get_rendered_docx(1566, SimpleNamespace())


@pytest.mark.asyncio
async def test_download_allows_high_fixed_template_issue_after_user_ack() -> None:
    """用户在下载弹窗显式确认后, fixed_template 高风险提示不得继续阻断导出。"""
    artifact = SimpleNamespace(
        docx_blob=b"docx",
        render_version=1,
        consistency_render_version=1,
        consistency_issues_json=json.dumps([
            {
                "type": "fixed_template_field_missing",
                "severity": "high",
                "message": "固定模板缺少签章字段",
            }
        ], ensure_ascii=False),
    )
    service = RenderService(
        db=SimpleNamespace(),
        project_resolver=AsyncMock(return_value=SimpleNamespace(status="completed")),
        ensure_preview_ready=lambda _project: None,
        ensure_render_artifact=AsyncMock(return_value=artifact),
    )

    docx_bytes = await service.get_rendered_docx(
        1566,
        SimpleNamespace(),
        allow_blocking_issues=True,
    )

    assert docx_bytes == b"docx"
