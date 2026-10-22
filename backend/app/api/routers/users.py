"""用户管理路由

提供管理员用户管理功能：用户列表、启用/禁用、角色修改等。
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_pagination, require_admin
from app.core.database import get_db
from app.core.security import get_password_hash
from app.models.user import User, UserRole
from app.schemas.auth import AdminPasswordResetResponse, UserResponse
from app.schemas.common import ApiResponse, PaginationParams
from app.schemas.presence import OnlineStatsResponse
from app.services.auth_service import AuthService
from app.services.presence_service import PresenceService

router = APIRouter(prefix="/users", tags=["用户管理"])


class AdminUserListItem(UserResponse):
    """管理员用户列表项"""
    is_online: bool = Field(default=False, description="是否处于在线窗口内")


class UserListResponse(BaseModel):
    """用户列表响应"""
    items: List[AdminUserListItem]
    total: int
    page: int
    page_size: int


class UserListFilters(BaseModel):
    """
    职责: 承载用户列表的查询筛选条件。
    不做: 不访问数据库或 Redis，不执行列表查询。
    """
    role: Optional[str] = None
    is_active: Optional[bool] = None
    is_online: Optional[bool] = None
    search: Optional[str] = None


def get_user_list_filters(
    role: Optional[str] = Query(None, description="角色筛选"),
    is_active: Optional[bool] = Query(None, description="状态筛选"),
    is_online: Optional[bool] = Query(None, description="仅当前在线用户"),
    search: Optional[str] = Query(None, description="搜索关键词（用户名/邮箱）"),
) -> UserListFilters:
    """组装用户列表查询筛选参数。"""
    return UserListFilters(
        role=role,
        is_active=is_active,
        is_online=is_online,
        search=search,
    )


class UserStatusUpdate(BaseModel):
    """用户状态更新请求"""
    is_active: bool = Field(..., description="是否启用")


class UserRoleUpdate(BaseModel):
    """用户角色更新请求"""
    role: str = Field(..., pattern="^(user|admin)$", description="用户角色")


class AdminUserCreate(BaseModel):
    """管理员创建用户请求"""
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: EmailStr = Field(..., description="邮箱")
    password: str = Field(..., min_length=8, description="密码")
    role: str = Field(default="user", pattern="^(user|admin)$", description="用户角色")


@router.post("", response_model=ApiResponse[UserResponse])
async def create_user(
    user_data: AdminUserCreate,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[UserResponse]:
    """管理员创建用户
    
    管理员可以直接创建新用户，包括指定角色。
    
    Args:
        user_data: 用户创建数据
        admin_user: 当前管理员用户
        db: 数据库会话
        
    Returns:
        ApiResponse[UserResponse]: 创建的用户信息
        
    Raises:
        HTTPException: 用户名或邮箱已存在时返回400
    """
    # 检查用户名是否已存在
    stmt = select(User).where(User.username == user_data.username)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已被注册"
        )
    
    # 检查邮箱是否已存在
    stmt = select(User).where(User.email == user_data.email)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="邮箱已被注册"
        )
    
    # 解析角色
    try:
        role = UserRole(user_data.role)
    except ValueError:
        role = UserRole.USER
    
    # 创建用户
    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
        role=role,
        is_active=True,
        email_verified=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    role_text = "管理员" if role == UserRole.ADMIN else "普通用户"
    logger.info(f"管理员 {admin_user.username} 创建了{role_text}: {user.username} (ID: {user.id})")
    
    return ApiResponse(
        code=200,
        message="用户创建成功",
        data=UserResponse.model_validate(user)
    )


@router.get("", response_model=ApiResponse[UserListResponse])
async def list_users(
    pagination: PaginationParams = Depends(get_pagination),
    filters: UserListFilters = Depends(get_user_list_filters),
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[UserListResponse]:
    """获取用户列表
    
    仅管理员可访问。支持分页、筛选和搜索。
    
    Args:
        pagination: 分页参数
        filters: 角色/启用状态/在线/关键词筛选
        admin_user: 当前管理员用户
        db: 数据库会话
        
    Returns:
        ApiResponse[UserListResponse]: 用户列表
    """
    # 构建查询
    query = select(User)
    count_query = select(func.count(User.id))
    
    # 角色筛选
    if filters.role:
        try:
            role_enum = UserRole(filters.role)
            query = query.where(User.role == role_enum)
            count_query = count_query.where(User.role == role_enum)
        except ValueError:
            pass
    
    # 启用状态筛选
    if filters.is_active is not None:
        query = query.where(User.is_active == filters.is_active)
        count_query = count_query.where(User.is_active == filters.is_active)

    # 在线筛选必须在分页前按 Redis 活跃窗口收敛，不能只装饰当前页
    page_online_ids: set[int] | None = None
    if filters.is_online:
        presence = await PresenceService().get_online_user_ids()
        if not presence.online_user_ids:
            return ApiResponse(
                code=200,
                message="获取成功",
                data=UserListResponse(
                    items=[],
                    total=0,
                    page=pagination.page,
                    page_size=pagination.page_size,
                ),
            )
        query = query.where(User.id.in_(presence.online_user_ids))
        count_query = count_query.where(User.id.in_(presence.online_user_ids))
        page_online_ids = presence.online_user_ids
    
    # 搜索
    if filters.search:
        search_pattern = f"%{filters.search}%"
        query = query.where(
            (User.username.ilike(search_pattern)) | 
            (User.email.ilike(search_pattern))
        )
        count_query = count_query.where(
            (User.username.ilike(search_pattern)) | 
            (User.email.ilike(search_pattern))
        )
    
    # 获取总数
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    
    # 分页查询
    offset = (pagination.page - 1) * pagination.page_size
    query = query.order_by(User.created_at.desc()).offset(offset).limit(pagination.page_size)
    
    result = await db.execute(query)
    users = result.scalars().all()

    if page_online_ids is None:
        page_online_ids = (
            await PresenceService().get_online_user_ids(
                candidate_user_ids=[u.id for u in users]
            )
        ).online_user_ids

    return ApiResponse(
        code=200,
        message="获取成功",
        data=UserListResponse(
            items=[
                AdminUserListItem.model_validate(u).model_copy(
                    update={"is_online": u.id in page_online_ids}
                )
                for u in users
            ],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size
        )
    )


@router.get("/online-stats", response_model=ApiResponse[OnlineStatsResponse])
async def get_online_stats(
    admin_user: User = Depends(require_admin),
) -> ApiResponse[OnlineStatsResponse]:
    """获取唯一在线用户数统计"""
    summary = await PresenceService().get_online_summary()
    return ApiResponse(
        code=200,
        message="获取成功" if not summary.degraded else "在线状态降级",
        data=OnlineStatsResponse(
            online_count=summary.online_count,
            online_window_seconds=summary.online_window_seconds,
            degraded=summary.degraded,
        ),
    )


@router.get("/{user_id}", response_model=ApiResponse[UserResponse])
async def get_user(
    user_id: int,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[UserResponse]:
    """获取用户详情
    
    Args:
        user_id: 用户ID
        admin_user: 当前管理员用户
        db: 数据库会话
        
    Returns:
        ApiResponse[UserResponse]: 用户详情
    """
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    
    return ApiResponse(
        code=200,
        message="获取成功",
        data=UserResponse.model_validate(user)
    )


@router.put("/{user_id}/status", response_model=ApiResponse[UserResponse])
async def update_user_status(
    user_id: int,
    status_data: UserStatusUpdate,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[UserResponse]:
    """更新用户状态（启用/禁用）
    
    Args:
        user_id: 用户ID
        status_data: 状态更新数据
        admin_user: 当前管理员用户
        db: 数据库会话
        
    Returns:
        ApiResponse[UserResponse]: 更新后的用户信息
    """
    # 不能禁用自己
    if user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能禁用自己的账户"
        )
    
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    
    user.is_active = status_data.is_active
    await db.commit()
    await db.refresh(user)
    
    action = "启用" if status_data.is_active else "禁用"
    logger.info(f"管理员 {admin_user.username} {action}了用户 {user.username}")
    
    return ApiResponse(
        code=200,
        message=f"用户已{action}",
        data=UserResponse.model_validate(user)
    )


@router.put("/{user_id}/role", response_model=ApiResponse[UserResponse])
async def update_user_role(
    user_id: int,
    role_data: UserRoleUpdate,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[UserResponse]:
    """更新用户角色
    
    Args:
        user_id: 用户ID
        role_data: 角色更新数据
        admin_user: 当前管理员用户
        db: 数据库会话
        
    Returns:
        ApiResponse[UserResponse]: 更新后的用户信息
    """
    # 不能修改自己的角色
    if user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能修改自己的角色"
        )
    
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    
    try:
        new_role = UserRole(role_data.role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的角色值"
        )
    
    old_role = user.role
    user.role = new_role
    await db.commit()
    await db.refresh(user)
    
    logger.info(
        f"管理员 {admin_user.username} 将用户 {user.username} "
        f"的角色从 {old_role.value} 修改为 {new_role.value}"
    )
    
    return ApiResponse(
        code=200,
        message="角色已更新",
        data=UserResponse.model_validate(user)
    )


@router.post("/{user_id}/reset-password", response_model=ApiResponse[AdminPasswordResetResponse])
async def reset_user_password(
    user_id: int,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[AdminPasswordResetResponse]:
    """管理员重置用户密码

    生成随机临时密码并返回,管理员需线下告知用户。

    Args:
        user_id: 目标用户ID
        admin_user: 当前管理员用户
        db: 数据库会话

    Returns:
        ApiResponse[AdminPasswordResetResponse]: 包含临时密码
    """
    if user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能重置自己的密码,请通过修改密码功能操作"
        )

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    service = AuthService(db)
    temp_password = await service.admin_reset_password(user, admin_user)

    return ApiResponse(
        code=200,
        message="密码已重置",
        data=AdminPasswordResetResponse(temporary_password=temp_password)
    )


@router.delete("/{user_id}", response_model=ApiResponse[None])
async def delete_user(
    user_id: int,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
) -> ApiResponse[None]:
    """删除用户
    
    谨慎操作：删除用户会导致其关联的项目失去所有者。
    建议优先使用禁用功能。
    
    Args:
        user_id: 用户ID
        admin_user: 当前管理员用户
        db: 数据库会话
        
    Returns:
        ApiResponse[None]: 操作结果
    """
    # 不能删除自己
    if user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除自己的账户"
        )
    
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    
    username = user.username
    await db.delete(user)
    await db.commit()
    
    logger.warning(f"管理员 {admin_user.username} 删除了用户 {username}")
    
    return ApiResponse(
        code=200,
        message="用户已删除",
        data=None
    )
