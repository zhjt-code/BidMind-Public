"""
职责: 按结果集基数读出「是否有行」或「至多一行」
不做: 不拼 SQL、不感知业务实体、不把多行静默收成第一行
"""

from typing import Any, TypeVar

T = TypeVar("T")


class AmbiguousQueryResult(Exception):
    """查询合同是至多一行, 实际返回了多行。"""


def occupied(result: Any) -> bool:
    """存在性: 0 行 False, 1 行及以上 True。禁止用 one_or_none 代替。"""
    return result.scalars().first() is not None


def unique_scalar_or_none(result: Any) -> T | None:
    """身份查询: 0 行 None, 1 行返回该对象, 多行抛 AmbiguousQueryResult。"""
    rows = result.scalars().all()
    if len(rows) > 1:
        raise AmbiguousQueryResult()
    return rows[0] if rows else None
