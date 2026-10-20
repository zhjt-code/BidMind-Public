"""公共 input-too-long 400 兜底测试。"""

import sys
from pathlib import Path

import httpx
import pytest
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.input_guard import (
    is_input_too_long_error,
    retry_on_input_too_long_400,
)


def _make_api_status_error(message: str, status_code: int = 400) -> APIStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status_code=status_code, request=request)
    return APIStatusError(message, response=response, body={"error": {"message": message}})


@pytest.mark.asyncio
async def test_retry_on_input_too_long_400_only_retries_target_error():
    calls = []

    async def _send(payload: dict) -> str:
        calls.append(payload.copy())
        if len(calls) == 1:
            raise _make_api_status_error("Range of input length should be [1, 30720]")
        return "ok"

    def _reduce(payload: dict, retry_round: int) -> tuple[dict, dict]:
        reduced = dict(payload)
        reduced["text"] = "较短文本"
        return reduced, {"retry_attempt": retry_round, "removed_segments": ["body"]}

    result = await retry_on_input_too_long_400(
        operation_name="guard-test",
        initial_payload={"text": "很长的原始文本" * 200},
        send_request=_send,
        reduce_payload_semantically=_reduce,
        max_retries=1,
        describe_payload=lambda payload: {"chars": len(payload["text"])},
    )

    assert result == "ok"
    assert len(calls) == 2
    assert len(calls[1]["text"]) < len(calls[0]["text"])


@pytest.mark.asyncio
async def test_retry_on_input_too_long_400_does_not_retry_generic_400():
    calls = []
    reduce_called = {"value": False}

    async def _send(payload: dict) -> str:
        calls.append(payload.copy())
        raise _make_api_status_error("schema invalid")

    def _reduce(payload: dict, retry_round: int) -> tuple[dict, dict]:
        reduce_called["value"] = True
        return payload, {"retry_attempt": retry_round}

    with pytest.raises(APIStatusError):
        await retry_on_input_too_long_400(
            operation_name="guard-test",
            initial_payload={"text": "原始文本"},
            send_request=_send,
            reduce_payload_semantically=_reduce,
            max_retries=1,
        )

    assert len(calls) == 1
    assert reduce_called["value"] is False


@pytest.mark.parametrize(
    "message",
    [
        "Range of input length should be [1, 30720]",
        "This model's maximum context length is 65536 tokens",
        "Exceeded limit on max bytes to request body : 6291456",
        "Total tokens of image and text exceed max message tokens",
    ],
)
def test_is_input_too_long_error_covers_byte_and_message_token_forms(message: str) -> None:
    assert is_input_too_long_error(_make_api_status_error(message)) is True


def test_is_input_too_long_error_ignores_unrelated_400() -> None:
    assert is_input_too_long_error(_make_api_status_error("schema invalid")) is False


@pytest.mark.parametrize(
    "message",
    [
        "Exceeded limit on max bytes to request body : 6291456",
        "Total tokens of image and text exceed max message tokens",
    ],
)
@pytest.mark.asyncio
async def test_retry_on_input_too_long_400_retries_qwen_and_doubao_forms(message: str):
    calls = []

    async def _send(payload: dict) -> str:
        calls.append(payload.copy())
        if len(calls) == 1:
            raise _make_api_status_error(message)
        return "ok"

    def _reduce(payload: dict, retry_round: int) -> tuple[dict, dict]:
        reduced = dict(payload)
        reduced["text"] = "较短文本"
        return reduced, {"retry_attempt": retry_round}

    result = await retry_on_input_too_long_400(
        operation_name="guard-test",
        initial_payload={"text": "很长的原始文本" * 200},
        send_request=_send,
        reduce_payload_semantically=_reduce,
        max_retries=1,
    )
    assert result == "ok"
    assert len(calls) == 2
    assert len(calls[1]["text"]) < len(calls[0]["text"])
