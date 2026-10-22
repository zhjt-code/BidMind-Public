"""上下文回拼测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.retriever.context_reassembler import ContextReassembler
from app.core.retriever.semantic_retriever import SearchResult
from app.models.chunk import Chunk


def _make_chunk(
    *,
    chunk_id: int,
    document_id: int = 1,
    content: str,
    page_num: int = 1,
    chunk_index: int = 0,
    chunk_type: str = "text",
    parent_block_id: str | None = "block-1",
) -> Chunk:
    chunk = Chunk(
        document_id=document_id,
        content=content,
        embedding=[0.0] * 1024,
        chunk_type=chunk_type,
        parent_block_id=parent_block_id,
        section_path="资格要求",
        chunk_fingerprint=f"fp-{chunk_id}",
        embedding_version="bge-m3-v1",
        page_num=page_num,
        chunk_index=chunk_index,
        char_count=len(content),
    )
    chunk.id = chunk_id
    return chunk


@pytest.mark.asyncio
async def test_context_reassembler_adds_neighbor_chunks() -> None:
    db = AsyncMock()
    primary = MagicMock()
    primary.scalars.return_value.all.return_value = [
        _make_chunk(chunk_id=1, content="主体块", chunk_index=5),
    ]
    neighbors = MagicMock()
    neighbors.scalars.return_value.all.return_value = [
        _make_chunk(chunk_id=2, content="前置上下文", chunk_index=4),
        _make_chunk(chunk_id=3, content="后置上下文", chunk_index=6),
    ]
    db.execute.side_effect = [primary, neighbors]

    reassembler = ContextReassembler(db)
    context = await reassembler.expand(
        document_id=1,
        search_results=[SearchResult(chunk_id=1, document_id=1, content="主体块", page_num=1, similarity=0.9)],
        image_chunks=[],
    )

    assert len(context.primary_results) == 3
    assert {item.chunk_id for item in context.added_neighbors} == {2, 3}


@pytest.mark.asyncio
async def test_context_reassembler_promotes_image_neighbors_to_image_results() -> None:
    db = AsyncMock()
    primary = MagicMock()
    primary.scalars.return_value.all.return_value = [
        _make_chunk(chunk_id=1, content="主体块", chunk_index=5),
    ]
    neighbors = MagicMock()
    neighbors.scalars.return_value.all.return_value = [
        _make_chunk(chunk_id=4, content="图片证据", chunk_index=6, chunk_type="image_evidence"),
    ]
    db.execute.side_effect = [primary, neighbors]

    reassembler = ContextReassembler(db)
    context = await reassembler.expand(
        document_id=1,
        search_results=[SearchResult(chunk_id=1, document_id=1, content="主体块", page_num=1, similarity=0.9)],
        image_chunks=[],
    )

    assert len(context.image_results) == 1
    assert context.image_results[0].chunk_id == 4


def _make_result_stub(items) -> MagicMock:
    stub = MagicMock()
    stub.scalars.return_value.all.return_value = items
    return stub


def _table_row(chunk_id: int, chunk_index: int, parent: str, content: str) -> Chunk:
    return _make_chunk(
        chunk_id=chunk_id,
        content=content,
        chunk_index=chunk_index,
        chunk_type="table_row_with_header",
        parent_block_id=parent,
    )


@pytest.mark.asyncio
async def test_table_seed_pulls_far_siblings_without_index_radius() -> None:
    """: 同 parent_block_id 的远距离表格行(超出半径)必须被补齐。"""
    db = AsyncMock()
    seed = _table_row(1, 5, "tbl-1", "表格行种子")
    db.execute.side_effect = [
        _make_result_stub([seed]),
        _make_result_stub([
            seed,  # 兄弟查询会返回种子自身, 必须被排除
            _table_row(21, 6, "tbl-1", "同表近行"),
            _table_row(22, 50, "tbl-1", "同表远行"),
        ]),
    ]

    reassembler = ContextReassembler(db)
    context = await reassembler.expand(
        document_id=1,
        search_results=[
            SearchResult(
                chunk_id=1, document_id=1, content="表格行种子",
                page_num=3, similarity=0.9, chunk_type="table_row_with_header",
            ),
        ],
        image_chunks=[],
    )

    added_ids = {item.chunk_id for item in context.added_neighbors}
    assert added_ids == {21, 22}, "半径外的同表远行必须被补齐且排除种子自身"
    sibling_sql = str(db.execute.call_args_list[1].args[0])
    sibling_where = sibling_sql.split("WHERE", 1)[1]
    assert "parent_block_id" in sibling_where
    assert "chunk_index >=" not in sibling_where, "同父块兄弟查询禁止 chunk_index 下界"
    assert "chunk_index <=" not in sibling_where, "同父块兄弟查询禁止 chunk_index 上界"
    assert "page_num" not in sibling_where, "表格兄弟查询按父块取行, 不掺页码条件"


@pytest.mark.asyncio
async def test_multiple_table_parents_share_budget_fairly() -> None:
    """: 多父块按种子相似度降序轮转分配预算, 首父块不得吃满。"""
    db = AsyncMock()
    seed_a = _table_row(1, 5, "tbl-1", "父块A种子")
    seed_b = _table_row(2, 40, "tbl-2", "父块B种子")
    db.execute.side_effect = [
        _make_result_stub([seed_a, seed_b]),
        _make_result_stub([
            _table_row(11, 6, "tbl-1", "A行1"),
            _table_row(12, 7, "tbl-1", "A行2"),
            _table_row(13, 8, "tbl-1", "A行3"),
        ]),
        _make_result_stub([
            _table_row(21, 41, "tbl-2", "B行1"),
            _table_row(22, 42, "tbl-2", "B行2"),
        ]),
    ]

    reassembler = ContextReassembler(db)
    reassembler.max_expansions = 2  # 收紧预算, 暴露"先到先得"分配缺陷

    context = await reassembler.expand(
        document_id=1,
        search_results=[
            SearchResult(chunk_id=1, document_id=1, content="父块A种子",
                         page_num=3, similarity=0.9, chunk_type="table_row_with_header"),
            SearchResult(chunk_id=2, document_id=1, content="父块B种子",
                         page_num=9, similarity=0.8, chunk_type="table_row_with_header"),
        ],
        image_chunks=[],
    )

    added_ids = {item.chunk_id for item in context.added_neighbors}
    assert added_ids == {11, 21}, "预算必须在两个父块间轮转, 禁止首父块耗尽"


@pytest.mark.asyncio
async def test_different_parent_tables_query_separately_and_never_merge() -> None:
    """ 刻意边界: 不同 parent_block_id 的表格逐父块单独查询, 禁止合并。"""
    db = AsyncMock()
    seed_a = _table_row(1, 5, "tbl-1", "父块A种子")
    seed_b = _table_row(2, 40, "tbl-2", "父块B种子")
    db.execute.side_effect = [
        _make_result_stub([seed_a, seed_b]),
        _make_result_stub([_table_row(11, 6, "tbl-1", "A行1")]),
        _make_result_stub([_table_row(21, 41, "tbl-2", "B行1")]),
    ]

    reassembler = ContextReassembler(db)
    context = await reassembler.expand(
        document_id=1,
        search_results=[
            SearchResult(chunk_id=1, document_id=1, content="父块A种子",
                         page_num=3, similarity=0.9, chunk_type="table_row_with_header"),
            SearchResult(chunk_id=2, document_id=1, content="父块B种子",
                         page_num=9, similarity=0.8, chunk_type="table_row_with_header"),
        ],
        image_chunks=[],
    )

    assert db.execute.call_count == 3, "种子查询 + 每父块各一条兄弟查询"
    stmt_a = db.execute.call_args_list[1].args[0]
    stmt_b = db.execute.call_args_list[2].args[0]
    params_a = [str(v) for v in stmt_a.compile().params.values()]
    params_b = [str(v) for v in stmt_b.compile().params.values()]
    assert any("tbl-1" in v for v in params_a) and not any("tbl-2" in v for v in params_a)
    assert any("tbl-2" in v for v in params_b) and not any("tbl-1" in v for v in params_b)
    assert "page_num" not in str(stmt_a).split("WHERE", 1)[1], (
        "父块查询不得混入页码条件(防跨表拼接)"
    )
    added_ids = {item.chunk_id for item in context.added_neighbors}
    assert added_ids == {11, 21}
