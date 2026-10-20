"""跨 Service 共享的项目访问校验

职责: 提供项目存在性查询与访问权限校验的共享实现(异步,含 DB 查询)
不做: 不做项目类型业务校验(BID_WRITING 等留在各 Service 本地)、不翻译
      HTTP 异常、不承担项目 CRUD

说明: 本函数由 review_service / requirement_service /
manual_scoring_item_service 三处逐字相同的 _get_project_or_raise 收敛而来
(批次二 S-07)。writing_service 与 retry_service 的同名方法因异常类型、
文案与项目类型校验不同(承载 HTTP 状态映射)保留本地实现。
异常文案是历史契约的一部分,禁止改动。
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.project import Project
from app.models.user import User, UserRole


async def get_project_or_raise(
    db: AsyncSession,
    project_id: int,
    user: Optional[User] = None,
    *,
    require_owner: bool = False,
) -> Project:
    """获取项目，不存在则抛 NotFoundError；提供 user 时校验权限

    Args:
        db: 异步数据库会话
        project_id: 项目 ID
        user: 当前用户；为 None 时跳过权限校验
        require_owner: 为 True 时仅项目所有者可操作（管理员也不能操作他人项目）

    Raises:
        NotFoundError: 项目不存在
        ForbiddenError: 无权限访问
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise NotFoundError(f"项目不存在: id={project_id}")
    if user is not None:
        if require_owner:
            if project.owner_id != user.id:
                raise ForbiddenError("仅项目所有者可执行此操作")
        elif user.role != UserRole.ADMIN and project.owner_id != user.id:
            raise ForbiddenError("无权限访问此项目")
    return project
