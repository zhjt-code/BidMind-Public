""" 防御测试: 多行 INSERT 按绑定预算切块, 单批参数不超过 32767。

风险映射: asyncpg 单语句绑定参数硬上限 32767; `.values(全量rows)` 的参数
数量 = 列数 × 行数 + ON CONFLICT 额外绑定, 随文档规模线性膨胀。

夹具:
1. 13 列库存事实形态（云端 material_facts INSERT 列数）
2. 4 列缓存形态（同形态不同实体, 证明批大小由列数算出而非表名）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.retriever.material_inventory import MaterialFact, persist_material_facts
from app.core.sql_bind_budget import (
    ASYNCPG_BIND_PARAM_LIMIT,
    count_compiled_bind_params,
    extra_binds_beyond_values,
    iter_bind_safe_row_batches,
    max_rows_per_bind_insert,
)

INVENTORY_FACT_COLUMNS = (
    "project_id",
    "canonical_key",
    "year",
    "noun",
    "page",
    "document_id",
    "bid_part",
    "chunk_id",
    "physical_page",
    "identifier",
    "subject",
    "people",
    "excerpt",
)
CACHE_ENTRY_COLUMNS = ("cache_key", "payload", "source", "version")


def _bind_table(name: str, columns: tuple[str, ...]) -> Table:
    metadata = MetaData()
    return Table(name, metadata, *[Column(column, String) for column in columns])


def _rows(columns: tuple[str, ...], count: int) -> list[dict[str, str]]:
    return [{column: f"{column}-{index}" for column in columns} for index in range(count)]


def _compiled_insert_params(table: Table, rows: list[dict[str, str]]) -> int:
    return count_compiled_bind_params(pg_insert(table).values(rows))


def test_unbatched_thirteen_column_insert_exceeds_asyncpg_limit() -> None:
    """形态夹具1: 13 列 × 2521 行一次展开必超上限。"""
    rows = _rows(INVENTORY_FACT_COLUMNS, 2521)
    table = _bind_table("inventory_facts", INVENTORY_FACT_COLUMNS)
    assert _compiled_insert_params(table, rows) > ASYNCPG_BIND_PARAM_LIMIT


def test_thirteen_column_insert_batches_stay_under_limit() -> None:
    """13 列库存事实切块后, 每批编译参数不超过 32767, 行不丢失。"""
    rows = _rows(INVENTORY_FACT_COLUMNS, 2521)
    table = _bind_table("inventory_facts", INVENTORY_FACT_COLUMNS)
    batches = list(iter_bind_safe_row_batches(rows))
    assert len(batches) == 2
    assert sum(len(batch) for batch in batches) == 2521
    for batch in batches:
        assert _compiled_insert_params(table, batch) <= ASYNCPG_BIND_PARAM_LIMIT


def test_unbatched_four_column_insert_exceeds_asyncpg_limit() -> None:
    """形态夹具2: 换 4 列缓存实体, 一次展开同样超限。"""
    overflow_rows = 32767 // 4 + 1
    rows = _rows(CACHE_ENTRY_COLUMNS, overflow_rows)
    table = _bind_table("cache_entries", CACHE_ENTRY_COLUMNS)
    assert _compiled_insert_params(table, rows) > ASYNCPG_BIND_PARAM_LIMIT


def test_four_column_cache_insert_batches_stay_under_limit() -> None:
    """4 列缓存切块后每批不超限; 批大小随列数变大, 不是写死 2520。"""
    rows = _rows(CACHE_ENTRY_COLUMNS, 9000)
    table = _bind_table("cache_entries", CACHE_ENTRY_COLUMNS)
    batches = list(iter_bind_safe_row_batches(rows))
    expected_size = max_rows_per_bind_insert(4)
    assert expected_size == 8191
    assert len(batches[0]) == expected_size
    assert sum(len(batch) for batch in batches) == 9000
    for batch in batches:
        assert _compiled_insert_params(table, batch) <= ASYNCPG_BIND_PARAM_LIMIT


def test_extra_binds_reduce_batch_size() -> None:
    rows = [{"a": "1", "b": "2"} for _ in range(10)]
    batches = list(iter_bind_safe_row_batches(rows, extra_binds=32765))
    assert [len(batch) for batch in batches] == [1] * 10


def test_empty_rows_yield_nothing() -> None:
    assert list(iter_bind_safe_row_batches([])) == []


def test_invalid_column_count_raises() -> None:
    try:
        max_rows_per_bind_insert(0)
    except ValueError as exc:
        assert "column_count" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_extra_binds_beyond_values_counts_on_conflict_literals() -> None:
    table = _bind_table("cache_entries", CACHE_ENTRY_COLUMNS)
    stmt = pg_insert(table).values(_rows(CACHE_ENTRY_COLUMNS, 1))
    stmt = stmt.on_conflict_do_update(
        index_elements=["cache_key"],
        set_={"payload": stmt.excluded.payload},
    )
    extra = extra_binds_beyond_values(stmt, row_count=1, column_count=4)
    assert extra >= 0
    assert count_compiled_bind_params(stmt) == 4 + extra


def _sample_fact(index: int) -> MaterialFact:
    return MaterialFact(
        canonical_material_key=f"key-{index}",
        raw_noun=f"noun-{index}",
        grade="material",
        document_id=1,
        bid_part=None,
        chunk_id=index + 1,
        physical_page=index,
        identifier=None,
        subject=None,
        source_channel="lexical",
        excerpt="x",
    )


def test_persist_material_facts_splits_when_rows_exceed_bind_budget() -> None:
    """调用点必须按预算多次 execute, 且每条语句绑定不超过上限。"""
    facts = [_sample_fact(index) for index in range(2800)]
    statements: list[object] = []

    class _FakeResult:
        rowcount = 10

    class _FakeSession:
        async def execute(self, stmt):
            statements.append(stmt)
            return _FakeResult()

    written = asyncio.run(persist_material_facts(_FakeSession(), 7, facts))
    assert len(statements) >= 2
    assert written == 10 * len(statements)
    for stmt in statements:
        assert count_compiled_bind_params(stmt) <= ASYNCPG_BIND_PARAM_LIMIT
