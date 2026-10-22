"""评分事实守恒校验。

职责: 比较评分事实变换前后的身份、scope、分值和证据覆盖是否守恒；
      提供按 scheme×lot 的 item 保真快照，供 neighbor_mutated 检测使用。
不做: 不修复数据、不决定评分表语义、不执行持久化。
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.core.extractor.scoring.scoring_facts import ScoringFactSet
from app.core.extractor.scoring.scoring_scope_contracts import ScopeIdentity


class ScoringIntegrityError(ValueError):
    """评分事实守恒失败。"""


@dataclass(frozen=True)
class ScoringIntegritySnapshot:
    """评分事实集合的守恒快照。"""

    fact_count: int
    fact_keys: tuple[str, ...]
    score_multiset: tuple[tuple[float, int], ...]
    totals_by_lot: tuple[tuple[str, float], ...]


def build_integrity_snapshot(fact_set: ScoringFactSet) -> ScoringIntegritySnapshot:
    """构建用于前后比较的评分事实快照。"""
    facts = fact_set.effective_facts
    score_counter = Counter(round(float(fact.score_weight or 0), 4) for fact in facts)
    return ScoringIntegritySnapshot(
        fact_count=len(facts),
        fact_keys=tuple(sorted(fact.fact_key for fact in facts)),
        score_multiset=tuple(sorted(score_counter.items())),
        totals_by_lot=tuple(sorted(fact_set.totals_by_lot().items())),
    )


def compare_integrity_snapshots(
    before: ScoringIntegritySnapshot,
    after: ScoringIntegritySnapshot,
) -> None:
    """确认评分事实变换没有删除、改分或改变lot合计。"""
    if after.fact_count < before.fact_count:
        raise ScoringIntegrityError(
            f"事实数量减少: before={before.fact_count}, after={after.fact_count}"
        )
    if after.score_multiset != before.score_multiset:
        raise ScoringIntegrityError(
            f"分值集合变化: before={before.score_multiset}, after={after.score_multiset}"
        )
    if after.fact_keys != before.fact_keys:
        raise ScoringIntegrityError("事实身份集合变化")
    if after.totals_by_lot != before.totals_by_lot:
        raise ScoringIntegrityError(
            f"采购包合计变化: before={before.totals_by_lot}, after={after.totals_by_lot}"
        )


# ---------------------------------------------------------------------------
# Item 级保真快照（用于 neighbor_mutated 检测）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringItemFidelitySignature:
    """单条评分项的保真签名：捕获关键字段的内容摘要，用于前后相等性比对。

    签名含：名称、分值、fact_type、volume、volume_label、规则摘要、页码、description/evidence 摘要。
    """

    name: str
    score: float
    fact_type: str
    volume: str
    volume_label: str
    rules_digest: str
    pages: tuple[int, ...]
    content_digest: str


@dataclass(frozen=True)
class ScoringItemFidelitySnapshot:
    """单个 scheme×lot scope 的全量保真快照（用于 neighbor_mutated 检测）。"""

    scope: ScopeIdentity
    signatures: tuple[ScoringItemFidelitySignature, ...]


def _fidelity_digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def _item_signature(item: Any) -> ScoringItemFidelitySignature:
    """从 ExtractedScoringItem 或兼容对象构建保真签名。"""
    name = str(getattr(item, "score_item_name", "") or "")
    score = round(float(getattr(item, "score_weight", 0) or 0), 4)
    fact_type = str(getattr(item, "fact_type", "") or "base_score")
    volume = str(getattr(item, "volume", "") or "")
    volume_label = str(getattr(item, "volume_label", "") or "")
    raw_pages = getattr(item, "source_pages", None) or []
    pages = tuple(sorted(int(p) for p in raw_pages if p))
    rules = list(getattr(item, "score_rules", []) or []) + list(
        getattr(item, "deduction_rules", []) or []
    )
    rules_digest = _fidelity_digest("|".join(sorted(rules)))
    description = str(getattr(item, "description", "") or "")
    content_digest = _fidelity_digest(description)
    return ScoringItemFidelitySignature(
        name=name,
        score=score,
        fact_type=fact_type,
        volume=volume,
        volume_label=volume_label,
        rules_digest=rules_digest,
        pages=pages,
        content_digest=content_digest,
    )


def _scope_of_item(item: Any) -> ScopeIdentity:
    scheme = str(getattr(item, "scheme", "") or "").strip() or "__single__"
    lot = str(getattr(item, "lot", "") or "").strip() or "__single__"
    return (scheme, lot)


def build_scope_fidelity_snapshot(
    scope: ScopeIdentity,
    items: tuple | list,
) -> ScoringItemFidelitySnapshot:
    """为指定 scope 构建排序稳定的保真快照。"""
    scope_items = [i for i in items if _scope_of_item(i) == scope]
    sigs = tuple(_item_signature(i) for i in scope_items)
    return ScoringItemFidelitySnapshot(scope=scope, signatures=sigs)


def build_all_scope_snapshots(
    items: tuple | list,
) -> dict[ScopeIdentity, ScoringItemFidelitySnapshot]:
    """按 scheme×lot 分组构建全量保真快照 map。"""
    scopes: set[ScopeIdentity] = {_scope_of_item(i) for i in items}
    return {scope: build_scope_fidelity_snapshot(scope, items) for scope in scopes}


def compare_untouched_scope_snapshots(
    before: dict[ScopeIdentity, ScoringItemFidelitySnapshot],
    after: dict[ScopeIdentity, ScoringItemFidelitySnapshot],
    target_scope: ScopeIdentity,
) -> list[ScopeIdentity]:
    """返回被意外修改的非 target scope 列表（空列表表示无 neighbor_mutated）。"""
    changed: list[ScopeIdentity] = []
    for scope, snap_before in before.items():
        if scope == target_scope:
            continue
        snap_after = after.get(scope)
        if snap_after is None or snap_after.signatures != snap_before.signatures:
            changed.append(scope)
    return changed


__all__ = [
    "ScoringIntegrityError",
    "ScoringIntegritySnapshot",
    "ScoringItemFidelitySignature",
    "ScoringItemFidelitySnapshot",
    "build_integrity_snapshot",
    "build_scope_fidelity_snapshot",
    "build_all_scope_snapshots",
    "compare_integrity_snapshots",
    "compare_untouched_scope_snapshots",
]
