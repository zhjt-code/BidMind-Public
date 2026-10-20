"""Redis 连接管理 (ARQ 任务队列 + 心跳持久化)"""

import asyncio
from typing import Optional

import redis as sync_redis
from arq.connections import RedisSettings, ArqRedis, create_pool
from loguru import logger

from app.core.config import settings

REDIS_SETTINGS = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    database=settings.REDIS_DB,
)

PUBSUB_REDIS_SETTINGS = RedisSettings(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    database=settings.REDIS_DB,
    conn_timeout=5,
    conn_retries=5,
    retry_on_timeout=True,
    max_connections=1,
)

# -- async pool (for ARQ enqueue) --
_pool: Optional[ArqRedis] = None
_pool_lock = asyncio.Lock()


async def get_redis_pool() -> Optional[ArqRedis]:
    """获取 ARQ Redis 连接池 (单例, 连接失败返回 None)"""
    global _pool
    if _pool is not None:
        try:
            await _pool.ping()
            return _pool
        except Exception:
            _pool = None

    async with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            _pool = await create_pool(REDIS_SETTINGS)
            logger.info(
                f"Redis 连接池已创建: {settings.REDIS_HOST}:{settings.REDIS_PORT}"
            )
            return _pool
        except Exception as e:
            logger.warning(f"Redis 不可用, 任务将回退到 asyncio 模式: {e}")
            return None


async def close_redis_pool() -> None:
    """关闭 Redis 连接池"""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
        logger.info("Redis 连接池已关闭")


async def create_redis_pubsub_client() -> Optional[ArqRedis]:
    """创建独立 Pub/Sub Redis 客户端，避免占用 ARQ 队列连接池。"""
    try:
        client = await create_pool(PUBSUB_REDIS_SETTINGS)
        logger.info(
            f"Redis Pub/Sub 客户端已创建: {settings.REDIS_HOST}:{settings.REDIS_PORT}"
        )
        return client
    except Exception as e:
        logger.warning(f"Redis Pub/Sub 客户端创建失败: {e}")
        return None


# -- sync client (for heartbeat read/write from sync functions) --
_sync_client: Optional[sync_redis.Redis] = None


def get_sync_redis() -> Optional[sync_redis.Redis]:
    """获取同步 Redis 客户端 (心跳读写, 连接失败返回 None)"""
    global _sync_client
    if _sync_client is not None:
        try:
            _sync_client.ping()
            return _sync_client
        except Exception:
            _sync_client = None

    try:
        _sync_client = sync_redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        _sync_client.ping()
        return _sync_client
    except Exception:
        _sync_client = None
        return None
