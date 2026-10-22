"""node_pool_router 池用途选择与 chat_json 转发测试。

覆盖目标:
- select_pool_purpose: mechanical→extract / judgment→review / 缺 key→review /
  extract 无通道→review / 显式 node_key 优先于 contextvar。
- NodePoolRoutingClient.chat_json: mechanical→extract_client，judgment→review_client。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.node_pool_router import (
    NodePoolRoutingClient,
    select_pool_purpose,
)
from app.core.ai.prompt_registry import current_node_key


def _meta(node_class: str) -> SimpleNamespace:
    return SimpleNamespace(node_class=node_class)


def _patch_registry(meta_by_key: dict[str, SimpleNamespace]):
    registry = SimpleNamespace(
        get_node_meta=lambda key: meta_by_key.get(key),
    )
    return patch(
        "app.core.ai.prompt_registry.get_registry",
        return_value=registry,
    )


def test_select_pool_purpose_mechanical_with_extract_channels() -> None:
    """mechanical + extract 有通道 → extract。"""
    with _patch_registry({"n.mech": _meta("mechanical")}):
        assert (
            select_pool_purpose(
                explicit_node_key="n.mech",
                extract_has_channels=True,
            )
            == "extract"
        )


def test_select_pool_purpose_judgment_routes_to_review() -> None:
    """judgment → review。"""
    with _patch_registry({"n.judge": _meta("judgment")}):
        assert (
            select_pool_purpose(
                explicit_node_key="n.judge",
                extract_has_channels=True,
            )
            == "review"
        )


def test_select_pool_purpose_missing_node_key_routes_to_review() -> None:
    """node_key 缺失 → review。"""
    token = current_node_key.set(None)
    try:
        assert select_pool_purpose(extract_has_channels=True) == "review"
    finally:
        current_node_key.reset(token)


def test_select_pool_purpose_no_extract_channels_forces_review() -> None:
    """extract_has_channels=False → review（即使 mechanical）。"""
    with _patch_registry({"n.mech": _meta("mechanical")}):
        assert (
            select_pool_purpose(
                explicit_node_key="n.mech",
                extract_has_channels=False,
            )
            == "review"
        )


def test_select_pool_purpose_explicit_node_key_overrides_contextvar() -> None:
    """显式 mechanical node_key 优先于 contextvar 中的 judgment。"""
    token = current_node_key.set("n.judge")
    try:
        with _patch_registry(
            {
                "n.judge": _meta("judgment"),
                "n.mech": _meta("mechanical"),
            },
        ):
            assert (
                select_pool_purpose(
                    explicit_node_key="n.mech",
                    extract_has_channels=True,
                )
                == "extract"
            )
    finally:
        current_node_key.reset(token)


@pytest.mark.asyncio
async def test_chat_json_forwards_mechanical_to_extract_client() -> None:
    """mechanical 节点 chat_json 转发到 extract_client。"""
    review_client = AsyncMock()
    review_client.channel_count = 1
    review_client.chat_json = AsyncMock(return_value={"from": "review"})

    extract_client = AsyncMock()
    extract_client.channel_count = 1
    extract_client.chat_json = AsyncMock(return_value={"from": "extract"})

    router = NodePoolRoutingClient(review_client, extract_client)
    with _patch_registry({"n.mech": _meta("mechanical")}):
        result = await router.chat_json("hello", node_key="n.mech")

    assert result == {"from": "extract"}
    extract_client.chat_json.assert_awaited()
    review_client.chat_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_json_forwards_judgment_to_review_client() -> None:
    """judgment 节点 chat_json 转发到 review_client。"""
    review_client = AsyncMock()
    review_client.channel_count = 1
    review_client.chat_json = AsyncMock(return_value={"from": "review"})

    extract_client = AsyncMock()
    extract_client.channel_count = 1
    extract_client.chat_json = AsyncMock(return_value={"from": "extract"})

    router = NodePoolRoutingClient(review_client, extract_client)
    with _patch_registry({"n.judge": _meta("judgment")}):
        result = await router.chat_json("hello", node_key="n.judge")

    assert result == {"from": "review"}
    review_client.chat_json.assert_awaited()
    extract_client.chat_json.assert_not_awaited()
