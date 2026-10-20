"""worker 存活键瞬时断档不得单次观测写终态。

问题形态: 间接证据（worker 存活键）单次缺失即判死；挽救链与判死链
共享 has_alive_worker_for，源抖动时两边同时失效。

夹具 1: review_project_job（本案例）
夹具 2: writing_compose_job（同形态不同实体）
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks import inspect_task_liveness  # noqa: E402
from app.core.tasks.lifecycle.liveness_evidence import (  # noqa: E402
    has_confirmed_pending_arq_job,
)
from app.core.tasks.lifecycle import (  # noqa: E402
    WORKER_MISSING_KEY_PREFIX,
    is_worker_confirmed_missing,
)


class _FakeRedis:
    """同步 Redis 子集：GET/SET NX EX/DELETE/EXPIRE。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expire_calls: list[tuple[str, int]] = []

    def get(self, key: str) -> Optional[str]:
        return self.store.get(key)

    def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: Optional[int] = None,
        **_kw: Any,
    ) -> Optional[bool]:
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed

    def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return key in self.store


def _missing_key(function_name: str) -> str:
    return f"{WORKER_MISSING_KEY_PREFIX}{function_name}"


def _fresh_arq_heartbeat(job_id: str = "job-live-001") -> dict[str, Any]:
    return {
        "queue_mode": "arq",
        "arq_job_id": job_id,
        "last_heartbeat": time.time() - 35,
        "stage": "步骤4-审查",
        "enqueued_at": time.time() - 300,
    }


def _stale_arq_heartbeat(job_id: str = "job-stale-001") -> dict[str, Any]:
    from app.core.tasks import HEARTBEAT_TIMEOUT

    return {
        "queue_mode": "arq",
        "arq_job_id": job_id,
        "last_heartbeat": time.time() - HEARTBEAT_TIMEOUT - 30,
        "stage": "queued",
        "enqueued_at": time.time() - 300,
    }


# ── is_worker_confirmed_missing ──────────────────────────────────────


@pytest.mark.parametrize(
    "function_name",
    ["review_project_job", "writing_compose_job"],
)
def test_first_missing_observation_is_not_confirmed(function_name: str) -> None:
    """首次观测缺失只记账，不得确认为死亡。审查与写标同一谓词。"""
    fake = _FakeRedis()
    t0 = 1_000_000.0
    with (
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
        patch("app.core.redis.get_sync_redis", return_value=fake),
    ):
        result = is_worker_confirmed_missing(function_name, grace_sec=90, now=t0)
    assert result is False
    assert _missing_key(function_name) in fake.store


@pytest.mark.parametrize(
    "function_name",
    ["review_project_job", "writing_compose_job"],
)
def test_sustained_missing_beyond_grace_is_confirmed(function_name: str) -> None:
    """持续缺失超过宽限才确认死亡。"""
    fake = _FakeRedis()
    t0 = 1_000_000.0
    fake.store[_missing_key(function_name)] = str(t0)
    with (
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
        patch("app.core.redis.get_sync_redis", return_value=fake),
    ):
        result = is_worker_confirmed_missing(
            function_name, grace_sec=90, now=t0 + 90
        )
    assert result is True


def test_alive_worker_clears_missing_marker() -> None:
    fake = _FakeRedis()
    key = _missing_key("review_project_job")
    fake.store[key] = str(time.time() - 120)
    with (
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=True),
        patch("app.core.redis.get_sync_redis", return_value=fake),
    ):
        result = is_worker_confirmed_missing("review_project_job", grace_sec=90)
    assert result is False
    assert key not in fake.store


def test_redis_error_is_not_confirmed_missing() -> None:
    """无法观测 ≠ 死亡；判死不可逆，Redis 异常必须 fail-open。"""
    with (
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
        patch(
            "app.core.redis.get_sync_redis",
            side_effect=ConnectionError("redis down"),
        ),
    ):
        result = is_worker_confirmed_missing("review_project_job", grace_sec=90)
    assert result is False


def test_worker_probe_error_is_not_confirmed_missing() -> None:
    with patch(
        "app.core.arq_runtime.has_alive_worker_for",
        side_effect=ConnectionError("worker scan failed"),
    ):
        result = is_worker_confirmed_missing("review_project_job", grace_sec=90)
    assert result is False


# ── inspect_task_liveness：心跳新鲜时不得凭瞬时 worker 键判死 ─────────


@pytest.mark.parametrize(
    ("task_name", "arq_function"),
    [
        ("review_project_3562", "review_project_job"),
        ("writing_compose_3562", "writing_compose_job"),
    ],
)
def test_fresh_heartbeat_first_worker_gap_stays_live(
    task_name: str,
    arq_function: str,
) -> None:
    fake = _FakeRedis()
    heartbeat = _fresh_arq_heartbeat()
    with (
        patch("app.core.tasks.get_task_heartbeat", return_value=heartbeat),
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
        patch("app.core.redis.get_sync_redis", return_value=fake),
    ):
        result = inspect_task_liveness(
            task_name,
            heartbeat_timeout=180,
            arq_function=arq_function,
        )
    assert result.state == "live"
    assert result.reason == "fresh_heartbeat"
    assert result.is_live is True


@pytest.mark.parametrize(
    ("task_name", "arq_function"),
    [
        ("review_project_3562", "review_project_job"),
        ("writing_compose_3562", "writing_compose_job"),
    ],
)
def test_fresh_heartbeat_confirmed_worker_missing_stays_live(
    task_name: str,
    arq_function: str,
) -> None:
    """: 心跳仍新鲜时，worker 缺失已确认也不得一票写 stale。

 夹具1 review_project_job；夹具2 writing_compose_job。
 """
    fake = _FakeRedis()
    fake.store[_missing_key(arq_function)] = str(time.time() - 91)
    heartbeat = _fresh_arq_heartbeat()
    with (
        patch("app.core.tasks.get_task_heartbeat", return_value=heartbeat),
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
        patch("app.core.redis.get_sync_redis", return_value=fake),
    ):
        result = inspect_task_liveness(
            task_name,
            heartbeat_timeout=180,
            arq_function=arq_function,
        )
    assert result.state == "live"
    assert result.reason == "fresh_heartbeat"
    assert result.is_live is True


# ── 挽救链：心跳过期 + 队列痕迹 + 瞬时断档仍挽救 ─────────────────────


def test_stale_heartbeat_rescues_when_job_pending_and_worker_gap_unconfirmed() -> None:
    heartbeat = _stale_arq_heartbeat()
    mock_redis = MagicMock()
    mock_redis.exists.return_value = 1
    with (
        patch("app.core.redis.get_sync_redis", return_value=mock_redis),
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
        patch(
            "app.core.tasks.lifecycle.is_worker_confirmed_missing",
            return_value=False,
        ),
    ):
        rescued = has_confirmed_pending_arq_job(
            heartbeat, arq_function="review_project_job"
        )
    assert rescued is True


def test_stale_heartbeat_still_rescues_when_worker_missing_within_window() -> None:
    """: ~630s 心跳龄仍小于无进展窗，确认缺失不得否决挽救。"""
    heartbeat = _stale_arq_heartbeat()
    mock_redis = MagicMock()
    mock_redis.exists.return_value = 1
    with (
        patch("app.core.redis.get_sync_redis", return_value=mock_redis),
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
        patch(
            "app.core.tasks.lifecycle.is_worker_confirmed_missing",
            return_value=True,
        ),
    ):
        rescued = has_confirmed_pending_arq_job(
            heartbeat, arq_function="writing_compose_job"
        )
    assert rescued is True


@pytest.mark.parametrize(
    "arq_function",
    ["review_project_job", "writing_compose_job"],
)
def test_zombie_heartbeat_does_not_rescue_when_worker_missing_confirmed(
    arq_function: str,
) -> None:
    """越过无进展窗后，确认缺失才把残留痕迹当僵尸。审查与写标同一谓词。"""
    heartbeat = {
        "queue_mode": "arq",
        "arq_job_id": "job-zombie-001",
        "last_heartbeat": time.time() - 1900,
        "stage": "步骤4-审查",
        "enqueued_at": time.time() - 2000,
    }
    mock_redis = MagicMock()
    mock_redis.exists.return_value = 1
    with (
        patch("app.core.redis.get_sync_redis", return_value=mock_redis),
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
        patch(
            "app.core.tasks.lifecycle.is_worker_confirmed_missing",
            return_value=True,
        ),
    ):
        rescued = has_confirmed_pending_arq_job(
            heartbeat, arq_function=arq_function
        )
    assert rescued is False


def test_inspect_stale_within_window_stays_live_despite_worker_missing() -> None:
    """状态接口 180s 墙钟过期后，有痕迹且未越无进展窗仍 live（云端误杀路径）。"""
    heartbeat = _stale_arq_heartbeat()
    mock_redis = MagicMock()
    mock_redis.exists.return_value = 1
    with (
        patch("app.core.tasks.get_task_heartbeat", return_value=heartbeat),
        patch("app.core.redis.get_sync_redis", return_value=mock_redis),
        patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
        patch(
            "app.core.tasks.lifecycle.is_worker_confirmed_missing",
            return_value=True,
        ),
    ):
        result = inspect_task_liveness(
            "review_project_3654",
            heartbeat_timeout=180,
            arq_function="review_project_job",
        )
    assert result.state == "live"
    assert result.reason == "arq_job_pending"
    assert result.is_live is True
