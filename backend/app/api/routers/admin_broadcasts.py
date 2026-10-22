"""广播管理路由（管理员专用）

提供广播的 CRUD：创建、列表、详情、更新、撤回（软删除）。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_pagination, require_admin
from app.core.database import get_db
from app.models.broadcast import Broadcast
from app.models.user import User
from app.schemas.broadcast import (
    BroadcastCreate,
    BroadcastCreatorInfo,
    BroadcastListResponse,
    BroadcastResponse,
    BroadcastUpdate,
)
from app.schemas.common import ApiResponse, PaginationParams
from app.services import broadcast_service

router = APIRouter(prefix="/admin/broadcasts", tags=["广播管理"])


def _to_response(b: Broadcast) -> BroadcastResponse:
    creator_info = None
    if b.creator:
        creator_info = BroadcastCreatorInfo(id=b.creator.id, username=b.creator.username)
    return BroadcastResponse(
        id=b.id,
        level=b.level.value if hasattr(b.level, "value") else b.level,
        title=b.title,
        content=b.content,
        ref_url=b.ref_url,
        show_banner=b.show_banner,
        dismissible=b.dismissible,
        starts_at=b.starts_at,
        ends_at=b.ends_at,
        created_by=b.created_by,
        creator=creator_info,
        deleted_at=b.deleted_at,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


@router.post("", response_model=ApiResponse[BroadcastResponse], status_code=201)
async def create_broadcast(
    data: BroadcastCreate,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BroadcastResponse]:
    """创建广播（管理员）"""
    try:
        broadcast = await broadcast_service.create_broadcast(
            db,
            level=data.level,
            title=data.title,
            content=data.content,
            ref_url=data.ref_url,
            show_banner=data.show_banner,
            dismissible=data.dismissible,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
            created_by=admin_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    await db.commit()
    await db.refresh(broadcast)

    logger.info(
        f"管理员 {admin_user.username} 创建广播 id={broadcast.id} level={broadcast.level.value}"
    )

    return ApiResponse(code=201, message="广播已创建", data=_to_response(broadcast))


@router.get("", response_model=ApiResponse[BroadcastListResponse])
async def list_broadcasts(
    pagination: PaginationParams = Depends(get_pagination),
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BroadcastListResponse]:
    """广播列表（管理员，含已撤回与已过期）"""
    items, total = await broadcast_service.list_broadcasts_admin(
        db,
        page=pagination.page,
        page_size=pagination.page_size,
        include_deleted=True,
    )
    return ApiResponse(
        code=200,
        message="获取成功",
        data=BroadcastListResponse(
            items=[_to_response(b) for b in items],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        ),
    )


@router.get("/{broadcast_id}", response_model=ApiResponse[BroadcastResponse])
async def get_broadcast(
    broadcast_id: int,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BroadcastResponse]:
    """广播详情（管理员）"""
    broadcast = await broadcast_service.get_broadcast_by_id(
        db, broadcast_id, include_deleted=True,
    )
    if not broadcast:
        raise HTTPException(status_code=404, detail="广播不存在")
    return ApiResponse(code=200, message="获取成功", data=_to_response(broadcast))


@router.patch("/{broadcast_id}", response_model=ApiResponse[BroadcastResponse])
async def update_broadcast(
    broadcast_id: int,
    data: BroadcastUpdate,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BroadcastResponse]:
    """更新广播（管理员）"""
    broadcast = await broadcast_service.get_broadcast_by_id(
        db, broadcast_id, include_deleted=True,
    )
    if not broadcast:
        raise HTTPException(status_code=404, detail="广播不存在")

    try:
        await broadcast_service.update_broadcast(
            db,
            broadcast,
            level=data.level,
            title=data.title,
            content=data.content,
            ref_url=data.ref_url,
            show_banner=data.show_banner,
            dismissible=data.dismissible,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()
    await db.refresh(broadcast)
    logger.info(f"管理员 {admin_user.username} 更新广播 id={broadcast_id}")
    return ApiResponse(code=200, message="更新成功", data=_to_response(broadcast))


@router.delete("/{broadcast_id}", response_model=ApiResponse[BroadcastResponse])
async def delete_broadcast(
    broadcast_id: int,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BroadcastResponse]:
    """撤回广播（管理员，软删除）"""
    broadcast = await broadcast_service.get_broadcast_by_id(
        db, broadcast_id, include_deleted=False,
    )
    if not broadcast:
        raise HTTPException(status_code=404, detail="广播不存在或已撤回")

    await broadcast_service.soft_delete_broadcast(db, broadcast)
    await db.commit()
    await db.refresh(broadcast)
    logger.info(f"管理员 {admin_user.username} 撤回广播 id={broadcast_id}")
    return ApiResponse(code=200, message="已撤回", data=_to_response(broadcast))
