"""表格线性化器

职责: 将 ExtractedTable 线性化为语义忠实的行文本片段, 供 ChunkPlanner 拼装 chunk.
不做: 不做分块切分/向量化, 不感知串标/审查/写标业务逻辑.

设计要点:
- 仅当严格判定为"可靠表头表"时才输出 `表头=值；表头=值`(列下标严格对齐, 不过滤空表头).
- 其余一切(含合并主导表/标签-值网格/不规则表)输出忠实结构行文本:
 `单元格1 | 单元格2 | ...`(按原始列序保留非空单元格, 不杜撰 key=value).
- 整行合并的标题行(只有一个非空单元格)自然退化为单行文本.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.parser.table_extractor import ExtractedTable


_HEADER_LABEL_MAX_LEN = 20
_HEADER_LABEL_MIN_LEN = 1


@dataclass(frozen=True)
class TableRowText:
    """线性化后的单行文本片段.

    Attributes:
        row_no: 行号(从1开始), 对应 ExtractedTable 清理后 data 的原始行序.
        content: 该行的线性化内容(不含 "表格第N行：" 前缀).
    """

    row_no: int
    content: str


def linearize_table(table: "ExtractedTable") -> list[TableRowText]:
    """将表格线性化为可读且不会错配的行文本列表.

 判定为"可靠表头表"时, 使用 `表头=值` 形式; 其余情况退化为按列序的结构文本,
 避免因合并单元格或标签-值网格导致键值错配.

 Args:
 table: 已由 TableExtractor 清理过的表格(None 单元格已转空串, 全空行已剔除).

 Returns:
 list[TableRowText]: 行号严格递增的线性化片段(可能少于原行数, 全空行被跳过).
 """
    if not table.data:
        return []

    if _is_reliable_header_table(table):
        return _linearize_header_table(table)
    return _linearize_structural(table)


def join_cell_lines_cjk_aware(text: str) -> str:
    """合并单元格内物理换行: 中文断行无空格, 其他断行保留单空格。"""
    if not text:
        return ""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if not lines:
        return ""
    result = lines[0]
    for line in lines[1:]:
        if result and _is_cjk(result[-1]) and _is_cjk(line[0]):
            result += line
        else:
            result += " " + line
    return result


def _is_reliable_header_table(table: "ExtractedTable") -> bool:
    """严格判定是否为"标准表头表".

    所有条件必须同时成立:
    - 行数 >= 2(至少表头 + 1 行数据)
    - 列数 >= 2
    - 首行每个单元格非空、长度 1-20、非纯数字、不含换行(标签型)
    - 每行列数等于首行列数(列对齐, 没有合并单元格破坏结构)
    """
    data = table.data
    if len(data) < 2:
        return False

    header = data[0]
    if len(header) < 2:
        return False

    for cell in header:
        text = (cell or "").strip()
        if not _is_label_like(text):
            return False

    expected_cols = len(header)
    for row in data:
        if len(row) != expected_cols:
            return False

    return True


def _is_label_like(text: str) -> bool:
    """标签型单元格判定: 短文本 + 非纯数字 + 单行."""
    if not text:
        return False
    if len(text) < _HEADER_LABEL_MIN_LEN or len(text) > _HEADER_LABEL_MAX_LEN:
        return False
    if "\n" in text or "\r" in text:
        return False
    if text.isdigit():
        return False
    return True


def _is_cjk(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
    )


def _linearize_header_table(table: "ExtractedTable") -> list[TableRowText]:
    """表头表线性化: 表体逐行按列下标 `表头=值`.

 - 列下标严格对齐, 不过滤空表头单元格(修复点).
 - 空值跳过, 全空行跳过.
 - row_no 从 2 开始(行 1 为表头).
 """
    header = [(cell or "").strip() for cell in table.data[0]]
    results: list[TableRowText] = []

    for row_offset, row in enumerate(table.data[1:], start=1):
        normalized = [join_cell_lines_cjk_aware(cell or "") for cell in row]
        pairs: list[str] = []
        for idx in range(min(len(header), len(normalized))):
            value = normalized[idx]
            if not value:
                continue
            label = header[idx] if header[idx] else f"列{idx + 1}"
            pairs.append(f"{label}={value}")
        if not pairs:
            continue
        content = "；".join(pairs)
        results.append(TableRowText(row_no=row_offset + 1, content=content))

    return results


def _linearize_structural(table: "ExtractedTable") -> list[TableRowText]:
    """结构文本线性化: 全部行按列序输出非空单元格 `单元格1 | 单元格2 | ...`.

    - row0 不被丢弃(可能是真实数据行或合并标题).
    - 全空行被跳过.
    - 不做任何"键=值"猜测, 彻底避免错配.
    """
    results: list[TableRowText] = []
    for idx, row in enumerate(table.data, start=1):
        normalized = [join_cell_lines_cjk_aware(cell or "") for cell in row]
        non_empty = [cell for cell in normalized if cell]
        if not non_empty:
            continue
        content = " | ".join(non_empty)
        results.append(TableRowText(row_no=idx, content=content))
    return results


__all__ = ["TableRowText", "linearize_table", "join_cell_lines_cjk_aware"]
