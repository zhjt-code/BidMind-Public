"""用户数据模型

定义用户表结构，支持用户认证和角色管理。
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    pass


class UserRole(str, enum.Enum):
    """用户角色枚举"""
    USER = "user"
    ADMIN = "admin"


class User(BaseModel):
    """用户模型
    
    存储用户账户信息，包括认证凭据和角色权限。
    
    Attributes:
        username: 用户名，唯一标识
        email: 邮箱地址，唯一
        hashed_password: bcrypt加密后的密码
        role: 用户角色 (user/admin)
        is_active: 账户是否启用
        last_login: 最后登录时间
        login_attempts: 登录失败次数（用于防暴力破解）
        locked_until: 账户锁定截止时间
        email_verified: 邮箱是否已验证（反多账号硬拦截依据）
        email_verified_at: 邮箱验证时间
    """
    
    __tablename__ = "users"
    
    username = Column(String(50), unique=True, index=True, nullable=False, comment="用户名")
    email = Column(String(255), unique=True, index=True, nullable=False, comment="邮箱")
    hashed_password = Column(String(255), nullable=False, comment="加密密码")
    role = Column(
        SQLEnum(UserRole, name="user_role", create_type=True),
        default=UserRole.USER,
        nullable=False,
        comment="用户角色"
    )
    is_active = Column(Boolean, default=True, nullable=False, comment="是否启用")
    last_login = Column(DateTime(timezone=True), nullable=True, comment="最后登录时间")
    login_attempts = Column(Integer, default=0, nullable=False, comment="登录失败次数")
    locked_until = Column(DateTime(timezone=True), nullable=True, comment="锁定截止时间")
    email_verified = Column(
        Boolean, default=False, nullable=False, comment="邮箱是否已验证"
    )
    email_verified_at = Column(
        DateTime(timezone=True), nullable=True, comment="邮箱验证时间"
    )
    token_version = Column(
        Integer, default=0, nullable=False,
        comment="令牌版本: 重置密码/管理员重置时自增, 令该用户旧 JWT 立即失效",
    )
    welcome_letter_acknowledged_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="欢迎信确认时间; NULL 表示待展示",
    )
    
    # 关联：用户拥有的项目
    projects = relationship("Project", back_populates="owner", lazy="dynamic")
    
    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username}, role={self.role})>"
    
    @property
    def is_admin(self) -> bool:
        """检查用户是否为管理员"""
        return self.role == UserRole.ADMIN
    
    def is_locked(self) -> bool:
        """检查账户是否被锁定"""
        if self.locked_until is None:
            return False
        return datetime.now(self.locked_until.tzinfo) < self.locked_until
    
    def get_login_attempts(self) -> int:
        """获取登录失败次数"""
        return self.login_attempts or 0
    
    def increment_login_attempts(self) -> None:
        """增加登录失败次数"""
        self.login_attempts = self.get_login_attempts() + 1
    
    def reset_login_attempts(self) -> None:
        """重置登录失败次数"""
        self.login_attempts = 0
        self.locked_until = None
