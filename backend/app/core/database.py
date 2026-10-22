"""数据库连接和会话管理"""

import asyncio
import os
import socket
import sys
from contextlib import asynccontextmanager
from functools import wraps
from typing import AsyncGenerator, AsyncIterator, Awaitable, Callable, Optional, TypeVar

from loguru import logger
from sqlalchemy import event, select
from sqlalchemy.exc import InterfaceError as SAInterfaceError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Tests use several event loops (pytest-asyncio and TestClient's portal). A
# pooled asyncpg connection cannot be reused safely across those loops.
_pool_options = (
    {"poolclass": NullPool}
    if os.environ.get("DB_USE_NULL_POOL") == "1"
    else {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_recycle": settings.DB_POOL_RECYCLE,
        "pool_timeout": settings.DB_POOL_TIMEOUT,
    }
)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG_SQL,
    pool_pre_ping=True,
    **_pool_options,
)


# 客户端 OS 层 TCP keepalive
# 职责: 让 OS 内核每 60s 主动探测连接活性, 防止云端 NAT/SLB 关闭半开 TCP 后被连接池复用
# 不做: 处理连接生命周期 / 池策略 / 业务重试逻辑
@event.listens_for(engine.sync_engine, "connect")
def _enable_tcp_keepalive(dbapi_connection, connection_record) -> None:  # noqa: ARG001
    try:
        asyncpg_conn = getattr(dbapi_connection, "_connection", None)
        if asyncpg_conn is None:
            return
        transport = getattr(asyncpg_conn, "_transport", None)
        if transport is None:
            return
        sock = transport.get_extra_info("socket")
        if sock is None:
            return
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if sys.platform.startswith("linux"):
            if hasattr(socket, "TCP_KEEPIDLE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
            if hasattr(socket, "TCP_KEEPINTVL"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 20)
            if hasattr(socket, "TCP_KEEPCNT"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except Exception as exc:  # noqa: BLE001 - 兜底, 不影响连接建立
        logger.warning(f"TCP keepalive 设置失败 (忽略): {exc}")

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# 别名（用于后台任务等需要独立会话的场景）
async_session_factory = AsyncSessionLocal

# 声明基类
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """依赖注入:获取数据库会话
    
    Yields:
        AsyncSession: 数据库会话
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """初始化数据库: 创建初始管理员账户。

    Schema 完全由 Alembic 管理 (alembic upgrade head),
    不再在应用启动时执行 create_all 或 ALTER TABLE。
    """
    await create_initial_admin()


async def create_initial_admin() -> None:
    """创建初始管理员账户
    
    从环境变量读取管理员配置，如果管理员账户不存在则创建。
    """
    from app.core.config import settings
    from app.core.security import get_password_hash
    from app.models.user import User, UserRole
    
    async with AsyncSessionLocal() as session:
        try:
            # 检查是否已存在管理员账户
            stmt = select(User).where(User.username == settings.ADMIN_USERNAME)
            result = await session.execute(stmt)
            existing_admin = result.scalar_one_or_none()
            
            if existing_admin:
                logger.debug(f"管理员账户已存在: {settings.ADMIN_USERNAME}")
                return
            
            # 创建管理员账户
            admin_user = User(
                username=settings.ADMIN_USERNAME,
                email=settings.ADMIN_EMAIL,
                hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
                role=UserRole.ADMIN,
                is_active=True
            )
            
            session.add(admin_user)
            await session.commit()
            
            logger.info(f"已创建初始管理员账户: {settings.ADMIN_USERNAME}")
            
        except Exception as e:
            await session.rollback()
            logger.warning(f"创建初始管理员账户失败: {e}")


async def close_db() -> None:
    """关闭数据库连接"""
    await engine.dispose()


def engine_pool_status() -> dict:
    """返回当前异步引擎连接池快照, 用于诊断 QueuePool 耗尽问题。: 排查 QueuePool limit reached 时, 直接读取 pool 指标比解析
 pg_stat_activity 更精准 (后者会受其他 worker / 连接复用影响)。

 Returns:
 dict: 包含 size / checkedin / checkedout / overflow / status_text 字段;
 非 QueuePool 实现 (如 NullPool) 时返回 status_text + flag。
 """
    try:
        pool = engine.pool
        snapshot = {
            "status_text": pool.status() if hasattr(pool, "status") else "n/a",
        }
        for attr in ("size", "checkedin", "checkedout", "overflow"):
            if hasattr(pool, attr):
                try:
                    value = getattr(pool, attr)
                    snapshot[attr] = value() if callable(value) else value
                except Exception as inner:
                    snapshot[attr] = f"err:{inner}"
        return snapshot
    except Exception as e:
        return {"status_text": f"unavailable: {e}"}


# ===================================================================
# DB 死连接窄重试装饰器
# 职责: 把"从 pool 取到云端已关闭的死连接"这类纯基建抖动对调用方透明化
# 不做: 处理任何业务级异常(仅匹配 InterfaceError 且包含 "connection is closed");
#       不处理事务一致性(重试整段函数, 由调用方自身保证幂等)
# ===================================================================

_T = TypeVar("_T")

_DEAD_CONNECTION_SIGNATURE = "connection is closed"


def is_dead_connection_error(exc: BaseException) -> bool:
    """判定异常是否属于"连接已被远端关闭"的可重试基建抖动。

    职责: 让调用方在 except 中识别"DB 死连接"这种纯基建异常,
          以便交给重试装饰器/上游处理, 而非按业务异常落库 FAILED 状态。
    """
    if not isinstance(exc, SAInterfaceError):
        return False
    return _DEAD_CONNECTION_SIGNATURE in str(exc).lower()


def with_db_reconnect(
    max_retries: int = 1,
    retry_delay: float = 0.3,
) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """装饰 async 函数, 捕获 asyncpg `connection is closed` 异常后重试。

    Args:
        max_retries: 最大重试次数(不含首次调用), 默认 1 次
        retry_delay: 重试前等待秒数, 给 pool 留时间替换死连接
    """

    def decorator(
        func: Callable[..., Awaitable[_T]],
    ) -> Callable[..., Awaitable[_T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> _T:
            attempts_allowed = max_retries + 1
            for attempt in range(attempts_allowed):
                try:
                    return await func(*args, **kwargs)
                except SAInterfaceError as exc:
                    if attempt + 1 >= attempts_allowed or not is_dead_connection_error(exc):
                        raise
                    logger.warning(
                        "[with_db_reconnect] {fn} 检测到死连接, "
                        "{used}/{total} 次尝试后重试: {err}",
                        fn=func.__name__,
                        used=attempt + 1,
                        total=attempts_allowed,
                        err=str(exc)[:200],
                    )
                    await asyncio.sleep(retry_delay)
            raise RuntimeError(  # pragma: no cover - 循环必然在 try 内 return 或 raise
                f"with_db_reconnect: {func.__name__} 重试循环异常退出"
            )

        return wrapper

    return decorator


# ===================================================================
# 进程级 "DB 会话许可" 信号量 + db_session_scope 上下文管理器
# 职责: 给"并发循环里的短 session"提供一个进程内全局闸门, 让同时打开的
#       短 session 数显著小于连接池容量 (DB_POOL_SIZE), 防止跨 job/跨流程
# 并发协程突发占满 QueuePool (同源问题的最终根治)。
# 不做: 不限制 API 层 get_db 与外层编排 session (它们由 max_jobs / 请求并发
#       自然受限), 避免与并发短 session 形成嵌套等待死锁。
# ===================================================================


_db_session_admission_semaphore: Optional["asyncio.Semaphore"] = None  # type: ignore[name-defined]
_db_session_admission_limit: int = 0  # 与 _db_session_admission_semaphore 同步设置


def _ensure_session_admission_semaphore() -> "asyncio.Semaphore":
    """懒加载进程级许可信号量, 容量来自 settings.DB_SESSION_ADMISSION_LIMIT。"""
    global _db_session_admission_semaphore, _db_session_admission_limit
    if _db_session_admission_semaphore is None:
        limit = max(int(getattr(settings, "DB_SESSION_ADMISSION_LIMIT", 12)), 1)
        _db_session_admission_semaphore = asyncio.Semaphore(limit)
        _db_session_admission_limit = limit
    return _db_session_admission_semaphore


@asynccontextmanager
async def db_session_scope() -> AsyncIterator[AsyncSession]:
    """获取一条受许可信号量保护的短 session。

    职责: 先获许可、再 `async with AsyncSessionLocal()` 打开短 session, 退出时
          自动释放许可与连接, 保证并发循环里短 session 总数 ≤ 许可上限。
    不做: 不做事务边界管理 (调用方自行 commit/rollback), 不做业务异常分类。

    用法:
        async with db_session_scope() as session:
            ... # 仅在该作用域内执行 DB 操作, 出域立即归还连接

    注意: 严禁在 API 层 get_db 与各 task 的外层编排 session 上使用本助手,
    避免与并发循环里的短 session 通过同一信号量形成嵌套等待死锁。
    """
    semaphore = _ensure_session_admission_semaphore()
    await semaphore.acquire()
    try:
        async with AsyncSessionLocal() as session:
            yield session
    finally:
        semaphore.release()


def db_session_admission_status() -> dict:
    """返回许可信号量当前状态, 仅用于诊断/测试。

    Returns:
        dict: 含 limit / available / in_use 字段; 若信号量未初始化则 lazy=False。
        limit 反映实际信号量的初始容量 (与 _ensure_session_admission_semaphore
        创建时锁定的值一致), 与 settings 解耦, 兼容测试覆写场景。
    """
    if _db_session_admission_semaphore is None:
        return {"lazy": False, "limit": int(getattr(settings, "DB_SESSION_ADMISSION_LIMIT", 12))}
    available = _db_session_admission_semaphore._value  # type: ignore[attr-defined]
    limit = _db_session_admission_limit or int(getattr(settings, "DB_SESSION_ADMISSION_LIMIT", 12))
    return {
        "lazy": True,
        "limit": limit,
        "available": int(available),
        "in_use": max(limit - int(available), 0),
    }
