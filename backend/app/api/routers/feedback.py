"""用户反馈路由

提供反馈提交（用户）和反馈管理（管理员）功能。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import get_current_user, get_pagination, require_admin
from app.core.database import get_db
from app.models.feedback import Feedback, FeedbackStatus, FeedbackType
from app.models.user import User
from app.schemas.common import ApiResponse, PaginationParams
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackListResponse,
    FeedbackProjectInfo,
    FeedbackResponse,
    FeedbackStatusUpdate,
    FeedbackUserInfo,
)

router = APIRouter(prefix="/feedback", tags=["用户反馈"])


def _build_feedback_response(fb: Feedback) -> FeedbackResponse:
    """将 ORM 对象转换为响应 Schema。

    Args:
        fb: Feedback ORM 实例（已 joined load user/project）

    Returns:
        FeedbackResponse: 序列化后的反馈响应
    """
    user_info = None
    if fb.user:
        user_info = FeedbackUserInfo(
            id=fb.user.id,
            username=fb.user.username,
            email=fb.user.email,
        )

    project_info = None
    if fb.project:
        project_info = FeedbackProjectInfo(
            id=fb.project.id,
            name=fb.project.name,
        )

    return FeedbackResponse(
        id=fb.id,
        user_id=fb.user_id,
        user=user_info,
        feedback_type=fb.feedback_type.value if isinstance(fb.feedback_type, FeedbackType) else fb.feedback_type,
        content=fb.content,
        page_url=fb.page_url,
        project_id=fb.project_id,
        project=project_info,
        context_info=fb.context_info,
        status=fb.status.value if isinstance(fb.status, FeedbackStatus) else fb.status,
        admin_note=fb.admin_note,
        created_at=fb.created_at,
        updated_at=fb.updated_at,
    )


# ==================== 用户端点 ====================


@router.post("", response_model=ApiResponse[FeedbackResponse], status_code=201)
async def create_feedback(
    data: FeedbackCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[FeedbackResponse]:
    """提交反馈

    登录用户可提交反馈，系统自动关联用户信息。

    Args:
        data: 反馈内容
        current_user: 当前登录用户
        db: 数据库会话

    Returns:
        ApiResponse[FeedbackResponse]: 创建的反馈信息
    """
    try:
        fb_type = FeedbackType(data.feedback_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的反馈类型"
        )

    feedback = Feedback(
        user_id=current_user.id,
        feedback_type=fb_type,
        content=data.content,
        page_url=data.page_url,
        project_id=data.project_id,
        context_info=data.context_info,
        status=FeedbackStatus.PENDING,
    )

    db.add(feedback)
    await db.commit()

    stmt = (
        select(Feedback)
        .where(Feedback.id == feedback.id)
        .options(selectinload(Feedback.user), selectinload(Feedback.project))
    )
    result = await db.execute(stmt)
    feedback = result.scalar_one()

    logger.info(f"用户 {current_user.username} 提交了反馈 (ID: {feedback.id}, 类型: {fb_type.value})")

    return ApiResponse(
        code=201,
        message="反馈提交成功",
        data=_build_feedback_response(feedback),
    )


# ==================== 管理员端点 ====================


@router.get("", response_model=ApiResponse[FeedbackListResponse])
async def list_feedbacks(
    pagination: PaginationParams = Depends(get_pagination),
    feedback_status: Optional[str] = Query(None, alias="status", description="状态筛选"),
    feedback_type: Optional[str] = Query(None, alias="type", description="类型筛选"),
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[FeedbackListResponse]:
    """获取反馈列表

    仅管理员可访问。支持按状态和类型筛选，按创建时间倒序。

    Args:
        pagination: 分页参数
        feedback_status: 状态筛选
        feedback_type: 类型筛选
        admin_user: 当前管理员用户
        db: 数据库会话

    Returns:
        ApiResponse[FeedbackListResponse]: 反馈列表
    """
    query = select(Feedback).options(
        selectinload(Feedback.user), selectinload(Feedback.project)
    )
    count_query = select(func.count(Feedback.id))

    if feedback_status:
        try:
            status_enum = FeedbackStatus(feedback_status)
            query = query.where(Feedback.status == status_enum)
            count_query = count_query.where(Feedback.status == status_enum)
        except ValueError:
            pass

    if feedback_type:
        try:
            type_enum = FeedbackType(feedback_type)
            query = query.where(Feedback.feedback_type == type_enum)
            count_query = count_query.where(Feedback.feedback_type == type_enum)
        except ValueError:
            pass

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (pagination.page - 1) * pagination.page_size
    query = query.order_by(Feedback.created_at.desc()).offset(offset).limit(pagination.page_size)

    result = await db.execute(query)
    feedbacks = result.scalars().all()

    return ApiResponse(
        code=200,
        message="获取成功",
        data=FeedbackListResponse(
            items=[_build_feedback_response(fb) for fb in feedbacks],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        ),
    )


@router.get("/{feedback_id}", response_model=ApiResponse[FeedbackResponse])
async def get_feedback(
    feedback_id: int,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[FeedbackResponse]:
    """获取反馈详情

    Args:
        feedback_id: 反馈ID
        admin_user: 当前管理员用户
        db: 数据库会话

    Returns:
        ApiResponse[FeedbackResponse]: 反馈详情
    """
    stmt = (
        select(Feedback)
        .where(Feedback.id == feedback_id)
        .options(selectinload(Feedback.user), selectinload(Feedback.project))
    )
    result = await db.execute(stmt)
    feedback = result.scalar_one_or_none()

    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="反馈不存在"
        )

    return ApiResponse(
        code=200,
        message="获取成功",
        data=_build_feedback_response(feedback),
    )


@router.patch("/{feedback_id}/status", response_model=ApiResponse[FeedbackResponse])
async def update_feedback_status(
    feedback_id: int,
    data: FeedbackStatusUpdate,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[FeedbackResponse]:
    """更新反馈状态和批注

    Args:
        feedback_id: 反馈ID
        data: 状态更新数据
        admin_user: 当前管理员用户
        db: 数据库会话

    Returns:
        ApiResponse[FeedbackResponse]: 更新后的反馈信息
    """
    stmt = select(Feedback).where(Feedback.id == feedback_id)
    result = await db.execute(stmt)
    feedback = result.scalar_one_or_none()

    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="反馈不存在"
        )

    try:
        new_status = FeedbackStatus(data.status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的状态值"
        )

    old_status = feedback.status
    old_note = feedback.admin_note
    feedback.status = new_status

    if data.admin_note is not None:
        feedback.admin_note = data.admin_note

    should_notify = (
        data.notify_user
        and (
            new_status in (FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED)
            or (data.admin_note is not None and data.admin_note != old_note)
        )
    )
    if should_notify:
        from app.services.message_service import create_feedback_reply_message
        await create_feedback_reply_message(db, feedback, feedback.admin_note)

    await db.commit()
    await db.refresh(feedback)

    logger.info(
        f"管理员 {admin_user.username} 更新反馈 {feedback_id} "
        f"状态: {old_status.value} -> {new_status.value}"
        f"{' (已发送站内信)' if should_notify else ''}"
    )

    return ApiResponse(
        code=200,
        message="状态已更新",
        data=_build_feedback_response(feedback),
    )
