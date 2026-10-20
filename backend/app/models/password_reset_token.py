"""密码重置令牌模型"""

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from app.models.base import BaseModel


class PasswordResetToken(BaseModel):
    """密码重置令牌

    职责: 承载一次性密码重置令牌(仅存哈希)及其有效期与使用状态
    不做: 邮件发送与密码变更(归 AuthService)
    """

    __tablename__ = "password_reset_tokens"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="所属用户",
    )
    token_hash = Column(
        String(64), nullable=False, unique=True, index=True,
        comment="sha256(原始令牌), 不存明文",
    )
    expires_at = Column(
        DateTime(timezone=True), nullable=False, comment="过期时间",
    )
    used_at = Column(
        DateTime(timezone=True), nullable=True, comment="使用时间(已用则非空)",
    )
