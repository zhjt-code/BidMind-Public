"""CloudClient `response_format` 能力探测回归测试。

锁定契约:
- 首次 provider 报告"不支持 response_format"时, `_chat_with_response_format`
  抛 `UnsupportedResponseFormatError` 并写入 `(base_url, model_name)` 进程级缓存。
- 第二次同 `(base_url, model_name)` 的调用直接短路为普通 chat, 不再发送
  `response_format`, 避免 dispatcher 多 key 通道场景下每个 key 都被刺一次 400。
- `chat_json` 捕获 `UnsupportedResponseFormatError` 后在本实例内回退为普通 chat
  并成功解析 JSON, 保持对外接口契约向后兼容。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.core.ai.cloud_client import (
    CloudClient,
    UnsupportedResponseFormatError,
    _UNSUPPORTED_RESPONSE_FORMAT_CACHE,
    _clear_response_format_cache,
    _is_response_format_unsupported,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_cache():
    _clear_response_format_cache()
    yield
    _clear_response_format_cache()


def _make_client(base_url: str = "https://demo.example.com/v1",
                 model_name: str = "demo-model") -> CloudClient:
    return CloudClient(
        base_url=base_url,
        api_key="sk-test",
        model_name=model_name,
        timeout=30.0,
    )


def _mk_chat_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


# ---------------------------------------------------------------------------
# _chat_with_response_format: 首次 400 -> UnsupportedResponseFormatError + 写缓存
# ---------------------------------------------------------------------------


def test_first_unsupported_400_raises_and_marks_cache() -> None:
    client = _make_client()

    async def _boom(**_kwargs: Any) -> Any:
        raise RuntimeError(
            "Error code: 400 - response_format.type: unknown variant 'object'"
        )

    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(side_effect=_boom),
    ):
        with pytest.raises(UnsupportedResponseFormatError):
            _run(client._chat_with_response_format(
                user_message="hi json",
                response_format={"type": "json_object"},
            ))

    assert _is_response_format_unsupported(client.base_url, client.model_name)
    assert client._json_schema_unsupported is True


# ---------------------------------------------------------------------------
# _chat_with_response_format: 缓存命中 -> 直接走 chat(), 不再发 response_format
# ---------------------------------------------------------------------------


def test_cache_hit_skips_response_format_on_second_instance() -> None:
    base_url = "https://demo.example.com/v1"
    model_name = "demo-model"

    client1 = _make_client(base_url=base_url, model_name=model_name)

    async def _boom(**_kwargs: Any) -> Any:
        raise RuntimeError(
            "Error code: 400 - response_format.type: unknown variant 'object'"
        )

    with patch.object(
        client1._client.chat.completions, "create", new=AsyncMock(side_effect=_boom),
    ):
        with pytest.raises(UnsupportedResponseFormatError):
            _run(client1._chat_with_response_format(
                user_message="hi json",
                response_format={"type": "json_object"},
            ))

    # 新建一个同 (base_url, model_name) 的独立 CloudClient 实例
    # 模拟 dispatcher 里多 key 通道 -> 多 CloudClient 的真实场景。
    client2 = _make_client(base_url=base_url, model_name=model_name)
    assert client2._json_schema_unsupported is False  # 实例级标志为 False
    # 但进程级缓存已命中
    assert _is_response_format_unsupported(base_url, model_name)

    observed_kwargs: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> Any:
        observed_kwargs.append(kwargs)
        return _mk_chat_response("{\"verdict\": \"agree\"}")

    with patch.object(
        client2._client.chat.completions, "create", new=AsyncMock(side_effect=_capture),
    ):
        result = _run(client2._chat_with_response_format(
            user_message="hi json",
            response_format={"type": "json_object"},
        ))

    assert "agree" in result
    assert len(observed_kwargs) == 1
    # 关键断言: 第二个实例也跳过了 response_format 参数
    assert "response_format" not in observed_kwargs[0]
    # 实例级标志也被同步置为 True
    assert client2._json_schema_unsupported is True


# ---------------------------------------------------------------------------
# chat_json: UnsupportedResponseFormatError 在本实例内回退成功
# ---------------------------------------------------------------------------


def test_chat_json_recovers_from_unsupported_within_same_instance() -> None:
    client = _make_client()

    call_log: list[dict[str, Any]] = []

    async def _first_unsupported_then_ok(**kwargs: Any) -> Any:
        call_log.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError(
                "Error code: 400 - response_format.type: unknown variant 'object'"
            )
        return _mk_chat_response('{"verdict": "agree", "confidence": 0.9, "reason": "ok"}')

    with patch.object(
        client._client.chat.completions,
        "create",
        new=AsyncMock(side_effect=_first_unsupported_then_ok),
    ):
        result = _run(client.chat_json(
            user_message="please reply json",
            json_schema={"type": "json_object"},
        ))

    assert isinstance(result, dict)
    assert result["verdict"] == "agree"
    # 两次调用: 一次带 response_format (400), 一次纯 chat 成功
    assert len(call_log) == 2
    assert "response_format" in call_log[0]
    assert "response_format" not in call_log[1]


def test_chat_json_can_propagate_unsupported_for_dispatcher() -> None:
    client = _make_client()

    call_log: list[dict[str, Any]] = []

    async def _unsupported(**kwargs: Any) -> Any:
        call_log.append(kwargs)
        raise RuntimeError(
            "Error code: 400 - response_format.type: unknown variant 'object'"
        )

    with patch.object(
        client._client.chat.completions,
        "create",
        new=AsyncMock(side_effect=_unsupported),
    ):
        with pytest.raises(UnsupportedResponseFormatError):
            _run(client.chat_json(
                user_message="please reply json",
                json_schema={"type": "json_object"},
                propagate_unsupported=True,
            ))

    assert len(call_log) == 1
    assert "response_format" in call_log[0]
    assert _is_response_format_unsupported(client.base_url, client.model_name)


# ---------------------------------------------------------------------------
# chat_with_validation 透传 propagate_unsupported
# ---------------------------------------------------------------------------


def test_chat_with_validation_propagates_unsupported_for_dispatcher() -> None:
    """propagate_unsupported=True 时, 不支持信号必须上抛而非静默退化为普通 chat。"""
    from pydantic import BaseModel

    class _Verdict(BaseModel):
        verdict: str

    client = _make_client()
    call_log: list[dict[str, Any]] = []

    async def _unsupported(**kwargs: Any) -> Any:
        call_log.append(kwargs)
        raise RuntimeError(
            "Error code: 400 - This response_format type is unavailable now"
        )

    with patch.object(
        client._client.chat.completions,
        "create",
        new=AsyncMock(side_effect=_unsupported),
    ):
        with pytest.raises(UnsupportedResponseFormatError):
            _run(client.chat_with_validation(
                user_message="extract facts",
                response_model=_Verdict,
                max_retries=1,
                json_schema={"type": "json_object"},
                propagate_unsupported=True,
            ))

    # 只发了一次带 response_format 的请求, 没有退化为普通 chat
    assert len(call_log) == 1
    assert "response_format" in call_log[0]


# ---------------------------------------------------------------------------
# chat_with_validation 校验失败时用同响应其余 JSON 候选回退
# ---------------------------------------------------------------------------


def test_chat_with_validation_recovers_from_prompt_echo_payload() -> None:
    """模型先回显 Prompt 内 JSON 常量再输出真实答案时, 校验必须回退到真实答案。"""
    from pydantic import BaseModel, ConfigDict

    class _Scope(BaseModel):
        model_config = ConfigDict(extra="forbid")
        scheme: str
        lot: str
        expected_total: float

    client = _make_client()
    echo_then_real = (
        '评分基准: {"expected_total": 100.0}\n'
        '{"scheme": "s1", "lot": "__single__", "expected_total": 100.0}'
    )

    with patch.object(
        client._client.chat.completions,
        "create",
        new=AsyncMock(return_value=_mk_chat_response(echo_then_real)),
    ):
        result = _run(client.chat_with_validation(
            user_message="extract facts",
            response_model=_Scope,
            max_retries=1,
        ))

    assert isinstance(result, _Scope)
    assert result.scheme == "s1"
    assert result.expected_total == 100.0


# ---------------------------------------------------------------------------
# 防御: 非 response_format 400 不触发缓存误写
# ---------------------------------------------------------------------------


def test_non_unsupported_400_does_not_mark_cache() -> None:
    client = _make_client()

    async def _other_400(**_kwargs: Any) -> Any:
        raise RuntimeError("Error code: 400 - 'messages' content too long")

    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(side_effect=_other_400),
    ):
        with pytest.raises(Exception) as exc_info:
            _run(client._chat_with_response_format(
                user_message="too long",
                response_format={"type": "json_object"},
            ))

    # 既不是 UnsupportedResponseFormatError, 也没有污染进程级缓存
    assert not isinstance(exc_info.value, UnsupportedResponseFormatError)
    assert not _is_response_format_unsupported(client.base_url, client.model_name)


# ---------------------------------------------------------------------------
# schema 被拒 != provider 不支持, 禁止污染能力缓存
# ---------------------------------------------------------------------------

# 生产实测原文（gpt-5.4-mini）。它同时包含 "response_format" 与 "json_schema" 关键词,
# 因此会命中旧的 is_unsupported 判定 —— 这正是本组测试要防守的误判入口。
_SCHEMA_REJECT_400 = (
    "Error code: 400 - {'error': {'message': \"Invalid schema for response_format "
    "'JudgeVerdict': In context=(), 'additionalProperties' is required to be supplied "
    "and to be false.\", 'type': 'invalid_request_error', 'param': 'response_format'}}"
)


def test_schema_rejection_does_not_mark_capability_cache() -> None:
    """schema 不合规是我方缺陷, 绝不能把健康通道标记为"不支持约束解码"。"""
    client = _make_client()

    async def _schema_reject(**_kwargs: Any) -> Any:
        raise RuntimeError(_SCHEMA_REJECT_400)

    with patch.object(
        client._client.chat.completions,
        "create",
        new=AsyncMock(side_effect=_schema_reject),
    ):
        with pytest.raises(UnsupportedResponseFormatError):
            _run(client._chat_with_response_format(
                user_message="judge this",
                response_format={"type": "json_schema", "json_schema": {"name": "X"}},
            ))

    # 核心防御: 进程级与实例级能力标记都必须保持干净
    assert not _is_response_format_unsupported(client.base_url, client.model_name)
    assert client._json_schema_unsupported is False
    assert _UNSUPPORTED_RESPONSE_FORMAT_CACHE == set()


def test_channel_still_uses_response_format_after_schema_rejection() -> None:
    """坏 schema 被拒后, 同通道的后续调用必须仍然走约束解码而非退化为普通 chat。"""
    base_url = "https://demo.example.com/v1"
    model_name = "demo-model"
    client1 = _make_client(base_url=base_url, model_name=model_name)

    async def _schema_reject(**_kwargs: Any) -> Any:
        raise RuntimeError(_SCHEMA_REJECT_400)

    with patch.object(
        client1._client.chat.completions,
        "create",
        new=AsyncMock(side_effect=_schema_reject),
    ):
        with pytest.raises(UnsupportedResponseFormatError):
            _run(client1._chat_with_response_format(
                user_message="judge this",
                response_format={"type": "json_schema", "json_schema": {"name": "X"}},
            ))

    client2 = _make_client(base_url=base_url, model_name=model_name)
    observed_kwargs: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> Any:
        observed_kwargs.append(kwargs)
        return _mk_chat_response('{"verdict": "agree"}')

    with patch.object(
        client2._client.chat.completions, "create", new=AsyncMock(side_effect=_capture),
    ):
        _run(client2._chat_with_response_format(
            user_message="judge again",
            response_format={"type": "json_object"},
        ))

    assert len(observed_kwargs) == 1
    assert "response_format" in observed_kwargs[0], (
        "通道被坏 schema 污染: 后续调用退化为普通 chat"
    )


def test_genuine_capability_gap_still_marks_cache() -> None:
    """回归: 真正的能力缺失必须仍然写粘性缓存, 不被 修复削弱。"""
    client = _make_client()

    async def _no_capability(**_kwargs: Any) -> Any:
        raise RuntimeError(
            "Error code: 400 - This response_format type is unavailable now"
        )

    with patch.object(
        client._client.chat.completions,
        "create",
        new=AsyncMock(side_effect=_no_capability),
    ):
        with pytest.raises(UnsupportedResponseFormatError):
            _run(client._chat_with_response_format(
                user_message="hi",
                response_format={"type": "json_object"},
            ))

    assert _is_response_format_unsupported(client.base_url, client.model_name)
    assert client._json_schema_unsupported is True
