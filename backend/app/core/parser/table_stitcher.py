"""跨页表格拼接器（P2 评分表完整性）。

职责: 把同一逻辑表跨页拆出的多个 ExtractedTable 按"列数一致 + 页连续 + 表头一致"
      拼接为一张逻辑整表，并可渲染为 markdown 供 LLM 整表结构化。
不做: 不做语义分块/向量化、不判定是否评分表、不调用 LLM/DB。

为何需要: pdfplumber 逐页发现表格，十几页的评分表会被切成多个独立 ExtractedTable，
逐行线性化后表头与数据行分散到不同 chunk，LLM 看不到整表，导致分值/评分项错配或漏提。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.parser.table_linearizer import join_cell_lines_cjk_aware

if TYPE_CHECKING:
    from app.core.parser.table_extractor import ExtractedTable


@dataclass(frozen=True)
class StitchedTable:
    """拼接后的逻辑整表。

    Attributes:
        page_start: 起始页码。
        page_end: 结束页码。
        rows: 整表行（含表头行），每个单元格为字符串。
        row_pages: 与 rows 对齐的原始物理页码。
        column_count: 列数。
        source_count: 由几个原始 ExtractedTable 拼成。
        scheme_split: 是否由表内序号重启切出的方案段。
    """

    page_start: int
    page_end: int
    rows: list[list[str]]
    row_pages: list[int]
    column_count: int
    source_count: int
    scheme_split: bool = False


def _norm_cell(cell: object) -> str:
    return re.sub(r"\s+", "", str(cell or "")).strip()


def _norm_row(row: list) -> tuple[str, ...]:
    return tuple(_norm_cell(c) for c in row)


def _column_count(table: "ExtractedTable") -> int:
    if not table.data:
        return 0
    return max(len(row) for row in table.data)



_SCHEME_RESTART_RE = re.compile(r"^\s*1[.．、]")
_SCORE_SPAN_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:[-~～—–至]\s*(-?\d+(?:\.\d+)?))?\s*分")
_SCHEME_TOTAL_TOLERANCE = 1.0


def _row_declared_score(row: list) -> float | None:
    """从评分对象列文本提取该行声明分值；区间取上限。"""
    text = _norm_cell(row[0] if row else "")
    matches = list(_SCORE_SPAN_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    try:
        left = float(match.group(1))
        right = float(match.group(2)) if match.group(2) is not None else left
    except (TypeError, ValueError):
        return None
    return max(left, right)


def _segment_declared_total(rows: list[list]) -> float:
    total = 0.0
    for row in rows:
        score = _row_declared_score(row)
        if score is not None and score > 0:
            total += score
    return round(total, 4)


def _is_scheme_restart_row(row: list) -> bool:
    return bool(row and _SCHEME_RESTART_RE.match(str(row[0] or "")))


def _split_scheme_restarts(group: dict) -> list[dict]:
    """同一stitched表内序号重新从1开始且前段闭合100时切成独立方案。"""
    rows = group["rows"]
    row_pages = group["row_pages"]
    if len(rows) < 4 or len(rows) != len(row_pages):
        return [group]
    header = rows[0]
    header_page = row_pages[0]
    start = 1
    parts: list[dict] = []
    for idx in range(2, len(rows)):
        if not _is_scheme_restart_row(rows[idx]):
            continue
        subtotal = _segment_declared_total(rows[start:idx])
        if abs(subtotal - 100.0) > _SCHEME_TOTAL_TOLERANCE:
            continue
        part_rows = [header, *rows[start:idx]]
        part_pages = [header_page, *row_pages[start:idx]]
        parts.append({**group, "rows": part_rows, "row_pages": part_pages, "scheme_split": True})
        start = idx
    if not parts:
        return [group]
    tail_rows = rows[start:]
    if tail_rows:
        tail_pages = row_pages[start:]
        parts.append({**group, "rows": [header, *tail_rows], "row_pages": [header_page, *tail_pages], "scheme_split": True})
    normalized: list[dict] = []
    for part in parts:
        pages = [p for p in part["row_pages"][1:] if isinstance(p, int)]
        normalized.append({
            **part,
            "page_start": min(pages) if pages else group["page_start"],
            "page_end": max(pages) if pages else group["page_end"],
            "source_count": 1,
        })
    return normalized


def stitch_cross_page_tables(
    page_tables: list[tuple[int, "ExtractedTable"]],
    *,
    max_page_gap: int = 1,
    split_scheme_restarts: bool = False,
) -> list[StitchedTable]:
    """将（页码, 表格）序列按逻辑表拼接。

    Args:
        page_tables: (page_num, ExtractedTable) 列表（无需预排序）。
        max_page_gap: 允许的页间隔（1=必须相邻页才视为同一逻辑表的续表）。
        split_scheme_restarts: 已确认存在多方案模板标题时, 按序号重启切分方案。

    Returns:
        list[StitchedTable]: 拼接后的逻辑表（列数不一致或页不连续者自成一表）。
    """
    ordered = sorted(
        (pt for pt in page_tables if pt[1] is not None and pt[1].data),
        key=lambda pt: (pt[0], getattr(pt[1], "table_index", 0)),
    )
    if not ordered:
        return []

    groups: list[dict] = []
    for page_num, table in ordered:
        col_count = _column_count(table)
        rows = [list(r) for r in table.data]
        row_pages = [page_num] * len(rows)
        header_norm = _norm_row(table.data[0]) if table.data else ()

        attached = False
        if groups:
            g = groups[-1]
            same_cols = g["column_count"] == col_count and col_count > 0
            page_continuous = 0 <= (page_num - g["page_end"]) <= max_page_gap
            if same_cols and page_continuous:
                body = rows
                # 续表若重复表头则跳过该表头行，避免重复
                body_pages = row_pages
                if header_norm and header_norm == g["header_norm"]:
                    body = rows[1:]
                    body_pages = row_pages[1:]
                g["rows"].extend(body)
                g["row_pages"].extend(body_pages)
                g["page_end"] = page_num
                g["source_count"] += 1
                attached = True

        if not attached:
            groups.append(
                {
                    "page_start": page_num,
                    "page_end": page_num,
                    "column_count": col_count,
                    "header_norm": header_norm,
                    "rows": rows,
                    "row_pages": row_pages,
                    "source_count": 1,
                    "scheme_split": False,
                }
            )

    split_groups = (
        [part for group in groups for part in _split_scheme_restarts(group)]
        if split_scheme_restarts else groups
    )
    return [
        StitchedTable(
            page_start=g["page_start"],
            page_end=g["page_end"],
            rows=[[_cell_text(c) for c in row] for row in g["rows"]],
            row_pages=list(g["row_pages"]),
            column_count=g["column_count"],
            source_count=g["source_count"],
            scheme_split=bool(g.get("scheme_split", False)),
        )
        for g in split_groups
    ]


def _cell_text(cell: object) -> str:
    return join_cell_lines_cjk_aware(str(cell or ""))


def render_stitched_table_markdown(table: StitchedTable, *, max_rows: int = 400) -> str:
    """把逻辑整表渲染为 markdown 文本（供 LLM 整表结构化）。"""
    if not table.rows:
        return ""
    rows = table.rows[:max_rows]
    width = table.column_count or max(len(r) for r in rows)

    def _fmt(row: list[str]) -> str:
        cells = [(row[i] if i < len(row) else "") for i in range(width)]
        return "| " + " | ".join(cells) + " |"

    lines = [f"[评分表 第{table.page_start}-{table.page_end}页]"]
    lines.append(_fmt(rows[0]))
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in rows[1:]:
        lines.append(_fmt(row))
    return "\n".join(lines)


__all__ = ["StitchedTable", "stitch_cross_page_tables", "render_stitched_table_markdown"]
