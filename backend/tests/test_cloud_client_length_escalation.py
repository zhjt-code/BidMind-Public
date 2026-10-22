""": chat_json 对 finish_reason=length 空响应与截断非空先同通道扩容再换组。

问题形态: 输出预算撞顶被当成通道故障或语义错误。夹具用两个不同模型名/载荷，证明机制不绑实体。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.core.ai.cloud_client import (
    CloudClient,
    EmptyLLMResponseError,
    LlmContentValidationError,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _client(model_name: str) -> CloudClient:
    return CloudClient(
        base_url="https://demo.example.com/v1",
        api_key="sk-test",
        model_name=model_name,
        timeout=30.0,
    )


def test_length_empty_escalates_then_succeeds_deepseek_fixture() -> None:
    """夹具一: deepseek 型思考模型，4000 空+length 后 8000 产出 JSON。"""
    client = _client("deepseek-v4-pro")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        budget = int(kwargs["max_tokens"])
        seen.append(budget)
        if budget < 8000:
            client._last_finish_reason = "length"
            return ""
        client._last_finish_reason = "stop"
        return '{"items":[{"description":"须具备安全生产许可证"}]}'

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        result = _run(client.chat_json(user_message="extract", max_tokens=4000))

    assert seen == [4000, 8000]
    assert result["items"][0]["description"] == "须具备安全生产许可证"


def test_length_empty_escalates_then_succeeds_second_entity() -> None:
    """夹具二: 同形态换实体（gemini 思考型），机制仍扩容一次。"""
    client = _client("gemini-3.5-flash")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        budget = int(kwargs["max_tokens"])
        seen.append(budget)
        if budget < 4000:
            client._last_finish_reason = "length"
            return ""
        client._last_finish_reason = "stop"
        return '{"ok": true}'

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        result = _run(client.chat_json(user_message="extract", max_tokens=2000))

    assert seen == [2000, 4000]
    assert result == {"ok": True}


def test_stop_empty_does_not_escalate() -> None:
    """负例: finish_reason=stop 的空响应不是预算故障，禁止扩容。"""
    client = _client("deepseek-v4-pro")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        seen.append(int(kwargs["max_tokens"]))
        client._last_finish_reason = "stop"
        return ""

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        with pytest.raises(EmptyLLMResponseError) as exc:
            _run(client.chat_json(user_message="extract", max_tokens=4000))

    assert seen == [4000]
    assert exc.value.finish_reason == "stop"


def test_ceiling_does_not_escalate() -> None:
    """负例: 已达 8192 封顶不再扩容。"""
    client = _client("kimi-k3")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        seen.append(int(kwargs["max_tokens"]))
        client._last_finish_reason = "length"
        return ""

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        with pytest.raises(EmptyLLMResponseError) as exc:
            _run(client.chat_json(user_message="extract", max_tokens=8192))

    assert seen == [8192]
    assert exc.value.finish_reason == "length"


def test_constrained_decoding_path_escalates_on_length_empty() -> None:
    """json_schema 路径也必须写 finish_reason，否则指纹读到陈旧值。"""
    from types import SimpleNamespace

    client = _client("escalation-cd-model")
    seen: list[int] = []

    def _resp(content: str, finish_reason: str) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0, total_tokens=1),
        )

    async def _create(**kwargs: Any) -> SimpleNamespace:
        budget = int(kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 0)
        seen.append(budget)
        if budget < 4000:
            return _resp("", "length")
        return _resp('{"ok": 1}', "stop")

    schema = {
        "type": "json_schema",
        "json_schema": {"name": "x", "schema": {"type": "object"}},
    }
    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(side_effect=_create),
    ):
        result = _run(
            client.chat_json(user_message="extract", max_tokens=2000, json_schema=schema)
        )

    assert seen == [2000, 4000]
    assert result == {"ok": 1}


def test_escalation_caps_at_8192() -> None:
    """5000×2 超过封顶时第二次只发 8192。"""
    client = _client("qwen3.8-max")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        seen.append(int(kwargs["max_tokens"]))
        if kwargs["max_tokens"] < 8192:
            client._last_finish_reason = "length"
            return ""
        client._last_finish_reason = "stop"
        return '{"items":[]}'

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        result = _run(client.chat_json(user_message="extract", max_tokens=5000))

    assert seen == [5000, 8192]
    assert result == {"items": []}


_TRUNCATED_OUTLINE = (
    '[{"title":"第一章","children":[{"title":"1.1 概述"},{"title":"1.2'
)
_COMPLETE_OUTLINE = (
    '[{"title":"第一章","children":[{"title":"1.1 概述"},'
    '{"title":"1.2 范围"}]},{"title":"第二章"}]'
)
_TRUNCATED_REVIEW = '{"requirement":"不得出现价格","verdict":"fail","evidence":"'
_COMPLETE_REVIEW = (
    '{"requirement":"不得出现价格","verdict":"fail","evidence":"投标函第3页"}'
)


def test_length_truncated_array_escalates_then_succeeds_outline_fixture() -> None:
    """夹具一(目录): 数组截断非空 + length，5000 后扩到 8192 拿全量。"""
    client = _client("outline-skeleton-model")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        budget = int(kwargs["max_tokens"])
        seen.append(budget)
        if budget < 8192:
            client._last_finish_reason = "length"
            return _TRUNCATED_OUTLINE
        client._last_finish_reason = "stop"
        return _COMPLETE_OUTLINE

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        result = _run(client.chat_json(user_message="outline", max_tokens=5000))

    assert seen == [5000, 8192]
    assert result[0]["children"][1]["title"] == "1.2 范围"
    assert result[1]["title"] == "第二章"


def test_length_truncated_object_escalates_then_succeeds_review_fixture() -> None:
    """夹具二(审查对象 JSON): 同形态换实体，2000 截断后 4000 完整。"""
    client = _client("review-item-model")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        budget = int(kwargs["max_tokens"])
        seen.append(budget)
        if budget < 4000:
            client._last_finish_reason = "length"
            return _TRUNCATED_REVIEW
        client._last_finish_reason = "stop"
        return _COMPLETE_REVIEW

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        result = _run(client.chat_json(user_message="review", max_tokens=2000))

    assert seen == [2000, 4000]
    assert result["evidence"] == "投标函第3页"


def test_stop_invalid_json_does_not_escalate() -> None:
    """负例: finish_reason=stop 的非法 JSON 不是预算故障，禁止扩容，走修复。"""
    client = _client("stop-invalid-json")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        seen.append(int(kwargs["max_tokens"]))
        client._last_finish_reason = "stop"
        return '[{"ok": true,}]'

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        result = _run(client.chat_json(user_message="extract", max_tokens=2000))

    assert seen == [2000]
    assert result == [{"ok": True}]


def test_length_truncated_at_ceiling_repairs_partial() -> None:
    """负例: 已达 8192 封顶不再扩容，截断数组走既有抢救保留已完整项。"""
    client = _client("ceiling-truncated-array")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        seen.append(int(kwargs["max_tokens"]))
        client._last_finish_reason = "length"
        return '[{"title":"第一章"},{"title":"第二'

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        result = _run(client.chat_json(user_message="outline", max_tokens=8192))

    assert seen == [8192]
    assert result == [{"title": "第一章"}]


def test_length_truncated_does_not_repair_before_escalation() -> None:
    """顺序: length 截断第一次不得调用修复，必须先扩容。"""
    client = _client("outline-trunc-order")
    seen: list[int] = []
    repair_calls: list[str] = []

    async def _chat(**kwargs: Any) -> str:
        budget = int(kwargs["max_tokens"])
        seen.append(budget)
        if budget < 4000:
            client._last_finish_reason = "length"
            return _TRUNCATED_OUTLINE
        client._last_finish_reason = "stop"
        return _COMPLETE_OUTLINE

    def _repair(content: str) -> Any:
        repair_calls.append(content[:20])
        return None

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        with patch.object(client, "_try_repair_json", side_effect=_repair):
            result = _run(client.chat_json(user_message="outline", max_tokens=2000))

    assert seen == [2000, 4000]
    assert repair_calls == []
    assert result[1]["title"] == "第二章"


def test_length_after_escalation_raises_validation_error_outline_fixture() -> None:
    """扩容一次仍 length，交 dispatcher 换组，不再同通道修复。"""
    client = _client("outline-still-truncated")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        seen.append(int(kwargs["max_tokens"]))
        client._last_finish_reason = "length"
        return _TRUNCATED_OUTLINE

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        with pytest.raises(LlmContentValidationError) as exc:
            _run(client.chat_json(user_message="outline", max_tokens=2000))

    assert seen == [2000, 4000]
    assert exc.value.suggested_max_tokens == 4000


def test_length_after_escalation_raises_validation_error_review_fixture() -> None:
    """同形态第二例: 审查对象 JSON 扩容后仍截断同样抛预算故障。"""
    client = _client("review-still-truncated")
    seen: list[int] = []

    async def _chat(**kwargs: Any) -> str:
        seen.append(int(kwargs["max_tokens"]))
        client._last_finish_reason = "length"
        return _TRUNCATED_REVIEW

    with patch.object(client, "chat", new=AsyncMock(side_effect=_chat)):
        with pytest.raises(LlmContentValidationError) as exc:
            _run(client.chat_json(user_message="review", max_tokens=2000))

    assert seen == [2000, 4000]
    assert exc.value.suggested_max_tokens == 4000
