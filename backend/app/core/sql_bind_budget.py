"""
职责: 按 asyncpg 单语句绑定上限把多行 INSERT 切成参数预算内的批次
不做: 不执行 SQL、不管理会话与事务、不折叠 IN/NOT IN、不解释 ON CONFLICT 语义
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, TypeVar

from sqlalchemy.dialects import postgresql

ASYNCPG_BIND_PARAM_LIMIT = 32767

RowMapping = TypeVar("RowMapping", bound=Mapping[str, Any])


def count_compiled_bind_params(stmt: Any) -> int:
    """编译语句后的绑定参数个数; 与 asyncpg 拒发判定同一口径。"""
    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    return len(compiled.params)


def extra_binds_beyond_values(
    stmt: Any,
    *,
    row_count: int,
    column_count: int,
) -> int:
    """VALUES 之外的绑定数, 供 ON CONFLICT / RETURNING 预留预算。"""
    return max(0, count_compiled_bind_params(stmt) - row_count * column_count)


def max_rows_per_bind_insert(
    column_count: int,
    *,
    extra_binds: int = 0,
    param_limit: int = ASYNCPG_BIND_PARAM_LIMIT,
) -> int:
    """单条 INSERT 在绑定上限内能容纳的最大行数。"""
    if column_count < 1:
        raise ValueError("column_count must be >= 1")
    if extra_binds < 0:
        raise ValueError("extra_binds must be >= 0")
    usable = param_limit - extra_binds
    if usable < column_count:
        raise ValueError("bind budget cannot fit one row")
    return usable // column_count


def iter_bind_safe_row_batches(
    rows: Sequence[RowMapping],
    *,
    extra_binds: int = 0,
    column_count: int | None = None,
    param_limit: int = ASYNCPG_BIND_PARAM_LIMIT,
) -> Iterator[list[RowMapping]]:
    """把多行映射切成每批绑定不超过 param_limit 的片段。"""
    if not rows:
        return
    inferred = column_count if column_count is not None else max(len(row) for row in rows)
    batch_size = max_rows_per_bind_insert(
        inferred,
        extra_binds=extra_binds,
        param_limit=param_limit,
    )
    for start in range(0, len(rows), batch_size):
        yield list(rows[start : start + batch_size])
