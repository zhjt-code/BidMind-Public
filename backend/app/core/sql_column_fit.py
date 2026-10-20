"""
职责: 按 SQLAlchemy 列定义宽度裁剪字符串, 使 VARCHAR 写入不超过库列上限
不做: 不执行 SQL、不管理会话、不改非字符串列、不发明列宽数字
"""

from __future__ import annotations

from typing import Any, Optional


def fit_string_column(value: Optional[str], column: Any) -> Optional[str]:
    """按列定义宽度截断自由文本, 宽度取自模型列定义单一来源。

    VARCHAR 无 length(如 Text) 时原样返回。None 直通。
    """
    if value is None:
        return None
    max_len = getattr(column.type, "length", None)
    if max_len is not None and len(value) > max_len:
        return value[:max_len]
    return value
