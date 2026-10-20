""": ReviewService.start_review 的进行中/终态幂等保护。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.exceptions import ServiceError
from app.models.project import ProjectStatus
from app.models.user import UserRole
from app.services.review_service import ReviewService


class _NoQueryDb:
    """终态短路分支不应再访问文档、要求、计费或任务队列。"""

    def __init__(self) -> None:
        self.execute_calls = 0
        self.commits = 0

    async def execute(self, _stmt: Any) -> Any:
        self.execute_calls += 1
        raise AssertionError("terminal start_review branch must not query downstream state")

    async def commit(self) -> None:
        self.commits += 1


def _project(status: ProjectStatus) -> SimpleNamespace:
    return SimpleNamespace(
        id=770,
        owner_id=11,
        status=status,
        error_message=None,
        extraction_warnings=[],
        billing_mode=None,
        billing_hold_id=None,
    )


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=99, role=UserRole.ADMIN, username="admin")


async def _service_for(project: SimpleNamespace) -> tuple[ReviewService, _NoQueryDb]:
    db = _NoQueryDb()
    service = ReviewService(db)  # type: ignore[arg-type]

    async def _resolve_project(project_id, user, *, require_owner=False):
        assert project_id == project.id
        assert user.role == UserRole.ADMIN
        assert require_owner is False
        return project

    service._get_project_or_raise = _resolve_project  # type: ignore[method-assign]
    return service, db


@pytest.mark.asyncio
async def test_start_review_reviewing_returns_current_task_without_restarting() -> None:
    service, db = await _service_for(_project(ProjectStatus.REVIEWING))

    result = await service.start_review(770, _user())  # type: ignore[arg-type]

    assert result["project_id"] == 770
    assert result["status"] == "processing"
    assert result["already_started"] is True
    assert db.execute_calls == 0
    assert db.commits == 0


@pytest.mark.asyncio
async def test_start_review_completed_returns_terminal_status_without_restarting() -> None:
    service, db = await _service_for(_project(ProjectStatus.COMPLETED))

    result = await service.start_review(770, _user())  # type: ignore[arg-type]

    assert result["project_id"] == 770
    assert result["status"] == "completed"
    assert result["already_completed"] is True
    assert db.execute_calls == 0
    assert db.commits == 0


@pytest.mark.asyncio
async def test_start_review_failed_must_use_retry_entrypoint() -> None:
    service, db = await _service_for(_project(ProjectStatus.FAILED))

    with pytest.raises(ServiceError, match="失败状态"):
        await service.start_review(770, _user())  # type: ignore[arg-type]

    assert db.execute_calls == 0
    assert db.commits == 0
