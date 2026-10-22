"""用户反馈数据模型

存储用户提交的反馈信息，支持分类、状态管理和管理员批注。
"""

import enum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    pass


class FeedbackType(str, enum.Enum):
    """反馈类型枚举"""
    BUG = "bug"
    FEATURE = "feature"
    REVIEW_QUESTION = "review_question"
    OTHER = "other"


class FeedbackStatus(str, enum.Enum):
    """反馈状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Feedback(BaseModel):
    """用户反馈模型

    存储用户提交的意见反馈，支持关联到具体项目。
    管理员可通过状态流转和批注进行处理。

    Attributes:
        user_id: 提交用户ID
        feedback_type: 反馈类型 (bug/feature/review_question/other)
        content: 反馈正文
        page_url: 提交时所在页面URL
        project_id: 关联项目ID（可选，从结果页提交时自动填充）
        context_info: 浏览器UA等环境信息（JSON）
        status: 处理状态 (pending/processing/resolved/closed)
        admin_note: 管理员批注
    """

    __tablename__ = "feedbacks"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="提交用户ID"
    )
    feedback_type = Column(
        SQLEnum(FeedbackType, name="feedback_type", create_type=True),
        nullable=False, comment="反馈类型"
    )
    content = Column(Text, nullable=False, comment="反馈正文")
    page_url = Column(String(500), nullable=True, comment="来源页面URL")
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True, index=True, comment="关联项目ID"
    )
    context_info = Column(JSON, nullable=True, comment="浏览器/环境信息")
    status = Column(
        SQLEnum(FeedbackStatus, name="feedback_status", create_type=True),
        default=FeedbackStatus.PENDING, nullable=False,
        index=True, comment="处理状态"
    )
    admin_note = Column(Text, nullable=True, comment="管理员批注")

    # 关联关系
    user = relationship("User", foreign_keys=[user_id], lazy="joined")
    project = relationship("Project", foreign_keys=[project_id], lazy="joined")

    def __repr__(self) -> str:
        return f"<Feedback(id={self.id}, type={self.feedback_type}, status={self.status})>"
