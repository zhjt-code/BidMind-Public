"""P0-B: 统一尝试预算 / 端到端 deadline / 401 快速冷却 防御测试。

锁定契约:
- AsyncOpenAI 构造必须 max_retries=0(重试决策统一归 Dispatcher);
- Dispatcher 单次逻辑调用的物理尝试数受 LLM_MAX_ATTEMPTS_DEFAULT 封顶,
  不再默认磨穿全部通道;
- 端到端 deadline 到期即中止(排队/慢通道均受约束), 抛 TimeoutError 族;
- 401/403: 通道长冷却、不计熔断(consecutive_failures 不增),
  同一模型组累计 2 次后强制跨组;
- 预算总闸关闭时退回现状(最大尝试数=通道数)。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from openai import APIStatusError

from app.core.ai.attempt_budget import (
    AttemptBudget,
    DeadlineClock,
    LlmDeadlineExceededError,
    resolve_budget,
)
from app.core.ai.cloud_client import CloudClient
from app.core.ai.dispatcher import AIDispatcher


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


def _status_error(status: int, message: str = "err") -> APIStatusError:
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return APIStatusError(message, response=resp, body={"error": {"message": message}})


# ---------- SDK 隐式重试关闭 ----------

def test_async_openai_max_retries_is_zero() -> None:
    client = CloudClient(
        base_url="https://demo.example.com/v1",
        api_key="sk-test", model_name="demo", timeout=30.0,
    )
    assert client._client.max_retries == 0, (
        "SDK 隐式重试必须关闭, 否则物理请求次数不可预算"
    )


# ---------- 预算模型 ----------

def test_budget_clamps_to_channel_count() -> None:
    budget = AttemptBudget(max_attempts=6, deadline_seconds=None)
    assert budget.clamp_attempts(25) == 6
    assert budget.clamp_attempts(3) == 3


def test_budget_disabled_keeps_legacy_behavior() -> None:
    with patch("app.core.config.settings.LLM_ATTEMPT_BUDGET_ENABLED", False):
        budget = resolve_budget()
    assert budget.clamp_attempts(25) == 25
    assert budget.deadline_seconds is None


def test_deadline_clock_expiry() -> None:
    clock = DeadlineClock(deadline_seconds=0.01, started_at=time.monotonic() - 1)
    assert clock.expired() is True
    with pytest.raises(LlmDeadlineExceededError):
        clock.raise_if_expired("test")
    no_deadline = DeadlineClock.start(None)
    assert no_deadline.remaining() is None
    assert no_deadline.expired() is False


# ---------- Dispatcher 预算封顶 ----------

@pytest.mark.asyncio
async def test_dispatcher_attempts_capped_by_budget() -> None:
    dispatcher = AIDispatcher(purpose="review")
    clients = []
    for i in range(10):
        c = _FakeClient(f"m{i}", _status_error(500, "boom"))
        clients.append(c)
        dispatcher.register_channel(
            c, config_id=f"cfg{i}", key_index=0,
            model_group=f"group{i}", priority=100,
        )

    with patch("app.core.config.settings.LLM_MAX_ATTEMPTS_DEFAULT", 3), \
         patch("app.core.config.settings.LLM_CALL_DEADLINE_SECONDS", 900.0):
        with pytest.raises(APIStatusError):
            await dispatcher.chat(user_message="hi")

    total_calls = sum(c.call_count for c in clients)
    assert total_calls == 3, (
        f"预算=3 时物理尝试必须封顶为 3, 实际 {total_calls}"
    )


@pytest.mark.asyncio
async def test_dispatcher_legacy_when_budget_disabled() -> None:
    dispatcher = AIDispatcher(purpose="review")
    clients = []
    for i in range(4):
        c = _FakeClient(f"m{i}", _status_error(500, "boom"))
        clients.append(c)
        dispatcher.register_channel(
            c, config_id=f"cfg{i}", key_index=0,
            model_group=f"group{i}", priority=100,
        )

    with patch("app.core.config.settings.LLM_ATTEMPT_BUDGET_ENABLED", False):
        with pytest.raises(APIStatusError):
            await dispatcher.chat(user_message="hi")

    assert sum(c.call_count for c in clients) == 4, "总闸关闭应保持磨全部通道的现状"


# ---------- 端到端 deadline ----------

@pytest.mark.asyncio
async def test_deadline_aborts_slow_channel() -> None:
    dispatcher = AIDispatcher(purpose="review")

    async def _slow(**_kwargs: Any) -> str:
        await asyncio.sleep(30)
        return "too late"

    slow = _FakeClient("slow-model", _slow)
    dispatcher.register_channel(
        slow, config_id="cfg_slow", key_index=0,
        model_group="groupS", priority=0,
    )

    start = time.monotonic()
    with patch("app.core.config.settings.LLM_CALL_DEADLINE_SECONDS", 0.5):
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            await dispatcher.chat(user_message="hi")
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"deadline=0.5s 时必须快速中止, 实际耗时 {elapsed:.1f}s"


# ---------- 401/403 快速冷却 ----------

@pytest.mark.asyncio
async def test_auth_error_long_cooldown_without_circuit() -> None:
    dispatcher = AIDispatcher(purpose="review")

    a1 = _FakeClient("qwen-max", _status_error(401, "invalid api key"))
    a2 = _FakeClient("qwen-max", _status_error(401, "invalid api key"))
    a3 = _FakeClient("qwen-max", _status_error(401, "invalid api key"))
    b1 = _FakeClient("deepseek", "正常响应")
    for idx, c in enumerate((a1, a2, a3)):
        dispatcher.register_channel(
            c, config_id="cfgA", key_index=idx, model_group="groupA", priority=0,
        )
    dispatcher.register_channel(
        b1, config_id="cfgB", key_index=0, model_group="groupB", priority=100,
    )

    result = await dispatcher.chat(user_message="hi")
    assert result == "正常响应"

    # 同组累计 2 次 401 后必须跨组, 不磨第三把 key
    group_a_calls = a1.call_count + a2.call_count + a3.call_count
    assert group_a_calls == 2, f"groupA 应只尝试 2 次, 实际 {group_a_calls}"
    assert b1.call_count == 1

    tried_states = [
        ch for ch in dispatcher._channels
        if ch.model_group == "groupA" and ch.total_errors > 0
    ]
    assert len(tried_states) == 2
    for state in tried_states:
        assert state.consecutive_failures == 0, "401 不得计入熔断计数"
        assert state.circuit_open is False
        assert state.cooldown_until > time.monotonic(), "401 通道必须进入长冷却"
        assert state.last_rate_limit_kind == "auth_or_config"


@pytest.mark.asyncio
async def test_server_error_5xx_still_counts_failure() -> None:
    """对照组: 500 仍按普通故障计数(熔断路径不受 401 分支影响)"""
    dispatcher = AIDispatcher(purpose="review")
    a1 = _FakeClient("m1", _status_error(500, "server boom"))
    b1 = _FakeClient("m2", "正常响应")
    dispatcher.register_channel(
        a1, config_id="cfgA", key_index=0, model_group="groupA", priority=0,
    )
    dispatcher.register_channel(
        b1, config_id="cfgB", key_index=0, model_group="groupB", priority=100,
    )

    result = await dispatcher.chat(user_message="hi")
    assert result == "正常响应"
    state_a = next(ch for ch in dispatcher._channels if ch.client is a1)
    assert state_a.consecutive_failures == 1, "500 必须继续累计故障"
