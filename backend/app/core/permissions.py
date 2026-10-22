"""项目权限判定（纯领域规则）

提供与 FastAPI / DB 解耦的同步权限判定函数，供 Service 层正向 import。
API 层 `app.api.dependencies.check_project_permission` 是本模块的薄包装，
负责把 `ForbiddenError` 翻译为 `HTTPException`，以兼容路由层依赖注入语义。

设计要点：
- 零 FastAPI / 零 SQLAlchemy 依赖，可单测、可在任意层调用
- 违例抛 `ForbiddenError`（`ServiceError` 子类），由路由层统一通过
  `handle_service_error` 转 HTTP 403，HTTP 响应字节级与历史一致
- 权限语义保持不变：owner 通过 / admin 默认通过 / require_owner=True 时 admin 不豁免
"""

from app.core.exceptions import ForbiddenError
from app.models.project import Project
from app.models.user import User, UserRole


def check_project_permission(
    project: Project,
    current_user: User,
    require_owner: bool = False,
) -> None:
    """检查用户是否有权操作指定项目（同步，无 DB 操作）。

    Args:
        project: 目标项目对象
        current_user: 当前用户对象
        require_owner: 是否要求必须是所有者（管理员也不能放行）

    Raises:
        ForbiddenError: 无权限时抛出，文案为「无权限访问此项目」
    """
    if current_user.role == UserRole.ADMIN and not require_owner:
        return

    if project.owner_id != current_user.id:
        raise ForbiddenError("无权限访问此项目")
