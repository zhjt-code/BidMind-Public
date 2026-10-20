"""认证相关Schema

定义用户认证、注册、令牌等数据模型。
"""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator
import re


class UserBase(BaseModel):
    """用户基础信息"""
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: EmailStr = Field(..., description="邮箱地址")


class UserCreate(UserBase):
    """用户注册请求"""
    password: str = Field(..., min_length=8, max_length=128, description="密码")
    confirm_password: str = Field(..., description="确认密码")
    fingerprint: Optional[str] = Field(
        default=None, max_length=128, description="设备指纹哈希(反多账号风控, 可选)"
    )
    
    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """验证用户名格式
        
        只允许字母、数字、下划线，且以字母开头。
        """
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", v):
            raise ValueError("用户名只能包含字母、数字、下划线，且必须以字母开头")
        return v
    
    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """验证密码强度"""
        if not any(c.isalpha() for c in v):
            raise ValueError("密码必须包含字母")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码必须包含数字")
        return v
    
    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        """验证两次密码一致"""
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("两次输入的密码不一致")
        return v


class UserLogin(BaseModel):
    """用户登录请求"""
    username: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="密码")


class VerifyEmailRequest(BaseModel):
    """邮箱验证请求"""
    token: str = Field(..., min_length=8, max_length=256, description="验证令牌")


class ResendVerificationRequest(BaseModel):
    """重发验证邮件请求"""
    email: EmailStr = Field(..., description="邮箱地址")


class ForgotPasswordRequest(BaseModel):
    """忘记密码: 发起重置请求"""
    email: EmailStr = Field(..., description="注册邮箱")


class ResetPasswordRequest(BaseModel):
    """忘记密码: 用令牌设置新密码"""
    token: str = Field(..., min_length=8, max_length=256, description="重置令牌")
    new_password: str = Field(..., min_length=8, max_length=128, description="新密码")
    confirm_password: str = Field(..., description="确认新密码")

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        """验证新密码强度"""
        if not any(c.isalpha() for c in v):
            raise ValueError("密码必须包含字母")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码必须包含数字")
        return v

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        """验证两次密码一致"""
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("两次输入的密码不一致")
        return v


class UserResponse(BaseModel):
    """用户信息响应"""
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    last_login: Optional[datetime] = None
    created_at: datetime
    welcome_letter_acknowledged_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    """用户信息更新请求"""
    email: Optional[EmailStr] = None
    current_password: Optional[str] = Field(None, description="当前密码（修改密码时必填）")
    new_password: Optional[str] = Field(None, min_length=8, max_length=128, description="新密码")
    
    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: Optional[str]) -> Optional[str]:
        """验证新密码强度"""
        if v is not None:
            if not any(c.isalpha() for c in v):
                raise ValueError("密码必须包含字母")
            if not any(c.isdigit() for c in v):
                raise ValueError("密码必须包含数字")
        return v


class Token(BaseModel):
    """令牌响应"""
    access_token: str = Field(..., description="访问令牌")
    refresh_token: str = Field(..., description="刷新令牌")
    token_type: str = Field(default="bearer", description="令牌类型")


class TokenRefresh(BaseModel):
    """令牌刷新请求"""
    refresh_token: str = Field(..., description="刷新令牌")


class TokenPayload(BaseModel):
    """令牌载荷"""
    sub: str = Field(..., description="主体（用户ID）")
    exp: datetime = Field(..., description="过期时间")
    type: Literal["access", "refresh"] = Field(..., description="令牌类型")
    role: Optional[str] = None


class AdminPasswordResetResponse(BaseModel):
    """管理员重置密码响应"""
    temporary_password: str = Field(..., description="临时密码(明文,仅返回一次)")


class PasswordChange(BaseModel):
    """密码修改请求"""
    current_password: str = Field(..., description="当前密码")
    new_password: str = Field(..., min_length=8, max_length=128, description="新密码")
    confirm_password: str = Field(..., description="确认新密码")
    
    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        """验证新密码强度"""
        if not any(c.isalpha() for c in v):
            raise ValueError("密码必须包含字母")
        if not any(c.isdigit() for c in v):
            raise ValueError("密码必须包含数字")
        return v
    
    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        """验证两次密码一致"""
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("两次输入的密码不一致")
        return v
