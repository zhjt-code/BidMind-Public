"""表格结构真源。

职责: 从单元格 bbox 槽网格提取列结构物理证据——叶子列刻度、跨列父表头、
 数据区 rowspan 合并列, 供装配器/列角色推断做"物理证据优先"的角色绑定。
不做: 不判定评分语义(词表归 column_role_inference)、不渲染 markdown、
 不产评分项、不调用 LLM/DB。

定位(2026-07-17 项目1700实测): pdfplumber 的 cells 跨度是无损结构证据——
表头"评分标准"横跨 分组+因素 两个叶子列(父表头)、"综合实力(25%)"以 rowspan
覆盖多个子项行(行分组), extract 文本数组丢弃这些跨度后, 下游只能用词表与
分布"猜"结构, 词义漂移(如"评分内容"实为规则列)即触发列角色反转。
"""

from __future__ import annotations

from dataclasses import dataclass

CellBBox = tuple[float, float, float, float]
_EDGE_CLUSTER_TOLERANCE = 1.5
_SPAN_MATCH_TOLERANCE = 2.0


@dataclass(frozen=True)
class ColumnStructureEvidence:
    """单张物理表的列结构证据(叶子列坐标系)。"""

    leaf_column_count: int
    # 叶子级表头文本; 被跨列父表头覆盖或无独立表头的列为 ""
    leaf_header_texts: tuple[str, ...]
    # 被跨列父表头覆盖的列(该父表头词面不代表任何单列语义)
    spanning_header_cols: frozenset[int]
    # 数据区存在 rowspan 合并(None槽)的列——行分组列的物理证据
    rowspan_merge_cols: frozenset[int]


def extract_column_structure(
    data: list[list[str]],
    cell_grid: list[list[CellBBox | None]] | None,
    header_row_index: int,
) -> ColumnStructureEvidence | None:
    """从表头行槽跨度与数据区 None 槽提取列结构证据; 证据不足返回 None。"""
    if not cell_grid or header_row_index < 0 or header_row_index >= len(cell_grid):
        return None
    if len(cell_grid) != len(data):
        return None
    x_edges = _cluster_edges(
        value
        for row in cell_grid
        for cell in row
        if cell is not None
        for value in (cell[0], cell[2])
    )
    if len(x_edges) < 3:
        return None
    leaf_count = len(x_edges) - 1

    header_slots = cell_grid[header_row_index]
    header_texts = data[header_row_index]
    leaf_headers = [""] * leaf_count
    spanning: set[int] = set()
    for slot_idx, bbox in enumerate(header_slots):
        if bbox is None:
            continue
        col_start = _nearest_edge_index(x_edges, bbox[0])
        col_end = _nearest_edge_index(x_edges, bbox[2])
        if col_start is None or col_end is None or col_end <= col_start:
            continue
        text = str(header_texts[slot_idx] if slot_idx < len(header_texts) else "") or ""
        if col_end - col_start > 1:
            # 跨列父表头: 词面不绑定任何单列, 覆盖列标记为 spanning
            spanning.update(range(col_start, col_end))
        elif 0 <= col_start < leaf_count:
            leaf_headers[col_start] = text.strip()

    merge_cols: set[int] = set()
    data_rows = [
        row for idx, row in enumerate(cell_grid) if idx != header_row_index
    ]
    for col in range(min(leaf_count, max((len(r) for r in cell_grid), default=0))):
        none_slots = sum(
            1 for row in data_rows if col < len(row) and row[col] is None
        )
        anchor_slots = sum(
            1 for row in data_rows if col < len(row) and row[col] is not None
        )
        if none_slots >= 1 and anchor_slots >= 1:
            merge_cols.add(col)

    return ColumnStructureEvidence(
        leaf_column_count=leaf_count,
        leaf_header_texts=tuple(leaf_headers),
        spanning_header_cols=frozenset(spanning),
        rowspan_merge_cols=frozenset(merge_cols),
    )


def _cluster_edges(values) -> list[float]:
    """把近邻坐标聚为刻度(容差内取首值), 返回升序刻度列表。"""
    ordered = sorted(set(round(float(v), 1) for v in values))
    edges: list[float] = []
    for value in ordered:
        if not edges or value - edges[-1] > _EDGE_CLUSTER_TOLERANCE:
            edges.append(value)
    return edges

def _nearest_edge_index(edges: list[float], value: float) -> int | None:
    """坐标值 → 最近刻度下标; 偏差超容差返回 None(结构证据不可信)。"""
    best_idx: int | None = None
    best_delta = _SPAN_MATCH_TOLERANCE + 1
    for idx, edge in enumerate(edges):
        delta = abs(edge - float(value))
        if delta < best_delta:
            best_delta = delta
            best_idx = idx
    return best_idx if best_delta <= _SPAN_MATCH_TOLERANCE else None


__all__ = ["CellBBox", "ColumnStructureEvidence", "extract_column_structure"]
