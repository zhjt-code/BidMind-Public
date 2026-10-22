""" 防御测试: 大列表 SQL 条件的绑定参数数量恒定。

风险映射: asyncpg 单语句绑定参数硬上限 32767; 逐元素 IN/NOT IN 展开随文档
分块数线性增长, 超大文档向量化的清理/回读语句会直接抛 InterfaceError。
本组测试以"参数数量不随集合规模增长"为核心断言。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import Integer, String, delete, select
from sqlalchemy.dialects import postgresql

from app.core.sql_array_filters import in_array_values, not_in_array_values
from app.models.chunk import Chunk
from app.models.writing_material_chunk import WritingMaterialChunk

ASYNCPG_PARAM_LIMIT = 32767
# 取超过 asyncpg 上限的集合规模, 复现 现场量级
OVERSIZED_COUNT = ASYNCPG_PARAM_LIMIT + 5000


def _compiled_params(stmt) -> dict:
    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    return dict(compiled.params)


def test_chunk_cleanup_delete_params_constant_beyond_asyncpg_limit() -> None:
    """ 核心: 指纹集合超过 32767 时, 清理 DELETE 仍只占 2 个绑定参数。"""
    fingerprints = [f"fp-{i:06d}" for i in range(OVERSIZED_COUNT)]
    stmt = delete(Chunk).where(
        Chunk.document_id == 1,
        not_in_array_values(
            Chunk.chunk_fingerprint, fingerprints, element_type=String()
        ),
    )
    params = _compiled_params(stmt)

    assert len(params) == 2  # document_id + 数组参数
    assert len(params) < ASYNCPG_PARAM_LIMIT
    array_values = [v for v in params.values() if isinstance(v, list)]
    assert len(array_values) == 1
    assert len(array_values[0]) == OVERSIZED_COUNT  # 集合完整传入, 未丢元素


def test_chunk_readback_select_params_constant_beyond_asyncpg_limit() -> None:
    """ 同类: inserted_ids 回读 SELECT 的参数数量不随 id 数量增长。"""
    inserted_ids = list(range(OVERSIZED_COUNT))
    stmt = select(Chunk).where(
        in_array_values(Chunk.id, inserted_ids, element_type=Integer())
    )
    params = _compiled_params(stmt)

    assert len(params) == 1  # 仅数组参数
    array_values = [v for v in params.values() if isinstance(v, list)]
    assert len(array_values[0]) == OVERSIZED_COUNT


def test_material_cleanup_delete_params_constant_beyond_asyncpg_limit() -> None:
    """ 同类: 素材向量清理 DELETE 同样保持参数数量恒定。"""
    fingerprints = [f"mfp-{i:06d}" for i in range(OVERSIZED_COUNT)]
    stmt = delete(WritingMaterialChunk).where(
        WritingMaterialChunk.material_id == 7,
        not_in_array_values(
            WritingMaterialChunk.chunk_fingerprint,
            fingerprints,
            element_type=String(),
        ),
    )
    params = _compiled_params(stmt)

    assert len(params) == 2
    array_values = [v for v in params.values() if isinstance(v, list)]
    assert len(array_values[0]) == OVERSIZED_COUNT


def test_rendered_sql_keeps_not_in_semantics_via_unnest() -> None:
    """语义护栏: 渲染 SQL 必须保留对原列的 NOT IN 判定, 且集合经 unnest 子查询注入。

    防止后续重构改成分批 NOT IN(每批会误删其他批次合法行)或改变比较语义。
    """
    stmt = delete(Chunk).where(
        Chunk.document_id == 1,
        not_in_array_values(Chunk.chunk_fingerprint, ["a", "b"], element_type=String()),
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "chunks.chunk_fingerprint" in sql
    assert "NOT IN (SELECT unnest(" in sql


def test_rendered_sql_keeps_in_semantics_via_unnest() -> None:
    """语义护栏: IN 版本同样经 unnest 子查询注入。"""
    stmt = select(Chunk).where(
        in_array_values(Chunk.id, [1, 2, 3], element_type=Integer())
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "chunks.id IN (SELECT unnest(" in sql
