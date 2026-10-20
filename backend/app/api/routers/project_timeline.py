"""项目级日志时间线管理路由 (阶段三).

提供管理员按项目维度查看错误日志 / 任务观测 / 里程碑 / 审计日志融合视图的 API。

设计:
- 仅管理员可见 (require_admin), 数据可能含跨用户敏感信息
- 与 /admin/error-logs 拆分独立 router, 职责清晰
- 入口供 ErrorLog 详情页/三大业务 Result 页跳转使用
"""

from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.api.dependencies import require_admin, get_pagination, PaginationParams
from app.services.project_timeline_service import get_project_timeline


router = APIRouter(prefix="/admin/projects", tags=["项目时间线"])


@router.get("/{project_id}/timeline", response_model=ApiResponse)
async def get_timeline(
    project_id: int,
    pagination: PaginationParams = Depends(get_pagination),
    since: Optional[datetime] = Query(default=None, description="开始时间(含)"),
    until: Optional[datetime] = Query(default=None, description="结束时间(含)"),
    levels: Optional[List[str]] = Query(default=None, description="错误级别筛选, 多选: WARNING/ERROR/CRITICAL"),
    task_type: Optional[str] = Query(default=None, description="任务类型筛选"),
    keyword: Optional[str] = Query(default=None, description="关键词模糊匹配 message/summary/detail"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """按项目维度查询融合时间线(仅管理员)。

    数据源:
    - system_error_logs: 错误/警告主线
    - task_observations: 任务级开始/阶段/结束事件
    - project_milestone_events: INFO 级业务里程碑
    - audit_logs: 用户操作 (resource_type='project' 关联)

    返回结构:
    - project: 项目基础信息 (id/name/status/owner_username)
    - summary: 错误数 / 警告数 / 任务运行数 / incident_id 数 / 总耗时
    - items: 按时间倒序的事件列表 (kind=error_log/task_observation/milestone/audit)
    """
    data = await get_project_timeline(
        db,
        project_id=project_id,
        since=since,
        until=until,
        levels=levels,
        task_type=task_type,
        keyword=keyword,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    if data["project"] is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    return ApiResponse(code=200, message="success", data=data)
