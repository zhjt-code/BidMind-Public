"""管理端关键词筛选的安全数值解析工具。

职责: 把"纯数字关键词"安全解析为可与 PostgreSQL INTEGER 列比较的 int32 值。
不做: 不构造任何 SQL 条件、不关心具体业务字段语义、不做字符串模糊匹配。
"""

from typing import Optional


# PostgreSQL INTEGER (int4) 上限; 超过则不可下推到 INTEGER 列比较, 否则触发 DataError。
PG_INT32_MAX = 2_147_483_647


def parse_pg_int32(raw: Optional[str]) -> Optional[int]:
    """将纯数字关键词安全解析为 int32 可比较的值。

 仅当 raw 去空白后为纯数字且不超过 int32 上限时返回 int, 否则返回 None。
 用于避免把超长数字关键词直接下推到 INTEGER 列比较。

 Args:
 raw: 原始关键词 (可能为 None / 含空白 / 非数字 / 超长数字)。

 Returns:
 合法 int32 值, 或 None (表示不应作为 ID 精确匹配)。
 """
    if not raw:
        return None
    key = raw.strip()
    if not key.isdigit():
        return None
    try:
        value = int(key)
    except ValueError:
        return None
    return value if value <= PG_INT32_MAX else None
