"""LLM 节点 node_class 配置契约测试。

覆盖:
1. NodeConfigUpdate 接受 mechanical/judgment，拒绝非法值；
2. NodeSummary 含 node_class，默认 judgment；
3. VersionService.update_node_config 写入 node_class（AsyncMock db）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.schemas.llm_center import NodeConfigUpdate, NodeSummary  # noqa: E402
from app.services.llm_center.version_service import VersionService  # noqa: E402


def test_node_config_update_accepts_mechanical_and_judgment() -> None:
    mechanical = NodeConfigUpdate(node_class="mechanical")
    judgment = NodeConfigUpdate(node_class="judgment")
    assert mechanical.node_class == "mechanical"
    assert judgment.node_class == "judgment"


def test_node_config_update_rejects_invalid_node_class() -> None:
    with pytest.raises(ValidationError):
        NodeConfigUpdate(node_class="invalid")  # type: ignore[arg-type]


def test_node_summary_defaults_node_class_to_judgment() -> None:
    summary = NodeSummary(
        node_key="bid_review.tender_extract_page",
        name="招标要求提取",
        visibility="main",
        editable_level="advanced",
        execution_channel="review",
        call_method="chat_json",
        default_temperature=0.1,
        default_max_tokens=2000,
    )
    assert summary.node_class == "judgment"


@pytest.mark.asyncio
async def test_update_node_config_writes_node_class() -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    node = MagicMock()
    node.node_class = "judgment"

    registry = MagicMock()
    registry.refresh = AsyncMock()

    with (
        patch(
            "app.services.llm_center.version_service._get_node_or_raise",
            new=AsyncMock(return_value=node),
        ),
        patch(
            "app.services.llm_center.version_service._broadcast_invalidate",
            new=AsyncMock(),
        ),
        patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ),
    ):
        service = VersionService(db)
        updated = await service.update_node_config(
            "bid_review.tender_extract_page",
            NodeConfigUpdate(node_class="mechanical"),
        )

    assert updated == {"node_class": "mechanical"}
    assert node.node_class == "mechanical"
    db.flush.assert_awaited()
    registry.refresh.assert_awaited()
