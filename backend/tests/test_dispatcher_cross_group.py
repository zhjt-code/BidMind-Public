"""Dispatcher 跨组 / 语义异常处置 回归测试。

锁定契约:
- `UnsupportedResponseFormatError`: 不 record_failure(不熔断),
 且把当前通道所在模型组加入 avoid_groups, 优先跨组而非再磨同组 key。
- `json.JSONDecodeError`: 语义错误, 不 record_failure, 不切换通道,
 直接上抛给上层 fail-safe(Judge/CoVe 等)。
- `LlmContentValidationError`: 内容校验失败, 不 record_failure(不熔断),
 加 avoid_groups 强制跨组换模型, 不磨同组剩余 key。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.cloud_client import (
    LlmContentValidationError,
    UnsupportedResponseFormatError,
)
from app.core.ai.dispatcher import AIDispatcher
from app.core.ai.llm_errors import EmptyLLMResponseError


class _FakeClient:
    """最小化假客户端: 每个方法返回预设 behavior(Exception 或值/callable)."""

    def __init__(self, model_name: str, behavior: Any,
                 base_url: str = "https://example.com/v1") -> None:
        self.base_url = base_url
        self.model_name = model_name
        self._behavior = behavior
        self._last_usage = None
        self.call_count = 0
        self.last_kwargs: dict[str, Any] = {}

    async def _run(self, **kwargs: Any) -> Any:
        self.call_count += 1
        self.last_kwargs = kwargs
        result = self._behavior
        if callable(result):
            result = await result(**kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    async def chat(self, **kwargs: Any) -> Any:
        return await self._run(**kwargs)

    async def chat_json(self, **kwargs: Any) -> Any:
        return await self._run(**kwargs)

    async def chat_with_validation(self, **kwargs: Any) -> Any:
        return await self._run(**kwargs)


@pytest.mark.asyncio
async def test_unsupported_response_format_forces_cross_group_failover() -> None:
    dispatcher = AIDispatcher(purpose="judge")

    # 组 A: 两个通道都抛 UnsupportedResponseFormatError (模拟整组 provider 不支持)
    a1 = _FakeClient("qwen-max", UnsupportedResponseFormatError("rf unsupported"))
    a2 = _FakeClient("qwen-max", UnsupportedResponseFormatError("rf unsupported"))
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(a2, config_id="groupA", key_index=1,
                                model_group="groupA", priority=0)

    # 组 B: 能正常返回 JSON dict
    b1 = _FakeClient("deepseek-chat", {"verdict": "agree", "confidence": 0.9, "reason": "ok"})
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    result = await dispatcher.chat_json(
        user_message="please reply json",
        json_schema={"type": "json_object"},
    )

    assert isinstance(result, dict)
    assert result["verdict"] == "agree"

    # 只有 1 个 A 组通道被尝试, 下一次就跨到 B 组, 不再磨 a2
    assert a1.call_count == 1, f"expect 1, got {a1.call_count}"
    assert a2.call_count == 0, "avoid_groups should skip sibling key in same group"
    assert b1.call_count == 1
    assert a1.last_kwargs["propagate_unsupported"] is True

    # UnsupportedResponseFormat 不计通道故障
    state_a1 = next(ch for ch in dispatcher._channels if ch.client is a1)
    state_a2 = next(ch for ch in dispatcher._channels if ch.client is a2)
    assert state_a1.consecutive_failures == 0
    assert state_a1.circuit_open is False
    assert state_a2.consecutive_failures == 0


@pytest.mark.asyncio
async def test_json_decode_error_is_raised_without_failover_or_failure_counting() -> None:
    dispatcher = AIDispatcher(purpose="judge")

    decode_err = json.JSONDecodeError("Expecting value", "not-json", 0)
    a1 = _FakeClient("qwen-max", decode_err)
    # 备份组; 若 dispatcher 误切组则会被调用
    b1 = _FakeClient("deepseek-chat", {"ok": True})

    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    with pytest.raises(json.JSONDecodeError):
        await dispatcher.chat_json(
            user_message="please reply json",
            json_schema={"type": "json_object"},
        )

    # 只尝试了 groupA 一次, 没有切到 groupB
    assert a1.call_count == 1
    assert b1.call_count == 0

    # 未计通道故障, 没熔断
    state_a1 = next(ch for ch in dispatcher._channels if ch.client is a1)
    assert state_a1.consecutive_failures == 0
    assert state_a1.circuit_open is False


@pytest.mark.asyncio
async def test_unsupported_response_format_all_groups_falls_back_without_circuit_break() -> None:
    """全部组都不支持 response_format 时, 最终回退普通 chat,
    且没有任何通道被熔断(避免污染全局状态)。"""
    dispatcher = AIDispatcher(purpose="judge")

    a1 = _FakeClient("qwen-max", UnsupportedResponseFormatError("rf unsupported"),
                     base_url="https://qwen.example.com/v1")

    async def _unsupported_then_plain_chat(**kwargs: Any) -> Any:
        if kwargs.get("propagate_unsupported"):
            raise UnsupportedResponseFormatError("rf unsupported")
        return {"verdict": "agree", "fallback": True}

    b1 = _FakeClient("kimi-k2.5", _unsupported_then_plain_chat,
                     base_url="https://kimi.example.com/v1")
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    result = await dispatcher.chat_json(
        user_message="please reply json",
        json_schema={"type": "json_object"},
    )

    assert result == {"verdict": "agree", "fallback": True}
    assert a1.call_count == 1
    assert b1.call_count == 2

    for ch in dispatcher._channels:
        assert ch.consecutive_failures == 0
        assert ch.circuit_open is False


# ---------------------------------------------------------------------------
# chat_with_validation + json_schema 必须注入 propagate_unsupported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_with_validation_injects_propagate_unsupported() -> None:
    """严格 schema 任务经 chat_with_validation 分发时, 能力不兼容信号
    必须上抛给 dispatcher 同秒跨组, 不得在原模型上退化为自由文本。"""
    dispatcher = AIDispatcher(purpose="review")

    a1 = _FakeClient("deepseek-chat", UnsupportedResponseFormatError("rf unsupported"))
    b1 = _FakeClient("qwen-max", {"scheme": "s1", "lot": "__single__"})
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    result = await dispatcher.chat_with_validation(
        user_message="extract facts",
        response_model=dict,
        json_schema={"type": "json_schema"},
    )

    assert result == {"scheme": "s1", "lot": "__single__"}
    assert a1.call_count == 1
    assert b1.call_count == 1
    # 关键断言: chat_with_validation 路径同样注入 propagate_unsupported
    assert a1.last_kwargs["propagate_unsupported"] is True
    # 能力不兼容不计通道故障
    state_a1 = next(ch for ch in dispatcher._channels if ch.client is a1)
    assert state_a1.consecutive_failures == 0
    assert state_a1.circuit_open is False


@pytest.mark.asyncio
async def test_chat_with_validation_all_unsupported_falls_back_without_propagate() -> None:
    """全部组都不支持 response_format 时, 最终回退同方法且 propagate=False。"""
    dispatcher = AIDispatcher(purpose="review")

    a1 = _FakeClient("deepseek-chat", UnsupportedResponseFormatError("rf unsupported"),
                     base_url="https://ds.example.com/v1")

    async def _unsupported_then_plain(**kwargs: Any) -> Any:
        if kwargs.get("propagate_unsupported"):
            raise UnsupportedResponseFormatError("rf unsupported")
        return {"scheme": "s1", "lot": "__single__", "fallback": True}

    b1 = _FakeClient("qwen-max", _unsupported_then_plain,
                     base_url="https://qwen.example.com/v1")
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    result = await dispatcher.chat_with_validation(
        user_message="extract facts",
        response_model=dict,
        json_schema={"type": "json_schema"},
    )

    assert result == {"scheme": "s1", "lot": "__single__", "fallback": True}
    assert a1.call_count == 1
    assert b1.call_count == 2
    assert b1.last_kwargs["propagate_unsupported"] is False

    for ch in dispatcher._channels:
        assert ch.consecutive_failures == 0
        assert ch.circuit_open is False


# ---------------------------------------------------------------------------
# 内容校验失败必须跨组换模型, 不磨同组 key
# ---------------------------------------------------------------------------


def test_llm_content_validation_error_is_value_error() -> None:
    """向后兼容契约: 既有调用方 catch ValueError 必须仍能捕获。"""
    assert issubclass(LlmContentValidationError, ValueError)


@pytest.mark.asyncio
async def test_content_validation_error_forces_cross_group_failover() -> None:
    """ 场景: deepseek 组温度0确定性输出形状错误, 换同组 key 必然同败;
 dispatcher 必须首败即跨组换模型, 不再磨同组剩余 key。"""
    dispatcher = AIDispatcher(purpose="review")

    err = LlmContentValidationError(
        "云端LLM调用 1 次后仍无法获得有效响应: 5 validation errors for FullCandidateScope"
    )
    a1 = _FakeClient("deepseek-chat", err)
    a2 = _FakeClient("deepseek-chat", err)
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(a2, config_id="groupA", key_index=1,
                                model_group="groupA", priority=0)

    b1 = _FakeClient("qwen-max", {"scheme": "s1", "lot": "__single__"})
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    result = await dispatcher.chat_with_validation(
        user_message="extract facts",
        response_model=dict,  # _FakeClient 不消费该参数
    )

    assert result == {"scheme": "s1", "lot": "__single__"}
    # 组 A 只被尝试 1 次即跨组, 同组第二个 key 不再被磨
    assert a1.call_count + a2.call_count == 1
    assert b1.call_count == 1

    # 内容校验失败不计通道故障、不熔断
    for ch in dispatcher._channels:
        assert ch.consecutive_failures == 0
        assert ch.circuit_open is False


@pytest.mark.asyncio
async def test_content_validation_error_avoids_bound_project_group() -> None:
    """真实项目路径: project_id 已绑定模型组时, avoid_groups 仍必须立即跨组。"""
    dispatcher = AIDispatcher(purpose="review")

    err = LlmContentValidationError(
        "云端LLM调用 1 次后仍无法获得有效响应: 5 validation errors for FullCandidateScope"
    )
    a1 = _FakeClient("deepseek-chat", err)
    a2 = _FakeClient("deepseek-chat", err)
    b1 = _FakeClient("qwen-max", {"scheme": "s1", "lot": "__single__"})
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(a2, config_id="groupA", key_index=1,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    # 模拟真实日志中的项目绑定状态: 项目2036已经绑定 deepseek 模型组。
    dispatcher._project_bindings[2036] = ("groupA", 0.0)

    result = await dispatcher._dispatch(
        "chat_with_validation",
        project_id=2036,
        user_message="extract facts",
        response_model=dict,
    )

    assert result == {"scheme": "s1", "lot": "__single__"}
    assert a1.call_count + a2.call_count == 1
    assert b1.call_count == 1


# ---------------------------------------------------------------------------
# 截断扩容知识跨通道续用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncation_budget_carried_across_groups() -> None:
    """通道内确认初始 max_tokens 不足后, 下一通道必须从扩容后的预算起步,
 不得从初始值重新截断(: 项目2231 L3 逐模型重复截断)。"""
    dispatcher = AIDispatcher(purpose="review")

    err = LlmContentValidationError(
        "云端LLM调用 2 次后仍无法获得有效响应: Expecting value",
        suggested_max_tokens=8192,
    )
    a1 = _FakeClient("deepseek-v4-pro", err)
    b1 = _FakeClient("glm-5.1", {"scheme": "s1", "lot": "__single__"})
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    result = await dispatcher.chat_with_validation(
        user_message="extract facts",
        response_model=dict,
        max_tokens=5000,
    )

    assert result == {"scheme": "s1", "lot": "__single__"}
    assert a1.last_kwargs["max_tokens"] == 5000
    assert b1.last_kwargs["max_tokens"] == 8192, (
        f"下一通道应续用扩容预算8192, 实际: {b1.last_kwargs.get('max_tokens')}"
    )


@pytest.mark.asyncio
async def test_suggested_budget_never_downgrades_original() -> None:
    """建议值低于当前请求预算时不得反向缩容。"""
    dispatcher = AIDispatcher(purpose="review")

    err = LlmContentValidationError(
        "云端LLM调用 2 次后仍无法获得有效响应: Expecting value",
        suggested_max_tokens=4000,
    )
    a1 = _FakeClient("deepseek-v4-pro", err)
    b1 = _FakeClient("glm-5.1", {"scheme": "s1", "lot": "__single__"})
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    result = await dispatcher.chat_with_validation(
        user_message="extract facts",
        response_model=dict,
        max_tokens=5000,
    )

    assert result == {"scheme": "s1", "lot": "__single__"}
    assert b1.last_kwargs["max_tokens"] == 5000


@pytest.mark.asyncio
async def test_validation_failure_without_suggestion_keeps_budget() -> None:
    """纯 schema 校验失败(无 suggested_max_tokens)时预算保持不变。"""
    dispatcher = AIDispatcher(purpose="review")

    err = LlmContentValidationError(
        "云端LLM调用 1 次后仍无法获得有效响应: 4 validation errors"
    )
    a1 = _FakeClient("qwen-max", err)
    b1 = _FakeClient("glm-5.1", {"scheme": "s1", "lot": "__single__"})
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    result = await dispatcher.chat_with_validation(
        user_message="extract facts",
        response_model=dict,
        max_tokens=5000,
    )

    assert result == {"scheme": "s1", "lot": "__single__"}
    assert b1.last_kwargs["max_tokens"] == 5000


@pytest.mark.asyncio
async def test_content_validation_error_all_groups_exhausted_raises() -> None:
    """所有组都内容校验失败时, 上抛 LlmContentValidationError 给调用方 fail-closed。"""
    dispatcher = AIDispatcher(purpose="review")

    err = LlmContentValidationError("云端LLM调用 1 次后仍无法获得有效响应: schema mismatch")
    a1 = _FakeClient("deepseek-chat", err)
    b1 = _FakeClient("qwen-max", err)
    dispatcher.register_channel(a1, config_id="groupA", key_index=0,
                                model_group="groupA", priority=0)
    dispatcher.register_channel(b1, config_id="groupB", key_index=0,
                                model_group="groupB", priority=100)

    with pytest.raises(ValueError):
        await dispatcher.chat_with_validation(
            user_message="extract facts",
            response_model=dict,
        )

    # 两个组各被尝试一次后耗尽
    assert a1.call_count == 1
    assert b1.call_count == 1
    for ch in dispatcher._channels:
        assert ch.consecutive_failures == 0
        assert ch.circuit_open is False


# ---------------------------------------------------------------------------
# EmptyLLMResponseError 换通道；畸形 JSONDecodeError 仍不换通道
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_llm_response_forces_cross_group_failover() -> None:
    """首通道空响应应跨组切换，不得像 JSONDecodeError 一样卡死同通道。"""
    dispatcher = AIDispatcher(purpose="extract")

    a1 = _FakeClient(
        "qwen-max",
        EmptyLLMResponseError(
            "empty",
            model_name="qwen-max",
            finish_reason="stop",
            raw_len=0,
        ),
    )
    a2 = _FakeClient(
        "qwen-max",
        EmptyLLMResponseError("empty-sibling", model_name="qwen-max"),
    )
    b1 = _FakeClient("deepseek-chat", [{"category": "QUAL", "description": "ok"}])

    dispatcher.register_channel(
        a1, config_id="groupA", key_index=0, model_group="groupA", priority=0
    )
    dispatcher.register_channel(
        a2, config_id="groupA", key_index=1, model_group="groupA", priority=0
    )
    dispatcher.register_channel(
        b1, config_id="groupB", key_index=0, model_group="groupB", priority=100
    )

    result = await dispatcher.chat_json(user_message="extract requirements")

    assert result == [{"category": "QUAL", "description": "ok"}]
    assert a1.call_count == 1
    assert a2.call_count == 0
    assert b1.call_count == 1

    state_a1 = next(ch for ch in dispatcher._channels if ch.client is a1)
    assert state_a1.consecutive_failures == 0
    assert state_a1.circuit_open is False


@pytest.mark.asyncio
async def test_empty_llm_response_all_groups_exhausted_raises() -> None:
    dispatcher = AIDispatcher(purpose="extract")

    err = EmptyLLMResponseError("empty", raw_len=0)
    a1 = _FakeClient("qwen-max", err)
    b1 = _FakeClient("deepseek-chat", err)
    dispatcher.register_channel(
        a1, config_id="groupA", key_index=0, model_group="groupA", priority=0
    )
    dispatcher.register_channel(
        b1, config_id="groupB", key_index=0, model_group="groupB", priority=100
    )

    with pytest.raises(EmptyLLMResponseError):
        await dispatcher.chat_json(user_message="extract requirements")

    assert a1.call_count == 1
    assert b1.call_count == 1
    for ch in dispatcher._channels:
        assert ch.consecutive_failures == 0
        assert ch.circuit_open is False
