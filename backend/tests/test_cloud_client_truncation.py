"""CloudClient.chat_with_validation JSON 截断自动扩容测试。

验证场景:
1. 截断时自动将 max_tokens 翻倍重试
2. max_tokens 扩容不超过 8192 上限
3. 非截断类 JSONDecodeError 不触发扩容
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.cloud_client import CloudClient


class _SimpleModel(BaseModel):
    name: str
    value: int


def _make_truncation_error() -> json.JSONDecodeError:
    return json.JSONDecodeError(
        "Unterminated string starting at",
        '{"name": "test", "value": ',
        26,
    )


def _make_generic_json_error() -> json.JSONDecodeError:
    return json.JSONDecodeError(
        "Extra data",
        '{"name": "test"}extra',
        16,
    )


@pytest.mark.asyncio
async def test_auto_expands_tokens_on_truncation():
    """截断时 max_tokens 应从 2000 扩容到 4000 后成功。"""
    client = CloudClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model_name="test-model",
    )

    call_tokens: list[int] = []

    async def _mock_chat_json(
        user_message: str,
        system_message=None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        **_kwargs,
    ):
        call_tokens.append(max_tokens)
        if max_tokens < 4000:
            raise _make_truncation_error()
        return {"name": "ok", "value": 42}

    with patch.object(client, "chat_json", side_effect=_mock_chat_json):
        result = await client.chat_with_validation(
            user_message="test",
            response_model=_SimpleModel,
            max_tokens=2000,
            max_retries=3,
        )

    assert result.name == "ok"
    assert result.value == 42
    assert call_tokens[0] == 2000
    assert call_tokens[1] == 4000


@pytest.mark.asyncio
async def test_token_ceiling_not_exceeded():
    """连续截断时 max_tokens 不超过 8192。"""
    client = CloudClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model_name="test-model",
    )

    call_tokens: list[int] = []

    async def _mock_chat_json(
        user_message: str,
        system_message=None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        **_kwargs,
    ):
        call_tokens.append(max_tokens)
        raise _make_truncation_error()

    with patch.object(client, "chat_json", side_effect=_mock_chat_json):
        with pytest.raises(ValueError, match="无法获得有效响应"):
            await client.chat_with_validation(
                user_message="test",
                response_model=_SimpleModel,
                max_tokens=4096,
                max_retries=3,
            )

    assert call_tokens == [4096, 8192, 8192]


@pytest.mark.asyncio
async def test_no_expansion_on_generic_json_error():
    """非截断类 JSONDecodeError 不触发 max_tokens 扩容。"""
    client = CloudClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model_name="test-model",
    )

    call_tokens: list[int] = []

    async def _mock_chat_json(
        user_message: str,
        system_message=None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        **_kwargs,
    ):
        call_tokens.append(max_tokens)
        raise _make_generic_json_error()

    with patch.object(client, "chat_json", side_effect=_mock_chat_json):
        with pytest.raises(ValueError, match="无法获得有效响应"):
            await client.chat_with_validation(
                user_message="test",
                response_model=_SimpleModel,
                max_tokens=2000,
                max_retries=3,
            )

    assert all(t == 2000 for t in call_tokens), f"非截断错误不应扩容,实际: {call_tokens}"
