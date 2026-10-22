"""成对最大/最小值评分列检测（/Task3）。

职责: 识别表头中明确成对声明最大值/最小值的列对（精确同义兼容最高/最低、
 上限/下限），在无独立权威分值/满分分值列且最大值列数据充分时返回最大值列索引。
不做: 不判断整表类型、不推断其他列角色、不调用LLM/DB。
"""

from __future__ import annotations

import re

from app.core.extractor.scoring.score_column_semantics import (
    SCORE_HEADER_TERMS,
    is_total_row,
    normalize_cell,
    parse_short_score_tolerant,
)

# 精确同义词集合（normalize_cell后全等匹配）
_MAX_TERMS: frozenset[str] = frozenset(["最大值", "最高", "最高值", "上限"])
_MIN_TERMS: frozenset[str] = frozenset(["最小值", "最低", "最低值", "下限"])

# 权威分值列词集（精确全等匹配）：
#   - 基于 SCORE_HEADER_TERMS（共享词表），确保与全局词表演进同步；
#   - 追加"满分分值"：SCORE_HEADER_TERMS 未收录的常见复合词；
#   - 显式排除 _MAX_TERMS | _MIN_TERMS：成对配对词永不作权威信号，
#     防词表扩充时意外将"最大值"/"最高"等升格为权威列（当前无重叠）。
# 设计原因：表头中一旦出现独立权威分值列，应跳过成对检测，
# 由旧逻辑直接选取该列，避免 max/min 配对错误覆盖真正的分值列。
_AUTHORITY_TERMS: frozenset[str] = (
    frozenset(SCORE_HEADER_TERMS)
    | frozenset(["满分分值"])         # 复合词：精确匹配语义无歧义
) - (_MAX_TERMS | _MIN_TERMS)         # 成对词永不入权威集合

_DIGITS_RE = re.compile(r"^\d+(?:\.\d+)?$")


def find_paired_header_columns(header: list[str]) -> tuple[int, int] | None:
    """在表头中寻找明确成对的最大值/最小值列，返回(max_idx, min_idx)或None。

    有独立权威分值列（_AUTHORITY_TERMS，基于 SCORE_HEADER_TERMS）时返回 None，
    由旧逻辑选取权威列。仅 max 无 min 或仅 min 无 max 时同样返回 None。
    """
    max_idx: int | None = None
    min_idx: int | None = None

    for i, cell in enumerate(header):
        norm = normalize_cell(cell)
        if norm in _AUTHORITY_TERMS:
            return None
        if norm in _MAX_TERMS:
            max_idx = i
        elif norm in _MIN_TERMS:
            min_idx = i

    if max_idx is None or min_idx is None:
        return None
    return (max_idx, min_idx)


def validate_and_select_max(
    max_idx: int,
    min_idx: int,
    data_rows: list[list[str]],
    *,
    min_score_rows: int = 3,
) -> int | None:
    """验证最大值列数据充分性与max>=min约束，充分时返回max_idx，否则返回None。

    规则:
    - max列至少min_score_rows行含正分值（>0）
    - 可比行（max和min均可解析为数值）中max<min时拒绝整张表
    - 含0的可比行仍参与max>=min验证
    """
    positive_hits = 0

    for row in data_rows:
        if is_total_row(row):
            continue

        max_val = _parse_numeric_cell(row, max_idx)
        min_val = _parse_numeric_cell(row, min_idx)

        if max_val is not None and min_val is not None:
            if max_val < min_val:
                return None

        pos = parse_short_score_tolerant(_cell(row, max_idx))
        # parse_short_score_tolerant 仅在 0 < value <= 200 时返回非 None，
        # pos is not None 与 fallback 路径惯用法一致，列0同等合法。
        if pos is not None:
            positive_hits += 1

    return max_idx if positive_hits >= min_score_rows else None


def _parse_numeric_cell(row: list[str], idx: int) -> float | None:
    """解析行中指定列为非负数值（0或正均可），非数值格返回None。"""
    if idx < 0 or idx >= len(row):
        return None
    norm = normalize_cell(row[idx])
    if not norm or not _DIGITS_RE.match(norm):
        return None
    try:
        return float(norm)
    except ValueError:
        return None


def _cell(row: list[str], idx: int) -> str:
    return str(row[idx] or "").strip() if 0 <= idx < len(row) else ""


__all__ = [
    "find_paired_header_columns",
    "validate_and_select_max",
]
