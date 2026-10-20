"""安全模块

提供JWT令牌生成/验证、密码哈希等安全功能。
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from jose import jwt, JWTError
from passlib.context import CryptContext
from loguru import logger

from app.core.config import settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _normalize_password(password: str) -> str:
    """处理超过bcrypt 72字节限制的密码

    超过72字节时使用SHA-256预哈希, 避免在多字节UTF-8字符边界截断。
    """
    raw = password.encode('utf-8')
    if len(raw) > 72:
        return hashlib.sha256(raw).hexdigest()
    return password


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码
    
    Args:
        plain_password: 明文密码
        hashed_password: 哈希后的密码
        
    Returns:
        bool: 密码是否匹配
    """
    return pwd_context.verify(_normalize_password(plain_password), hashed_password)


def get_password_hash(password: str) -> str:
    """获取密码哈希
    
    bcrypt限制密码最大72字节，超出部分会被截断。
    
    Args:
        password: 明文密码
        
    Returns:
        str: bcrypt哈希后的密码
    """
    return pwd_context.hash(_normalize_password(password))


def create_access_token(
    subject: str | int,
    expires_delta: Optional[timedelta] = None,
    extra_data: Optional[dict[str, Any]] = None,
    token_version: Optional[int] = None,
) -> str:
    """创建访问令牌
    
    Args:
        subject: 令牌主体（通常是用户ID）
        expires_delta: 过期时间增量
        extra_data: 额外数据（如角色信息）
        token_version: 令牌版本（与 User.token_version 对齐, 用于会话强失效）
        
    Returns:
        str: JWT访问令牌
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    to_encode = {
        "sub": str(subject),
        "exp": expire,
        "type": "access"
    }
    
    if extra_data:
        to_encode.update(extra_data)
    if token_version is not None:
        to_encode["ver"] = token_version
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


def create_refresh_token(
    subject: str | int,
    expires_delta: Optional[timedelta] = None,
    token_version: Optional[int] = None,
) -> str:
    """创建刷新令牌
    
    Args:
        subject: 令牌主体（通常是用户ID）
        expires_delta: 过期时间增量
        token_version: 令牌版本（与 User.token_version 对齐, 用于会话强失效）
        
    Returns:
        str: JWT刷新令牌
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
    
    to_encode = {
        "sub": str(subject),
        "exp": expire,
        "type": "refresh"
    }
    if token_version is not None:
        to_encode["ver"] = token_version
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


def decode_token(token: str) -> Optional[dict[str, Any]]:
    """解码JWT令牌
    
    Args:
        token: JWT令牌字符串
        
    Returns:
        Optional[dict]: 解码后的payload，失败返回None
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError as e:
        logger.debug(f"JWT解码失败: {e}")
        return None


def validate_password_strength(password: str) -> tuple[bool, str]:
    """验证密码强度
    
    要求：
    - 最少8个字符
    - 包含字母和数字
    
    Args:
        password: 待验证的密码
        
    Returns:
        tuple[bool, str]: (是否有效, 错误信息)
    """
    if len(password) < 8:
        return False, "密码长度至少8位"
    
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    
    if not has_letter:
        return False, "密码必须包含字母"
    
    if not has_digit:
        return False, "密码必须包含数字"
    
    return True, ""
