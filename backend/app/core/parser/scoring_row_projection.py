"""低样本评分行投影。

职责: 将单行退化续表中的"名称-分值-规则"三元组投影为评分行候选。
不做: 不定位评分区段、不提交表格认领、不解析整表、不访问数据库或调用LLM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.extractor.scoring.score_column_semantics import (
    normalize_cell,
    parse_adjustment_score,
    parse_short_score,
)

_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
_MAX_NAME_CHARS = 30
_MIN_RULE_CHARS = 10
# 条款号(3.4 / 2.2.2)与分值同为数字格, 但小数点后位数/分段数不同; 条款号不得当分值。
_CLAUSE_NUMBER_RE = re.compile(r"^\d+(?:\s*\.\s*\d+){1,}$")


@dataclass(frozen=True)
class ProjectedScoringRow:
    """退化单行评分三元组。"""

    name: str
    score_text: str
    score: float
    rule: str


def _is_clause_number(cell: object) -> bool:
    """单元格是否为条款号(3.4 / 2.2.2)——与分值同为数字格, 但不是评分事实。"""
    return bool(_CLAUSE_NUMBER_RE.match(normalize_cell(str(cell or ""))))


def _parse_projected_score(cell: object) -> float | None:
    """解析投影行分值: 正分值优先, 回退调整项(0/-N)。"""
    text = str(cell or "")
    value = parse_short_score(text)
    return value if value is not None else parse_adjustment_score(text)


def project_scoring_row(row: list[str]) -> ProjectedScoringRow | None:
    """从退化表格行中投影出评分事实候选。

 判定从严: 恰一个分值格、一个短中文名称格、至少一个长中文规则格。
 分值格含正分值与调整项(0/-N, 扣分行); 条款号(3.4/2.2.2)一律排除。
 """
    score_cells = [
        (idx, cell, parsed)
        for idx, cell in enumerate(row)
        if not _is_clause_number(cell)
        and (parsed := _parse_projected_score(cell)) is not None
    ]
    if len(score_cells) != 1:
        return None

    score_idx, score_text, score = score_cells[0]
    name = ""
    rules: list[str] = []
    for idx, cell in enumerate(row):
        if idx == score_idx:
            continue
        compact = normalize_cell(str(cell or ""))
        if not compact or not _CHINESE_RE.search(compact):
            continue
        if len(compact) <= _MAX_NAME_CHARS and not name:
            name = compact
            continue
        if len(compact) >= _MIN_RULE_CHARS:
            rules.append(str(cell or "").strip())

    rule = " ".join(part for part in rules if part).strip()
    if not name or len(normalize_cell(rule)) < _MIN_RULE_CHARS:
        return None
    return ProjectedScoringRow(
        name=name,
        score_text=str(score_text or "").strip(),
        score=score,
        rule=rule,
    )


__all__ = ["ProjectedScoringRow", "project_scoring_row"]
