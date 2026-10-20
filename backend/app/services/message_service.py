"""站内信服务

集中管理站内信的创建逻辑，供路由层和反馈处理调用。
"""

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.message import Message, MessageType
from app.models.feedback import Feedback, FeedbackType

FEEDBACK_TYPE_LABELS: dict[str, str] = {
    FeedbackType.BUG.value: "Bug报告",
    FeedbackType.FEATURE.value: "功能建议",
    FeedbackType.REVIEW_QUESTION.value: "审查疑问",
    FeedbackType.OTHER.value: "其他",
}


async def create_message(
    db: AsyncSession,
    *,
    user_id: int,
    msg_type: MessageType,
    title: str,
    content: str,
    ref_type: Optional[str] = None,
    ref_id: Optional[int] = None,
) -> Message:
    """创建一条站内信（不 commit，由调用方统一提交事务）。"""
    message = Message(
        user_id=user_id,
        type=msg_type,
        title=title,
        content=content,
        ref_type=ref_type,
        ref_id=ref_id,
    )
    db.add(message)
    await db.flush()
    logger.info(f"站内信已创建: user_id={user_id}, type={msg_type.value}, ref={ref_type}:{ref_id}")
    return message


async def create_feedback_reply_message(
    db: AsyncSession,
    feedback: Feedback,
    admin_note: Optional[str],
) -> Message:
    """为反馈回复创建站内信。

    Args:
        db: 数据库会话（由调用方管理事务）
        feedback: 反馈 ORM 对象
        admin_note: 管理员批注内容
    """
    fb_type_val = feedback.feedback_type.value if isinstance(feedback.feedback_type, FeedbackType) else feedback.feedback_type
    type_label = FEEDBACK_TYPE_LABELS.get(fb_type_val, "反馈")
    title = f"您的{type_label}已处理"

    content_parts = [f"您提交的{type_label}已更新。"]
    if admin_note:
        content_parts.append(f"\n管理员回复：{admin_note}")

    return await create_message(
        db,
        user_id=feedback.user_id,
        msg_type=MessageType.FEEDBACK_REPLY,
        title=title,
        content="".join(content_parts),
        ref_type="feedback",
        ref_id=feedback.id,
    )
