""" 回归：Dispatcher CancelledError 显式捕获，active_requests 归零。

职责: 验证主调用路径与 UnsupportedResponseFormat 最终 fallback 路径
 在 CancelledError 时均能递减 active_requests 恰好一次后原样重抛。
不做: 不测试 Prompt / seed / extractor / DB / API。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")


class _FakeClient:
    """最小化假客户端，支持可配置行为。"""

    def __init__(self, model_name: str, base_url: str = "https://example.com/v1") -> None:
        self.base_url = base_url
        self.model_name = model_name
        self._last_usage = None

    async def chat_with_validation(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def chat_json(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def chat(self, **kwargs: Any) -> Any:
        raise NotImplementedError


def _make_dispatcher() -> Any:
    from app.core.ai.dispatcher import AIDispatcher
    return AIDispatcher(purpose="test_cancel")


@pytest.mark.asyncio
async def test_main_path_cancel_propagates() -> None:
    """主调用路径 CancelledError 原样重抛，不被 Exception 分支吞噬。"""
    dispatcher = _make_dispatcher()
    client = _FakeClient("qwen")

    async def _cancel(**kw: Any) -> Any:
        raise asyncio.CancelledError("mid-flight cancel")

    client.chat_with_validation = _cancel
    dispatcher.register_channel(client, "cfg1", key_index=0)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.chat_with_validation(
            user_message="test", response_model=MagicMock(),
        )


@pytest.mark.asyncio
async def test_main_path_cancel_restores_active_requests() -> None:
    """主调用路径 CancelledError 后 active_requests 恢复为 0。"""
    dispatcher = _make_dispatcher()
    client = _FakeClient("qwen")

    async def _cancel(**kw: Any) -> Any:
        raise asyncio.CancelledError("cancelled mid-flight")

    client.chat_with_validation = _cancel
    dispatcher.register_channel(client, "cfg1", key_index=0)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.chat_with_validation(
            user_message="test", response_model=MagicMock(),
        )

    assert dispatcher._channels[0].active_requests == 0, (
        f"CancelledError 后 active_requests 应为 0，"
        f"实际: {dispatcher._channels[0].active_requests}"
    )


@pytest.mark.asyncio
async def test_fallback_path_cancel_propagates() -> None:
    """UnsupportedResponseFormat 最终 fallback 路径的 CancelledError 原样重抛。"""
    from app.core.ai.cloud_client import UnsupportedResponseFormatError
    dispatcher = _make_dispatcher()
    client = _FakeClient("qwen")
    call_count = 0

    async def _behavior(**kw: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise UnsupportedResponseFormatError("not supported")
        raise asyncio.CancelledError("fallback cancel")

    client.chat_json = _behavior
    dispatcher.register_channel(client, "cfg1", key_index=0)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.chat_json(user_message="test", json_schema={"type": "object"})


@pytest.mark.asyncio
async def test_fallback_path_cancel_restores_active_requests() -> None:
    """UnsupportedResponseFormat fallback 路径 CancelledError 后 active_requests=0。"""
    from app.core.ai.cloud_client import UnsupportedResponseFormatError
    dispatcher = _make_dispatcher()
    client = _FakeClient("qwen")
    call_count = 0

    async def _behavior(**kw: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise UnsupportedResponseFormatError("not supported")
        raise asyncio.CancelledError("fallback cancel")

    client.chat_json = _behavior
    dispatcher.register_channel(client, "cfg1", key_index=0)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.chat_json(user_message="test", json_schema={"type": "object"})

    assert dispatcher._channels[0].active_requests == 0, (
        f"fallback CancelledError 后 active_requests 应为 0，"
        f"实际: {dispatcher._channels[0].active_requests}"
    )
