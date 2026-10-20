"""认证路由

提供用户登录、注册、令牌刷新等认证接口。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.user import User
from app.schemas.auth import (
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
    Token,
    TokenRefresh,
    PasswordChange,
    VerifyEmailRequest,
    ResendVerificationRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.schemas.common import ApiResponse
from app.api.dependencies import get_current_user, handle_service_error
from app.services.auth_service import AuthService
from app.core.exceptions import ServiceError
from app.services.user_welcome_service import UserWelcomeService


router = APIRouter(prefix="/auth", tags=["认证"])


def _client_ip(request: Request) -> str | None:
    """提取客户端 IP(优先 X-Forwarded-For 首段, 适配反代)"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/register", response_model=ApiResponse[UserResponse])
@limiter.limit(lambda: settings.RATE_LIMIT_REGISTER)
async def register(
    request: Request,
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[UserResponse]:
    """用户注册

    开放注册，新用户默认为普通用户角色。
    IP级限流: 每分钟最多5次请求（防批量注册）。
    """
    try:
        service = AuthService(db)
        user = await service.register(
            user_data,
            fingerprint=user_data.fingerprint,
            ip=_client_ip(request),
        )
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="注册成功",
        data=UserResponse.model_validate(user),
    )


@router.post("/verify-email", response_model=ApiResponse[None])
async def verify_email(
    payload: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[None]:
    """邮箱验证: 校验令牌并激活账户(首次验证后按风控发放试用)"""
    try:
        service = AuthService(db)
        await service.verify_email(payload.token)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(code=200, message="邮箱验证成功", data=None)


@router.post("/resend-verification", response_model=ApiResponse[None])
@limiter.limit(lambda: settings.RATE_LIMIT_REGISTER)
async def resend_verification(
    request: Request,
    payload: ResendVerificationRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[None]:
    """重发验证邮件; 为防账号枚举, 无论邮箱是否存在/已验证均返回成功"""
    service = AuthService(db)
    await service.resend_verification(payload.email)
    return ApiResponse(code=200, message="如该邮箱存在且未验证，验证邮件已发送", data=None)


@router.post("/forgot-password", response_model=ApiResponse[None])
@limiter.limit(lambda: settings.RATE_LIMIT_REGISTER)
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[None]:
    """忘记密码: 发起重置; 为防账号枚举, 无论邮箱是否存在均返回成功"""
    service = AuthService(db)
    await service.request_password_reset(payload.email)
    return ApiResponse(code=200, message="如该邮箱已注册，重置邮件已发送", data=None)


@router.post("/reset-password", response_model=ApiResponse[None])
@limiter.limit(lambda: settings.RATE_LIMIT_REGISTER)
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[None]:
    """忘记密码: 用令牌设置新密码(成功后旧会话立即失效)"""
    try:
        service = AuthService(db)
        await service.reset_password(payload.token, payload.new_password)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(code=200, message="密码重置成功，请用新密码登录", data=None)


@router.post("/login", response_model=ApiResponse[Token])
@limiter.limit(lambda: settings.RATE_LIMIT_LOGIN)
async def login(
    request: Request,
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[Token]:
    """用户登录

    支持用户名或邮箱登录，返回访问令牌和刷新令牌。
    IP级限流: 每分钟最多10次请求（防暴力破解）。
    用户级限流: 连续5次密码错误锁定15分钟。
    """
    try:
        service = AuthService(db)
        token_data = await service.login(credentials.username, credentials.password)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="登录成功",
        data=Token(**token_data),
    )


@router.post("/refresh", response_model=ApiResponse[Token])
async def refresh_token(
    token_data: TokenRefresh,
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[Token]:
    """刷新访问令牌

    使用刷新令牌获取新的访问令牌。
    """
    try:
        service = AuthService(db)
        new_tokens = await service.refresh_token(token_data.refresh_token)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="令牌刷新成功",
        data=Token(**new_tokens),
    )


@router.get("/me", response_model=ApiResponse[UserResponse])
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
) -> ApiResponse[UserResponse]:
    """获取当前用户信息"""
    return ApiResponse(
        code=200,
        message="获取成功",
        data=UserResponse.model_validate(current_user),
    )


@router.put("/me", response_model=ApiResponse[UserResponse])
async def update_current_user(
    user_data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[UserResponse]:
    """更新当前用户信息"""
    try:
        service = AuthService(db)
        user = await service.update_user(current_user, user_data)
    except ServiceError as e:
        handle_service_error(e)

    return ApiResponse(
        code=200,
        message="更新成功",
        data=UserResponse.model_validate(user),
    )


@router.put("/me/welcome-letter-acknowledgement", response_model=ApiResponse[UserResponse])
async def acknowledge_welcome_letter(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UserResponse]:
    """确认当前用户已阅读内置欢迎信；重复调用保持幂等。"""
    service = UserWelcomeService(db)
    user = await service.acknowledge_welcome_letter(current_user)
    return ApiResponse(
        code=200,
        message="欢迎信已确认",
        data=UserResponse.model_validate(user),
    )


@router.post("/change-password", response_model=ApiResponse[Token])
async def change_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[Token]:
    """修改密码

    成功后此前签发的旧会话(其他设备)立即失效, 返回当前设备的新令牌对供前端替换。
    """
    try:
        service = AuthService(db)
        tokens = await service.change_password(
            current_user,
            password_data.current_password,
            password_data.new_password,
        )
    except ServiceError as e:
        handle_service_error(e)

    logger.info(f"用户修改密码: {current_user.username}")
    return ApiResponse(code=200, message="密码修改成功", data=Token(**tokens))
