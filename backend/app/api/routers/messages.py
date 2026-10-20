"""站内信路由

提供当前用户的消息列表、未读数、标记已读等功能。

广播能力（拉取式合并）：
- /unread_count：合并 messages 未读数 + broadcasts 未读数
- /：列表合并 messages 与广播（按时间倒序）
- /banners：顶部 Banner 候选
- /broadcasts/{id}/read：标记单条广播已读
- /broadcasts/{id}/dismiss：关闭广播 Banner（不再展示横幅，但仍在通知中心可见）
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_pagination
from app.core.database import get_db
from app.models.message import Message
from app.models.user import User
from app.schemas.broadcast import BannerItem, BannerListResponse
from app.schemas.common import ApiResponse, PaginationParams
from app.schemas.message import MessageListResponse, MessageResponse, UnreadCountResponse
from app.services import broadcast_service

router = APIRouter(prefix="/messages", tags=["站内信"])


# ============ 内部转换 ============

def _message_to_response(msg: Message) -> MessageResponse:
    return MessageResponse(
        id=msg.id,
        user_id=msg.user_id,
        type=msg.type.value if hasattr(msg.type, "value") else msg.type,
        title=msg.title,
        content=msg.content,
        is_read=msg.is_read,
        read_at=msg.read_at,
        ref_type=msg.ref_type,
        ref_id=msg.ref_id,
        created_at=msg.created_at,
        updated_at=msg.updated_at,
    )


def _broadcast_to_message_response(b, is_read: bool, user_id: int) -> MessageResponse:
    """将广播伪装为 MessageResponse 给通知中心列表使用。

    约定：
    - id 用负数，避免与 messages.id 主键冲突；前端按 id 区分时也要适配
    - type 固定为 "broadcast"
    - ref_type="broadcast"、ref_id=广播真实 id（前端用此调用 broadcasts 相关接口）
    """
    level_value = b.level.value if hasattr(b.level, "value") else b.level
    return MessageResponse(
        id=-int(b.id),
        user_id=user_id,
        type="broadcast",
        title=f"[{level_value.upper()}] {b.title}",
        content=b.content,
        is_read=is_read,
        read_at=None,
        ref_type="broadcast",
        ref_id=int(b.id),
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


# ============ 未读计数（合并） ============

@router.get("/unread_count", response_model=ApiResponse[UnreadCountResponse])
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UnreadCountResponse]:
    """获取当前用户未读消息数（合并 messages 与生效中的未读广播）。"""
    msg_result = await db.execute(
        select(func.count(Message.id))
        .where(Message.user_id == current_user.id, Message.is_read == False)  # noqa: E712
    )
    msg_unread = msg_result.scalar() or 0

    bc_unread = await broadcast_service.count_unread_broadcasts(db, current_user.id)

    return ApiResponse(
        code=200,
        message="获取成功",
        data=UnreadCountResponse(count=msg_unread + bc_unread),
    )


# ============ 列表（合并） ============

@router.get("", response_model=ApiResponse[MessageListResponse])
async def list_messages(
    pagination: PaginationParams = Depends(get_pagination),
    unread_only: bool = Query(False, description="仅显示未读"),
    source: Optional[str] = Query(
        None, pattern="^(message|broadcast)$",
        description="数据源筛选：message 仅原站内信；broadcast 仅广播；为空合并",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MessageListResponse]:
    """获取通知中心消息列表（合并站内信与广播）。

    实现策略：分别按各自规则查全部结果，Python 内合并、排序、再分页。
    适用前提：单用户的消息总量较小（数百级别），合并成本可接受。
    """
    # 1) 拉 messages
    msg_filters = [Message.user_id == current_user.id]
    if unread_only:
        msg_filters.append(Message.is_read == False)  # noqa: E712

    if source != "broadcast":
        msg_stmt = (
            select(Message).where(*msg_filters).order_by(Message.created_at.desc())
        )
        msgs = (await db.execute(msg_stmt)).scalars().all()
    else:
        msgs = []

    # 2) 拉广播（生效中且未撤回）
    if source != "message":
        bc_pairs, _ = await broadcast_service.list_user_broadcasts_in_inbox(
            db,
            current_user.id,
            unread_only=unread_only,
            page=1,
            page_size=10_000,
        )
    else:
        bc_pairs = []

    # 3) 合并 + 排序
    merged = [
        (m.created_at, _message_to_response(m)) for m in msgs
    ] + [
        (b.created_at, _broadcast_to_message_response(b, is_read, current_user.id))
        for b, is_read in bc_pairs
    ]
    merged.sort(key=lambda x: x[0], reverse=True)

    total = len(merged)
    offset = (pagination.page - 1) * pagination.page_size
    items = [resp for _, resp in merged[offset: offset + pagination.page_size]]

    return ApiResponse(
        code=200,
        message="获取成功",
        data=MessageListResponse(
            items=items,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        ),
    )


# ============ 单条已读 ============

@router.patch("/{message_id}/read", response_model=ApiResponse[MessageResponse])
async def mark_read(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MessageResponse]:
    """标记单条消息为已读（仅限本人消息，否则返回404避免枚举）。

    注意：此接口仅处理 messages 表行；广播请使用 /broadcasts/{id}/read。
    """
    result = await db.execute(
        select(Message).where(Message.id == message_id, Message.user_id == current_user.id)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")

    if not msg.is_read:
        msg.is_read = True
        msg.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(msg)

    return ApiResponse(code=200, message="已标记为已读", data=_message_to_response(msg))


# ============ 全部已读（合并） ============

@router.post("/read_all", response_model=ApiResponse[UnreadCountResponse])
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UnreadCountResponse]:
    """将当前用户所有未读消息标记为已读（含 messages 与广播）。"""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(Message)
        .where(Message.user_id == current_user.id, Message.is_read == False)  # noqa: E712
        .values(is_read=True, read_at=now)
    )
    await broadcast_service.mark_all_broadcasts_read(db, current_user.id)
    await db.commit()
    return ApiResponse(code=200, message="全部已读", data=UnreadCountResponse(count=0))


# ============ Banner 顶部横幅 ============

@router.get("/banners", response_model=ApiResponse[BannerListResponse])
async def list_banners(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[BannerListResponse]:
    """获取当前用户应显示的 Banner（最多 3 条，按级别优先级排序）。"""
    pairs = await broadcast_service.list_banners_for_user(
        db, current_user.id, max_count=3,
    )
    items = [
        BannerItem(
            id=b.id,
            level=b.level.value if hasattr(b.level, "value") else b.level,
            title=b.title,
            content=b.content,
            ref_url=b.ref_url,
            dismissible=b.dismissible,
            is_read=is_read,
            created_at=b.created_at,
        )
        for b, is_read in pairs
    ]
    return ApiResponse(code=200, message="获取成功", data=BannerListResponse(items=items))


# ============ 广播：标已读 / 关闭 ============

@router.patch("/broadcasts/{broadcast_id}/read", response_model=ApiResponse[UnreadCountResponse])
async def mark_broadcast_read(
    broadcast_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UnreadCountResponse]:
    """标记广播为已读（不会让 Banner 消失）。"""
    broadcast = await broadcast_service.get_broadcast_by_id(db, broadcast_id, include_deleted=False)
    if not broadcast:
        raise HTTPException(status_code=404, detail="广播不存在或已撤回")

    await broadcast_service.mark_broadcast_read(db, current_user.id, broadcast_id)
    await db.commit()

    # 返回最新未读总数（含 messages + 广播），方便前端刷新
    msg_result = await db.execute(
        select(func.count(Message.id))
        .where(Message.user_id == current_user.id, Message.is_read == False)  # noqa: E712
    )
    msg_unread = msg_result.scalar() or 0
    bc_unread = await broadcast_service.count_unread_broadcasts(db, current_user.id)

    return ApiResponse(
        code=200,
        message="已标记为已读",
        data=UnreadCountResponse(count=msg_unread + bc_unread),
    )


@router.post("/broadcasts/{broadcast_id}/dismiss", response_model=ApiResponse[UnreadCountResponse])
async def dismiss_broadcast(
    broadcast_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UnreadCountResponse]:
    """关闭广播 Banner（同时标记为已读）。

    关闭后该用户后续不再看到此广播的横幅，但通知中心列表仍可见。
    """
    broadcast = await broadcast_service.get_broadcast_by_id(db, broadcast_id, include_deleted=False)
    if not broadcast:
        raise HTTPException(status_code=404, detail="广播不存在或已撤回")

    if not broadcast.dismissible:
        raise HTTPException(status_code=400, detail="该广播不允许关闭")

    await broadcast_service.dismiss_broadcast(db, current_user.id, broadcast_id)
    await db.commit()

    msg_result = await db.execute(
        select(func.count(Message.id))
        .where(Message.user_id == current_user.id, Message.is_read == False)  # noqa: E712
    )
    msg_unread = msg_result.scalar() or 0
    bc_unread = await broadcast_service.count_unread_broadcasts(db, current_user.id)

    return ApiResponse(
        code=200,
        message="已关闭",
        data=UnreadCountResponse(count=msg_unread + bc_unread),
    )
