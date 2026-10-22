"""广播站内信数据模型

支持管理员一次发布、所有用户可见的全员广播能力。

设计要点（参考 GitLab Broadcast Messages）：
- broadcasts 表：一条广播一行（避免 fan-out 写放大）
- broadcast_user_states 表：用户对广播的"已读 / 已关闭"状态
- 已读 != 已关闭：通知中心标已读不会让顶部 Banner 消失，二者解耦
- 软删除（deleted_at）支持管理员撤回，回滚只改一行
"""

import enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    pass


class BroadcastLevel(str, enum.Enum):
    """广播级别枚举

    决定 Banner 颜色与排序优先级（critical > warning > info > success）。
    """
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    CRITICAL = "critical"


class Broadcast(BaseModel):
    """广播主体

    Attributes:
        level: 级别（决定 Banner 颜色与优先级）
        title: 标题
        content: 正文（纯文本）
        ref_url: 横幅可点击跳转的内部路径，可空
        show_banner: 是否在顶部 Banner 渲染（False 表示仅进通知中心）
        dismissible: Banner 是否可关闭（critical 公告可设为 False）
        starts_at: 生效起始时间
        ends_at: 失效时间，NULL 表示长期有效
        created_by: 发布者用户ID（管理员）
        deleted_at: 软删除时间，非空即视为已撤回
    """

    __tablename__ = "broadcasts"

    level = Column(
        SQLEnum(BroadcastLevel, name="broadcast_level_enum", create_type=True),
        nullable=False, default=BroadcastLevel.INFO, comment="广播级别"
    )
    title = Column(String(200), nullable=False, comment="广播标题")
    content = Column(Text, nullable=False, comment="广播正文")
    ref_url = Column(String(500), nullable=True, comment="点击横幅跳转的内部路径")
    show_banner = Column(
        Boolean, nullable=False, default=True,
        comment="是否在顶部 Banner 显示（False 仅进通知中心）"
    )
    dismissible = Column(
        Boolean, nullable=False, default=True,
        comment="Banner 是否可被用户关闭"
    )
    starts_at = Column(
        DateTime(timezone=True), nullable=False, index=True,
        comment="生效起始时间"
    )
    ends_at = Column(
        DateTime(timezone=True), nullable=True, index=True,
        comment="失效时间，NULL 表示长期有效"
    )
    created_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, comment="发布者用户ID"
    )
    deleted_at = Column(
        DateTime(timezone=True), nullable=True, index=True,
        comment="软删除时间（撤回）"
    )

    creator = relationship("User", foreign_keys=[created_by], lazy="joined")

    __table_args__ = (
        Index("ix_broadcasts_active_window", "deleted_at", "starts_at", "ends_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Broadcast(id={self.id}, level={self.level}, "
            f"title={self.title!r}, deleted_at={self.deleted_at})>"
        )


class BroadcastUserState(BaseModel):
    """用户对广播的状态

    每个 (user_id, broadcast_id) 唯一一行。
    - read_at：通知中心点击/标记已读时写入；用于未读计数。
    - dismissed_at：用户在 Banner 上点 X 关闭时写入；该用户后续不再看到此 Banner。

    设计原因：read_at 与 dismissed_at 独立维护，标已读不影响 Banner 显示，避免误关闭。
    """

    __tablename__ = "broadcast_user_states"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="用户ID"
    )
    broadcast_id = Column(
        Integer, ForeignKey("broadcasts.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="广播ID"
    )
    read_at = Column(
        DateTime(timezone=True), nullable=True,
        comment="通知中心标已读时间"
    )
    dismissed_at = Column(
        DateTime(timezone=True), nullable=True,
        comment="Banner 关闭时间"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "broadcast_id", name="uq_broadcast_user_state"),
    )

    def __repr__(self) -> str:
        return (
            f"<BroadcastUserState(user_id={self.user_id}, "
            f"broadcast_id={self.broadcast_id}, "
            f"read_at={self.read_at}, dismissed_at={self.dismissed_at})>"
        )
