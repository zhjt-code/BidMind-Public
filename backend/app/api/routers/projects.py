"""项目管理路由

提供项目 CRUD 操作的 RESTful API。
支持用户权限控制：普通用户只能访问自己的项目，管理员可访问所有项目。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    PaginationParams,
    get_current_user,
    get_pagination,
    handle_service_error,
    project_log_context,
)
from app.core.database import get_db
from app.core.exceptions import ServiceError
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.project import (
    BatchDeleteRequest,
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectRunModeUpdate,
    ProjectUpdate,
)
from app.schemas.project_dashboard import ProjectDashboardResponse
from app.services.express_flow.run_mode import ProjectRunMode
from app.services.express_flow.run_mode_service import RunModeService
from app.services.project_dashboard_service import ProjectDashboardService
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", dependencies=[Depends(project_log_context)])


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    pagination: PaginationParams = Depends(get_pagination),
    status_filter: Optional[str] = Query(default=None, description="状态筛选"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ProjectListResponse:
    """获取项目列表

    支持分页和状态筛选。
    - 普通用户：只返回自己创建的项目
    - 管理员：返回所有项目
    """
    try:
        service = ProjectService(db)
        data = await service.list_projects(
            user=current_user,
            page=pagination.page,
            page_size=pagination.page_size,
            status_filter=status_filter,
        )
    except ServiceError as e:
        handle_service_error(e)

    return ProjectListResponse(
        items=[ProjectResponse.model_validate(p) for p in data["items"]],
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
        total_pages=data["total_pages"],
    )


@router.post("", response_model=ApiResponse[ProjectResponse], status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[ProjectResponse]:
    """创建项目

    自动关联当前用户为项目所有者。
    """
    try:
        service = ProjectService(db)
        project = await service.create(project_data, owner=current_user)
    except ServiceError as e:
        handle_service_error(e)

    logger.info(f"创建项目: id={project.id}, name={project.name}, owner={current_user.username}")
    return ApiResponse(
        code=201,
        message="项目创建成功",
        data=ProjectResponse.model_validate(project),
    )


@router.get("/dashboard", response_model=ApiResponse[ProjectDashboardResponse])
async def get_project_dashboard(
    window_days: int = Query(default=30, ge=7, le=90, description="新建与完成趋势窗口天数"),
    health_window_days: int = Query(default=30, ge=7, le=90, description="运行健康窗口天数"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProjectDashboardResponse]:
    """获取项目中心控制台聚合数据。"""
    service = ProjectDashboardService(db)
    data = await service.get_dashboard(
        current_user,
        window_days=window_days,
        health_window_days=health_window_days,
    )
    return ApiResponse(code=200, message="success", data=data)


@router.get("/{project_id}", response_model=ApiResponse[ProjectResponse])
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[ProjectResponse]:
    """获取项目详情

    需要项目所有者或管理员权限。
    """
    try:
        service = ProjectService(db)
        project = await service.get(project_id, user=current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="success",
        data=ProjectResponse.model_validate(project),
    )


@router.put("/{project_id}", response_model=ApiResponse[ProjectResponse])
async def update_project(
    project_id: int,
    project_data: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[ProjectResponse]:
    """更新项目

    只更新提供的字段。需要项目所有者或管理员权限。
    """
    try:
        service = ProjectService(db)
        project = await service.update(project_id, project_data, user=current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="项目更新成功",
        data=ProjectResponse.model_validate(project),
    )


@router.post("/batch-delete", response_model=ApiResponse)
async def batch_delete_projects(
    body: BatchDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """批量删除项目

    接收项目 ID 列表，逐一校验权限后删除。
    部分失败时返回成功和失败的明细。
    """
    try:
        service = ProjectService(db)
        result = await service.batch_delete(body.project_ids, user=current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(code=200, message="批量删除完成", data=result)


@router.patch("/{project_id}/run-mode", response_model=ApiResponse[ProjectResponse])
async def set_project_run_mode(
    project_id: int,
    body: ProjectRunModeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProjectResponse]:
    """在上传尚未进入提取前设置人工确认 / 一步到位。"""
    try:
        project = await RunModeService(db).set_run_mode(
            project_id,
            ProjectRunMode(body.run_mode),
            current_user,
        )
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="运行模式已更新",
        data=ProjectResponse.model_validate(project),
    )


@router.delete("/{project_id}", response_model=ApiResponse)
async def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """删除项目

    删除项目及其关联的所有数据（文档、分块、要求、响应、结果）。
    同时终止该项目相关的所有后台任务。需要项目所有者或管理员权限。
    """
    try:
        service = ProjectService(db)
        result = await service.delete(project_id, user=current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(code=200, message="项目删除成功", data=result)
