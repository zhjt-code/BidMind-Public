"""串标项目加载走 core 权限，无权限仍返回 HTTP 403。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.models.user import UserRole
from app.services.collusion_status_service import get_project_or_404


class _ProjectDb:
    def __init__(self, project: SimpleNamespace | None) -> None:
        self.project = project

    async def execute(self, _stmt: Any) -> Any:
        project = self.project

        class _Result:
            def scalar_one_or_none(self) -> SimpleNamespace | None:
                return project

        return _Result()


def _user(user_id: int, role: UserRole) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, role=role)


@pytest.mark.asyncio
async def test_non_owner_user_still_gets_http_403() -> None:
    project = SimpleNamespace(id=3, owner_id=2)
    with pytest.raises(HTTPException) as exc:
        await get_project_or_404(3, _ProjectDb(project), _user(9, UserRole.USER))

    assert exc.value.status_code == 403
    assert exc.value.detail == "无权限访问此项目"


@pytest.mark.asyncio
async def test_other_non_owner_require_owner_admin_still_gets_http_403() -> None:
    """同形态第二例：管理员在 require_owner 下也不能绕过所有者校验。"""
    project = SimpleNamespace(id=8, owner_id=2)
    with pytest.raises(HTTPException) as exc:
        await get_project_or_404(
            8,
            _ProjectDb(project),
            _user(1, UserRole.ADMIN),
            require_owner=True,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_and_admin_still_load_project() -> None:
    owned = SimpleNamespace(id=3, owner_id=2)
    loaded = await get_project_or_404(3, _ProjectDb(owned), _user(2, UserRole.USER))
    assert loaded is owned

    foreign = SimpleNamespace(id=4, owner_id=99)
    loaded_admin = await get_project_or_404(
        4, _ProjectDb(foreign), _user(1, UserRole.ADMIN)
    )
    assert loaded_admin is foreign
