"""AI 设置跨进程运行时失效通知测试。

覆盖目标:
- 设置保存后广播的 payload 具备稳定合同。
- worker/API 收到 reload 事件后重建对应 Dispatcher。
- 优先级变更只热更新 channel priority, 不重建 Dispatcher。
- Redis 或消息异常不影响业务保存链路。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core import ai_settings


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


@pytest.mark.asyncio
async def test_publish_runtime_invalidate_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """广播 payload 必须携带 type/purpose/reload_dispatcher, 供其它进程判断刷新策略。"""
    fake_redis = _FakeRedis()

    async def _get_redis_pool() -> _FakeRedis:
        return fake_redis

    import app.core.redis as redis_mod

    monkeypatch.setattr(redis_mod, "get_redis_pool", _get_redis_pool)

    await ai_settings.publish_runtime_invalidate(
        purpose="review",
        reload_dispatcher=True,
    )

    assert len(fake_redis.published) == 1
    channel, raw_payload = fake_redis.published[0]
    assert channel == ai_settings.AI_SETTINGS_INVALIDATE_CHANNEL
    payload = json.loads(raw_payload)
    assert payload == {
        "type": "reload",
        "purpose": "review",
        "reload_dispatcher": True,
    }


@pytest.mark.asyncio
async def test_publish_runtime_invalidate_ignores_missing_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis 不可用时只降级记录日志, 不抛异常阻断保存路径。"""

    async def _get_redis_pool() -> None:
        return None

    import app.core.redis as redis_mod

    monkeypatch.setattr(redis_mod, "get_redis_pool", _get_redis_pool)

    await ai_settings.publish_runtime_invalidate(
        purpose="vision",
        reload_dispatcher=True,
    )


@pytest.mark.asyncio
async def test_reload_message_rebuilds_runtime_for_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reload 消息应重新读取持久化配置并重建对应用途的 Dispatcher。"""
    loaded_model = object()
    calls: list[tuple[Any, str | None]] = []

    monkeypatch.setattr(ai_settings, "load_ai_settings", lambda: loaded_model)
    monkeypatch.setattr(
        ai_settings,
        "_sync_to_runtime",
        lambda model, purpose=None: calls.append((model, purpose)),
    )

    await ai_settings._handle_runtime_invalidate_message(
        {"data": json.dumps({"type": "reload", "purpose": "judge"})}
    )

    assert calls == [(loaded_model, "judge")]


@pytest.mark.asyncio
async def test_priority_message_only_syncs_channel_priorities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """priority 消息只更新通道优先级, 不应重建 Dispatcher。"""
    priority_calls: list[str | None] = []
    rebuild_called = False

    def _sync_channel_priorities(purpose: str | None = None) -> None:
        priority_calls.append(purpose)

    def _sync_to_runtime(*_args: Any, **_kwargs: Any) -> None:
        nonlocal rebuild_called
        rebuild_called = True

    import app.core.ai.ai_factory as ai_factory

    monkeypatch.setattr(ai_factory, "sync_channel_priorities", _sync_channel_priorities)
    monkeypatch.setattr(ai_settings, "_sync_to_runtime", _sync_to_runtime)

    await ai_settings._handle_runtime_invalidate_message(
        {"data": json.dumps({"type": "priority", "purpose": "review"})}
    )

    assert priority_calls == ["review"]
    assert rebuild_called is False


@pytest.mark.asyncio
async def test_runtime_invalidate_message_tolerates_bad_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """异常消息必须被忽略, 不能让订阅循环退出。"""
    rebuild_called = False

    def _sync_to_runtime(*_args: Any, **_kwargs: Any) -> None:
        nonlocal rebuild_called
        rebuild_called = True

    monkeypatch.setattr(ai_settings, "_sync_to_runtime", _sync_to_runtime)

    await ai_settings._handle_runtime_invalidate_message({"data": "{bad-json"})
    await ai_settings._handle_runtime_invalidate_message(
        {"data": json.dumps({"type": "reload", "purpose": "unknown"})}
    )

    assert rebuild_called is False
