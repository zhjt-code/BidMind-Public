"""Dispatcher 参数不兼容类 400 降级测试。"""

import sys
from pathlib import Path

import httpx
import pytest
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.dispatcher import AIDispatcher


class _FakeClient:
    def __init__(self, model_name: str, behavior):
        self.base_url = "https://example.com/v1"
        self.model_name = model_name
        self._behavior = behavior
        self.call_count = 0

    async def chat(self, **kwargs):
        self.call_count += 1
        result = self._behavior
        if callable(result):
            result = await result(**kwargs)
        if isinstance(result, Exception):
            raise result
        return result


def _make_api_status_error(message: str, status_code: int = 400) -> APIStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status_code=status_code, request=request)
    return APIStatusError(message, response=response, body={"error": {"message": message}})


@pytest.mark.asyncio
async def test_dispatcher_failsover_on_retryable_bad_request_400():
    dispatcher = AIDispatcher(purpose="review")
    bad_request = _make_api_status_error(
        "invalid temperature: only 1 is allowed for this model",
    )
    dispatcher.register_channel(
        _FakeClient("kimi-k2.5", bad_request),
        config_id="kimi",
        model_group="kimi",
        priority=0,
    )
    dispatcher.register_channel(
        _FakeClient("deepseek-chat", "ok"),
        config_id="deepseek",
        model_group="deepseek",
        priority=100,
    )

    result = await dispatcher.chat("hello")

    assert result == "ok"


@pytest.mark.asyncio
async def test_dispatcher_does_not_failover_on_generic_bad_request_400():
    dispatcher = AIDispatcher(purpose="review")
    bad_request = _make_api_status_error("prompt too long for current request")
    backup_called = {"value": False}

    async def _backup(**kwargs):
        backup_called["value"] = True
        return "should-not-run"

    dispatcher.register_channel(
        _FakeClient("kimi-k2.5", bad_request),
        config_id="kimi",
        model_group="kimi",
        priority=0,
    )
    dispatcher.register_channel(
        _FakeClient("deepseek-chat", _backup),
        config_id="deepseek",
        model_group="deepseek",
        priority=100,
    )

    with pytest.raises(APIStatusError):
        await dispatcher.chat("hello")

    assert backup_called["value"] is False


@pytest.mark.asyncio
async def test_dispatcher_exhausts_bound_group_keys_before_cross_group_failover():
    dispatcher = AIDispatcher(purpose="review")
    service_unavailable = _make_api_status_error("upstream unavailable", status_code=503)
    primary_keys = [
        _FakeClient("primary-model", service_unavailable)
        for _ in range(5)
    ]
    backup = _FakeClient("backup-model", "ok")

    for idx, client in enumerate(primary_keys):
        dispatcher.register_channel(
            client,
            config_id="primary",
            key_index=idx,
            model_group="primary",
            priority=0,
        )
    dispatcher.register_channel(
        backup,
        config_id="backup",
        key_index=0,
        model_group="backup",
        priority=100,
    )

    result = await dispatcher._dispatch("chat", project_id=1381, user_message="hello")

    assert result == "ok"
    assert [client.call_count for client in primary_keys] == [1, 1, 1, 1, 1]
    assert backup.call_count == 1


@pytest.mark.asyncio
async def test_dispatcher_uses_fourth_key_success_before_cross_group_failover():
    dispatcher = AIDispatcher(purpose="review")
    service_unavailable = _make_api_status_error("upstream unavailable", status_code=503)
    primary_keys = [
        _FakeClient("primary-model", service_unavailable),
        _FakeClient("primary-model", service_unavailable),
        _FakeClient("primary-model", service_unavailable),
        _FakeClient("primary-model", "ok"),
        _FakeClient("primary-model", service_unavailable),
    ]
    backup = _FakeClient("backup-model", "should-not-run")

    for idx, client in enumerate(primary_keys):
        dispatcher.register_channel(
            client,
            config_id="primary",
            key_index=idx,
            model_group="primary",
            priority=0,
        )
    dispatcher.register_channel(
        backup,
        config_id="backup",
        key_index=0,
        model_group="backup",
        priority=100,
    )

    result = await dispatcher._dispatch("chat", project_id=1381, user_message="hello")

    assert result == "ok"
    assert [client.call_count for client in primary_keys] == [1, 1, 1, 1, 0]
    assert backup.call_count == 0
