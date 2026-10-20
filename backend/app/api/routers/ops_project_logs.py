"""运维监控 - 项目维度日志追踪路由 (阶段二)。

仅管理员可见; 在 /admin/ops/{flow} 三业务链路下提供按项目聚合的日志概览,
点开进 /admin/projects/{id}/timeline 看完整时间线。
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.core.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.services.ops_metrics.project_logs import (
    batch_delete_project_logs,
    batch_resolve_project_errors,
    get_flow_project_logs,
    is_valid_flow,
    resolve_batch_target_ids,
)


router = APIRouter(prefix="/admin/ops", tags=["运维监控-项目日志"])


@router.get("/project-logs/{flow}", response_model=ApiResponse)
async def list_flow_project_logs(
    flow: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: Optional[str] = Query(default=None, description="按项目名/属主用户名搜索"),
    days: int = Query(default=30, ge=1, le=365, description="时间窗口(天)"),
    only_unresolved: bool = Query(default=False, description="仅看有未处理错误的项目"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """按业务链路返回项目维度日志概览 (仅管理员)。

    items 含在世与已删项目, 按最近活动倒序; 已删项目用删除前回填的快照名展示。
    """
    if not is_valid_flow(flow):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的 flow, 仅支持 tender/collusion/writer",
        )
    since = datetime.now(timezone.utc) - timedelta(days=days)
    data = await get_flow_project_logs(
        db,
        flow=flow,
        page=page,
        page_size=page_size,
        keyword=keyword,
        since=since,
        only_unresolved=only_unresolved,
    )
    return ApiResponse(code=200, message="success", data=data)


class ProjectLogBatchRequest(BaseModel):
    """项目维度批处理请求 (对齐 error-logs 的 select_all + 排除语义)。"""

    project_ids: Optional[List[int]] = Field(default=None, max_length=1000)
    select_all: bool = False
    exclude_project_ids: List[int] = Field(default_factory=list, max_length=1000)
    keyword: Optional[str] = None
    days: int = Field(default=30, ge=1, le=365)
    only_unresolved: bool = False

    @model_validator(mode="after")
    def _validate_selection(self) -> "ProjectLogBatchRequest":
        if not self.select_all and not self.project_ids:
            raise ValueError("project_ids 不能为空, 或使用 select_all=true")
        return self


@router.post("/project-logs/{flow}/batch-resolve", response_model=ApiResponse)
async def batch_resolve_flow_project_logs(
    flow: str,
    body: ProjectLogBatchRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """批量把选中项目的未处理错误标记为已处理 (仅管理员)。"""
    if not is_valid_flow(flow):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的 flow, 仅支持 tender/collusion/writer",
        )
    since = datetime.now(timezone.utc) - timedelta(days=body.days)
    target_ids = await resolve_batch_target_ids(
        db,
        flow=flow,
        select_all=body.select_all,
        project_ids=body.project_ids,
        exclude_ids=body.exclude_project_ids,
        keyword=body.keyword,
        only_unresolved=body.only_unresolved,
        since=since,
    )
    result = await batch_resolve_project_errors(
        db, flow=flow, project_ids=target_ids, resolved_by=admin.username
    )
    await db.commit()
    return ApiResponse(code=200, message="批量标记完成", data=result)


@router.post("/project-logs/{flow}/batch-delete", response_model=ApiResponse)
async def batch_delete_flow_project_logs(
    flow: str,
    body: ProjectLogBatchRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """批量清理选中项目的错误日志 (仅管理员)。"""
    if not is_valid_flow(flow):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的 flow, 仅支持 tender/collusion/writer",
        )
    since = datetime.now(timezone.utc) - timedelta(days=body.days)
    target_ids = await resolve_batch_target_ids(
        db,
        flow=flow,
        select_all=body.select_all,
        project_ids=body.project_ids,
        exclude_ids=body.exclude_project_ids,
        keyword=body.keyword,
        only_unresolved=body.only_unresolved,
        since=since,
    )
    result = await batch_delete_project_logs(db, flow=flow, project_ids=target_ids)
    await db.commit()
    return ApiResponse(code=200, message="批量清理完成", data=result)
