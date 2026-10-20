"""认证业务服务

封装用户注册、登录、令牌刷新、信息更新等认证相关业务逻辑。
Service层不依赖任何HTTP概念，通过领域异常传递错误。
"""

import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.config import settings
from app.models.user import User, UserRole
from app.models.email_verification_token import EmailVerificationToken
from app.models.password_reset_token import PasswordResetToken
from app.schemas.auth import UserCreate, UserUpdate
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.query_cardinality import (
    AmbiguousQueryResult,
    occupied,
    unique_scalar_or_none,
)
from app.core.exceptions import (
    ServiceError,
    AuthenticationError,
    ForbiddenError,
)
from app.core.email import send_verification_email, send_password_reset_email
from app.core.disposable_email import is_disposable_email
from app.services.abuse_guard_service import AbuseGuardService
from app.services.audit_service import audit_log

MAX_LOGIN_ATTEMPTS = 5
LOCK_DURATION_MINUTES = 15


class AuthService:
    """认证业务服务"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @staticmethod
    def _hash_token(raw: str) -> str:
        """对验证令牌做 sha256, 仅存哈希不存明文"""
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _issue_verification_token(self, user: User) -> str:
        """生成一条邮箱验证令牌(存哈希), 返回原始令牌用于拼链接"""
        raw = secrets.token_urlsafe(32)
        self._db.add(EmailVerificationToken(
            user_id=user.id,
            token_hash=self._hash_token(raw),
            expires_at=datetime.now(timezone.utc) + timedelta(
                hours=settings.EMAIL_VERIFY_TOKEN_TTL_HOURS
            ),
        ))
        await self._db.flush()
        return raw

    async def _send_verification(self, user: User) -> None:
        """签发令牌并发送验证邮件(失败仅记日志, 不反噬主流程)"""
        raw = await self._issue_verification_token(user)
        await self._db.commit()
        verify_url = (
            f"{settings.FRONTEND_BASE_URL.rstrip('/')}/verify-email?token={raw}"
        )
        await send_verification_email(user.email, verify_url)

    async def register(
        self,
        user_data: UserCreate,
        fingerprint: str | None = None,
        ip: str | None = None,
    ) -> User:
        """注册新用户

        开启邮箱验证(EMAIL_VERIFICATION_ENABLED)时:
        - 拦截一次性邮箱域名;
        - 用户初始 email_verified=False, 登录前必须验证;
        - 记录设备指纹/IP 风控信号;
        - 试用额度改为"验证通过后"按设备指纹软上限发放, 注册时不发放。
        关闭时维持旧行为(注册即视为已验证并发放试用)。

        Raises:
            ServiceError: 用户名或邮箱已被注册 / 邮箱域名不被支持
        """
        stmt = select(User).where(
            or_(
                User.username == user_data.username,
                User.email == user_data.email,
            )
        )
        result = await self._db.execute(stmt)
        if occupied(result):
            raise ServiceError("用户名或邮箱已被注册")

        gate = settings.EMAIL_VERIFICATION_ENABLED
        if gate and is_disposable_email(user_data.email):
            raise ServiceError("该邮箱域名不被支持，请使用常用邮箱注册")

        user = User(
            username=user_data.username,
            email=user_data.email,
            hashed_password=get_password_hash(user_data.password),
            role=UserRole.USER,
            is_active=True,
            email_verified=not gate,  # 关闭验证则视为已验证; 开启则待验证
        )

        self._db.add(user)
        await self._db.commit()
        await self._db.refresh(user)

        logger.info(f"新用户注册: {user.username} (ID: {user.id}), gate={gate}")
        await audit_log(
            self._db, user_id=user.id, username=user.username,
            category="system",
            action="register", resource_type="user", resource_id=user.id,
        )

        if gate:
            try:
                await AbuseGuardService.record_registration_signal(
                    self._db, user.id, fingerprint, ip
                )
                await self._send_verification(user)
            except Exception as e:
                logger.warning(f"注册验证流程异常(不影响注册): {e}")

        return user

    async def verify_email(self, token: str) -> User:
        """校验邮箱验证令牌并置 email_verified。

        Raises:
            ServiceError: 链接无效 / 已使用 / 已过期 / 用户不存在
        """
        rec = (await self._db.execute(
            select(EmailVerificationToken).where(
                EmailVerificationToken.token_hash == self._hash_token(token)
            )
        )).scalar_one_or_none()
        if rec is None:
            raise ServiceError("验证链接无效")
        if rec.used_at is not None:
            raise ServiceError("验证链接已使用，请直接登录或重新发送验证邮件")

        now = datetime.now(timezone.utc)
        expires = rec.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < now:
            raise ServiceError("验证链接已过期，请重新发送验证邮件")

        user = (await self._db.execute(
            select(User).where(User.id == rec.user_id)
        )).scalar_one_or_none()
        if user is None:
            raise ServiceError("用户不存在")

        was_verified = user.email_verified
        rec.used_at = now
        if not was_verified:
            user.email_verified = True
            user.email_verified_at = now
        await self._db.commit()

        logger.info(f"邮箱验证成功: user={user.id}")
        return user

    async def resend_verification(self, email: str) -> None:
        """重发验证邮件; 为防账号枚举, 无论邮箱是否存在/已验证均静默返回"""
        user = (await self._db.execute(
            select(User).where(User.email == email)
        )).scalar_one_or_none()
        if user is None or user.email_verified:
            return
        try:
            await self._send_verification(user)
        except Exception as e:
            logger.warning(f"重发验证邮件失败: {e}")

    async def _send_password_reset(self, user: User) -> None:
        """签发密码重置令牌(存哈希)并发送重置邮件"""
        raw = secrets.token_urlsafe(32)
        self._db.add(PasswordResetToken(
            user_id=user.id,
            token_hash=self._hash_token(raw),
            expires_at=datetime.now(timezone.utc) + timedelta(
                minutes=settings.PASSWORD_RESET_TOKEN_TTL_MINUTES
            ),
        ))
        await self._db.commit()
        reset_url = (
            f"{settings.FRONTEND_BASE_URL.rstrip('/')}/reset-password?token={raw}"
        )
        await send_password_reset_email(user.email, reset_url)

    async def request_password_reset(self, email: str) -> None:
        """发起密码重置; 为防账号枚举, 无论邮箱是否存在/可用均静默返回"""
        user = (await self._db.execute(
            select(User).where(User.email == email)
        )).scalar_one_or_none()
        if user is None or not user.is_active:
            return
        try:
            # 作废该用户旧的未用重置令牌, 保证同一时刻只有最新链接有效
            await self._db.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.used_at.is_(None),
                )
                .values(used_at=datetime.now(timezone.utc))
            )
            await self._send_password_reset(user)
        except Exception as e:
            logger.warning(f"发送密码重置邮件失败: {e}")

    async def reset_password(self, token: str, new_password: str) -> None:
        """用重置令牌设置新密码

        成功后清除登录失败锁定, 并自增 token_version 令该用户所有旧 JWT 立即失效。

        Raises:
            ServiceError: 链接无效 / 已使用 / 已过期 / 用户不存在
        """
        rec = (await self._db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == self._hash_token(token)
            )
        )).scalar_one_or_none()
        if rec is None:
            raise ServiceError("重置链接无效")
        if rec.used_at is not None:
            raise ServiceError("重置链接已使用，请重新发起找回密码")

        now = datetime.now(timezone.utc)
        expires = rec.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < now:
            raise ServiceError("重置链接已过期，请重新发起找回密码")

        user = (await self._db.execute(
            select(User).where(User.id == rec.user_id)
        )).scalar_one_or_none()
        if user is None:
            raise ServiceError("用户不存在")

        user.hashed_password = get_password_hash(new_password)
        user.reset_login_attempts()  # 清除失败计数并解锁
        user.token_version = (user.token_version or 0) + 1  # 令旧 JWT 立即失效
        rec.used_at = now
        await self._db.commit()

        logger.info(f"密码重置成功: user={user.id}")
        await audit_log(
            self._db, user_id=user.id, username=user.username,
            category="system",
            action="password_reset", resource_type="user", resource_id=user.id,
        )

    async def login(self, username: str, password: str) -> dict[str, Any]:
        """用户登录，返回令牌字典

        支持用户名或邮箱登录。连续失败达到上限后锁定账户。

        Args:
            username: 用户名或邮箱
            password: 明文密码

        Returns:
            包含 access_token、refresh_token、token_type 的字典

        Raises:
            AuthenticationError: 用户名或密码错误
            ForbiddenError: 账户被锁定或禁用
        """
        stmt = select(User).where(
            or_(User.username == username, User.email == username)
        )
        result = await self._db.execute(stmt)
        try:
            user = unique_scalar_or_none(result)
        except AmbiguousQueryResult:
            raise AuthenticationError("用户名或密码错误") from None

        if not user:
            raise AuthenticationError("用户名或密码错误")

        if user.is_locked():
            remaining_time = user.locked_until - datetime.now(timezone.utc)
            remaining_minutes = max(1, int(remaining_time.total_seconds() / 60))
            raise ForbiddenError(f"账户已被锁定，请{remaining_minutes}分钟后重试")

        if not user.is_active:
            raise ForbiddenError("账户已被禁用，请联系管理员")

        if not verify_password(password, user.hashed_password):
            user.increment_login_attempts()

            if user.get_login_attempts() >= MAX_LOGIN_ATTEMPTS:
                user.locked_until = datetime.now(timezone.utc) + timedelta(
                    minutes=LOCK_DURATION_MINUTES
                )
                await self._db.commit()
                raise ForbiddenError(
                    f"密码错误次数过多，账户已被锁定{LOCK_DURATION_MINUTES}分钟"
                )

            await self._db.commit()
            remaining = MAX_LOGIN_ATTEMPTS - user.get_login_attempts()
            raise AuthenticationError(f"密码错误，还剩{remaining}次尝试机会")

        # 邮箱验证硬拦截: 密码正确但未验证则拒绝登录(存量用户迁移已豁免)
        if settings.EMAIL_VERIFICATION_ENABLED and not user.email_verified:
            raise ForbiddenError("邮箱未验证，请先查收验证邮件完成验证后再登录")

        user.reset_login_attempts()
        user.last_login = datetime.now(timezone.utc)
        await self._db.commit()

        access_token = create_access_token(
            subject=user.id, extra_data={"role": user.role.value},
            token_version=user.token_version,
        )
        refresh = create_refresh_token(
            subject=user.id, token_version=user.token_version,
        )

        logger.info(f"用户登录: {user.username} (ID: {user.id})")
        await audit_log(
            self._db, user_id=user.id, username=user.username,
            category="system",
            action="login", resource_type="user", resource_id=user.id,
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh,
            "token_type": "bearer",
        }

    async def refresh_token(self, refresh_token_str: str) -> dict[str, Any]:
        """使用刷新令牌获取新的令牌对

        Args:
            refresh_token_str: 刷新令牌字符串

        Returns:
            包含 access_token、refresh_token、token_type 的字典

        Raises:
            AuthenticationError: 令牌无效或用户不存在
            ForbiddenError: 账户已被禁用
        """
        payload = decode_token(refresh_token_str)

        if not payload:
            raise AuthenticationError("无效的刷新令牌")

        if payload.get("type") != "refresh":
            raise AuthenticationError("令牌类型错误")

        user_id = payload.get("sub")
        if not user_id:
            raise AuthenticationError("无效的刷新令牌")

        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            raise AuthenticationError("无效的刷新令牌")

        stmt = select(User).where(User.id == uid)
        result = await self._db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise AuthenticationError("用户不存在")

        if not user.is_active:
            raise ForbiddenError("账户已被禁用")

        # 会话强失效: 刷新令牌版本与当前用户版本不一致(密码已重置)则拒绝
        if int(payload.get("ver", 0) or 0) != int(user.token_version or 0):
            raise AuthenticationError("会话已失效，请重新登录")

        access_token = create_access_token(
            subject=user.id, extra_data={"role": user.role.value},
            token_version=user.token_version,
        )
        refresh = create_refresh_token(
            subject=user.id, token_version=user.token_version,
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh,
            "token_type": "bearer",
        }

    async def update_user(self, user: User, user_data: UserUpdate) -> User:
        """更新用户信息（邮箱和/或密码）

        Args:
            user: 当前用户对象（由调用方提供，已绑定到session）
            user_data: 待更新的字段

        Returns:
            更新后的用户对象

        Raises:
            ServiceError: 邮箱已被占用或当前密码错误
        """
        if user_data.email and user_data.email != user.email:
            stmt = select(User).where(User.email == user_data.email)
            result = await self._db.execute(stmt)
            if result.scalar_one_or_none():
                raise ServiceError("邮箱已被使用")
            user.email = user_data.email

        if user_data.new_password:
            if not user_data.current_password:
                raise ServiceError("修改密码需要提供当前密码")

            if not verify_password(user_data.current_password, user.hashed_password):
                raise ServiceError("当前密码错误")

            user.hashed_password = get_password_hash(user_data.new_password)
            # 改密令此前签发的旧 JWT 立即失效(与 change_password / reset_password 一致)
            user.token_version = (user.token_version or 0) + 1

        await self._db.commit()
        await self._db.refresh(user)
        return user

    @staticmethod
    def _generate_temporary_password(length: int = 12) -> str:
        """生成满足强度要求的随机临时密码(含字母+数字)"""
        alphabet = string.ascii_letters + string.digits
        while True:
            password = ''.join(secrets.choice(alphabet) for _ in range(length))
            if (any(c.isalpha() for c in password)
                    and any(c.isdigit() for c in password)):
                return password

    async def admin_reset_password(
        self, target_user: User, admin_user: User
    ) -> str:
        """管理员重置用户密码

        生成随机临时密码,哈希后写入用户记录,返回明文临时密码。

        Args:
            target_user: 被重置密码的目标用户
            admin_user: 执行操作的管理员

        Returns:
            明文临时密码(仅此一次返回,不持久化明文)
        """
        temp_password = self._generate_temporary_password()
        target_user.hashed_password = get_password_hash(temp_password)

        target_user.reset_login_attempts()
        target_user.locked_until = None
        # 管理员重置后令该用户所有旧 JWT 立即失效
        target_user.token_version = (target_user.token_version or 0) + 1

        await self._db.commit()

        logger.info(
            f"管理员 {admin_user.username} 重置了用户 "
            f"{target_user.username} (ID: {target_user.id}) 的密码"
        )
        await audit_log(
            self._db,
            user_id=admin_user.id,
            username=admin_user.username,
            category="system",
            action="admin_reset_password",
            resource_type="user",
            resource_id=target_user.id,
        )

        return temp_password

    async def change_password(
        self, user: User, current_password: str, new_password: str
    ) -> dict[str, Any]:
        """修改密码并轮换会话令牌

        校验当前密码后更新密码, 并自增 token_version 令此前签发的所有 JWT 立即失效
        (与 reset_password / admin_reset_password 保持一致), 随后用新版本重签当前
        会话的令牌对返回: 发起改密的设备凭新令牌保持登录, 其他设备旧会话立即失效。

        Args:
            user: 当前用户对象
            current_password: 当前密码明文
            new_password: 新密码明文

        Returns:
            新的令牌字典(access_token / refresh_token / token_type)

        Raises:
            ServiceError: 当前密码验证失败
        """
        if not verify_password(current_password, user.hashed_password):
            raise ServiceError("当前密码错误")

        user.hashed_password = get_password_hash(new_password)
        user.token_version = (user.token_version or 0) + 1  # 令此前签发的旧 JWT 立即失效
        await self._db.commit()

        logger.info(f"用户修改密码: {user.username}")
        await audit_log(
            self._db, user_id=user.id, username=user.username,
            category="system",
            action="change_password", resource_type="user", resource_id=user.id,
        )

        access_token = create_access_token(
            subject=user.id, extra_data={"role": user.role.value},
            token_version=user.token_version,
        )
        refresh = create_refresh_token(
            subject=user.id, token_version=user.token_version,
        )
        return {
            "access_token": access_token,
            "refresh_token": refresh,
            "token_type": "bearer",
        }
