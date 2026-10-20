"""API 依赖注入

提供路由层的公共依赖。
"""

from typing import NoReturn, Optional
from fastapi import Path, Query, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.schemas.common import PaginationParams
from app.core.database import get_db
from app.core.security import decode_token
from app.core.exceptions import ForbiddenError, ServiceError
from app.core.middleware import set_log_user_id, set_log_project_id
from app.core.permissions import check_project_permission as _check_project_permission_core
from app.models.user import User, UserRole
from app.models.project import Project


# HTTP Bearer 认证方案
security = HTTPBearer(auto_error=False)


def handle_service_error(e: ServiceError) -> NoReturn:
    """将 Service 层异常转换为 HTTPException

 带 error_code 的异常输出 dict detail（结构化 403，
 如余额不足附带 available/required）；其余保持字符串 detail 字节级兼容。
 """
    error_code = getattr(e, "error_code", None)
    if error_code:
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "error_code": error_code,
                "message": e.detail,
                **(getattr(e, "error_data", None) or {}),
            },
        )
    raise HTTPException(status_code=e.status_code, detail=e.detail)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """获取当前登录用户
    
    从请求头中提取JWT令牌并验证，返回对应的用户对象。
    
    Args:
        credentials: HTTP Bearer 认证凭据
        db: 数据库会话
        
    Returns:
        User: 当前登录用户
        
    Raises:
        HTTPException: 未认证或令牌无效时返回401
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭据",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    token = credentials.credentials
    payload = decode_token(token)
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的访问令牌",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # 检查令牌类型
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌类型错误",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # 获取用户
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌载荷无效",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    stmt = select(User).where(User.id == int(user_id))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # 会话强失效: 令牌版本与用户当前版本不一致(密码已重置/被管理员重置)则拒绝
    # 旧令牌无 ver 视为 0, 存量用户 token_version 默认 0, 兼容不掉线
    if int(payload.get("ver", 0) or 0) != int(getattr(user, "token_version", 0) or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="会话已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用"
        )
    
    set_log_user_id(user.id)
    return user


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """获取当前登录用户（可选）
    
    与get_current_user类似，但未登录时返回None而不是抛出异常。
    用于既支持游客访问又需要识别登录用户的场景。
    
    Args:
        credentials: HTTP Bearer 认证凭据
        db: 数据库会话
        
    Returns:
        Optional[User]: 当前登录用户或None
    """
    if not credentials:
        return None
    
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


async def require_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """要求管理员权限
    
    验证当前用户是否为管理员。
    
    Args:
        current_user: 当前登录用户
        
    Returns:
        User: 当前用户（如果是管理员）
        
    Raises:
        HTTPException: 非管理员用户返回403
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user


def get_pagination(
    page: int = Query(default=1, ge=1, description="页码（从1开始）"),
    page_size: int = Query(default=20, ge=1, le=500, description="每页数量")
) -> PaginationParams:
    """获取分页参数
    
    Args:
        page: 页码
        page_size: 每页数量
        
    Returns:
        PaginationParams: 分页参数对象
    """
    return PaginationParams(page=page, page_size=page_size)


def project_log_context(request: Request) -> Optional[int]:
    """日志上下文依赖: 从 Request.path_params 推断 project_id 并写入请求级 ContextVar。

    设计要点:
    - 通过 Request 而非 Path 取参, 不强制路径必须含 project_id, 因此可挂在 router 级 dependencies
    - 路径无 project_id 或解析失败时静默返回 None, 不影响业务
    - 用法（router 级 dependencies, 不侵入业务签名）:

        router = APIRouter(dependencies=[Depends(project_log_context)])

        @router.post("/projects/{project_id}/foo")
        async def foo(...):
            ...

    与 logger.contextualize 的关系:
    - 本依赖面向 HTTP 入站, 让所有请求处理过程中的日志自动带 project_id
    - 后台任务则由 worker.py / tasks/__init__ 用 contextualize 注入
    - 两路覆盖率叠加, 共同消除 system_error_logs.project_id 为 NULL 的盲区
    """
    raw = request.path_params.get("project_id")
    if raw is None:
        return None
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None
    set_log_project_id(pid)
    return pid


def get_optional_status(
    status: Optional[str] = Query(default=None, description="项目状态筛选")
) -> Optional[str]:
    """获取可选的状态筛选参数
    
    Args:
        status: 状态字符串
        
    Returns:
        Optional[str]: 状态值或 None
    """
    return status


def check_project_permission(
    project: Project,
    current_user: User,
    require_owner: bool = False,
) -> None:
    """检查项目权限（路由层薄包装，同步无 DB 操作）。

    实际判定逻辑下沉到 `app.core.permissions.check_project_permission`，
    本入口仅负责把领域异常翻译为 FastAPI `HTTPException`，以保持路由
    依赖注入语义。Service 层请直接 import core 版本，不要走这里绕一圈。

    Args:
        project: 项目对象
        current_user: 当前用户
        require_owner: 是否要求必须是所有者（管理员也不能操作）

    Raises:
        HTTPException: 无权限时抛出 403
    """
    try:
        _check_project_permission_core(project, current_user, require_owner=require_owner)
    except ForbiddenError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=e.detail,
        )
