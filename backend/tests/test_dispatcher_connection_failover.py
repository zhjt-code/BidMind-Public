""": 传输错误必须弃组，不得只磨同模型下一把 key。

问题形态: APIConnectionError 落入通用 Exception，_select_channel 仍锁绑定组。
夹具一: qwen 组 Connection error → 下一跳必须落到其它组。
夹具二: extract purpose 的传输失败不得污染 review 粘滞。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import APIConnectionError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.dispatcher import AIDispatcher
from app.core.ai.timeout_avoidance import reset_timeout_avoids


class _FakeClient:
    def __init__(self, model_name: str, behavior: Any) -> None:
        self.base_url = "https://example.com/v1"
        self.model_name = model_name
        self._behavior = behavior
        self._last_usage = None
        self.call_count = 0

    async def chat(self, **kwargs: Any) -> Any:
        self.call_count += 1
        result = self._behavior
        if callable(result):
            result = await result(**kwargs)
        if isinstance(result, Exception):
            raise result
        return result


def _connection_error() -> APIConnectionError:
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    return APIConnectionError(request=req)


@pytest.fixture(autouse=True)
def _reset_avoids() -> None:
    reset_timeout_avoids()
    yield
    reset_timeout_avoids()


@pytest.mark.asyncio
async def test_connection_error_skips_sibling_key_and_crosses_group() -> None:
    dispatcher = AIDispatcher(purpose="review")
    a0 = _FakeClient("qwen3.7-plus", _connection_error())
    a1 = _FakeClient("qwen3.7-plus", _connection_error())
    b0 = _FakeClient("deepseek-v4", "ok-from-other-group")
    dispatcher.register_channel(
        a0, config_id="review_cloud", key_index=0, model_group="qwen", priority=0,
    )
    dispatcher.register_channel(
        a1, config_id="review_cloud", key_index=2, model_group="qwen", priority=0,
    )
    dispatcher.register_channel(
        b0, config_id="review_alt", key_index=0, model_group="deepseek", priority=100,
    )

    result = await dispatcher.chat(user_message="hi")

    assert result == "ok-from-other-group"
    assert a0.call_count == 1
    assert a1.call_count == 0, "传输错误后不得磨同组下一把 key"
    assert b0.call_count == 1


@pytest.mark.asyncio
async def test_connection_error_sticky_skips_model_on_next_dispatch() -> None:
    dispatcher = AIDispatcher(purpose="review")
    slow = _FakeClient("qwen3.7-plus", _connection_error())
    fast = _FakeClient("deepseek-v4", "ok")
    dispatcher.register_channel(
        slow, config_id="cfgA", key_index=0, model_group="qwen", priority=0,
    )
    dispatcher.register_channel(
        fast, config_id="cfgB", key_index=0, model_group="deepseek", priority=100,
    )

    first = await dispatcher.chat(user_message="item-1")
    assert first == "ok"
    assert slow.call_count == 1
    assert fast.call_count == 1

    second = await dispatcher.chat(user_message="item-2")
    assert second == "ok"
    assert slow.call_count == 1, "后续独立 dispatch 不得再先打刚传输失败的模型"
    assert fast.call_count == 2


@pytest.mark.asyncio
async def test_connection_sticky_is_purpose_local() -> None:
    """夹具二: extract 的传输失败不得污染 review 粘滞。"""
    extract = AIDispatcher(purpose="extract")
    extract_fail = _FakeClient("qwen3.7-plus", _connection_error())
    extract_ok = _FakeClient("deepseek-v4", "extract-ok")
    extract.register_channel(
        extract_fail, config_id="extA", key_index=0, model_group="qwen", priority=0,
    )
    extract.register_channel(
        extract_ok, config_id="extB", key_index=0, model_group="deepseek", priority=100,
    )
    assert await extract.chat(user_message="extract") == "extract-ok"

    review = AIDispatcher(purpose="review")
    review_qwen = _FakeClient("qwen3.7-plus", "review-qwen-ok")
    review_alt = _FakeClient("deepseek-v4", "review-alt")
    review.register_channel(
        review_qwen, config_id="revA", key_index=0, model_group="qwen", priority=0,
    )
    review.register_channel(
        review_alt, config_id="revB", key_index=0, model_group="deepseek", priority=100,
    )

    assert await review.chat(user_message="review") == "review-qwen-ok"
    assert review_qwen.call_count == 1
    assert review_alt.call_count == 0
