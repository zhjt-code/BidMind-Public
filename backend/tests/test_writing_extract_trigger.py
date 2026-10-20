"""写标约束提取触发前置条件防御测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import ServiceError
from app.models.project import ProjectStatus
from app.services.writing_service import WritingService


def _project(status: ProjectStatus = ProjectStatus.CREATED) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        status=status,
        billing_mode="trial",
        owner_id=1,
    )


@pytest.mark.asyncio
async def test_trigger_extract_rejects_missing_tender_before_dispatch() -> None:
    """缺少招标文件时必须在入队前拦截, 避免 worker 才报未找到。"""
    service = WritingService(SimpleNamespace())  # type: ignore[arg-type]
    service._get_project_or_raise = AsyncMock(return_value=_project())  # type: ignore[method-assign]
    service._get_tender_doc = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(ServiceError) as exc_info:
        await service.trigger_extract(project_id=7, user=SimpleNamespace(id=1))

    assert "未找到招标文件" in exc_info.value.detail


@pytest.mark.asyncio
async def test_trigger_extract_rejects_unparsed_tender_before_dispatch() -> None:
    """招标文件尚未解析完成时不得提前投递约束提取任务。"""
    service = WritingService(SimpleNamespace())  # type: ignore[arg-type]
    service._get_project_or_raise = AsyncMock(return_value=_project(ProjectStatus.PARSING))  # type: ignore[method-assign]
    service._get_tender_doc = AsyncMock(
        return_value=SimpleNamespace(id=11, parsed=0)
    )  # type: ignore[method-assign]

    with pytest.raises(ServiceError) as exc_info:
        await service.trigger_extract(project_id=7, user=SimpleNamespace(id=1))

    assert "尚未解析完成" in exc_info.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ProjectStatus.UPLOADING, ProjectStatus.PARSED])
async def test_trigger_extract_dispatches_when_tender_is_parsed(
    status: ProjectStatus,
) -> None:
    """招标文件已解析完成时仍按原契约创建 TaskRun 并投递后台任务。"""
    # db 需要支持 commit，BillingGuard.pre_check 后会调用 await self.db.commit()
    service = WritingService(SimpleNamespace(commit=AsyncMock()))  # type: ignore[arg-type]
    service._get_project_or_raise = AsyncMock(return_value=_project(status))  # type: ignore[method-assign]
    service._get_tender_doc = AsyncMock(
        return_value=SimpleNamespace(id=11, parsed=1)
    )  # type: ignore[method-assign]

    retry_service = SimpleNamespace(
        begin_attempt=AsyncMock(return_value=SimpleNamespace(id=101, attempt=1)),
        attach_dispatch_result=AsyncMock(),
    )

    with (
        patch("app.services.writing_service.is_task_active", return_value=False),
        patch("app.services.retry_service.RetryService", return_value=retry_service),
        patch(
            "app.core.tasks.dispatch_background_job",
            new=AsyncMock(return_value=("arq", "job-7")),
        ) as dispatch_background_job,
        # BillingGuard.pre_check 需要真实 db，测试中不涉及计费逻辑，整体 stub
        patch("app.billing.services.billing_guard.BillingGuard.pre_check", new=AsyncMock()),
    ):
        result = await service.trigger_extract(project_id=7, user=SimpleNamespace(id=1))

    assert result == {
        "task_name": "writing_extract_7",
        "message": "约束提取已启动",
        "queue_mode": "arq",
    }
    retry_service.begin_attempt.assert_awaited_once()
    retry_service.attach_dispatch_result.assert_awaited_once()
    dispatch_background_job.assert_awaited_once()
