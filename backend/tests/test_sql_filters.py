"""parse_pg_int32 边界守卫单元测试 (同源遗漏防御)。

验证: 仅当关键词为纯数字且 <= int32 上限时返回 int, 否则返回 None;
避免把超长数字关键词下推到 PostgreSQL INTEGER 列比较触发 DataError。
"""

from app.core.sql_filters import PG_INT32_MAX, parse_pg_int32


def test_normal_values():
    assert parse_pg_int32("0") == 0
    assert parse_pg_int32("501") == 501
    assert parse_pg_int32(str(PG_INT32_MAX)) == PG_INT32_MAX


def test_boundary_at_int32_limit():
    # 恰好 int32 上限可用; 上限 + 1 必须拒绝 (核心边界)
    assert parse_pg_int32("2147483647") == 2147483647
    assert parse_pg_int32("2147483648") is None


def test_oversized_numeric_returns_none():
    # 19 位 time_ns 量级数字, 即触发过 int32 越界 500 的真实输入
    assert parse_pg_int32("1782114188410628300") is None


def test_non_numeric_returns_none():
    assert parse_pg_int32("abc") is None
    assert parse_pg_int32("12a") is None
    assert parse_pg_int32("3.14") is None
    assert parse_pg_int32("-5") is None  # 负号使 isdigit() 为 False


def test_empty_and_none_return_none():
    assert parse_pg_int32(None) is None
    assert parse_pg_int32("") is None
    assert parse_pg_int32("   ") is None


def test_strips_surrounding_whitespace():
    assert parse_pg_int32("  501  ") == 501
