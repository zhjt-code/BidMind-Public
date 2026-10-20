"""招标要求管理路由

提供招标要求的查询、更新、确认、提取触发等 API。
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.schemas.requirement import (
    RequirementResponse,
    RequirementListResponse,
    RequirementCreate,
    ManualScoringItemCreate,
    RequirementUpdate,
    RequirementConfirmRequest,
)
from app.schemas.common import ApiResponse
from app.api.dependencies import get_current_user, handle_service_error, project_log_context
from app.services.manual_scoring_item_service import ManualScoringItemService
from app.services.requirement_service import RequirementService
from app.services.requirement_reextract_service import RequirementReextractService
from app.core.exceptions import ServiceError

router = APIRouter(
    prefix="/projects/{project_id}/requirements",
    dependencies=[Depends(project_log_context)],
)


@router.get("", response_model=RequirementListResponse)
async def list_requirements(
    project_id: int,
    confirmed: Optional[bool] = Query(default=None, description="是否已确认"),
    category: Optional[str] = Query(default=None, description="分类筛选"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> RequirementListResponse:
    """获取项目的招标要求列表"""
    try:
        service = RequirementService(db)
        data = await service.list_requirements(
            project_id, current_user, confirmed=confirmed, category=category,
        )
    except ServiceError as e:
        handle_service_error(e)

    return RequirementListResponse(
        total=data["total"],
        confirmed_count=data["confirmed_count"],
        pending_count=data["pending_count"],
        items=[RequirementResponse.model_validate(r) for r in data["items"]],
        scoring_totals_report=data.get("scoring_totals_report"),
        scoring_ledger_report=data.get("scoring_ledger_report"),
        scoring_verdict=data.get("scoring_verdict"),
    )


@router.post("", response_model=ApiResponse[RequirementResponse], status_code=status.HTTP_201_CREATED)
async def create_requirement(
    project_id: int,
    data: RequirementCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[RequirementResponse]:
    """手动添加招标要求"""
    try:
        service = RequirementService(db)
        requirement = await service.create(project_id, data, current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=201,
        message="招标要求添加成功",
        data=RequirementResponse.model_validate(requirement),
    )


@router.post("/scoring-items", response_model=ApiResponse[RequirementResponse], status_code=status.HTTP_201_CREATED)
async def create_manual_scoring_item(
    project_id: int,
    data: ManualScoringItemCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[RequirementResponse]:
    """手动补录具体评分项"""
    try:
        service = ManualScoringItemService(db)
        requirement = await service.create_manual_scoring_item(project_id, data, current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=201,
        message="评分项添加成功",
        data=RequirementResponse.model_validate(requirement),
    )


@router.get("/{requirement_id}", response_model=ApiResponse[RequirementResponse])
async def get_requirement(
    project_id: int,
    requirement_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[RequirementResponse]:
    """获取单条招标要求详情"""
    try:
        service = RequirementService(db)
        requirement = await service.get(project_id, requirement_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="success",
        data=RequirementResponse.model_validate(requirement),
    )


@router.put("/{requirement_id}", response_model=ApiResponse[RequirementResponse])
async def update_requirement(
    project_id: int,
    requirement_id: int,
    update_data: RequirementUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[RequirementResponse]:
    """更新招标要求（人工确认时使用）"""
    try:
        service = RequirementService(db)
        requirement = await service.update(
            project_id, requirement_id, update_data, current_user,
        )
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="招标要求更新成功",
        data=RequirementResponse.model_validate(requirement),
    )


@router.post("/confirm", response_model=ApiResponse)
async def confirm_requirements(
    project_id: int,
    confirm_data: RequirementConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """批量确认招标要求"""
    try:
        service = RequirementService(db)
        count = await service.confirm_batch(
            project_id, confirm_data.requirement_ids, current_user,
        )
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message=f"成功确认 {count} 条招标要求",
        data={"confirmed_count": count},
    )


@router.post("/confirm-all", response_model=ApiResponse)
async def confirm_all_requirements(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """确认所有招标要求"""
    try:
        service = RequirementService(db)
        count = await service.confirm_all(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message=f"成功确认 {count} 条招标要求",
        data={"confirmed_count": count},
    )


@router.post("/reextract", response_model=ApiResponse)
async def reextract_requirements(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """清空旧确认清单，并从已解析文档重新提取招标要求"""
    try:
        service = RequirementReextractService(db)
        result = await service.reextract_from_parsed_documents(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="旧确认清单已清空，重新提取任务已启动",
        data=result,
    )


@router.delete("/{requirement_id}", response_model=ApiResponse)
async def delete_requirement(
    project_id: int,
    requirement_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """删除单条招标要求"""
    try:
        service = RequirementService(db)
        await service.delete(project_id, requirement_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="招标要求删除成功",
        data={"deleted_id": requirement_id},
    )


@router.post("/extract", response_model=ApiResponse)
async def extract_requirements(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """从招标文件中提取要求（后台任务）"""
    try:
        service = RequirementService(db)
        result = await service.extract(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    msg = "招标要求提取任务已启动"
    if result.get("skipped"):
        if result["status"] == "completed":
            msg = f"已存在 {result['count']} 条招标要求"
        else:
            msg = "招标要求提取正在进行中，请稍候"

    return ApiResponse(code=200, message=msg, data=result)


@router.get("/extract/status", response_model=ApiResponse)
async def get_extract_status(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """获取招标要求提取进度"""
    try:
        service = RequirementService(db)
        data = await service.get_extract_status(project_id, current_user)
    except ServiceError as e:
        handle_service_error(e)

    status_msg = {
        "completed": "提取完成",
        "failed": "提取失败",
        "idle": "任务未启动",
        "running": "提取中",
    }
    return ApiResponse(
        code=200,
        message=status_msg.get(data["status"], "提取中"),
        data=data,
    )
