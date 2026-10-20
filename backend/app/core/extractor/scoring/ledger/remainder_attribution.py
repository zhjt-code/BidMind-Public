"""账本唯一余项守恒归集。

职责: 在单一作用域内，仅当唯一声明缺口精确等于全部未归组分值时，为报告视图补组标签。
不做: 不搜索子集、不按词面猜组、不跨方案/标包归集、不修改评分项对象或数据库。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from app.core.extractor.scoring.ledger.contracts import (
    SCORE_MATCH_TOLERANCE,
    LedgerCapture,
    LedgerItemEntry,
)
from app.core.extractor.scoring.ledger.label_normalization import normalize_group_label


@dataclass(frozen=True)
class RemainderAttribution:
    """唯一余项归集结果；未满足门禁时 entries 原样返回且 diagnostic 为空。"""

    entries: tuple[LedgerItemEntry, ...]
    diagnostic: str = ""
    target_label: str = ""
    attributed_sum: float = 0.0
    attributed_count: int = 0


def attribute_unique_remainder(
    capture: LedgerCapture,
    entries: tuple[LedgerItemEntry, ...],
) -> RemainderAttribution:
    """按严格守恒门禁把全部未归组条目归入唯一缺口组，否则零动作。"""
    if (
        len(capture.pairs) < 2
        or capture.conflicts
        or capture.scheme_evidence
        or any(pair.weight is not None for pair in capture.pairs)
    ):
        return RemainderAttribution(entries)

    positive = [entry for entry in entries if float(entry.score or 0.0) > 0]
    scopes = {(entry.scheme, entry.lot) for entry in positive}
    if len(scopes) > 1:
        return RemainderAttribution(entries)

    declared: dict[str, tuple[str, float]] = {}
    for pair in capture.pairs:
        norm = pair.norm_label or normalize_group_label(pair.label)
        if not norm or norm in declared:
            return RemainderAttribution(entries)
        declared[norm] = (pair.label, float(pair.score))

    derived: dict[str, float] = {}
    unlabeled = [
        entry for entry in positive if not normalize_group_label(entry.volume_label)
    ]
    if not unlabeled:
        return RemainderAttribution(entries)
    for entry in positive:
        norm = normalize_group_label(entry.volume_label)
        if not norm:
            continue
        if norm not in declared:
            return RemainderAttribution(entries)
        derived[norm] = round(derived.get(norm, 0.0) + float(entry.score), 4)

    gaps: dict[str, float] = {}
    for norm, (_label, score) in declared.items():
        gap = round(score - derived.get(norm, 0.0), 4)
        if gap < -SCORE_MATCH_TOLERANCE:
            return RemainderAttribution(entries)
        gaps[norm] = gap
    missing = [
        norm for norm, gap in gaps.items() if gap > SCORE_MATCH_TOLERANCE
    ]
    if len(missing) != 1:
        return RemainderAttribution(entries)

    target_norm = missing[0]
    target_label, _declared_score = declared[target_norm]
    unlabeled_sum = round(sum(float(entry.score) for entry in unlabeled), 4)
    if abs(unlabeled_sum - gaps[target_norm]) > SCORE_MATCH_TOLERANCE:
        return RemainderAttribution(entries)
    if abs(
        sum(float(entry.score or 0.0) for entry in positive)
        - sum(score for _label, score in declared.values())
    ) > SCORE_MATCH_TOLERANCE:
        return RemainderAttribution(entries)

    attributed = tuple(
        replace(entry, volume_label=target_label)
        if (
            float(entry.score or 0.0) > 0
            and not normalize_group_label(entry.volume_label)
        )
        else entry
        for entry in entries
    )
    return RemainderAttribution(
        entries=attributed,
        diagnostic=(
            f"唯一剩余组守恒归集: {len(unlabeled)}条/{unlabeled_sum:g}分"
            f" -> {target_label}"
        ),
        target_label=target_label,
        attributed_sum=unlabeled_sum,
        attributed_count=len(unlabeled),
    )


__all__ = ["RemainderAttribution", "attribute_unique_remainder"]
