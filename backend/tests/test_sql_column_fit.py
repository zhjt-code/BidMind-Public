""" 防御测试: VARCHAR 落库按模型列宽裁剪, 禁止超宽触发截断异常。

风险映射: PostgreSQL StringDataRightTruncationError; 自由文本写入
VARCHAR(n) 时未按列定义裁剪, 单条超宽使整批 INSERT 失败并中断提取。

夹具:
1. material_facts 的 255 列(canonical_material_key / raw_noun / subject)
2. 同形态不同宽度: identifier VARCHAR(128)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import Column, Integer, MetaData, String, Table, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.retriever.material_inventory import MaterialFact, persist_material_facts
from app.core.sql_column_fit import fit_string_column
from app.models.material_fact import MaterialFact as MaterialFactRow


def _varchar_column(length: int) -> Column:
    metadata = MetaData()
    table = Table("probe", metadata, Column("body", String(length)))
    return table.c.body


def test_fit_clips_overwide_varchar() -> None:
    column = _varchar_column(255)
    assert fit_string_column("k" * 300, column) == "k" * 255


def test_fit_keeps_short_and_none() -> None:
    column = _varchar_column(255)
    assert fit_string_column("short", column) == "short"
    assert fit_string_column(None, column) is None


def test_fit_text_column_is_not_clipped() -> None:
    metadata = MetaData()
    table = Table("probe", metadata, Column("body", Text()))
    long_text = "e" * 400
    assert fit_string_column(long_text, table.c.body) == long_text


def test_unfitted_varchar_insert_would_exceed_length() -> None:
    """形态夹具1: 255 列一次写入 300 字会超列宽(对照未裁剪路径)。"""
    metadata = MetaData()
    table = Table(
        "inventory_facts",
        metadata,
        Column("id", Integer),
        Column("canonical_material_key", String(255)),
    )
    stmt = pg_insert(table).values(
        [{"id": 1, "canonical_material_key": "k" * 300}]
    )
    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    values = [v for v in compiled.params.values() if isinstance(v, str)]
    assert any(len(v) > 255 for v in values)


def _sample_fact(**overrides) -> MaterialFact:
    payload = dict(
        canonical_material_key="key",
        raw_noun="noun",
        grade="material",
        document_id=1,
        bid_part=None,
        chunk_id=1,
        physical_page=1,
        identifier=None,
        subject=None,
        source_channel="lexical",
        excerpt="x",
    )
    payload.update(overrides)
    return MaterialFact(**payload)


def _compiled_string_params(stmt) -> list[str]:
    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    return [v for v in compiled.params.values() if isinstance(v, str)]


def test_persist_clips_255_noun_and_key() -> None:
    """形态夹具1: 材料键/原名词超过 255 时按列宽裁剪后落库。"""
    columns = MaterialFactRow.__table__.c
    key_width = columns.canonical_material_key.type.length
    noun_width = columns.raw_noun.type.length
    subject_width = columns.subject.type.length
    fact = _sample_fact(
        canonical_material_key="k" * (key_width + 40),
        raw_noun="n" * (noun_width + 40),
        subject="s" * (subject_width + 40),
    )
    statements: list[object] = []

    class _FakeResult:
        rowcount = 1

    class _FakeSession:
        async def execute(self, stmt):
            statements.append(stmt)
            return _FakeResult()

    asyncio.run(persist_material_facts(_FakeSession(), 7, [fact]))
    assert statements
    strings = _compiled_string_params(statements[0])
    assert "k" * key_width in strings
    assert "n" * noun_width in strings
    assert "s" * subject_width in strings
    assert "k" * (key_width + 40) not in strings
    assert "n" * (noun_width + 40) not in strings


def test_persist_clips_128_identifier() -> None:
    """形态夹具2: 换 128 宽的 identifier, 同一裁剪机制覆盖。"""
    columns = MaterialFactRow.__table__.c
    ident_width = columns.identifier.type.length
    assert ident_width == 128
    fact = _sample_fact(identifier="I" * (ident_width + 50))
    statements: list[object] = []

    class _FakeResult:
        rowcount = 1

    class _FakeSession:
        async def execute(self, stmt):
            statements.append(stmt)
            return _FakeResult()

    asyncio.run(persist_material_facts(_FakeSession(), 7, [fact]))
    strings = _compiled_string_params(statements[0])
    assert "I" * ident_width in strings
    assert "I" * (ident_width + 50) not in strings
