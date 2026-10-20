"""审计日志管理路由

提供管理员查看、统计、批量删除审计日志的 API。
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import PaginationParams, get_pagination, require_admin
from app.core.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.services.audit_service import AuditService


class AuditLogBatchFilters(BaseModel):
    """批量操作时透传的筛选条件"""
    category: Optional[str] = None
    action: Optional[str] = None
    resource_type: Optional[str] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    keyword: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class AuditBatchDeleteRequest(BaseModel):
    """批量删除请求体"""
    log_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=1000)
    select_all: bool = False
    exclude_ids: List[int] = Field(default_factory=list, max_length=1000)
    filters: AuditLogBatchFilters = Field(default_factory=AuditLogBatchFilters)

    @model_validator(mode="after")
    def validate_selection(self) -> "AuditBatchDeleteRequest":
        if self.select_all:
            return self
        if not self.log_ids:
            raise ValueError("log_ids 不能为空，或使用 select_all=true")
        return self


router = APIRouter(prefix="/admin/audit-logs", tags=["审计日志"])


@router.get("", response_model=ApiResponse)
async def list_audit_logs(
    pagination: PaginationParams = Depends(get_pagination),
    category: Optional[str] = Query(default=None, description="业务分类: bid_review/collusion_check/bid_writing/system"),
    action: Optional[str] = Query(default=None, description="操作类型筛选"),
    resource_type: Optional[str] = Query(default=None, description="资源类型筛选"),
    user_id: Optional[int] = Query(default=None, description="用户ID筛选"),
    username: Optional[str] = Query(default=None, description="用户名模糊搜索"),
    keyword: Optional[str] = Query(default=None, description="详情关键词搜索"),
    start_date: Optional[datetime] = Query(default=None, description="开始时间"),
    end_date: Optional[datetime] = Query(default=None, description="结束时间"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """查询审计日志列表（仅管理员）

    支持按业务分类、操作类型、资源类型、用户ID、用户名、关键词、时间范围筛选。
    """
    service = AuditService(db)
    data = await service.list_logs(
        page=pagination.page,
        page_size=pagination.page_size,
        category=category,
        action=action,
        resource_type=resource_type,
        user_id=user_id,
        username=username,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
    )

    items = [
        {
            "id": log.id,
            "user_id": log.user_id,
            "username": log.username,
            "category": log.category,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in data["items"]
    ]

    return ApiResponse(
        code=200,
        message="success",
        data={
            "items": items,
            "total": data["total"],
            "page": data["page"],
            "page_size": data["page_size"],
            "total_pages": data["total_pages"],
        },
    )


@router.get("/stats", response_model=ApiResponse)
async def get_audit_stats(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """获取审计日志统计数据（仅管理员）"""
    service = AuditService(db)
    stats = await service.get_stats()
    return ApiResponse(code=200, message="success", data=stats)


@router.post("/batch-delete", response_model=ApiResponse)
async def batch_delete_audit_logs(
    body: AuditBatchDeleteRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """批量删除审计日志（仅管理员）"""
    service = AuditService(db)
    result = await service.batch_delete(
        log_ids=body.log_ids,
        select_all=body.select_all,
        exclude_ids=body.exclude_ids,
        **body.filters.model_dump(exclude_none=True),
    )
    await db.commit()
    return ApiResponse(code=200, message="批量删除完成", data=result)
