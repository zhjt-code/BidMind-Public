"""标书编写 -- 评分项响应索引。

职责: 为 scoring_criterion 建立直接来源、等价约束和正文事实三类响应索引。
不做: 不生成正文、不修改绑定、不调用 LLM、不落库。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.writer.clause_coverage_checker import parse_source_clause_ids


def build_scoring_response_index(
    scoring_clauses: list[Any],
    all_clauses: list[Any],
    drafts: list[Any],
) -> dict[int, list[Any]]:
    """返回 scoring_clause_id -> 响应该评分项的草稿列表。"""
    draft_id_pairs = [
        (draft, parse_source_clause_ids(getattr(draft, "source_clause_ids", None)))
        for draft in drafts
    ]
    covered_clause_ids = set().union(
        *(ids for _, ids in draft_id_pairs)
    ) if draft_id_pairs else set()
    clause_by_id = {
        int(getattr(clause, "id"))
        for clause in all_clauses
        if getattr(clause, "id", None) is not None
    }
    _ = clause_by_id  # 保留局部命名语义，避免后续误把 all_clauses 当已过滤集合。

    index: dict[int, list[Any]] = {}
    for scoring in scoring_clauses:
        scoring_id = getattr(scoring, "id", None)
        if scoring_id is None:
            continue
        scoring_id_int = int(scoring_id)
        refs = [
            draft for draft, ids in draft_id_pairs
            if scoring_id_int in ids
        ]
        if refs:
            index[scoring_id_int] = refs
            continue

        related_ids = _related_clause_ids(scoring, all_clauses)
        related_refs = [
            draft for draft, ids in draft_id_pairs
            if ids & related_ids
        ]
        if related_refs:
            index[scoring_id_int] = related_refs
            continue

        equivalent_ids = {
            int(getattr(clause, "id"))
            for clause in all_clauses
            if getattr(clause, "id", None) is not None
            and int(getattr(clause, "id")) in covered_clause_ids
            and _is_equivalent_scoring_clause(scoring, clause)
        }
        equivalent_refs = [
            draft for draft, ids in draft_id_pairs
            if ids & equivalent_ids
        ]
        if equivalent_refs:
            index[scoring_id_int] = equivalent_refs
            continue

        fact_refs = [
            draft for draft in drafts
            if _draft_text_responds_to_scoring(scoring, getattr(draft, "content", "") or "")
        ]
        if fact_refs:
            index[scoring_id_int] = fact_refs
    return index


def _related_clause_ids(scoring: Any, all_clauses: list[Any]) -> set[int]:
    meta = _meta(scoring)
    related = {
        int(item)
        for item in meta.get("related_clause_ids", [])
        if _is_int_like(item)
    }
    scoring_id = getattr(scoring, "id", None)
    for clause in all_clauses:
        clause_meta = _meta(clause)
        ref = clause_meta.get("scoring_ref")
        if (
            isinstance(ref, dict)
            and _is_int_like(ref.get("clause_id"))
            and int(ref["clause_id"]) == int(scoring_id)
            and getattr(clause, "id", None) is not None
        ):
            related.add(int(getattr(clause, "id")))
    return related


def _is_equivalent_scoring_clause(scoring: Any, clause: Any) -> bool:
    if getattr(scoring, "id", None) == getattr(clause, "id", None):
        return False
    scoring_name = _score_item_name(scoring)
    text = _normalize(f"{getattr(clause, 'content', '') or ''} {_score_item_name(clause)}")
    scoring_text = _normalize(f"{scoring_name} {getattr(scoring, 'content', '') or ''}")
    if scoring_name and _normalize(scoring_name) in text:
        return True
    if _shared_evidence_keywords(scoring_text, text) and _scores_are_compatible(
        scoring, clause,
    ):
        return True
    return False


def _draft_text_responds_to_scoring(scoring: Any, content: str) -> bool:
    text = _normalize(_strip_html(content))
    if not text:
        return False
    name = _normalize(_score_item_name(scoring))
    scoring_text = _normalize(f"{name} {_strip_score_numbers(getattr(scoring, 'content', '') or '')}")
    numbers = set(re.findall(r"\d+(?:\.\d+)?", scoring_text))
    important_numbers = {num for num in numbers if float(num) > 1}
    number_hit = not important_numbers or any(num in text for num in important_numbers)
    name_hit = not name or name in text or _token_overlap(name, text) >= 1
    action_terms = ("完成", "提交", "报告", "成果", "工期", "测试")
    relevant_actions = [term for term in action_terms if term in scoring_text]
    action_hit = not relevant_actions or any(term in text for term in relevant_actions)
    return name_hit and number_hit and action_hit


def _meta(clause: Any) -> dict[str, Any]:
    raw = getattr(clause, "metadata_json", None)
    return raw if isinstance(raw, dict) else {}


def _score_item_name(clause: Any) -> str:
    meta = _meta(clause)
    value = meta.get("score_item_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return str(getattr(clause, "content", "") or "")[:40]


def _token_overlap(left: str, right: str) -> int:
    left_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", left))
    right_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", right))
    return len(left_terms & right_terms)


def _shared_evidence_keywords(left: str, right: str) -> bool:
    keywords = ("业绩", "资质", "证书", "设备", "人员", "工期", "质量认证")
    return any(keyword in left and keyword in right for keyword in keywords)


def _scores_are_compatible(scoring: Any, clause: Any) -> bool:
    scoring_score = _positive_score(scoring)
    clause_score = _positive_score(clause)
    if scoring_score is None or clause_score is None:
        return True
    return scoring_score == clause_score


def _positive_score(clause: Any) -> float | None:
    try:
        value = float(getattr(clause, "score_weight", None) or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def _strip_score_numbers(text: str) -> str:
    return re.sub(r"(?:得|满分)\s*\d+(?:\.\d+)?\s*分", "", text or "")


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


__all__ = ["build_scoring_response_index"]
