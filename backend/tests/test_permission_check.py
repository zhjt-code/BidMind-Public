"""项目权限判定单元测试（P1 级防御）。

防御目标（Task 4 后端权限边界清理）:
- 纯领域函数 `app.core.permissions.check_project_permission` 覆盖四象限:
  owner 通过 / admin 默认通过 / 非 owner 拒绝 / require_owner=True 时 admin 也被拒
- API 层薄包装 `app.api.dependencies.check_project_permission` 把
  `ForbiddenError` 翻译为 `HTTPException(403, "无权限访问此项目")`
- HTTP 文案与状态码不得漂移（路由调用方契约红线）

避免对 DB / FastAPI app 依赖，纯函数 + Mock User/Project 即可断言。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from fastapi import HTTPException, status  # noqa: E402

from app.api.dependencies import (
    check_project_permission as api_check_project_permission,  # noqa: E402
)
from app.core.exceptions import ForbiddenError  # noqa: E402
from app.core.permissions import (
    check_project_permission as core_check_project_permission,  # noqa: E402
)
from app.models.user import UserRole  # noqa: E402


def _make_user(user_id: int, role: UserRole) -> MagicMock:
    """构造最小 User mock（仅暴露 id + role）。"""
    user = MagicMock(spec=["id", "role"])
    user.id = user_id
    user.role = role
    return user


def _make_project(owner_id: int) -> MagicMock:
    """构造最小 Project mock（仅暴露 owner_id）。"""
    project = MagicMock(spec=["owner_id"])
    project.owner_id = owner_id
    return project


# ── 核心层四象限 ──────────────────────────────────────────────────


def test_core_owner_user_passes() -> None:
    """USER 角色 + 自己是 owner: 应通过（不抛异常）。"""
    user = _make_user(user_id=10, role=UserRole.USER)
    project = _make_project(owner_id=10)

    core_check_project_permission(project, user)
    core_check_project_permission(project, user, require_owner=True)


def test_core_admin_default_passes_for_non_owner() -> None:
    """ADMIN 角色 + 不是 owner + require_owner=False: 应通过。"""
    admin = _make_user(user_id=99, role=UserRole.ADMIN)
    project = _make_project(owner_id=10)

    core_check_project_permission(project, admin)


def test_core_non_owner_user_raises_forbidden() -> None:
    """USER 角色 + 不是 owner: 抛 ForbiddenError，文案固定。"""
    user = _make_user(user_id=20, role=UserRole.USER)
    project = _make_project(owner_id=10)

    with pytest.raises(ForbiddenError) as excinfo:
        core_check_project_permission(project, user)

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "无权限访问此项目"


def test_core_require_owner_blocks_admin_when_not_owner() -> None:
    """ADMIN 角色 + 不是 owner + require_owner=True: 应被拒（admin 不豁免 owner-only 操作）。"""
    admin = _make_user(user_id=99, role=UserRole.ADMIN)
    project = _make_project(owner_id=10)

    with pytest.raises(ForbiddenError) as excinfo:
        core_check_project_permission(project, admin, require_owner=True)

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "无权限访问此项目"


def test_core_admin_who_is_owner_passes_require_owner() -> None:
    """ADMIN 角色 + 同时是 owner + require_owner=True: 应通过（owner 检查优先于 admin 豁免）。"""
    admin_owner = _make_user(user_id=10, role=UserRole.ADMIN)
    project = _make_project(owner_id=10)

    core_check_project_permission(project, admin_owner, require_owner=True)


# ── API 薄包装：异常翻译契约 ───────────────────────────────────


def test_api_wrapper_translates_forbidden_to_http_exception() -> None:
    """API 层薄包装应把 ForbiddenError 翻译为 HTTPException(403) 同文案。"""
    user = _make_user(user_id=20, role=UserRole.USER)
    project = _make_project(owner_id=10)

    with pytest.raises(HTTPException) as excinfo:
        api_check_project_permission(project, user)

    assert excinfo.value.status_code == status.HTTP_403_FORBIDDEN
    assert excinfo.value.detail == "无权限访问此项目"


def test_api_wrapper_passes_through_on_allowed() -> None:
    """API 层薄包装在允许通过的情形下应静默返回（None）。"""
    user = _make_user(user_id=10, role=UserRole.USER)
    project = _make_project(owner_id=10)

    assert api_check_project_permission(project, user) is None


def test_api_wrapper_require_owner_blocks_admin() -> None:
    """API 层 require_owner=True 路径同样要把 ForbiddenError 翻译为 HTTPException。"""
    admin = _make_user(user_id=99, role=UserRole.ADMIN)
    project = _make_project(owner_id=10)

    with pytest.raises(HTTPException) as excinfo:
        api_check_project_permission(project, admin, require_owner=True)

    assert excinfo.value.status_code == status.HTTP_403_FORBIDDEN
    assert excinfo.value.detail == "无权限访问此项目"
