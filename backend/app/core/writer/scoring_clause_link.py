"""标书编写 -- 散落评分条目关联收口

职责: 识别其它 sub_type(内容要求/必备材料/强调重点/差异化提示)中与权威 scoring_criterion
 等价的散落条目，建立双向 metadata 关联(scoring_ref / related_clause_ids)。
不做: 不删除/不搬家条目、不改 sub_type、不改分值、不落库(调用方提交)、不做评分提取。

定位: 评分内容可能因特殊性被 LLM 分到其它标签(用户反馈"个别散落其它标签下")。物理归集会
 破坏原组消费通道(如材料清单/加分方向)，故采用"权威清单 + 关联收口"——原条目留在原组，
 仅打关联标记，供确认页展示归属、避免同一评分点两处重复确认(复用审查侧 归并理念，
 但只关联不删除)。等价判定复用 scoring_reconcile 的确定性多条件函数(已打磨)。
"""

from __future__ import annotations

from typing import Any

from app.core.extractor.scoring.scoring_reconcile import (
    bigram_similarity,
    is_same_scoring_item,
)
from app.models.writing_clause import WritingClause

_SCORING_SUB_TYPE = "scoring_criterion"

# 可能承载评分点但被 LLM 误分到其它标签的候选子类型(保守集)。
# 刻意不含 disqualification(废标红线需保持纯粹)与结构/提交类(语义无关)。
_LINK_CANDIDATE_SUB_TYPES = frozenset(
    {"content_required", "material_required", "emphasis_point", "differentiation_hint"}
)


def _meta(clause: WritingClause) -> dict[str, Any]:
    raw = getattr(clause, "metadata_json", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _authority_name(clause: WritingClause) -> str:
    return str(_meta(clause).get("score_item_name") or clause.content or "")


def link_scattered_scoring_clauses(clauses: list[WritingClause]) -> int:
    """为与权威评分项等价的散落条目建立双向 metadata 关联(就地修改 metadata_json)。

    Returns:
        新建立的关联对数(散落条目 → 权威评分项)。

    幂等: 权威条目 related_clause_ids 以并集追加，重复运行不产生重复;
          散落条目 scoring_ref 覆盖写入(同值)。
    """
    authoritative = [
        c
        for c in clauses
        if c.sub_type == _SCORING_SUB_TYPE and getattr(c, "id", None) is not None
    ]
    scattered = [
        c
        for c in clauses
        if c.sub_type in _LINK_CANDIDATE_SUB_TYPES and getattr(c, "id", None) is not None
    ]
    if not authoritative or not scattered:
        return 0

    linked = 0
    related_map: dict[int, set[int]] = {}
    for clause in scattered:
        best: WritingClause | None = None
        best_sim = -1.0
        for auth in authoritative:
            if not is_same_scoring_item(
                _authority_name(auth),
                auth.content,
                clause.content,
                clause.content,
                weight_a=getattr(auth, "score_weight", None),
                weight_b=getattr(clause, "score_weight", None),
            ):
                continue
            sim = bigram_similarity(auth.content, clause.content)
            if sim > best_sim:
                best, best_sim = auth, sim
        if best is None:
            continue
        meta = _meta(clause)
        meta["scoring_ref"] = {
            "clause_id": best.id,
            "score_item_name": _authority_name(best),
            "max_score": getattr(best, "score_weight", None),
        }
        clause.metadata_json = meta
        related_map.setdefault(int(best.id), set()).add(int(clause.id))
        linked += 1

    for auth in authoritative:
        new_ids = related_map.get(int(auth.id))
        if not new_ids:
            continue
        meta = _meta(auth)
        existing = meta.get("related_clause_ids")
        merged = set(existing) if isinstance(existing, list) else set()
        merged |= new_ids
        meta["related_clause_ids"] = sorted(merged)
        auth.metadata_json = meta

    return linked
