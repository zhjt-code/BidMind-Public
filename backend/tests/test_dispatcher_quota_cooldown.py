"""Dispatcher 429 子类冷却行为回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.dispatcher import AIDispatcher  # noqa: E402


class _FakeClient:
    def __init__(self, model_name: str, behavior: Any) -> None:
        self.base_url = "https://example.com/v1"
        self.model_name = model_name
        self._behavior = behavior
        self._last_usage = None
        self.call_count = 0

    async def chat(self, **kwargs: Any) -> Any:
        self.call_count += 1
        if isinstance(self._behavior, Exception):
            raise self._behavior
        return self._behavior


def _make_quota_error() -> APIStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status_code=429, request=request)
    return APIStatusError(
        "Error code: 429 - insufficient_quota",
        response=response,
        body={
            "error": {
                "message": "You exceeded your current quota",
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            }
        },
    )


@pytest.mark.asyncio
async def test_quota_exhausted_cools_channel_without_rate_limit_aimd() -> None:
    dispatcher = AIDispatcher(purpose="review")
    quota_client = _FakeClient("qwen-max", _make_quota_error())
    backup_client = _FakeClient("deepseek-chat", "ok")

    dispatcher.register_channel(
        quota_client,
        config_id="qwen",
        key_index=0,
        model_group="qwen",
        priority=0,
    )
    dispatcher.register_channel(
        backup_client,
        config_id="deepseek",
        key_index=0,
        model_group="deepseek",
        priority=100,
    )

    result = await dispatcher.chat("hello")

    assert result == "ok"
    assert quota_client.call_count == 1
    assert backup_client.call_count == 1

    quota_state = next(ch for ch in dispatcher._channels if ch.client is quota_client)
    controller_stats = dispatcher._get_controller().get_stats()

    assert quota_state.total_quota_exhausted == 1
    assert quota_state.total_rate_limits == 0
    assert quota_state.last_rate_limit_kind == "quota_exhausted"
    assert quota_state.cooldown_until > 0
    assert quota_state.is_selectable() is False
    assert controller_stats["rate_limited_count"] == 0
