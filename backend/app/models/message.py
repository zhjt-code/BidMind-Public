"""站内信数据模型

存储系统发给用户的站内消息，支持分类、已读管理和来源引用。
"""

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    pass


class MessageType(str, enum.Enum):
    """消息类型枚举"""
    FEEDBACK_REPLY = "feedback_reply"
    SYSTEM = "system"


class Message(BaseModel):
    """站内信模型

    Attributes:
        user_id: 接收用户ID
        type: 消息类型
        title: 消息标题
        content: 消息正文
        is_read: 是否已读
        read_at: 已读时间
        ref_type: 来源类型（如 "feedback"），用于前端跳转
        ref_id: 来源ID
    """

    __tablename__ = "messages"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="接收用户ID"
    )
    type = Column(
        SQLEnum(MessageType, name="message_type_enum", create_type=True),
        nullable=False, default=MessageType.SYSTEM, comment="消息类型"
    )
    title = Column(String(200), nullable=False, comment="消息标题")
    content = Column(Text, nullable=False, comment="消息正文")
    is_read = Column(Boolean, default=False, nullable=False, index=True, comment="是否已读")
    read_at = Column(DateTime(timezone=True), nullable=True, comment="已读时间")
    ref_type = Column(String(50), nullable=True, comment="来源类型")
    ref_id = Column(Integer, nullable=True, comment="来源ID")

    user = relationship("User", foreign_keys=[user_id], lazy="joined")

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, user_id={self.user_id}, type={self.type}, is_read={self.is_read})>"
