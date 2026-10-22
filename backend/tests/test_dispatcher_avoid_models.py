"""Dispatcher `_select_channel` 的 avoid_models 软偏好回归测试。

锁定契约:
1. avoid_models 命中部分 model_name 时，优先选未命中通道；
2. 全部 model_name 都在 avoid_models 时 fail-open，仍返回某个通道（非 None）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.dispatcher import AIDispatcher  # noqa: E402


class _FakeClient:
    """轻量假客户端：仅提供 register_channel / avoid_models 所需属性。"""

    def __init__(self, model_name: str) -> None:
        self.base_url = "https://example.com/v1"
        self.model_name = model_name
        self._last_usage = None

    async def chat(self, **kwargs: Any) -> str:
        return "ok"


def _build_dispatcher_with_two_models() -> tuple[AIDispatcher, str, str]:
    dispatcher = AIDispatcher(purpose="review")
    model_a = "model-alpha"
    model_b = "model-beta"
    dispatcher.register_channel(
        _FakeClient(model_a),
        config_id="cfg-a",
        key_index=0,
        model_group="group-a",
        priority=10,
    )
    dispatcher.register_channel(
        _FakeClient(model_b),
        config_id="cfg-b",
        key_index=0,
        model_group="group-b",
        priority=10,
    )
    return dispatcher, model_a, model_b


@pytest.mark.asyncio
async def test_select_channel_prefers_non_avoided_model() -> None:
    dispatcher, model_a, model_b = _build_dispatcher_with_two_models()

    chosen = await dispatcher._select_channel(avoid_models={model_a})

    assert chosen is not None
    assert chosen.client.model_name == model_b


@pytest.mark.asyncio
async def test_select_channel_fail_open_when_all_models_avoided() -> None:
    dispatcher, model_a, model_b = _build_dispatcher_with_two_models()

    chosen = await dispatcher._select_channel(avoid_models={model_a, model_b})

    assert chosen is not None
    assert chosen.client.model_name in {model_a, model_b}
