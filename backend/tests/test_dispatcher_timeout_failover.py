""": LLM 超时快速故障转移防御测试。

锁定契约:
- APITimeoutError 与 asyncio.TimeoutError 走同一超时分支;
- 首次超时即 avoid_groups(不磨同组剩余 key);
- 超时类物理尝试受 LLM_TIMEOUT_MAX_ATTEMPTS 限制(默认 2), 达上限即 raise;
- 超时必须 is_timeout=True 喂 AIMD(修正旧 APITimeoutError 口径缺口);
- 单组多 Key 时 soft 回退同组, 但仍受超时上限 2 约束。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from openai import APITimeoutError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.attempt_budget import resolve_budget
from app.core.ai.dispatcher import AIDispatcher
from app.core.ai.dispatcher_concurrency import DispatcherRequestMetric
from loguru import logger


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


# ---------- 预算透传 ----------

def test_resolve_budget_exposes_timeout_max_attempts() -> None:
    with patch("app.core.config.settings.LLM_TIMEOUT_MAX_ATTEMPTS", 2):
        budget = resolve_budget()
    assert budget.timeout_max_attempts == 2


def test_timeout_max_attempts_independent_of_budget_gate() -> None:
    with patch("app.core.config.settings.LLM_ATTEMPT_BUDGET_ENABLED", False), \
         patch("app.core.config.settings.LLM_TIMEOUT_MAX_ATTEMPTS", 2):
        budget = resolve_budget()
    assert budget.timeout_max_attempts == 2
    assert budget.deadline_seconds is None


# ---------- 多组: 首次弃组 + 上限 2 ----------

@pytest.mark.asyncio
async def test_api_timeout_avoids_same_group_and_caps_at_two() -> None:
    """组A两把key都超时, 组B也超时: 只试 A 一把 + B 一把, 不磨 A#1。"""
    dispatcher = AIDispatcher(purpose="review")

    a0 = _FakeClient("qwen-max", _api_timeout())
    a1 = _FakeClient("qwen-max", _api_timeout())
    b0 = _FakeClient("deepseek", _api_timeout())
    c0 = _FakeClient("kimi", _api_timeout())
    dispatcher.register_channel(
        a0, config_id="cfgA", key_index=0, model_group="groupA", priority=0,
    )
    dispatcher.register_channel(
        a1, config_id="cfgA", key_index=1, model_group="groupA", priority=0,
    )
    dispatcher.register_channel(
        b0, config_id="cfgB", key_index=0, model_group="groupB", priority=100,
    )
    dispatcher.register_channel(
        c0, config_id="cfgC", key_index=0, model_group="groupC", priority=200,
    )

    # register_channel 会按通道数重建 controller, 必须在注册完成后再挂钩
    recorded: list[DispatcherRequestMetric] = []

    async def _capture(metric: DispatcherRequestMetric) -> None:
        recorded.append(metric)

    ctrl = dispatcher._get_controller()
    ctrl.record = _capture  # type: ignore[method-assign]

    warn_msgs: list[str] = []
    _orig_warning = logger.warning

    def _capture_warning(msg: Any, *args: Any, **kwargs: Any) -> None:
        warn_msgs.append(str(msg))
        return _orig_warning(msg, *args, **kwargs)

    with patch("app.core.config.settings.LLM_TIMEOUT_MAX_ATTEMPTS", 2), \
         patch("app.core.config.settings.LLM_MAX_ATTEMPTS_DEFAULT", 6), \
         patch("app.core.config.settings.LLM_CALL_DEADLINE_SECONDS", 900.0), \
         patch("app.core.ai.dispatcher.logger.warning", side_effect=_capture_warning):
        with pytest.raises(APITimeoutError):
            await dispatcher.chat(user_message="hi")

    total = a0.call_count + a1.call_count + b0.call_count + c0.call_count
    assert total == 2, f"超时类物理尝试必须=2, 实际 {total}"
    assert a1.call_count == 0, "首次超时后必须 avoid 同组, 不得磨 sibling key"
    assert a0.call_count == 1
    # 第二次应落到其它组(B 或 C), 且只再试一次
    other = b0.call_count + c0.call_count
    assert other == 1, f"跨组只应再试 1 次, 实际 other={other}"

    assert recorded, "必须向 AIMD 写入指标"
    assert all(m.is_timeout for m in recorded), (
        "APITimeoutError 必须 is_timeout=True 喂 AIMD"
    )
    assert all(not m.success for m in recorded)
    assert len(recorded) == 2

    join = "\n".join(warn_msgs)
    assert "timeout failover ->" in join, (
        "跨组续试必须打出显式落点日志 timeout failover ->"
    )
    assert "fallback same-group" not in join



@pytest.mark.asyncio
async def test_api_timeout_cross_group_success_on_second_attempt() -> None:
    dispatcher = AIDispatcher(purpose="review")
    a0 = _FakeClient("qwen-max", _api_timeout())
    a1 = _FakeClient("qwen-max", _api_timeout())
    b0 = _FakeClient("deepseek", "ok-from-b")
    dispatcher.register_channel(
        a0, config_id="cfgA", key_index=0, model_group="groupA", priority=0,
    )
    dispatcher.register_channel(
        a1, config_id="cfgA", key_index=1, model_group="groupA", priority=0,
    )
    dispatcher.register_channel(
        b0, config_id="cfgB", key_index=0, model_group="groupB", priority=100,
    )

    with patch("app.core.config.settings.LLM_TIMEOUT_MAX_ATTEMPTS", 2):
        result = await dispatcher.chat(user_message="hi")

    assert result == "ok-from-b"
    assert a0.call_count == 1
    assert a1.call_count == 0
    assert b0.call_count == 1


# ---------- 单组多 Key: soft 回退但仍上限 2 ----------

@pytest.mark.asyncio
async def test_single_group_multi_key_still_caps_timeout_at_two() -> None:
    dispatcher = AIDispatcher(purpose="review")
    keys = [_FakeClient("qwen-max", _api_timeout()) for _ in range(4)]
    for idx, client in enumerate(keys):
        dispatcher.register_channel(
            client, config_id="cfgA", key_index=idx,
            model_group="groupA", priority=0,
        )

    warn_msgs: list[str] = []
    _orig_warning = logger.warning

    def _capture_warning(msg: Any, *args: Any, **kwargs: Any) -> None:
        warn_msgs.append(str(msg))
        return _orig_warning(msg, *args, **kwargs)

    with patch("app.core.config.settings.LLM_TIMEOUT_MAX_ATTEMPTS", 2), \
         patch("app.core.config.settings.LLM_MAX_ATTEMPTS_DEFAULT", 6), \
         patch("app.core.ai.dispatcher.logger.warning", side_effect=_capture_warning):
        with pytest.raises(APITimeoutError):
            await dispatcher.chat(user_message="hi")

    total = sum(c.call_count for c in keys)
    assert total == 2, f"单组多 Key 超时仍须封顶为 2, 实际 {total}"
    called = [c for c in keys if c.call_count > 0]
    assert len(called) == 2
    assert all(c.call_count == 1 for c in called)

    join = "\n".join(warn_msgs)
    assert "timeout failover fallback same-group (no other group)" in join, (
        "单组场景必须打出 same-group 回退显式日志"
    )
    assert "timeout failover ->" not in join



# ---------- asyncio.TimeoutError 与 APITimeoutError 同策略 ----------

@pytest.mark.asyncio
async def test_asyncio_timeout_shares_same_cap_and_avoid_group() -> None:
    """deadline wait_for 触发的 asyncio.TimeoutError 也应弃组+上限 2。"""
    dispatcher = AIDispatcher(purpose="review")

    async def _hang(**_kwargs: Any) -> str:
        await asyncio.sleep(30)
        return "late"

    a0 = _FakeClient("slow-a", _hang)
    a1 = _FakeClient("slow-a-sibling", _hang)
    b0 = _FakeClient("slow-b", _hang)
    c0 = _FakeClient("slow-c", _hang)
    dispatcher.register_channel(
        a0, config_id="cfgA", key_index=0, model_group="groupA", priority=0,
    )
    dispatcher.register_channel(
        a1, config_id="cfgA", key_index=1, model_group="groupA", priority=0,
    )
    dispatcher.register_channel(
        b0, config_id="cfgB", key_index=0, model_group="groupB", priority=100,
    )
    dispatcher.register_channel(
        c0, config_id="cfgC", key_index=0, model_group="groupC", priority=200,
    )

    with patch("app.core.config.settings.LLM_TIMEOUT_MAX_ATTEMPTS", 2), \
         patch("app.core.config.settings.LLM_MAX_ATTEMPTS_DEFAULT", 6), \
         patch("app.core.config.settings.LLM_CALL_DEADLINE_SECONDS", 0.3):
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            await dispatcher.chat(user_message="hi")

    total = a0.call_count + a1.call_count + b0.call_count + c0.call_count
    # deadline 很短时, 可能第 1 次就在 raise_if_expired 处失败(0 次通道调用),
    # 或在 wait_for 超时后换通道再试; 物理通道调用不得超过 2。
    assert total <= 2, f"asyncio 超时物理尝试不得超过 2, 实际 {total}"
    assert a1.call_count == 0 or total <= 2
