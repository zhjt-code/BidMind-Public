"""Prompt 版本管理路由

提供管理员管理 Prompt 模板版本的 API。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.api.dependencies import require_admin, get_pagination, handle_service_error, PaginationParams
from app.services.prompt_service import PromptService
from app.core.exceptions import ServiceError

router = APIRouter(prefix="/admin/prompts", tags=["Prompt管理"])


class PromptCreateRequest(BaseModel):
    purpose: str = Field(..., description="用途: tender_extract/bid_extract/review 等")
    content: str = Field(..., min_length=10, description="Prompt 模板文本")
    name: Optional[str] = Field(default=None, description="版本名称")
    activate: bool = Field(default=False, description="创建后立即激活")


def _serialize(pv) -> dict:
    return {
        "id": pv.id,
        "purpose": pv.purpose,
        "version": pv.version,
        "name": pv.name,
        "content": pv.content,
        "is_active": pv.is_active,
        "created_at": pv.created_at.isoformat() if pv.created_at else None,
        "updated_at": pv.updated_at.isoformat() if pv.updated_at else None,
    }


@router.get("", response_model=ApiResponse)
async def list_prompt_versions(
    pagination: PaginationParams = Depends(get_pagination),
    purpose: Optional[str] = Query(default=None, description="按用途筛选"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """查询 Prompt 版本列表（仅管理员）"""
    service = PromptService(db)
    data = await service.list_versions(
        purpose=purpose,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return ApiResponse(
        code=200,
        message="success",
        data={
            "items": [_serialize(pv) for pv in data["items"]],
            "total": data["total"],
            "page": data["page"],
            "page_size": data["page_size"],
            "total_pages": data["total_pages"],
        },
    )


@router.post("", response_model=ApiResponse)
async def create_prompt_version(
    body: PromptCreateRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """创建新 Prompt 版本（仅管理员）"""
    try:
        service = PromptService(db)
        pv = await service.create(
            purpose=body.purpose,
            content=body.content,
            name=body.name,
            activate=body.activate,
        )
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(code=201, message="创建成功", data=_serialize(pv))


@router.post("/{version_id}/activate", response_model=ApiResponse)
async def activate_prompt_version(
    version_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """激活指定 Prompt 版本（仅管理员）"""
    try:
        service = PromptService(db)
        pv = await service.activate(version_id)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(code=200, message="激活成功", data=_serialize(pv))


@router.delete("/{version_id}", response_model=ApiResponse)
async def delete_prompt_version(
    version_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """删除 Prompt 版本（仅管理员，不可删除激活版本）"""
    try:
        service = PromptService(db)
        await service.delete(version_id)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(code=200, message="删除成功", data={"deleted_id": version_id})
