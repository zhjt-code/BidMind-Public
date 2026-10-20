"""评分区邻近 table_row_with_header 权重表消费。

职责: 从邻近页 table_row_with_header chunk 内容重建网格, 依次尝试百分号权重表
      与裸数字权重表解析, 产出卷定义。
不做: 不查 DB、不产评分子项、不调用 LLM。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.extractor.scoring.weighted_volume_table import (
    parse_bare_weight_volume_table,
    parse_weighted_volume_definitions,
)

_ROW_PREFIX_RE = re.compile(r"^表格第\d+行[：:]\s*(.*)$")
_WEIGHT_HEADER_TERMS = ("权重", "占比", "比重")


def volume_defs_from_neighbor_chunks(chunks: list[Any]) -> dict:
    """从邻近表行 chunk 重建外层权重表并解析卷定义; 无可靠网格返回空 dict。"""
    merged: dict = {}
    for header, data in _rebuild_weight_grids(chunks):
        parsed = parse_weighted_volume_definitions(header, data)
        if not parsed:
            parsed = parse_bare_weight_volume_table(header, data)
        for volume, vdef in parsed.items():
            merged.setdefault(volume, vdef)
    return merged


def _rebuild_weight_grids(chunks: list[Any]) -> list[tuple[list[str], list[list[str]]]]:
    """按权重表头切段: 新表头开启新网格, 明细表头不打断当前段。"""
    ordered = sorted(
        chunks,
        key=lambda chunk: (
            int(getattr(chunk, "page_num", 0) or 0),
            int(getattr(chunk, "chunk_index", 0) or 0),
        ),
    )
    grids: list[tuple[list[str], list[list[str]]]] = []
    header: list[str] = []
    data: list[list[str]] = []
    for chunk in ordered:
        row = parse_table_row_chunk(getattr(chunk, "content", None) or "")
        if not row:
            continue
        if _is_weight_header(row):
            if header:
                grids.append((header, data))
            header = row
            data = []
            continue
        if header:
            data.append(row)
    if header:
        grids.append((header, data))
    return grids


def parse_table_row_chunk(content: str) -> list[str]:
    """把 table_row_with_header 线性文本还原为按原顺序排列的非结构化单元格。"""
    text = str(content or "").strip()
    if not text:
        return []
    match = _ROW_PREFIX_RE.match(text)
    body = match.group(1) if match else text
    cells = [cell.strip() for cell in body.split("|")]
    return cells if any(cells) else []


def _is_weight_header(row: list[str]) -> bool:
    """列级表头: ≥2 列且存在短单元格命中权重/占比/比重, 拒绝正文夹带「权重」。"""
    if len(row) < 2:
        return False
    return any(
        any(term in cell for term in _WEIGHT_HEADER_TERMS) and len(cell.strip()) <= 24
        for cell in row
    )


__all__ = ["parse_table_row_chunk", "volume_defs_from_neighbor_chunks"]
