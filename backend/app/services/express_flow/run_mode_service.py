"""
职责: 在上传尚未进入提取前设置项目 run_mode。
不做: 不推进流水线、不修改项目业务状态、不走通用 ProjectUpdate。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ServiceError
from app.core.permissions import check_project_permission
from app.models.project import Project, ProjectType
from app.models.user import User
from app.services.express_flow.run_mode import (
    RUN_MODE_EDITABLE_STATUSES,
    ProjectRunMode,
)


class RunModeService:
    """
    职责: 校验并写入项目 run_mode。
    不做: 不启动提取/审查/撰写，不处理串标分析。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def set_run_mode(
        self,
        project_id: int,
        run_mode: ProjectRunMode,
        user: User,
    ) -> Project:
        result = await self.db.execute(
            select(Project)
            .options(selectinload(Project.owner))
            .where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise NotFoundError(f"项目不存在: id={project_id}")

        check_project_permission(project, user)

        if project.project_type == ProjectType.COLLUSION_CHECK:
            raise ServiceError("串标审查为固定一步流程，无需设置运行模式")

        if project.status not in RUN_MODE_EDITABLE_STATUSES:
            raise ServiceError("提取开始后不能再更改运行模式")

        project.run_mode = run_mode.value
        await self.db.flush()
        # flush 会过期 onupdate 列；全量 refresh 会过期关系。响应里这两类都必须显式加载。
        await self.db.refresh(project, ["updated_at", "owner"])
        return project
