"""系统错误日志管理路由

提供管理员查看、搜索、统计和标记系统错误日志的 API。
"""

from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.api.dependencies import require_admin, get_pagination, PaginationParams
from app.services.error_log_service import ErrorLogService


class ErrorLogBatchFilters(BaseModel):
    """批量操作时透传的筛选条件"""
    category: Optional[str] = None
    level: Optional[str] = None
    source: Optional[str] = None
    module: Optional[str] = None
    task_type: Optional[str] = None
    project_id: Optional[int] = None
    user_id: Optional[int] = None
    resolved: Optional[bool] = None
    keyword: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    fingerprints: Optional[List[str]] = Field(default=None, description="按消息指纹批量选中")
    traffic_class: Optional[str] = Field(
        default=None,
        description="流量类别: production(默认) / admin_test / regression / unknown / all",
    )


class BatchLogRequest(BaseModel):
    log_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=1000)
    select_all: bool = False
    exclude_ids: List[int] = Field(default_factory=list, max_length=1000)
    filters: ErrorLogBatchFilters = Field(default_factory=ErrorLogBatchFilters)

    @model_validator(mode="after")
    def validate_selection(self) -> "BatchLogRequest":
        if self.select_all:
            return self
        if self.filters.fingerprints:
            return self
        if not self.log_ids:
            raise ValueError("log_ids 不能为空，或使用 select_all=true / fingerprints")
        return self

class CreateSuppressionRequest(BaseModel):
    """创建抑制规则的请求体"""
    reason: str = Field(default="", max_length=500, description="抑制原因")


class ToggleSuppressionRequest(BaseModel):
    """启用/禁用抑制规则"""
    enabled: bool


router = APIRouter(prefix="/admin/error-logs", tags=["错误日志"])


def _serialize_log_item(item: dict) -> dict:
    """统一序列化单条错误日志(含链路根因新字段), 供 list/chain/detail 复用。"""
    log = item["log"]
    return {
        "id": log.id,
        "category": log.category,
        "level": log.level,
        "message": log.message,
        "module": log.module,
        "function_name": log.function_name,
        "line_number": log.line_number,
        "request_id": log.request_id,
        "user_id": log.user_id,
        "username": item.get("username"),
        "project_id": log.project_id,
        "project_name": item.get("project_name"),
        "task_type": log.task_type,
        "stage": log.stage,
        "source": log.source,
        "traffic_class": log.traffic_class,
        "occurrence_count": log.occurrence_count,
        "resolved": log.resolved,
        "resolved_by": log.resolved_by,
        "resolved_at": log.resolved_at.isoformat() if log.resolved_at else None,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        # 错误链路与根因定位（阶段一）
        "incident_id": log.incident_id,
        "chain_seq": log.chain_seq,
        "is_root_cause": log.is_root_cause,
        "exception_type": log.exception_type,
        "origin_module": log.origin_module,
        "origin_function": log.origin_function,
        "origin_line": log.origin_line,
    }


@router.get("", response_model=ApiResponse)
async def list_error_logs(
    pagination: PaginationParams = Depends(get_pagination),
    category: Optional[str] = Query(default=None, description="业务分类: bid_review/collusion_check/bid_writing/system"),
    level: Optional[str] = Query(default=None, description="日志级别: WARNING/ERROR/CRITICAL"),
    source: Optional[str] = Query(default=None, description="来源: backend/worker"),
    module: Optional[str] = Query(default=None, description="模块名(模糊匹配)"),
    task_type: Optional[str] = Query(default=None, description="任务类型"),
    project_id: Optional[int] = Query(default=None, description="项目ID"),
    user_id: Optional[int] = Query(default=None, description="用户ID"),
    resolved: Optional[bool] = Query(default=None, description="是否已处理"),
    keyword: Optional[str] = Query(default=None, description="消息关键词"),
    start_date: Optional[datetime] = Query(default=None, description="开始时间"),
    end_date: Optional[datetime] = Query(default=None, description="结束时间"),
    fingerprint: Optional[str] = Query(default=None, description="消息指纹(用于展开分组)"),
    incident_id: Optional[str] = Query(default=None, description="错误链路ID(用于过滤同一次失败的全部日志)"),
    traffic_class: Optional[str] = Query(
        default=None,
        description="流量类别: 不传=仅 production; admin_test/regression/unknown; all=不过滤",
    ),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """查询系统错误日志列表(仅管理员)"""
    service = ErrorLogService(db)
    data = await service.list_logs(
        page=pagination.page,
        page_size=pagination.page_size,
        category=category,
        level=level,
        source=source,
        module=module,
        task_type=task_type,
        project_id=project_id,
        user_id=user_id,
        resolved=resolved,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
        fingerprint=fingerprint,
        incident_id=incident_id,
        traffic_class=traffic_class,
    )

    items = [_serialize_log_item(item) for item in data["items"]]

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


@router.get("/grouped", response_model=ApiResponse)
async def list_grouped_error_logs(
    pagination: PaginationParams = Depends(get_pagination),
    sort_by: str = Query(default="latest_at", description="排序方式: latest_at(按时间) / count(按数量)"),
    category: Optional[str] = Query(default=None, description="业务分类"),
    level: Optional[str] = Query(default=None, description="日志级别"),
    source: Optional[str] = Query(default=None, description="来源"),
    module: Optional[str] = Query(default=None, description="模块名(模糊匹配)"),
    task_type: Optional[str] = Query(default=None, description="任务类型"),
    project_id: Optional[int] = Query(default=None, description="项目ID"),
    user_id: Optional[int] = Query(default=None, description="用户ID"),
    resolved: Optional[bool] = Query(default=None, description="是否已处理"),
    keyword: Optional[str] = Query(default=None, description="消息关键词"),
    start_date: Optional[datetime] = Query(default=None, description="开始时间"),
    end_date: Optional[datetime] = Query(default=None, description="结束时间"),
    traffic_class: Optional[str] = Query(
        default=None,
        description="流量类别: 不传=仅 production; admin_test/regression/unknown; all=不过滤",
    ),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """按消息指纹分组查询错误日志(仅管理员)"""
    if sort_by not in ("latest_at", "count"):
        sort_by = "latest_at"
    service = ErrorLogService(db)
    data = await service.list_grouped_logs(
        page=pagination.page,
        page_size=pagination.page_size,
        sort_by=sort_by,
        category=category,
        level=level,
        source=source,
        module=module,
        task_type=task_type,
        project_id=project_id,
        user_id=user_id,
        resolved=resolved,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
        traffic_class=traffic_class,
    )
    return ApiResponse(code=200, message="success", data=data)


@router.get("/stats", response_model=ApiResponse)
async def get_error_stats(
    hours: int = Query(default=24, ge=1, le=168, description="统计时间范围(小时)"),
    traffic_class: Optional[str] = Query(
        default=None,
        description="流量类别: 不传=仅 production; admin_test/regression/unknown; all=不过滤",
    ),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """获取错误统计数据(仅管理员)"""
    service = ErrorLogService(db)
    stats = await service.get_stats(hours=hours, traffic_class=traffic_class)
    return ApiResponse(code=200, message="success", data=stats)


# ── 抑制规则端点（必须在 /{log_id} 之前注册）──────────────────


@router.get("/suppression-rules", response_model=ApiResponse)
async def list_suppression_rules(
    pagination: PaginationParams = Depends(get_pagination),
    enabled: Optional[bool] = Query(default=None, description="按启用状态筛选"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """分页列出日志抑制规则(仅管理员)"""
    service = ErrorLogService(db)
    data = await service.list_suppression_rules(
        page=pagination.page,
        page_size=pagination.page_size,
        enabled=enabled,
    )
    items = [
        {
            "id": rule.id,
            "module": rule.module,
            "function_name": rule.function_name,
            "message_prefix": rule.message_prefix,
            "level": rule.level,
            "category": rule.category,
            "enabled": rule.enabled,
            "reason": rule.reason,
            "created_by": rule.created_by,
            "source_log_id": rule.source_log_id,
            "suppressed_count": rule.suppressed_count,
            "last_suppressed_at": rule.last_suppressed_at.isoformat() if rule.last_suppressed_at else None,
            "created_at": rule.created_at.isoformat() if rule.created_at else None,
        }
        for rule in data["items"]
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


@router.patch("/suppression-rules/{rule_id}", response_model=ApiResponse)
async def toggle_suppression_rule(
    rule_id: int,
    body: ToggleSuppressionRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """启用/禁用抑制规则(仅管理员)"""
    service = ErrorLogService(db)
    rule = await service.toggle_suppression_rule(rule_id, enabled=body.enabled)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="规则不存在")
    await db.commit()
    action = "已启用" if body.enabled else "已禁用"
    return ApiResponse(code=200, message=f"抑制规则{action}")


@router.delete("/suppression-rules/{rule_id}", response_model=ApiResponse)
async def delete_suppression_rule(
    rule_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """删除抑制规则(仅管理员)"""
    service = ErrorLogService(db)
    ok = await service.delete_suppression_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="规则不存在")
    await db.commit()
    return ApiResponse(code=200, message="抑制规则已删除")


@router.post("/recompute-fingerprints", response_model=ApiResponse)
async def recompute_fingerprints(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """重新计算所有日志的消息指纹(归一化规则更新后调用, 仅管理员)"""
    service = ErrorLogService(db)
    result = await service.recompute_fingerprints()
    await db.commit()
    return ApiResponse(code=200, message=f"已更新 {result['updated']} 条日志的指纹", data=result)


# ── 错误链路端点（必须在 /{log_id} 之前注册）──────────────────


@router.get("/chain/{incident_id}", response_model=ApiResponse)
async def get_error_chain(
    incident_id: str,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """按错误链路ID(incident_id)拉取该次失败的全部日志(仅管理员)。

    返回按 chain_seq 升序排列的日志列表, 第一条通常为根因(is_root_cause=True)。
    用于错误日志详情页的链路抽屉展示。
    """
    service = ErrorLogService(db)
    rows = await service.list_chain(incident_id)
    items = [_serialize_log_item(item) for item in rows]
    return ApiResponse(
        code=200,
        message="success",
        data={
            "incident_id": incident_id,
            "items": items,
            "total": len(items),
        },
    )


# ── 单条日志端点 ────────────────────────────────────────────────


@router.get("/{log_id}", response_model=ApiResponse)
async def get_error_detail(
    log_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """获取错误日志详情(含完整堆栈,仅管理员)"""
    service = ErrorLogService(db)
    detail = await service.get_detail(log_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="日志不存在")

    base = _serialize_log_item(detail)
    base["traceback"] = detail["log"].traceback
    return ApiResponse(code=200, message="success", data=base)


@router.patch("/{log_id}/resolve", response_model=ApiResponse)
async def resolve_error_log(
    log_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """标记错误日志为已处理(仅管理员)"""
    service = ErrorLogService(db)
    log = await service.resolve_log(log_id, resolved_by=admin.username)
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="日志不存在")
    await db.commit()
    return ApiResponse(code=200, message="已标记为已处理")


@router.post("/{log_id}/suppress", response_model=ApiResponse)
async def suppress_error_log(
    log_id: int,
    body: CreateSuppressionRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """从指定日志创建抑制规则，后续同类日志不再入库(仅管理员)"""
    service = ErrorLogService(db)
    rule = await service.create_suppression_rule(
        log_id,
        created_by=admin.username,
        reason=body.reason,
    )
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="日志不存在或已存在相同的抑制规则",
        )
    await db.commit()
    return ApiResponse(
        code=200,
        message="抑制规则已创建",
        data={
            "id": rule.id,
            "module": rule.module,
            "message_prefix": rule.message_prefix,
        },
    )


@router.post("/batch-delete", response_model=ApiResponse)
async def batch_delete_error_logs(
    body: BatchLogRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """批量删除错误日志(仅管理员)"""
    service = ErrorLogService(db)
    result = await service.batch_delete(
        log_ids=body.log_ids,
        select_all=body.select_all,
        exclude_ids=body.exclude_ids,
        **body.filters.model_dump(exclude_none=True),
    )
    await db.commit()
    return ApiResponse(code=200, message="批量删除完成", data=result)


@router.post("/batch-resolve", response_model=ApiResponse)
async def batch_resolve_error_logs(
    body: BatchLogRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """批量标记错误日志为已处理(仅管理员)"""
    service = ErrorLogService(db)
    result = await service.batch_resolve(
        log_ids=body.log_ids,
        select_all=body.select_all,
        exclude_ids=body.exclude_ids,
        resolved_by=admin.username,
        **body.filters.model_dump(exclude_none=True),
    )
    await db.commit()
    return ApiResponse(code=200, message="批量标记完成", data=result)
