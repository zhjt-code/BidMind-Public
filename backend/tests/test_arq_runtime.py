"""ARQ 运行时辅助单元测试。

覆盖:
- 心跳信息的序列化/反序列化
- 新鲜度判定 (新鲜/过期)
- function_name 过滤
- can_use_arq 的三条件判定
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.arq_runtime import (  # noqa: E402
    ARQ_WORKER_ALIVE_KEY_PREFIX,
    _build_worker_info,
    _is_fresh,
    _parse_worker_info,
    can_use_arq,
    generate_worker_id,
    has_alive_worker_for_async,
    record_worker_heartbeat_async,
    scan_alive_workers_async,
)


def test_generate_worker_id_has_host_pid_shape() -> None:
    wid = generate_worker_id()
    parts = wid.split("-")
    assert len(parts) >= 3
    # 最后一段应为 6 字符短 uuid
    assert len(parts[-1]) == 6


def test_build_and_parse_worker_info_roundtrip() -> None:
    info = _build_worker_info("w-1", ["process_document_job", "review_project_job"])
    text = json.dumps(info)
    parsed = _parse_worker_info(text)
    assert parsed is not None
    assert parsed["worker_id"] == "w-1"
    assert "process_document_job" in parsed["functions"]
    assert isinstance(parsed["last_seen"], int)


def test_parse_worker_info_tolerates_bytes_and_junk() -> None:
    # bytes 输入
    info = _build_worker_info("w-2", ["fn"])
    parsed = _parse_worker_info(json.dumps(info).encode())
    assert parsed is not None and parsed["worker_id"] == "w-2"
    # 垃圾字符串返回 None
    assert _parse_worker_info("not-json") is None
    # None 返回 None
    assert _parse_worker_info(None) is None


def test_is_fresh_respects_window(monkeypatch) -> None:
    # 伪造 settings.ARQ_WORKER_FRESHNESS_WINDOW=10 秒

    fresh_info = {"last_seen": int(time.time())}
    stale_info = {"last_seen": int(time.time()) - 10_000}
    assert _is_fresh(fresh_info) is True
    assert _is_fresh(stale_info) is False


# 合法事件循环堵塞上限: encode_gate CPU/batch stall=120s, OCR HTTP=120s。
# 新鲜窗口短于该上限时，存活键过期会被当成执行体消失。
_LEGAL_EVENT_LOOP_STALL_SEC = 120


def test_freshness_window_covers_legal_event_loop_stall() -> None:
    """TTL/新鲜窗必须盖住进程内合法堵塞，禁止短于 encode/OCR 上限。"""
    from app.core.config import settings

    assert settings.ARQ_WORKER_FRESHNESS_WINDOW >= _LEGAL_EVENT_LOOP_STALL_SEC
    assert settings.ARQ_WORKER_HEARTBEAT_TTL >= settings.ARQ_WORKER_FRESHNESS_WINDOW
    assert settings.ARQ_WORKER_FRESHNESS_WINDOW >= int(settings.OCR_HTTP_TIMEOUT)


def test_cpu_encode_length_stall_still_fresh() -> None:
    """夹具1: CPU 编码卡环 70s（<120s 上限）时 last_seen 仍新鲜。"""
    info = {"last_seen": int(time.time()) - 70}
    assert _is_fresh(info) is True


def test_ocr_http_length_stall_still_fresh() -> None:
    """夹具2: OCR HTTP 卡环 100s（同形态不同实体）时 last_seen 仍新鲜。"""
    info = {"last_seen": int(time.time()) - 100}
    assert _is_fresh(info) is True


# 谓词层覆盖 (A1) ---

def test_should_wait_for_worker_covers_all_non_config_reasons() -> None:
    """形态 A 等待窗应覆盖 function_worker_missing / enqueue_error 等非配置类失败;
    prefer_arq_disabled 属配置层异常, 不应让任务死等。
    """
    from app.core.arq_runtime import ArqDispatchResult

    for reason in ("worker_missing", "function_worker_missing", "redis_unavailable", "enqueue_error"):
        result = ArqDispatchResult(queued=False, function_name="f", reason=reason)
        assert result.should_wait_for_worker is True, reason

    for reason in ("prefer_arq_disabled", "available", "queued"):
        result = ArqDispatchResult(
            queued=(reason == "queued"),
            function_name="f",
            reason=reason,
            job_id="j" if reason == "queued" else None,
        )
        assert result.should_wait_for_worker is False, reason


def test_allows_in_process_fallback_only_when_arq_disabled_and_flag_on(
    monkeypatch,
) -> None:
    """同进程 fallback 仅在 ARQ 没启用 (prefer_arq_disabled) 且开关打开时允许。
 ARQ 已配置但故障时永不挪到 API 进程。
 """
    from app.core.arq_runtime import ArqDispatchResult

    monkeypatch.setattr(
        "app.core.arq_runtime.settings.BACKGROUND_FALLBACK_ENABLED", True, raising=False
    )
    disabled = ArqDispatchResult(queued=False, function_name="f", reason="prefer_arq_disabled")
    assert disabled.allows_in_process_fallback is True

    # 任意其他 reason 都不允许
    for reason in ("worker_missing", "function_worker_missing", "enqueue_error", "queued"):
        result = ArqDispatchResult(
            queued=(reason == "queued"),
            function_name="f",
            reason=reason,
            job_id="j" if reason == "queued" else None,
        )
        assert result.allows_in_process_fallback is False, reason

    monkeypatch.setattr(
        "app.core.arq_runtime.settings.BACKGROUND_FALLBACK_ENABLED", False, raising=False
    )
    assert disabled.allows_in_process_fallback is False


@pytest.mark.asyncio
async def test_scan_alive_workers_async_filters_stale() -> None:
    class _FakeRedis:
        def __init__(self, entries: dict[str, str]):
            self._entries = entries

        async def scan_iter(self, match: str):
            for k in list(self._entries.keys()):
                if k.startswith(match.rstrip("*")):
                    yield k

        async def get(self, key: str):
            return self._entries.get(key)

    now = int(time.time())
    fake = _FakeRedis({
        f"{ARQ_WORKER_ALIVE_KEY_PREFIX}fresh-1": json.dumps({
            "worker_id": "fresh-1",
            "host": "h",
            "pid": 1,
            "functions": ["process_document_job"],
            "last_seen": now,
        }),
        f"{ARQ_WORKER_ALIVE_KEY_PREFIX}stale-1": json.dumps({
            "worker_id": "stale-1",
            "host": "h",
            "pid": 2,
            "functions": ["process_document_job"],
            "last_seen": now - 100000,
        }),
    })

    workers = await scan_alive_workers_async(fake)
    ids = [w["worker_id"] for w in workers]
    assert "fresh-1" in ids
    assert "stale-1" not in ids


@pytest.mark.asyncio
async def test_has_alive_worker_for_async_filters_by_function() -> None:
    class _FakeRedis:
        def __init__(self, entries: dict[str, str]):
            self._entries = entries

        async def scan_iter(self, match: str):
            for k in list(self._entries.keys()):
                if k.startswith(match.rstrip("*")):
                    yield k

        async def get(self, key: str):
            return self._entries.get(key)

    now = int(time.time())
    fake = _FakeRedis({
        f"{ARQ_WORKER_ALIVE_KEY_PREFIX}w-a": json.dumps({
            "worker_id": "w-a",
            "host": "h",
            "pid": 1,
            "functions": ["review_project_job"],
            "last_seen": now,
        }),
    })

    assert await has_alive_worker_for_async(fake, "review_project_job") is True
    assert await has_alive_worker_for_async(fake, "process_document_job") is False
    # 未指定 function 只要有任意新鲜 worker 即返回 True
    assert await has_alive_worker_for_async(fake, None) is True


@pytest.mark.asyncio
async def test_record_worker_heartbeat_async_handles_none_client() -> None:
    # Redis 不可用时应静默返回 False 而非抛异常
    ok = await record_worker_heartbeat_async(None, "w-x", ["fn"])
    assert ok is False


@pytest.mark.asyncio
async def test_can_use_arq_returns_false_when_switch_off(monkeypatch) -> None:
    import app.core.arq_runtime as ar

    monkeypatch.setattr(ar.settings, "VECTOR_PREFER_ARQ", False, raising=False)
    assert await can_use_arq("process_document_job") is False


@pytest.mark.asyncio
async def test_can_use_arq_returns_false_when_no_pool(monkeypatch) -> None:
    import app.core.arq_runtime as ar

    monkeypatch.setattr(ar.settings, "VECTOR_PREFER_ARQ", True, raising=False)
    # Redis 池返回 None → False
    async def _no_pool():
        return None

    monkeypatch.setattr("app.core.redis.get_redis_pool", _no_pool)
    assert await can_use_arq("process_document_job") is False


@pytest.mark.asyncio
async def test_can_use_arq_requires_alive_worker(monkeypatch) -> None:
    import app.core.arq_runtime as ar

    monkeypatch.setattr(ar.settings, "VECTOR_PREFER_ARQ", True, raising=False)

    class _FakePool:
        async def scan_iter(self, match):
            # 返回空 → 无 worker
            if False:
                yield ""
            return

        async def get(self, key):
            return None

        async def ping(self):
            return True

    async def _pool():
        return _FakePool()

    monkeypatch.setattr("app.core.redis.get_redis_pool", _pool)
    assert await can_use_arq("process_document_job") is False
