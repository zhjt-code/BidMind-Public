"""P0-A: LLM attempt 记录器与结果分类防御测试。

锁定契约:
- 同一 dispatch 上下文内的多次物理请求共享 logical_call_id 且 attempt_no 递增;
- content None / "" / 纯 <think> 一律计 empty(语义);
- chat_json 解析失败把当次 attempt 精化为 invalid_json;
- chat_with_validation 校验耗尽把当次 attempt 精化为 schema_invalid;
- 异常分类: 401->auth_error, 配额 429->quota_exhausted, 超时->timeout;
- traffic_class 由 contextvar 控制, 测试流量不得计入 production;
- 总闸 LLM_ATTEMPT_EVENTS_ENABLED=False 时不产生事件;
- queue_wait_ms 只记在首个 attempt。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError
from pydantic import BaseModel

from app.core.ai.attempt_recorder import (
    current_traffic_class,
    get_attempt_recorder,
)
from app.core.ai.cloud_client import CloudClient, LlmContentValidationError
from app.core.ai.llm_outcome import LlmOutcome, classify_exception
from app.core.ai.llm_errors import EmptyLLMResponseError


@pytest.fixture(autouse=True)
def _reset_recorder():
    rec = get_attempt_recorder()
    rec.reset_for_tests()
    yield rec
    rec.reset_for_tests()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_client() -> CloudClient:
    return CloudClient(
        base_url="https://demo.example.com/v1",
        api_key="sk-test",
        model_name="demo-attempt-model",
        timeout=30.0,
    )


def _mk_response(content: str | None, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )


def _events() -> list:
    return list(get_attempt_recorder()._queue)


# ---------- 物理 attempt 计数与逻辑调用共享 ----------

def test_three_attempts_share_logical_id_and_increment() -> None:
    client = _make_client()
    rec = get_attempt_recorder()

    async def _scenario() -> None:
        token = rec.begin_dispatch(
            purpose="review", project_id=101, method="chat", queue_wait_ms=42,
        )
        try:
            rec.set_channel(config_id="cfg_a", provider="deepseek")
            with patch.object(
                client._client.chat.completions, "create",
                new=AsyncMock(return_value=_mk_response("ok")),
            ):
                for _ in range(3):
                    await client.chat(user_message="hi")
        finally:
            rec.end_dispatch(token)

    _run(_scenario())

    events = _events()
    assert len(events) == 3
    logical_ids = {e.logical_call_id for e in events}
    assert len(logical_ids) == 1, "同一逻辑调用的物理尝试必须共享 logical_call_id"
    assert [e.attempt_no for e in events] == [1, 2, 3]
    assert all(e.purpose == "review" for e in events)
    assert all(e.project_id == 101 for e in events)
    assert all(e.config_id == "cfg_a" for e in events)
    assert all(e.provider == "deepseek" for e in events)
    # queue_wait 只记首个 attempt
    assert events[0].queue_wait_ms == 42
    assert events[1].queue_wait_ms is None
    assert events[2].queue_wait_ms is None


# ---------- 空响应分类 ----------

@pytest.mark.parametrize("content", [None, "", "  ", "<think>只有思考</think>"])
def test_empty_like_content_classified_empty(content: str | None) -> None:
    client = _make_client()

    with patch.object(
        client._client.chat.completions, "create",
        new=AsyncMock(return_value=_mk_response(content)),
    ):
        _run(client.chat(user_message="hi"))

    events = _events()
    assert len(events) == 1
    assert events[0].outcome == LlmOutcome.EMPTY.value


def test_valid_content_classified_valid_with_tokens() -> None:
    client = _make_client()

    with patch.object(
        client._client.chat.completions, "create",
        new=AsyncMock(return_value=_mk_response("正常回复")),
    ):
        _run(client.chat(user_message="hi"))

    events = _events()
    assert len(events) == 1
    assert events[0].outcome == LlmOutcome.VALID.value
    assert events[0].http_status == 200
    assert events[0].prompt_tokens == 11
    assert events[0].completion_tokens == 7


# ---------- 内容级精化 ----------

def test_chat_json_parse_failure_amends_invalid_json() -> None:
    client = _make_client()

    with patch.object(
        client._client.chat.completions, "create",
        new=AsyncMock(return_value=_mk_response("这不是JSON内容也无法修复")),
    ):
        with pytest.raises(json.JSONDecodeError):
            _run(client.chat_json(user_message="给我json"))

    events = _events()
    assert len(events) == 1
    assert events[0].outcome == LlmOutcome.INVALID_JSON.value, (
        "HTTP 200 但 JSON 非法的 attempt 不得计 valid"
    )


class _NeedBar(BaseModel):
    bar: int


def test_chat_with_validation_schema_failure_amends_schema_invalid() -> None:
    client = _make_client()

    with patch.object(
        client._client.chat.completions, "create",
        new=AsyncMock(return_value=_mk_response('{"foo": 1}')),
    ):
        with pytest.raises(LlmContentValidationError):
            _run(client.chat_with_validation(
                user_message="给我json",
                response_model=_NeedBar,
                max_retries=1,
            ))

    events = _events()
    assert len(events) == 1
    assert events[0].outcome == LlmOutcome.SCHEMA_INVALID.value, (
        "HTTP 200 但 Pydantic 校验失败的 attempt 不得计 valid"
    )


def test_empty_response_via_chat_json_stays_empty() -> None:
    client = _make_client()

    with patch.object(
        client._client.chat.completions, "create",
        new=AsyncMock(return_value=_mk_response("<think>全是思考</think>")),
    ):
        with pytest.raises(EmptyLLMResponseError):
            _run(client.chat_json(user_message="给我json"))

    events = _events()
    assert len(events) == 1
    assert events[0].outcome == LlmOutcome.EMPTY.value


# ---------- 异常分类矩阵 ----------

def _status_error(status: int, message: str = "err") -> APIStatusError:
    req = httpx.Request("POST", "https://demo.example.com/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return APIStatusError(message, response=resp, body={"error": {"message": message}})


def test_classify_exception_matrix() -> None:
    outcome, status, _ = classify_exception(_status_error(401, "unauthorized"))
    assert outcome is LlmOutcome.AUTH_ERROR and status == 401

    outcome, status, _ = classify_exception(_status_error(500, "server boom"))
    assert outcome is LlmOutcome.HTTP_ERROR and status == 500

    outcome, status, kind = classify_exception(
        _status_error(429, "insufficient_quota: balance exhausted")
    )
    assert outcome is LlmOutcome.QUOTA_EXHAUSTED and status == 429
    assert kind == "quota_exhausted"

    outcome, status, kind = classify_exception(_status_error(429, "rate limit reached"))
    assert outcome is LlmOutcome.RATE_LIMITED and kind == "rate_limit"

    outcome, _, _ = classify_exception(asyncio.TimeoutError())
    assert outcome is LlmOutcome.TIMEOUT

    outcome, _, _ = classify_exception(
        json.JSONDecodeError("Expecting value", "doc", 0)
    )
    assert outcome is LlmOutcome.INVALID_JSON

    outcome, _, _ = classify_exception(
        EmptyLLMResponseError("empty", model_name="m")
    )
    assert outcome is LlmOutcome.EMPTY


def test_http_error_attempt_recorded_from_call_path() -> None:
    client = _make_client()

    with patch.object(
        client._client.chat.completions, "create",
        new=AsyncMock(side_effect=_status_error(500, "boom")),
    ):
        with pytest.raises(APIStatusError):
            _run(client.chat(user_message="hi"))

    events = _events()
    assert len(events) == 1
    assert events[0].outcome == LlmOutcome.HTTP_ERROR.value
    assert events[0].http_status == 500


# ---------- 流量隔离与总闸 ----------

def test_traffic_class_defaults_to_regression_in_tests() -> None:
    """conftest autouse fixture 必须把测试流量标为 regression"""
    client = _make_client()
    with patch.object(
        client._client.chat.completions, "create",
        new=AsyncMock(return_value=_mk_response("ok")),
    ):
        _run(client.chat(user_message="hi"))
    events = _events()
    assert events and events[0].traffic_class == "regression"


def test_traffic_class_admin_test_override() -> None:
    client = _make_client()
    token = current_traffic_class.set("admin_test")
    try:
        with patch.object(
            client._client.chat.completions, "create",
            new=AsyncMock(return_value=_mk_response("ok")),
        ):
            _run(client.chat(user_message="hi"))
    finally:
        current_traffic_class.reset(token)
    events = _events()
    assert events and events[0].traffic_class == "admin_test"


def test_master_switch_disables_recording() -> None:
    client = _make_client()
    with patch("app.core.config.settings.LLM_ATTEMPT_EVENTS_ENABLED", False):
        with patch.object(
            client._client.chat.completions, "create",
            new=AsyncMock(return_value=_mk_response("ok")),
        ):
            _run(client.chat(user_message="hi"))
    assert _events() == []


# ---------- 落库路径 ----------

def test_flush_pending_writes_rows_via_session() -> None:
    client = _make_client()
    with patch.object(
        client._client.chat.completions, "create",
        new=AsyncMock(return_value=_mk_response("ok")),
    ):
        _run(client.chat(user_message="hi"))
    assert len(_events()) == 1

    added: list = []

    class _FakeSession:
        def add_all(self, rows: list) -> None:
            added.extend(rows)

        async def commit(self) -> None:
            return None

        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    with patch(
        "app.core.database.async_session_factory",
        new=MagicMock(return_value=_FakeSession()),
    ):
        inserted = _run(get_attempt_recorder().flush_pending())

    assert inserted == 1
    assert len(added) == 1
    assert added[0].outcome == LlmOutcome.VALID.value
    assert added[0].model_name == "demo-attempt-model"
    assert len(_events()) == 0, "flush 后缓冲必须清空"
