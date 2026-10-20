""": 空/空白卷名不得打穿照搬目录生成。

问题形态: 上游标题类字段未归一，下游 OutlineNodeGenerated 硬校验打穿任务。
夹具1: volume_title=""；夹具2（同形态）: volume_title=" "。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.writer.outline_generator import OutlineGenerator
from app.schemas.writing import (
    BidDirectoryItem,
    BidDirectorySpec,
    BidVolumeSpec,
    OutlineNodeGenerated,
)


def test_bid_volume_spec_strips_and_keeps_empty_volume_title() -> None:
    empty = BidVolumeSpec(volume_type="technical", volume_title="", items=[])
    blank = BidVolumeSpec(volume_type="technical", volume_title="   ", items=[])
    assert empty.volume_title == ""
    assert blank.volume_title == ""


def test_bid_directory_item_rejects_blank_title() -> None:
    with pytest.raises(ValidationError):
        BidDirectoryItem(order=1, title="   ")


def test_outline_node_generated_rejects_blank_title() -> None:
    with pytest.raises(ValidationError):
        OutlineNodeGenerated(title="", node_type="free_writing")
    with pytest.raises(ValidationError):
        OutlineNodeGenerated(title="   ", node_type="free_writing")


def _spec_with_volume_title(volume_title: str) -> BidDirectorySpec:
    return BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "technical",
                "volume_title": volume_title,
                "items": [
                    {"order": 1, "title": "技术方案"},
                    {"order": 2, "title": "实施方案"},
                ],
            }
        ],
    )


async def _run_from_spec(
    monkeypatch: pytest.MonkeyPatch,
    volume_title: str,
) -> list[OutlineNodeGenerated]:
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    saved: list[OutlineNodeGenerated] = []

    async def _capture_save(_project_id: int, nodes: list[OutlineNodeGenerated]):
        saved.extend(nodes)
        return nodes

    monkeypatch.setattr(
        generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        generator, "_enrich_skeleton_node", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(generator, "_save_tree", _capture_save)

    await generator._generate_from_spec(7, _spec_with_volume_title(volume_title), 30000)
    return saved


@pytest.mark.asyncio
async def test_empty_volume_title_omits_wrapper_and_keeps_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """夹具1: 空 volume_title 不崩溃，合法 items 提升为顶层节点。"""
    saved = await _run_from_spec(monkeypatch, "")
    assert [n.title for n in saved] == ["技术方案", "实施方案"]
    assert all(n.depth == 0 for n in saved)


@pytest.mark.asyncio
async def test_whitespace_volume_title_omits_wrapper_same_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """夹具2（同形态不同实体）: 空白 volume_title 同样省略包装、保留条目。"""
    saved = await _run_from_spec(monkeypatch, "   ")
    assert [n.title for n in saved] == ["技术方案", "实施方案"]
    assert all(n.depth == 0 for n in saved)
