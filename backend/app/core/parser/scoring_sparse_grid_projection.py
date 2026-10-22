"""稀疏网格评分表投影（Task4 几何核心）。

职责: 从超宽稀疏物理表发现零跨越列边界，生成不可变 SparseGridProjectionSchema；
 将续表用 Schema 投影为等宽视觉列列表；非法跨越边界时拒绝返回 None。
 新增: SparseGridDecision 聚焦决策 API（decide_sparse_grid_action），
 收口发现/投影/异常降级/尾表分类，让 assembler 接线压缩为 ≤10 行消费。
不做: 不提取评分项、不选择满分值、不做卷归属判定、不修改 assembler 或其他生产代码、
 不调用 LLM/DB、不依赖项数/总分闭合自证、不访问 app.api.*。
 （尾表安全分类会检查严格短分值与合计行语义，但仅用于门禁判断，
 不产生任何 ScoringItem，不解释评分含义。）

# 投影后合并槽边界（文档化）
# action=="project" 时 assembler 须将 none_slots 全部置为空 frozenset，不能沿用原始
# cell_grid——两类信息的可用性不同，需要明确区分：
#
# ① 同一物理行内文本：_project_row 将同一视觉列的多 cell 文本按行内顺序空格拼接，
# 行内的文本内容已完整保留在 projected_rows 中。
#
# ② 跨行 rowspan 共享规则继承证据（cell_grid 的 None 槽）：投影后 cell_grid 被清空，
# None 槽坐标信息不再可用， 的 merge-slot 继承机制无法在投影段生效。
# 这是已知边界：超宽稀疏表的 rowspan 规则继承能力在投影路径下不支持。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.extractor.scoring.score_column_semantics import (
    HEADER_SCORE_TERMS,
    TOTAL_ROW_TERMS,
    is_scoring_header_row,
    is_total_row,
    normalize_cell,
    parse_short_score,
)

# ── 类型别名 ──────────────────────────────────────────────────────────────────
CellBBox = tuple[float, float, float, float]  # (x0, top, x1, bottom)：与 pdfplumber 一致
# 内部变量命名约定：x0=左边界, x1=右边界（旧版曾误标注为 x2，已统一）
_Row = list[object]
_Grid = list[list[CellBBox | None]]

# ── 常量 ─────────────────────────────────────────────────────────────────────
_MIN_PHYSICAL_WIDTH: int = 12
_MIN_VISUAL_COLS: int = 3
_MAX_VISUAL_COLS: int = 8
_EDGE_CLUSTER_TOL: float = 1.5   # 与 table_grid_model 口径对齐
_BOUNDARY_SPAN_TOL: float = 2.0  # 判断跨越边界的容差（浮点安全裕量）

# 尾表尾注前缀（startswith 匹配）
_TAIL_PREFIXES: tuple[str, ...] = ("注：", "注:", "说明：", "说明:")
# 尾表合计/总分关键词（精确匹配，来自 TOTAL_ROW_TERMS）
_TAIL_EXACT: frozenset[str] = frozenset(TOTAL_ROW_TERMS)

# ── 公开 API ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SparseGridProjectionSchema:
    """稀疏表格投影 Schema（不可变）。

    boundaries: 升序全局列边界 x 坐标（含左右外边界），
                长度 = column_count + 1。
    column_count: 视觉列数，3 ≤ column_count ≤ 8。
    score_column_index: 分值视觉列 0-based 下标（由 discover 设置；直接构造可为 None）。
    """

    boundaries: tuple[float, ...]
    column_count: int
    score_column_index: int | None = None


@dataclass(frozen=True)
class SparseGridDecision:
    """assembler 接线消费的稀疏投影决策（不可变）。

 action 语义:
 "project" - 投影成功，使用 projected_rows
 （首表 discover+project 或续表 schema 复用）
 "claim_without_rows" - 认领为结构尾表，不消费行
 （三重门禁：无评分表头 + 无独立计分行 + 有尾注/合计信号）
 "fallthrough" - 回退 legacy 路径，assembler 按既有逻辑处理

 new_schema: 首表 discover 时返回新建 schema；其余情况为 None（沿用当前 schema）
 projected_rows: action=="project" 时的投影结果；其他为 None
 warning: 异常降级时的非空说明（assembler 仅记日志，不抛）

 ## 合并槽边界（文档化）
 action=="project" 时，assembler 须将 none_slots 全部置为空 frozenset。
 两类信息可用性不同：
 - 同一物理行内文本：_project_row 按行内顺序空格拼接，projected_rows 已完整保留。
 - 跨行 rowspan 继承证据（cell_grid None 槽）：投影后 cell_grid 清空，None 槽
 位置信息不再可用， 的 merge-slot 规则继承机制在投影段无法生效。
 这是投影路径的已知限制，超宽稀疏表不支持跨行 rowspan 规则继承。
 """

    action: str  # "project" | "claim_without_rows" | "fallthrough"
    projected_rows: list[list[str]] | None
    new_schema: SparseGridProjectionSchema | None
    warning: str


def discover_sparse_scoring_projection(
    data: list[_Row],
    cell_grid: _Grid | None,
) -> SparseGridProjectionSchema | None:
    """从首表（表头在 row 0）发现稀疏评分表投影 Schema。

    门禁（任一不满足返回 None）：
    1. cell_grid 非空且与 data 行数对齐
    2. 物理宽度 >= 12
    3. row 0 具备评分表表头签名
    4. 零跨越边界形成 3-8 个视觉列
    5. 投影后表头仍具评分签名且含独立分值列（score_column_index 可定位）
    """
    if not _check_preconditions(data, cell_grid):
        return None

    header_row = [str(c or "") for c in data[0]]
    if not is_scoring_header_row(header_row):
        return None

    bboxes = _collect_bboxes(cell_grid)  # type: ignore[arg-type]
    if not bboxes:
        return None

    edges = _cluster_x_edges(bboxes)
    if len(edges) < _MIN_VISUAL_COLS + 1:
        return None

    zero_crossing = _zero_crossing_edges(edges, bboxes)
    column_count = len(zero_crossing) - 1
    if not (_MIN_VISUAL_COLS <= column_count <= _MAX_VISUAL_COLS):
        return None

    # 用临时 schema（无 score_column_index）投影表头以验证签名和定位分值列
    schema_tmp = SparseGridProjectionSchema(
        boundaries=tuple(zero_crossing),
        column_count=column_count,
    )
    projected_header = _project_row(data[0], cell_grid[0], schema_tmp)  # type: ignore[index]
    if projected_header is None:
        return None
    if not is_scoring_header_row(projected_header):
        return None

    score_col = _find_score_col_index(projected_header)
    if score_col is None:
        return None  # 无法定位分值列，拒绝 discover

    return SparseGridProjectionSchema(
        boundaries=tuple(zero_crossing),
        column_count=column_count,
        score_column_index=score_col,
    )


def project_sparse_scoring_table(
    data: list[_Row],
    cell_grid: _Grid | None,
    schema: SparseGridProjectionSchema,
) -> list[list[str]] | None:
    """将表格（首表或续表）用 Schema 投影为视觉列列表。

    任一非空 cell 的 bbox 跨越 Schema 边界时返回 None（跨界拒绝）。
    cell_grid 为 None 或行数与 data 不对齐时也返回 None。
    """
    if cell_grid is None or len(cell_grid) != len(data):
        return None

    result: list[list[str]] = []
    for row_data, row_grid in zip(data, cell_grid):
        projected = _project_row(row_data, row_grid, schema)
        if projected is None:
            return None
        result.append(projected)
    return result


def decide_sparse_grid_action(
    data: list[_Row],
    cell_grid: _Grid | None,
    current_schema: SparseGridProjectionSchema | None,
) -> SparseGridDecision:
    """assembler 接线的统一决策入口：发现/投影/异常降级/尾表分类。

    捕获所有投影异常，返回安全降级决策；assembler 仅记日志，不抛。

    尾表分类（claim_without_rows 门禁）会检查严格短分值（parse_short_score）与
    合计行语义（is_total_row），但仅用于判断"是否含独立计分行"的门禁条件，
    不产生任何 ScoringItem，不选择满分值，不做卷归属判定。
    """
    try:
        return _decide_impl(data, cell_grid, current_schema)
    except Exception as exc:  # noqa: BLE001
        return SparseGridDecision(
            action="fallthrough",
            projected_rows=None,
            new_schema=None,
            warning=f"投影决策异常({type(exc).__name__}): {exc}",
        )


# ── 内部工具 ──────────────────────────────────────────────────────────────────


def _decide_impl(
    data: list[_Row],
    cell_grid: _Grid | None,
    current_schema: SparseGridProjectionSchema | None,
) -> SparseGridDecision:
    """decide_sparse_grid_action 的核心逻辑（无异常保护，调用方负责 catch）。"""
    _FT = SparseGridDecision(action="fallthrough", projected_rows=None, new_schema=None, warning="")

    # 情形 1: 无 schema → 尝试从首表 discover
    if current_schema is None:
        new_schema = discover_sparse_scoring_projection(data, cell_grid)
        if new_schema is None:
            return _FT
        proj = project_sparse_scoring_table(data, cell_grid, new_schema)
        if proj is None:
            return SparseGridDecision("fallthrough", None, None, "discover 成功但首表投影失败")
        return SparseGridDecision("project", proj, new_schema, "")

    # 情形 2: 已有 schema → 尝试投影续表
    proj = project_sparse_scoring_table(data, cell_grid, current_schema)
    if proj is not None:
        return SparseGridDecision("project", proj, None, "")

    # 情形 3: 投影失败 → 三重门禁判断是否认领为结构尾表
    # 前置：需要对齐的 cell_grid 才能逐行判断
    if cell_grid is None or len(cell_grid) != len(data):
        return _FT

    # 门禁 1: 无评分表头（有表头应走 legacy 重新建段）
    if any(is_scoring_header_row([str(c or "") for c in row]) for row in data):
        return _FT

    # 门禁 2: 无独立计分行（任一真实计分行 → fall-through，legacy 可见）
    if _has_independent_scoring_rows(data, cell_grid, current_schema):
        return _FT

    # 门禁 3: 需要有尾注/合计信号（纯异质表 → fall-through，不强认领）
    if not _has_tail_signals(data):
        return _FT

    return SparseGridDecision("claim_without_rows", None, None, "")


def _has_independent_scoring_rows(
    data: list[_Row],
    cell_grid: _Grid,
    schema: SparseGridProjectionSchema,
) -> bool:
    """任一行在分值视觉列有有效短分值 → 视为含独立计分行。

    两条检测路径（保守优先）：
    1. 投影成功：检查 projected_rows[score_column_index]；
    2. 跨界行（_project_row 返回 None）：退回 bbox 几何检测——
       查找中心 x 落在 score 视觉列 x 区间内的 raw cell，检查短分值。
       此路径防止"列宽变化评分续表"因所有行都无法投影而被错误认领。

    合计/汇总行在两条路径上均跳过（优先检查原始文本，再检查投影）。
    score_column_index 为 None 时保守返回 False（无法定位分值列）。
    """
    score_col = schema.score_column_index
    if score_col is None:
        return False  # schema 不完整，保守不认独立计分行

    bounds = schema.boundaries
    sc_x0 = bounds[score_col]
    sc_x1 = bounds[score_col + 1]

    for row_data, row_grid in zip(data, cell_grid):
        raw_texts = [str(c or "").strip() for c in row_data]
        proj = _project_row(row_data, row_grid, schema)
        if proj is None:
            # 跨界行：退回 bbox 几何检测（防"列宽变化评分续表"被吞）
            if is_total_row(raw_texts):
                continue
            if _raw_row_has_score_in_col(row_data, row_grid, sc_x0, sc_x1):
                return True
            continue
        # 投影成功路径
        if is_total_row(proj):
            continue
        if score_col < len(proj) and parse_short_score(proj[score_col]) is not None:
            return True
    return False


def _raw_row_has_score_in_col(
    row_data: _Row,
    row_grid: list[CellBBox | None],
    sc_x0: float,
    sc_x1: float,
) -> bool:
    """跨界行降级检测：任一 cell 的 bbox 中心落在分值视觉列 x 区间内且含短分值。

    仅查看 bbox center 在 [sc_x0−TOL, sc_x1+TOL) 范围内的单元格，
    严格限定检测范围，防止规则列中的数字误判。
    """
    for slot_idx, bbox in enumerate(row_grid):
        if bbox is None:
            continue
        x0, x1 = float(bbox[0]), float(bbox[2])
        center = (x0 + x1) / 2.0
        if not (sc_x0 - _BOUNDARY_SPAN_TOL <= center < sc_x1 + _BOUNDARY_SPAN_TOL):
            continue
        raw = row_data[slot_idx] if slot_idx < len(row_data) else None
        text = ("" if raw is None else str(raw)).strip()
        if parse_short_score(text) is not None:
            return True
    return False


def _has_tail_signals(data: list[_Row]) -> bool:
    """数据行含结构合计/总分/续写尾注等信号（按原始 cell 文本扫描）。"""
    for row in data:
        for cell in row:
            text = (str(cell) if cell is not None else "").strip()
            if not text:
                continue
            if text in _TAIL_EXACT:
                return True
            if any(text.startswith(pfx) for pfx in _TAIL_PREFIXES):
                return True
    return False


def _check_preconditions(data: list[_Row], cell_grid: _Grid | None) -> bool:
    """基础前置条件：非空、行数对齐、物理宽度 >= 12。"""
    if not cell_grid or not data:
        return False
    if len(cell_grid) != len(data):
        return False
    if not data[0]:
        return False
    physical_width = max((len(row) for row in cell_grid), default=0)
    return physical_width >= _MIN_PHYSICAL_WIDTH


def _collect_bboxes(
    cell_grid: _Grid,
) -> list[tuple[float, float, float, float]]:
    """收集全表所有非 None、宽度 > 0 的 bbox（x0, top, x1, bottom）。"""
    out: list[tuple[float, float, float, float]] = []
    for row in cell_grid:
        for bbox in row:
            if bbox is None:
                continue
            x0, top, x1, bottom = (
                float(bbox[0]), float(bbox[1]),
                float(bbox[2]), float(bbox[3]),
            )
            if x1 > x0:
                out.append((x0, top, x1, bottom))
    return out


def _cluster_x_edges(
    bboxes: list[tuple[float, float, float, float]],
) -> list[float]:
    """从 bbox 的 x0/x1 提取聚类后的升序列刻度（容差 _EDGE_CLUSTER_TOL）。

    复用 table_grid_model._cluster_edges 口径，保持与上游证据链一致。
    """
    raw = sorted({round(float(v), 1) for bbox in bboxes for v in (bbox[0], bbox[2])})
    edges: list[float] = []
    for v in raw:
        if not edges or v - edges[-1] > _EDGE_CLUSTER_TOL:
            edges.append(v)
    return edges


def _zero_crossing_edges(
    edges: list[float],
    bboxes: list[tuple[float, float, float, float]],
) -> list[float]:
    """返回零跨越边列表（含左右外边界）。

    边 e 为零跨越当且仅当：全表所有 bbox 均未满足
      bbox.x0 < e - TOL  AND  bbox.x1 > e + TOL
    （即没有任何单元格横跨该边界线）。
    """
    result: list[float] = []
    for edge in edges:
        crossed = any(
            b_x0 < edge - _BOUNDARY_SPAN_TOL and b_x1 > edge + _BOUNDARY_SPAN_TOL
            for b_x0, _, b_x1, _ in bboxes
        )
        if not crossed:
            result.append(edge)
    return result


def _col_index_for_cell(
    x0: float,
    x1: float,
    schema: SparseGridProjectionSchema,
) -> int | None:
    """将 bbox 的 x 范围映射到视觉列下标。

    若 bbox 严格跨越任一 Schema 边界（x0 < boundary < x1，超出容差），
    返回 None（触发跨界拒绝）。否则返回所属视觉列 0-based 下标。
    """
    bounds = schema.boundaries
    # 检查是否跨越任意内部边界
    for b in bounds[1:-1]:
        if x0 < b - _BOUNDARY_SPAN_TOL and x1 > b + _BOUNDARY_SPAN_TOL:
            return None

    # 定位所属视觉列（按 bbox 中心点落在哪个区间）
    center = (x0 + x1) / 2.0
    for i in range(schema.column_count):
        if bounds[i] - _BOUNDARY_SPAN_TOL <= center < bounds[i + 1] + _BOUNDARY_SPAN_TOL:
            return i
    return None


def _project_row(
    row_data: _Row,
    row_grid: list[CellBBox | None],
    schema: SparseGridProjectionSchema,
) -> list[str] | None:
    """投影单行到视觉列列表。

    遍历该行所有非 None bbox：
    - 若对应文本非空且 bbox 跨越 Schema 边界 → 返回 None（拒绝整行）。
    - 文本为空的 bbox 跳过（不触发跨界检测）。
    - 同一视觉列多个文本用空格拼接（防重叠覆盖）。
    """
    projected: list[str] = [""] * schema.column_count
    for slot_idx, bbox in enumerate(row_grid):
        if bbox is None:
            continue
        raw = row_data[slot_idx] if slot_idx < len(row_data) else None
        text = ("" if raw is None else str(raw)).strip()
        if not text:
            continue  # 空 cell 不触发跨界检测
        x0, x1 = float(bbox[0]), float(bbox[2])
        col = _col_index_for_cell(x0, x1, schema)
        if col is None:
            return None  # 非空 cell 跨越边界
        projected[col] = (projected[col] + " " + text).strip() if projected[col] else text
    return projected


def _find_score_col_index(header: list[str]) -> int | None:
    """投影表头中分值视觉列的 0-based 下标；无则返回 None。

    判据与 is_scoring_header_row 的 has_score 判据对齐（HEADER_SCORE_TERMS 包含匹配）。
    """
    score_terms = frozenset(HEADER_SCORE_TERMS)
    for idx, cell in enumerate(header):
        compact = normalize_cell(cell)
        if not compact:
            continue
        if any(term in compact for term in score_terms):
            return idx
    return None


__all__ = [
    "CellBBox",
    "SparseGridDecision",
    "SparseGridProjectionSchema",
    "decide_sparse_grid_action",
    "discover_sparse_scoring_projection",
    "project_sparse_scoring_table",
]
