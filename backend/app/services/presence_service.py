"""在线用户状态服务"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Sequence

from loguru import logger

from app.core.config import settings
from app.core.redis import get_redis_pool


PRESENCE_ONLINE_USERS_KEY = "presence:online_users"


@dataclass(frozen=True)
class PresenceWriteResult:
    """在线心跳写入结果"""

    ok: bool
    degraded: bool
    online_window_seconds: int


@dataclass(frozen=True)
class OnlinePresenceSummary:
    """在线用户统计结果"""

    online_count: int
    online_window_seconds: int
    degraded: bool


@dataclass(frozen=True)
class OnlinePresenceUsers:
    """在线用户 ID 查询结果"""

    online_user_ids: set[int]
    online_window_seconds: int
    degraded: bool


class PresenceService:
    """
    职责: 维护基于 Redis 活跃窗口的唯一在线用户集合。
    不做: 不签发令牌、不判断用户权限、不写入用户表或审计日志。
    """

    def __init__(self, online_window_seconds: int | None = None) -> None:
        self._online_window_seconds = int(
            online_window_seconds
            or getattr(settings, "USER_PRESENCE_ONLINE_WINDOW_SECONDS", 120)
        )

    async def mark_user_seen(self, user_id: int) -> PresenceWriteResult:
        """记录用户最近一次心跳时间"""
        redis = await get_redis_pool()
        if redis is None:
            return PresenceWriteResult(
                ok=False,
                degraded=True,
                online_window_seconds=self._online_window_seconds,
            )

        now = time.time()
        try:
            await redis.zadd(PRESENCE_ONLINE_USERS_KEY, {str(user_id): now})
            return PresenceWriteResult(
                ok=True,
                degraded=False,
                online_window_seconds=self._online_window_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - 在线统计允许降级, 不阻断业务
            logger.warning(f"在线用户心跳写入失败, 已降级: {exc}")
            return PresenceWriteResult(
                ok=False,
                degraded=True,
                online_window_seconds=self._online_window_seconds,
            )

    async def get_online_summary(self) -> OnlinePresenceSummary:
        """获取全局唯一在线用户数"""
        redis = await get_redis_pool()
        if redis is None:
            return OnlinePresenceSummary(
                online_count=0,
                online_window_seconds=self._online_window_seconds,
                degraded=True,
            )

        now = time.time()
        threshold = now - self._online_window_seconds
        try:
            await self._remove_stale_members(redis, now)
            online_count = int(
                await redis.zcount(PRESENCE_ONLINE_USERS_KEY, threshold, "+inf")
            )
            return OnlinePresenceSummary(
                online_count=online_count,
                online_window_seconds=self._online_window_seconds,
                degraded=False,
            )
        except Exception as exc:  # noqa: BLE001 - 在线统计允许降级, 不阻断用户管理页
            logger.warning(f"在线用户统计读取失败, 已降级: {exc}")
            return OnlinePresenceSummary(
                online_count=0,
                online_window_seconds=self._online_window_seconds,
                degraded=True,
            )

    async def get_online_user_ids(
        self,
        candidate_user_ids: Sequence[int] | None = None,
    ) -> OnlinePresenceUsers:
        """读取在线用户 ID；传入候选时只返回与候选的交集。"""
        redis = await get_redis_pool()
        if redis is None:
            return OnlinePresenceUsers(
                online_user_ids=set(),
                online_window_seconds=self._online_window_seconds,
                degraded=True,
            )

        now = time.time()
        threshold = now - self._online_window_seconds
        try:
            await self._remove_stale_members(redis, now)
            online_user_ids = await self._read_online_user_ids(redis, threshold)
            if candidate_user_ids is not None:
                online_user_ids = online_user_ids & {
                    int(user_id) for user_id in candidate_user_ids
                }
            return OnlinePresenceUsers(
                online_user_ids=online_user_ids,
                online_window_seconds=self._online_window_seconds,
                degraded=False,
            )
        except Exception as exc:  # noqa: BLE001 - 在线状态允许降级
            logger.warning(f"在线用户列表读取失败, 已降级: {exc}")
            return OnlinePresenceUsers(
                online_user_ids=set(),
                online_window_seconds=self._online_window_seconds,
                degraded=True,
            )

    async def is_user_online(self, user_id: int) -> bool:
        """判断单个用户是否处于在线窗口内"""
        result = await self.get_online_user_ids(candidate_user_ids=[user_id])
        return user_id in result.online_user_ids

    async def _remove_stale_members(self, redis, now: float) -> None:
        threshold = now - self._online_window_seconds
        await redis.zremrangebyscore(PRESENCE_ONLINE_USERS_KEY, "-inf", threshold)

    async def _read_online_user_ids(self, redis, threshold: float) -> set[int]:
        raw_members = await redis.zrangebyscore(
            PRESENCE_ONLINE_USERS_KEY,
            threshold,
            "+inf",
        )
        online_user_ids: set[int] = set()
        for raw_member in raw_members:
            if isinstance(raw_member, bytes):
                raw_member = raw_member.decode("utf-8")
            try:
                online_user_ids.add(int(raw_member))
            except (TypeError, ValueError):
                logger.warning(f"忽略非法在线用户 member: {raw_member!r}")
        return online_user_ids
