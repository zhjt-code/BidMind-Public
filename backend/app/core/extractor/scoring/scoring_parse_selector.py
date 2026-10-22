"""评分表确定性解析候选仲裁。

职责: 在同一评分表的多个确定性解析候选中选择最可信结果。
不做: 不直接解析表格单元、不修改评分项、不访问DB或LLM。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.requirement import ExtractedScoringItem

from .numeric_score_column_parser import NumericScoreColumnResult


@dataclass(frozen=True)
class SelectedScoringParse:
    """候选仲裁后的最小解析结果。"""

    items: list[ExtractedScoringItem]
    note: str
    diagnostics: list[str] | None = None


def select_embedded_or_numeric_parse(
    *,
    embedded_items: list[ExtractedScoringItem],
    numeric_result: NumericScoreColumnResult | None,
    tolerance: float = 1.0,
) -> SelectedScoringParse | None:
    """在嵌入式与独立分值列结果中选择更可信候选。

    三列表评分表可能同时触发价格公式嵌入式解析与独立分值列解析。嵌入式解析只命中
    一个价格项时属于"局部成功", 不应阻断能覆盖更多子项且总分可信的列统计结果。
    """
    numeric_items = _dedupe_items(numeric_result.items) if numeric_result is not None else []
    if not embedded_items and not numeric_items:
        return None
    if not numeric_items:
        return SelectedScoringParse(
            items=embedded_items,
            note=f"嵌入式分值评分表 {len(embedded_items)} 项",
            diagnostics=[],
        )
    if not embedded_items:
        return SelectedScoringParse(
            items=numeric_items,
            note=numeric_result.note,
            diagnostics=list(numeric_result.diagnostics),
        )

    embedded_score = _candidate_score(embedded_items, tolerance)
    numeric_score = _candidate_score(numeric_items, tolerance)
    if numeric_score > embedded_score:
        return SelectedScoringParse(
            items=numeric_items,
            note=numeric_result.note,
            diagnostics=list(numeric_result.diagnostics),
        )
    return SelectedScoringParse(
        items=embedded_items,
        note=f"嵌入式分值评分表 {len(embedded_items)} 项",
        diagnostics=[],
    )


def _candidate_score(items: list[ExtractedScoringItem], tolerance: float) -> tuple[int, int, float]:
    """候选排序键: 总分可信优先, 子项数其次, 接近100再次。"""
    total = sum(float(getattr(item, "score_weight", 0) or 0) for item in items)
    not_over_100 = int(total <= 100.0 + tolerance)
    closeness = -abs(100.0 - total)
    return (not_over_100, len(items), closeness)


def _dedupe_items(items: list[ExtractedScoringItem]) -> list[ExtractedScoringItem]:
    """候选内部去重, 避免重复装配行让完整列统计候选被误判为超分。"""
    deduped: list[ExtractedScoringItem] = []
    seen: set[tuple[str, float, str | None]] = set()
    for item in items:
        key = (
            item.score_item_name,
            float(item.score_weight or 0),
            item.volume,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


__all__ = ["SelectedScoringParse", "select_embedded_or_numeric_parse"]
