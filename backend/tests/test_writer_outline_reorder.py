from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ServiceError
from app.models.writing_outline_node import OutlineNodeType
from app.services.writing_service import WritingService


class _FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalarResult(self._items)


@pytest.mark.asyncio
async def test_recalculate_outline_depths_follows_parent_links() -> None:
    nodes = [
        SimpleNamespace(id=1, parent_id=None, depth=9, sort_order=0),
        SimpleNamespace(id=2, parent_id=1, depth=9, sort_order=0),
        SimpleNamespace(id=3, parent_id=2, depth=9, sort_order=0),
        SimpleNamespace(id=4, parent_id=1, depth=9, sort_order=1),
    ]
    fake_db = SimpleNamespace(
        execute=AsyncMock(return_value=_FakeExecuteResult(nodes)),
    )
    service = WritingService(fake_db)

    await service._recalculate_outline_depths(project_id=1)

    assert nodes[0].depth == 0
    assert nodes[1].depth == 1
    assert nodes[2].depth == 2
    assert nodes[3].depth == 1


@pytest.mark.asyncio
async def test_update_outline_node_strips_number_prefix() -> None:
    node = SimpleNamespace(id=10, project_id=1, title="旧标题")
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=node),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=1))  # type: ignore[method-assign]
    service._invalidate_render_artifact = AsyncMock()  # type: ignore[method-assign]

    updated = await service.update_outline_node(
        project_id=1,
        node_id=10,
        user=SimpleNamespace(),
        data={"title": "第1部分 新的标题"},
    )

    assert updated.title == "新的标题"


@pytest.mark.asyncio
async def test_update_outline_node_clears_target_word_count_for_fixed_template() -> None:
    node = SimpleNamespace(
        id=11,
        project_id=1,
        title="业绩汇总表",
        node_type=OutlineNodeType.data_fill,
        target_word_count=900,
    )
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=node),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=1))  # type: ignore[method-assign]
    service._invalidate_render_artifact = AsyncMock()  # type: ignore[method-assign]

    updated = await service.update_outline_node(
        project_id=1,
        node_id=11,
        user=SimpleNamespace(),
        data={"node_type": "fixed_template"},
    )

    assert updated.node_type == OutlineNodeType.fixed_template
    assert updated.target_word_count is None


@pytest.mark.asyncio
async def test_update_outline_node_rejects_data_fill_target_word_count_above_limit() -> None:
    node = SimpleNamespace(
        id=12,
        project_id=1,
        title="业绩汇总表",
        node_type=OutlineNodeType.data_fill,
        target_word_count=900,
    )
    fake_db = SimpleNamespace(
        get=AsyncMock(return_value=node),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = WritingService(fake_db)
    service._get_project_or_raise = AsyncMock(return_value=SimpleNamespace(id=1))  # type: ignore[method-assign]
    service._invalidate_render_artifact = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(ServiceError, match="data_fill 章节建议字数需在 100 到 3000 之间"):
        await service.update_outline_node(
            project_id=1,
            node_id=12,
            user=SimpleNamespace(),
            data={"target_word_count": 4000},
        )


@pytest.mark.asyncio
async def test_recalculate_outline_depths_handles_four_level_tree() -> None:
    """深层目录树(4级)的 depth 重算"""
    nodes = [
        SimpleNamespace(id=1, parent_id=None, depth=9, sort_order=0),
        SimpleNamespace(id=2, parent_id=1, depth=9, sort_order=0),
        SimpleNamespace(id=3, parent_id=2, depth=9, sort_order=0),
        SimpleNamespace(id=4, parent_id=3, depth=9, sort_order=0),
        SimpleNamespace(id=5, parent_id=3, depth=9, sort_order=1),
        SimpleNamespace(id=6, parent_id=2, depth=9, sort_order=1),
        SimpleNamespace(id=7, parent_id=1, depth=9, sort_order=1),
    ]
    fake_db = SimpleNamespace(
        execute=AsyncMock(return_value=_FakeExecuteResult(nodes)),
    )
    service = WritingService(fake_db)

    await service._recalculate_outline_depths(project_id=1)

    assert nodes[0].depth == 0
    assert nodes[1].depth == 1
    assert nodes[2].depth == 2
    assert nodes[3].depth == 3
    assert nodes[4].depth == 3
    assert nodes[5].depth == 2
    assert nodes[6].depth == 1
