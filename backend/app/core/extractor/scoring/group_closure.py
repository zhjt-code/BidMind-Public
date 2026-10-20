"""评分表组基准闭环诊断。

职责: 从评分表网格识别组小计声明(如"商务部分34分"), 对照组内可解析分值合计产出诊断。
不做: 不产评分项、不改卷别/卷定义、不调用 LLM/DB。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.extractor.scoring.score_column_semantics import (
    is_total_row,
    normalize_cell,
    parse_short_score_tolerant,
)

_GROUP_MARKER_RE = re.compile(
    r"(?P<label>[\u4e00-\u9fff]{1,12}(?:部分|标|卷))(?P<score>\d+(?:\.\d+)?)分"
)
_GROUP_MARKER_MAX_LEN = 24
# 治理阶段5 消歧: 原名 _TOLERANCE 与 score_candidates 同名但取值差 100 倍(1.0 vs 0.01)。
# 本处是**分组小计闭合**容差(组内合计对分组声明分值, 允许 1 分), 与候选分值比对的
# 0.01 分精度容差语义不同。仅改名不改值。
_GROUP_CLOSURE_TOLERANCE = 1.0


@dataclass(frozen=True)
class GroupSubtotal:
    """评分表组小计声明。"""

    label: str
    score: float
    row_index: int


def parse_group_subtotals(grid: list[list[str]]) -> list[GroupSubtotal]:
    """解析评分表中的组小计声明行。

    只识别短单元格中的 `X部分N分` / `X标N分` / `X卷N分` 形态，避免从长规则正文中
    抽取普通分值表达。
    """
    subtotals: list[GroupSubtotal] = []
    for row_index, row in enumerate(grid):
        if is_total_row(row):
            continue
        for cell in row:
            compact = normalize_cell(cell)
            if not compact or len(compact) > _GROUP_MARKER_MAX_LEN:
                continue
            match = _GROUP_MARKER_RE.search(compact)
            if not match:
                continue
            try:
                score = float(match.group("score"))
            except (TypeError, ValueError):
                continue
            if score <= 0:
                continue
            subtotals.append(
                GroupSubtotal(
                    label=match.group("label"),
                    score=score,
                    row_index=row_index,
                )
            )
            break
    return subtotals


def build_group_closure_diagnostics(
    grid: list[list[str]],
    *,
    score_col: int | None,
    tolerance: float = _GROUP_CLOSURE_TOLERANCE,
) -> list[str]:
    """对组小计声明做闭环校验, 输出缺口诊断。"""
    if score_col is None:
        return []
    subtotals = parse_group_subtotals(grid)
    if not subtotals:
        return []

    diagnostics: list[str] = []
    for idx, subtotal in enumerate(subtotals):
        start = subtotal.row_index
        end = subtotals[idx + 1].row_index if idx + 1 < len(subtotals) else len(grid)
        parsed_sum = _sum_scores_in_rows(grid[start:end], score_col)
        if abs(parsed_sum - subtotal.score) <= tolerance:
            continue
        diagnostics.append(
            f"组'{subtotal.label}'声明{_format_score(subtotal.score)}分，"
            f"表内可解析{_format_score(parsed_sum)}分，"
            f"缺口{_format_score(abs(subtotal.score - parsed_sum))}分"
        )
    return diagnostics


def _sum_scores_in_rows(rows: list[list[str]], score_col: int) -> float:
    total = 0.0
    for row in rows:
        if is_total_row(row):
            continue
        score = parse_short_score_tolerant(_cell(row, score_col))
        if score is not None:
            total += score
    return round(total, 4)


def _format_score(value: float) -> str:
    rounded = round(float(value), 4)
    if rounded.is_integer():
        return str(int(rounded))
    return str(rounded).rstrip("0").rstrip(".")


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


__all__ = [
    "GroupSubtotal",
    "build_group_closure_diagnostics",
    "parse_group_subtotals",
]
