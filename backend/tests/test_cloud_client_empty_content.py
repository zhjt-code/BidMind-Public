"""CloudClient.chat() 空响应 (message.content=None) 守卫回归测试。

锁定契约:
- 当 provider 返回 message.content=None（如 gemini-3-flash-preview 在 aihubmix
  上的稳定性故障）时, chat() 不得抛 TypeError, 必须返回 ""。
- 空响应必须按 status="empty" 记入 record_llm_call, 不得污染 success 统计。
- 必须 emit empty_return_total{purpose="chat"} 指标, 让运维侧能观测。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.core.ai.cloud_client import CloudClient


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_client() -> CloudClient:
    return CloudClient(
        base_url="https://demo.example.com/v1",
        api_key="sk-test",
        model_name="demo-empty-model",
        timeout=30.0,
    )


def _mk_response(content: str | None, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0, total_tokens=1),
    )


def test_chat_returns_empty_string_when_content_is_none() -> None:
    client = _make_client()

    async def _empty(**_kwargs: Any) -> Any:
        return _mk_response(content=None, finish_reason="stop")

    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(side_effect=_empty),
    ):
        result = _run(client.chat(user_message="hi"))

    assert result == ""


def test_chat_does_not_raise_typeerror_on_none_content() -> None:
    """回归: line 355 `len(content)` 在 content=None 时抛 TypeError。"""
    client = _make_client()

    async def _empty(**_kwargs: Any) -> Any:
        return _mk_response(content=None)

    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(side_effect=_empty),
    ):
        # 不抛 TypeError 即视为修复生效
        result = _run(client.chat(user_message="hi"))

    assert result == ""


def test_chat_records_empty_metric_on_none_content() -> None:
    client = _make_client()

    async def _empty(**_kwargs: Any) -> Any:
        return _mk_response(content=None, finish_reason="stop")

    with patch("app.core.ai.cloud_client.record_llm_call") as mock_record, \
         patch("app.core.ai.cloud_client.empty_return_total") as mock_metric, \
         patch.object(
             client._client.chat.completions, "create",
             new=AsyncMock(side_effect=_empty),
         ):
        _run(client.chat(user_message="hi"))

    record_calls = mock_record.call_args_list
    assert len(record_calls) == 1
    args, _ = record_calls[0]
    assert args[0] == client.model_name
    assert args[1] == "chat"
    assert args[2] == "empty"  # 关键: 不得是 "success"

    mock_metric.labels.assert_called_once_with(flow="all", purpose="chat")
    mock_metric.labels.return_value.inc.assert_called_once()


def test_chat_normal_response_unchanged() -> None:
    """守卫不影响正常响应路径。"""
    client = _make_client()

    async def _ok(**_kwargs: Any) -> Any:
        return _mk_response(content="hello world")

    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(side_effect=_ok),
    ):
        result = _run(client.chat(user_message="hi"))

    assert result == "hello world"


def test_chat_records_success_metric_on_normal_response() -> None:
    client = _make_client()

    async def _ok(**_kwargs: Any) -> Any:
        return _mk_response(content="hello")

    with patch("app.core.ai.cloud_client.record_llm_call") as mock_record, \
         patch.object(
             client._client.chat.completions, "create",
             new=AsyncMock(side_effect=_ok),
         ):
        _run(client.chat(user_message="hi"))

    record_calls = mock_record.call_args_list
    assert len(record_calls) == 1
    args, _ = record_calls[0]
    assert args[2] == "success"


def test_chat_json_raises_empty_llm_response_error_on_blank_content() -> None:
    """: chat_json 清洗后为空须抛 EmptyLLMResponseError，非 char0 JSONDecodeError。"""
    import json

    from app.core.ai.llm_errors import EmptyLLMResponseError

    client = _make_client()

    async def _empty(**_kwargs: Any) -> Any:
        return _mk_response(content=None, finish_reason="stop")

    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(side_effect=_empty),
    ):
        with pytest.raises(EmptyLLMResponseError) as exc_info:
            _run(client.chat_json(user_message="hi"))

    err = exc_info.value
    assert err.raw_len == 0
    assert err.finish_reason == "stop"
    assert err.think_consumed is False
    assert not isinstance(err, json.JSONDecodeError)


def test_chat_json_raises_empty_when_think_tags_consume_all_output() -> None:
    """: 仅 think 标签时清洗后为空，须抛 EmptyLLMResponseError。"""
    from app.core.ai.llm_errors import EmptyLLMResponseError

    client = _make_client()
    think_only = "<think>reasoning only</think>"

    async def _think(**_kwargs: Any) -> Any:
        return _mk_response(content=think_only, finish_reason="stop")

    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(side_effect=_think),
    ):
        with pytest.raises(EmptyLLMResponseError) as exc_info:
            _run(client.chat_json(user_message="hi"))

    err = exc_info.value
    assert err.raw_len == len(think_only)
    assert err.think_consumed is True


def test_chat_json_malformed_nonempty_still_raises_json_decode_error() -> None:
    """有正文但非法 JSON 仍走 JSONDecodeError，供提取层同通道格式纠偏。"""
    import json

    client = _make_client()

    async def _bad(**_kwargs: Any) -> Any:
        return _mk_response(content="这不是JSON", finish_reason="stop")

    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(side_effect=_bad),
    ):
        with pytest.raises(json.JSONDecodeError) as exc_info:
            _run(client.chat_json(user_message="hi"))

    assert exc_info.value.pos == 0
    assert "Expecting value" in str(exc_info.value)
