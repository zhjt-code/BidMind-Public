"""恢复重投前终态二次确认防御测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.models.project import ProjectStatus


def _db_with_status(status: ProjectStatus | None):
    result = Mock()
    result.one_or_none.return_value = (status, "terminal error") if status else None
    db = Mock()
    db.expire_all = Mock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_review_recheck_project_terminal_detects_completed() -> None:
    """审查恢复分支重投前应能识别 worker 已提交的完成态。"""
    from app.services.review_service import _recheck_project_terminal

    db = _db_with_status(ProjectStatus.COMPLETED)

    result = await _recheck_project_terminal(db, project_id=1)

    assert result == (ProjectStatus.COMPLETED, "terminal error")
    db.expire_all.assert_not_called()
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_recheck_project_terminal_ignores_running_state() -> None:
    """非终态仍允许原恢复逻辑继续判断。"""
    from app.services.review_service import _recheck_project_terminal

    db = _db_with_status(ProjectStatus.REVIEWING)

    result = await _recheck_project_terminal(db, project_id=1)

    assert result is None
    db.expire_all.assert_not_called()


@pytest.mark.asyncio
async def test_collusion_recheck_project_terminal_detects_failed() -> None:
    """串标恢复分支重投前应能识别 worker 已提交的失败终态。"""
    from app.api.routers.collusion import _recheck_project_terminal

    db = _db_with_status(ProjectStatus.FAILED)

    result = await _recheck_project_terminal(db, project_id=1)

    assert result == (ProjectStatus.FAILED, "terminal error")
    db.expire_all.assert_not_called()
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_collusion_recheck_project_terminal_ignores_running_state() -> None:
    """串标项目仍在 REVIEWING 时不短路恢复分支。"""
    from app.api.routers.collusion import _recheck_project_terminal

    db = _db_with_status(ProjectStatus.REVIEWING)

    result = await _recheck_project_terminal(db, project_id=1)

    assert result is None
    db.expire_all.assert_not_called()
