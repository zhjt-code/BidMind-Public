""": 超时弃组对同 purpose 后续独立 dispatch 粘滞。

锁定契约:
- 第一次超时后，第二次独立 dispatch 起步即避开同一 model_name;
- extract purpose 的超时不污染 review purpose（同形态第二例）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from openai import APITimeoutError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.dispatcher import AIDispatcher
from app.core.ai.timeout_avoidance import current_avoids, record_timeout, reset_timeout_avoids


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


def _api_timeout() -> APITimeoutError:
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    return APITimeoutError(request=req)


def _two_group_dispatcher(purpose: str) -> tuple[AIDispatcher, _FakeClient, _FakeClient]:
    dispatcher = AIDispatcher(purpose=purpose)
    slow = _FakeClient("qwen-max", _api_timeout())
    fast = _FakeClient("doubao-seed", "ok-from-backup")
    dispatcher.register_channel(
        slow, config_id="cfgA", key_index=0, model_group="groupA", priority=0,
    )
    dispatcher.register_channel(
        fast, config_id="cfgB", key_index=0, model_group="groupB", priority=100,
    )
    return dispatcher, slow, fast


@pytest.mark.asyncio
async def test_second_dispatch_skips_recently_timed_out_model() -> None:
    reset_timeout_avoids()
    dispatcher, slow, fast = _two_group_dispatcher("review")

    with patch("app.core.config.settings.LLM_TIMEOUT_MAX_ATTEMPTS", 2):
        first = await dispatcher.chat(user_message="chapter-a")
        assert first == "ok-from-backup"
        assert slow.call_count == 1
        assert fast.call_count == 1

        second = await dispatcher.chat(user_message="chapter-b")

    assert second == "ok-from-backup"
    assert slow.call_count == 1, "第二次独立 dispatch 不得再先打刚超时的模型"
    assert fast.call_count == 2


@pytest.mark.asyncio
async def test_extract_timeout_does_not_stick_to_review() -> None:
    """同形态第二例: purpose 隔离，extract 超时不让 review 起步避让。"""
    reset_timeout_avoids()
    extract, extract_slow, extract_fast = _two_group_dispatcher("extract")
    review, review_slow, review_fast = _two_group_dispatcher("review")

    with patch("app.core.config.settings.LLM_TIMEOUT_MAX_ATTEMPTS", 2):
        await extract.chat(user_message="classify-batch")
        groups, models = current_avoids("extract")
        assert "groupA" in groups
        assert "qwen-max" in models
        review_groups, review_models = current_avoids("review")
        assert review_groups == set()
        assert review_models == set()

        result = await review.chat(user_message="chapter-judge")

    assert result == "ok-from-backup"
    assert extract_slow.call_count == 1
    assert extract_fast.call_count == 1
    assert review_slow.call_count == 1, "review 不受 extract 粘滞污染，仍从主组起步"
    assert review_fast.call_count == 1


def test_record_timeout_expires_after_ttl() -> None:
    reset_timeout_avoids()
    record_timeout("review", "qwen-max", "groupA", now=100.0)
    groups, models = current_avoids("review", now=100.1)
    assert groups == {"groupA"}
    assert models == {"qwen-max"}
    groups, models = current_avoids("review", now=100.1 + 181.0)
    assert groups == set()
    assert models == set()
