"""叶子完整性正向证据评估（数字优先绑定重构, 前身）。

职责: 从网格分值多重集与账本条目正分多重集构造名称无关的严格闭合证据——
 结构统计选取分值列(数值密度+序号列算术剔除), 无分值列的网格回退既有
 叶子清单路径; 两侧多重集完全相等且合计闭合 100±0.01 才 verified。
不做: 不判 verdict/归卷, 不调用 DB/LLM, 不做多 scope 聚合, 不做名称匹配。

判定哲学: 数字批准, 名称只否决——正向通过完全由分值多重集+闭合
决定(名称变体不阻断召回); fail-closed 叶子名清单作负向否决闸(复用
missing_leaf_names 包含口径), 拦同分值幻觉替换。名称清单提不出名字的行
不产生否决(宁漏否决不假否), 该窗口为已声明天花板。
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.extractor.scoring.group_column import parse_labeled_score
from app.core.extractor.scoring.leaf_inventory import (
    leaf_inventory_from_grid,
    missing_leaf_names,
    stitch_grid_continuation_rows,
    unresolved_group_rule_rows,
)
from app.core.extractor.scoring.ledger.contracts import (
    DEFAULT_TOTAL_ASSUMPTION,
    SCORE_MATCH_TOLERANCE,
    LeafIntegrityEvidence,
    LedgerItemEntry,
    LedgerReport,
)
from app.core.extractor.scoring.score_column_semantics import (
    parse_adjustment_score,
    parse_short_score_tolerant,
)
from app.core.extractor.scoring.score_total_row_policy import is_total_row_strict

_DEFAULT = LeafIntegrityEvidence()
# 结构选列门槛: 列上可解析数值 >= 2 行才有统计意义, 单行表走回退路径。
_MIN_COLUMN_NUMERIC_ROWS = 2
# 序号列算术判据: 严格递增且首值 <= 2(条款号 1/1.1/2... 形态), 与词表无关。
_SEQUENCE_FIRST_MAX = 2.0


def _cell_numeric(cell: Any) -> float | None:
    """独立数值格解析: 正分(含水印容忍) + 调整项零/负分(口径复用)。

 0 分扣分说明行(如"失信行为扣 0.00")是分值列的合法形态, 只认正分会把
 整列误判为非数值列(all-or-nothing 判据下一格失败即整列出局)。
 """
    value = parse_short_score_tolerant(cell)
    if value is not None:
        return value
    return parse_adjustment_score(cell)


def _numeric_data_rows(grid: list[list[Any]]) -> list[list[str]]:
    """缝合后保留"含独立数值格"的数据行; 表头/声明/合计行按构造出局。

    表头与声明行整行无独立数值格, 无需词表即被排除; 合计行复用
    is_total_row_strict(封闭结构判据), 防 "合计|100" 污染多重集。
    """
    rows: list[list[str]] = []
    for cells in stitch_grid_continuation_rows(grid or []):
        if is_total_row_strict(cells):
            continue
        if any(_cell_numeric(cell) is not None for cell in cells):
            rows.append(cells)
    return rows


def _column_values(rows: list[list[str]], col: int) -> list[float] | None:
    """列 col 的独立数值序列; 任一非空格解析失败即整列出局(纯数值列判据)。"""
    values: list[float] = []
    for cells in rows:
        if col >= len(cells) or not str(cells[col] or "").strip():
            continue
        value = _cell_numeric(cells[col])
        if value is None:
            return None
        values.append(value)
    return values if len(values) >= _MIN_COLUMN_NUMERIC_ROWS else None


def _is_sequence_column(values: list[float]) -> bool:
    """序号列算术判据: 严格递增且首值 <= 2(覆盖 1,2,3.. 与 1.1,1.2.. 条款形态)。"""
    if len(values) < _MIN_COLUMN_NUMERIC_ROWS or values[0] > _SEQUENCE_FIRST_MAX:
        return False
    return all(a < b for a, b in zip(values, values[1:]))


def _select_score_column(rows: list[list[str]]) -> list[float] | None:
    """结构统计选分值列: 唯一的非序号纯数值列才采信, 多候选歧义 fail-closed。"""
    if not rows:
        return None
    width = max(len(cells) for cells in rows)
    candidates: list[list[float]] = []
    for col in range(width):
        values = _column_values(rows, col)
        if values is None or _is_sequence_column(values):
            continue
        candidates.append(values)
        if len(candidates) > 1:
            return None
    return candidates[0] if candidates else None


def _grid_positive_scores(grid: list[list[Any]]) -> list[float]:
    """单网格正分多重集: 分值列优先; 无列结构时回退括注叶子清单+无叶组头。"""
    values = _select_score_column(_numeric_data_rows(grid))
    if values is not None:
        return [v for v in values if v > 0]
    scores = [s for _, s in leaf_inventory_from_grid(grid) if s > 0]
    for group_cell, _rule in unresolved_group_rule_rows(grid):
        result = parse_labeled_score(group_cell)
        if result is not None and result[1] > 0:
            scores.append(result[1])
    return scores


def assess_leaf_integrity(
    grids: list[list[list[Any]]],
    entries: tuple[LedgerItemEntry, ...],
    report: LedgerReport,
) -> LeafIntegrityEvidence:
    """评估叶子账本正向证据(分值多重集口径, 名称无关)。

    verified=True 当且仅当:
      - 网格侧正分多重集非空且合计 == DEFAULT_TOTAL_ASSUMPTION(100) ± 0.01;
      - report 无 insufficiency_flags 和 multi_scheme_signal;
      - 网格侧与账本条目正分侧多重集完全相等;
      - fail-closed 叶子名清单相对提取项名无缺失(名称否决闸, 拦同分幻觉替换;
        单 scope 下与 bridge 覆盖闸同源冗余, 多 scope 下是唯一名称防线)。
    """
    inventory: list[float] = []
    for grid in grids or []:
        inventory.extend(_grid_positive_scores(grid))
    if not inventory:
        return _DEFAULT

    inv_count = len(inventory)
    inv_total = round(sum(inventory), 4)

    # 报告有拒收标记时跳过，返回部分元数据（供诊断）
    if report.insufficiency_flags or report.multi_scheme_signal:
        return LeafIntegrityEvidence(inventory_count=inv_count, total=inv_total)

    # 固定比较 DEFAULT_TOTAL_ASSUMPTION=100，不依赖 report.expected_total
    if abs(inv_total - DEFAULT_TOTAL_ASSUMPTION) > SCORE_MATCH_TOLERANCE:
        return LeafIntegrityEvidence(inventory_count=inv_count, total=inv_total)

    inv_counter: Counter[float] = Counter(round(v, 4) for v in inventory)
    entry_counter: Counter[float] = Counter(
        round(float(e.score), 4) for e in entries if float(e.score or 0.0) > 0
    )
    matched = sum((inv_counter & entry_counter).values())

    if inv_counter != entry_counter:
        return LeafIntegrityEvidence(
            inventory_count=inv_count, matched_count=matched, total=inv_total
        )

    # 名称否决闸: 可确定提取的叶子名必须被提取项名覆盖(双向包含), 否则拒绝。
    name_inventory = [
        leaf for grid in grids or [] for leaf in leaf_inventory_from_grid(grid)
    ]
    extracted_names = [str(e.name) for e in entries if float(e.score or 0.0) > 0]
    if missing_leaf_names(name_inventory, extracted_names):
        return LeafIntegrityEvidence(
            inventory_count=inv_count, matched_count=matched, total=inv_total
        )

    return LeafIntegrityEvidence(
        verified=True,
        basis="single_100",
        inventory_count=inv_count,
        matched_count=matched,
        total=inv_total,
    )


__all__ = ["assess_leaf_integrity"]
