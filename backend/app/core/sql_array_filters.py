"""大列表 SQL 条件的单参数数组表达式。

职责: 把大规模值集合折叠为单个 PostgreSQL 数组绑定参数的 IN / NOT IN 等价条件。
不做: 不管理会话与事务、不承担业务过滤语义、不处理空集合语义(由调用方保证非空)。

背景: asyncpg 单条语句绑定参数硬上限为 32767。`col.in_(values)` /
`col.not_in(values)` 会为集合中每个元素展开一个独立参数, 集合规模随文档分块数
线性增长时必然突破上限。本模块用 `unnest(:array)` 子查询把整个集合折叠为
1 个数组参数, 参数数量恒定且语义与原生 IN / NOT IN 一致。

注意: NOT IN 语义不可按批拆分(每批会误删其他批次的合法行), 必须整体传入。
"""

from typing import Any, Collection

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.type_api import TypeEngine


def _array_subquery(values: Collection[Any], element_type: TypeEngine):
    """构造 SELECT unnest(:array) 标量子查询, 整个集合仅占 1 个绑定参数。"""
    array_param = literal(list(values), type_=ARRAY(element_type))
    return select(func.unnest(array_param)).scalar_subquery()


def in_array_values(
    column: ColumnElement,
    values: Collection[Any],
    *,
    element_type: TypeEngine,
) -> ColumnElement[bool]:
    """col IN (SELECT unnest(:array)) —— 单参数替代逐元素 IN 展开。

    Args:
        column: 参与比较的列。
        values: 非空值集合。
        element_type: 数组元素的 SQLAlchemy 类型, 如 String() / Integer()。
    """
    return column.in_(_array_subquery(values, element_type))


def not_in_array_values(
    column: ColumnElement,
    values: Collection[Any],
    *,
    element_type: TypeEngine,
) -> ColumnElement[bool]:
    """col NOT IN (SELECT unnest(:array)) —— 单参数替代逐元素 NOT IN 展开。

    与原生 NOT IN 一致, 空集合会使条件退化为恒真(全表命中),
    调用方必须先以 `if values:` 守卫。
    """
    return column.not_in(_array_subquery(values, element_type))
