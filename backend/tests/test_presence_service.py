"""在线用户 PresenceService 单元测试"""

from __future__ import annotations

import pytest

from app.services import presence_service
from app.services.presence_service import PresenceService


class FakeRedis:
    """覆盖 PresenceService 使用的最小 Redis ZSET API"""

    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        zset = self._zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in zset:
                added += 1
            zset[member] = score
        return added

    async def zremrangebyscore(self, key: str, min_score, max_score) -> int:
        zset = self._zsets.setdefault(key, {})
        lower = float("-inf") if min_score == "-inf" else float(min_score)
        upper = float("inf") if max_score == "+inf" else float(max_score)
        stale_members = [
            member for member, score in zset.items() if lower <= score <= upper
        ]
        for member in stale_members:
            del zset[member]
        return len(stale_members)

    async def zcount(self, key: str, min_score, max_score) -> int:
        zset = self._zsets.setdefault(key, {})
        lower = float("-inf") if min_score == "-inf" else float(min_score)
        upper = float("inf") if max_score == "+inf" else float(max_score)
        return sum(1 for score in zset.values() if lower <= score <= upper)

    async def zrangebyscore(self, key: str, min_score, max_score) -> list[str]:
        zset = self._zsets.setdefault(key, {})
        lower = float("-inf") if min_score == "-inf" else float(min_score)
        upper = float("inf") if max_score == "+inf" else float(max_score)
        return [
            member
            for member, score in sorted(zset.items(), key=lambda item: item[1])
            if lower <= score <= upper
        ]


@pytest.mark.asyncio
async def test_mark_user_seen_counts_unique_users(monkeypatch) -> None:
    redis = FakeRedis()

    async def fake_get_redis_pool():
        return redis

    monkeypatch.setattr(presence_service, "get_redis_pool", fake_get_redis_pool)

    service = PresenceService(online_window_seconds=120)
    first = await service.mark_user_seen(7)
    second = await service.mark_user_seen(7)
    summary = await service.get_online_summary()
    users = await service.get_online_user_ids(candidate_user_ids=[7, 8])

    assert first.ok is True
    assert second.ok is True
    assert summary.degraded is False
    assert summary.online_count == 1
    assert users.online_user_ids == {7}
    all_online = await service.get_online_user_ids()
    assert all_online.online_user_ids == {7}


@pytest.mark.asyncio
async def test_get_online_summary_removes_stale_users(monkeypatch) -> None:
    redis = FakeRedis()

    async def fake_get_redis_pool():
        return redis

    monkeypatch.setattr(presence_service, "get_redis_pool", fake_get_redis_pool)
    monkeypatch.setattr(presence_service.time, "time", lambda: 1_000.0)

    service = PresenceService(online_window_seconds=120)
    await redis.zadd(presence_service.PRESENCE_ONLINE_USERS_KEY, {
        "1": 800.0,
        "2": 950.0,
    })

    summary = await service.get_online_summary()
    users = await service.get_online_user_ids(candidate_user_ids=[1, 2])

    assert summary.online_count == 1
    assert users.online_user_ids == {2}


@pytest.mark.asyncio
async def test_presence_service_degrades_when_redis_unavailable(monkeypatch) -> None:
    async def fake_get_redis_pool():
        return None

    monkeypatch.setattr(presence_service, "get_redis_pool", fake_get_redis_pool)

    service = PresenceService(online_window_seconds=120)
    write_result = await service.mark_user_seen(7)
    summary = await service.get_online_summary()
    users = await service.get_online_user_ids(candidate_user_ids=[7])

    assert write_result.ok is False
    assert write_result.degraded is True
    assert summary.degraded is True
    assert summary.online_count == 0
    assert users.degraded is True
    assert users.online_user_ids == set()
