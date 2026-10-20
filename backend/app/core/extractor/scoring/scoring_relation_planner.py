"""评分事实与普通要求的关联计划。

职责: 规划normal要求与权威评分事实之间的可逆关系。
不做: 不删除评分事实、不搬运分值、不调用LLM、不决定最终评分全集。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RelationType = Literal["equivalent", "covers", "ambiguous"]


@dataclass(frozen=True)
class ScoringRelation:
    """normal要求与评分事实的可逆关联。"""

    normal_id: int
    scoring_id: int
    relation_type: RelationType
    lot: str | None = None

    @property
    def fact_key(self) -> str:
        """当前Requirement投影下的稳定事实引用。"""
        return f"requirement:{self.scoring_id}"


@dataclass
class ScoringRelationPlan:
    """一批关联关系计划。"""

    relations: list[ScoringRelation] = field(default_factory=list)

    def add(self, relation: ScoringRelation) -> None:
        self.relations.append(relation)


def append_scoring_relation(normal: Any, relation: ScoringRelation) -> None:
    """把评分事实关系写入normal元数据，不搬运分值。"""
    meta = dict(getattr(normal, "scoring_metadata", None) or {})
    keys = list(meta.get("linked_scoring_fact_keys") or [])
    if relation.fact_key not in keys:
        keys.append(relation.fact_key)
    meta["linked_scoring_fact_keys"] = keys
    lots = list(meta.get("linked_scoring_lots") or [])
    if relation.lot and relation.lot not in lots:
        lots.append(relation.lot)
    if lots:
        meta["linked_scoring_lots"] = lots
    existing_type = meta.get("scoring_relation_type")
    if existing_type and existing_type != relation.relation_type:
        meta["scoring_relation_type"] = "covers"
    elif len(keys) > 1:
        meta["scoring_relation_type"] = "covers"
    else:
        meta["scoring_relation_type"] = relation.relation_type
    normal.scoring_metadata = meta
    if relation.relation_type in {"equivalent", "covers"}:
        try:
            normal.is_key_review = 0
        except AttributeError:
            pass


__all__ = [
    "ScoringRelation",
    "ScoringRelationPlan",
    "append_scoring_relation",
]
