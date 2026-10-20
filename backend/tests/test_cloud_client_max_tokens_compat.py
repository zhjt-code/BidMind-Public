"""max_tokens / max_completion_tokens 兼容性测试。

覆盖目标:
- 错误识别函数能精准命中 OpenAI 官方 'Unsupported parameter' 错误
- 默认走 max_tokens; 缓存命中后切换为 max_completion_tokens
- chat 首次 400 max_tokens 错误后自动降级重试一次成功
- 其他 400 错误（如鉴权失败）不触发降级，且不污染缓存
- 进程级缓存：同 (base_url, model_name) 已被标记后，新 CloudClient 实例零 400
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai import cloud_client as cc_module
from app.core.ai.cloud_client import (
    CloudClient,
    _apply_token_limit_kwargs,
    _clear_max_completion_tokens_cache,
    _is_max_completion_tokens_required,
    _is_max_tokens_unsupported_error,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _clear_max_completion_tokens_cache()
    yield
    _clear_max_completion_tokens_cache()


def _make_unsupported_error() -> APIStatusError:
    """构造 OpenAI 真实抛错的 APIStatusError 实例。"""
    body = {
        "error": {
            "message": (
                "Unsupported parameter: 'max_tokens' is not supported with "
                "this model. Use 'max_completion_tokens' instead."
            ),
            "type": "invalid_request_error",
            "param": "max_tokens",
            "code": "unsupported_parameter",
        }
    }
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(status_code=400, request=request)
    return APIStatusError(
        message=(
            "Error code: 400 - {'error': {'message': \"Unsupported parameter: "
            "'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead.\"}}"
        ),
        response=response,
        body=body,
    )


def _make_other_400_error() -> APIStatusError:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(status_code=400, request=request)
    return APIStatusError(
        message="Error code: 400 - {'error': {'message': 'Invalid API key'}}",
        response=response,
        body={"error": {"message": "Invalid API key"}},
    )


def _make_chat_response(text: str):
    """伪造 OpenAI ChatCompletion 响应（仅用到 choices[0].message.content/usage）。"""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = text
    response.choices[0].finish_reason = "stop"
    response.usage = None
    return response


def test_is_max_tokens_unsupported_error_matches_openai_message():
    err = _make_unsupported_error()
    assert _is_max_tokens_unsupported_error(err) is True


def test_is_max_tokens_unsupported_error_rejects_unrelated_400():
    err = _make_other_400_error()
    assert _is_max_tokens_unsupported_error(err) is False


def test_apply_token_limit_kwargs_default_uses_max_tokens():
    kw = _apply_token_limit_kwargs("https://api.x.com", "model_a", 1000)
    assert kw == {"max_tokens": 1000}


def test_apply_token_limit_kwargs_returns_new_name_after_mark():
    base, model = "https://api.x.com", "model_b"
    cc_module._mark_max_completion_tokens_required(base, model)
    kw = _apply_token_limit_kwargs(base, model, 800)
    assert kw == {"max_completion_tokens": 800}


@pytest.mark.asyncio
async def test_chat_auto_downgrades_on_max_tokens_400(monkeypatch: pytest.MonkeyPatch):
    """首次 max_tokens 400 → 自动用 max_completion_tokens 重试一次并成功。"""
    client = CloudClient(
        base_url="https://api.example.com",
        api_key="sk-test",
        model_name="gpt-5-test",
        timeout=5.0,
        retry_on_429=False,
    )

    received_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        received_kwargs.append(kwargs)
        if "max_tokens" in kwargs:
            raise _make_unsupported_error()
        return _make_chat_response("pong")

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    result = await client.chat(user_message="ping", max_tokens=100)

    assert result == "pong"
    assert len(received_kwargs) == 2, "应当先用 max_tokens 失败，再用 max_completion_tokens 重试"
    assert "max_tokens" in received_kwargs[0]
    assert "max_completion_tokens" in received_kwargs[1]
    assert "max_tokens" not in received_kwargs[1]
    assert _is_max_completion_tokens_required(client.base_url, client.model_name) is True


@pytest.mark.asyncio
async def test_chat_does_not_downgrade_on_unrelated_400(monkeypatch: pytest.MonkeyPatch):
    """非 max_tokens 类的 400 必须直接抛出，不污染缓存。"""
    client = CloudClient(
        base_url="https://api.example.com",
        api_key="sk-test",
        model_name="gpt-test",
        timeout=5.0,
        retry_on_429=False,
    )

    async def fake_create(**_kwargs):
        raise _make_other_400_error()

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    with pytest.raises(APIStatusError):
        await client.chat(user_message="ping", max_tokens=100)

    assert _is_max_completion_tokens_required(client.base_url, client.model_name) is False


@pytest.mark.asyncio
async def test_warm_cache_skips_400_for_new_client(monkeypatch: pytest.MonkeyPatch):
    """缓存命中后，新 CloudClient 实例直接用 max_completion_tokens，零 400。"""
    cc_module._mark_max_completion_tokens_required(
        "https://api.example.com", "gpt-5-test"
    )
    client = CloudClient(
        base_url="https://api.example.com",
        api_key="sk-test",
        model_name="gpt-5-test",
        timeout=5.0,
        retry_on_429=False,
    )

    received_kwargs: list[dict] = []

    async def fake_create(**kwargs):
        received_kwargs.append(kwargs)
        return _make_chat_response("pong")

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    result = await client.chat(user_message="ping", max_tokens=100)

    assert result == "pong"
    assert len(received_kwargs) == 1, "缓存命中应一次成功，不应再发起降级重试"
    assert "max_completion_tokens" in received_kwargs[0]
    assert "max_tokens" not in received_kwargs[0]
