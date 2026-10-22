"""
职责: 在 LLM 合并前自动折叠低风险近重复招标要求。
不做: 不处理受保护条目、不跨类别合并、不做开放语义推断。
"""

from __future__ import annotations

from loguru import logger
from rapidfuzz import fuzz

from app.core.extractor.tender_extractor_parts.dedup_normalize import (
    normalize_dedup_key,
    numeric_fingerprint,
)
from app.core.extractor.tender_extractor_parts.protected_premerge import (
    _is_protected_requirement,
)
from app.schemas.requirement import ExtractedRequirement


def collapse_near_duplicates(
    requirements: list[ExtractedRequirement],
    *,
    threshold: float = 0.9,
) -> list[ExtractedRequirement]:
    """折叠低风险、数字事实一致的字符 3-gram 近重复条目。"""
    if len(requirements) <= 1:
        return requirements

    survivors: list[ExtractedRequirement] = []
    removed = 0
    for req in requirements:
        match_idx = None
        if not _is_protected_requirement(req):
            for idx, existing in enumerate(survivors):
                if _can_auto_merge(existing, req, threshold):
                    match_idx = idx
                    break
        if match_idx is None:
            survivors.append(req)
            continue
        survivors[match_idx] = _merge_low_risk(survivors[match_idx], req)
        removed += 1

    if removed:
        logger.info("近重复预合并: {} -> {} (移除 {} 条)", len(requirements), len(survivors), removed)
    return survivors


def _can_auto_merge(
    left: ExtractedRequirement,
    right: ExtractedRequirement,
    threshold: float,
) -> bool:
    if left.category != right.category:
        return False
    if _is_protected_requirement(left) or _is_protected_requirement(right):
        return False
    if numeric_fingerprint(left.description) != numeric_fingerprint(right.description):
        return False
    return max(
        _ngram_jaccard(left.description, right.description),
        fuzz.ratio(
            normalize_dedup_key(left.description),
            normalize_dedup_key(right.description),
        ) / 100,
    ) >= threshold


def _merge_low_risk(
    existing: ExtractedRequirement,
    extra: ExtractedRequirement,
) -> ExtractedRequirement:
    pages = sorted(set([*(existing.source_pages or []), *(extra.source_pages or [])]))
    keywords = _merge_unique(existing.keywords or [], extra.keywords or [])
    sub_requirements = _merge_unique(existing.sub_requirements or [], extra.sub_requirements or [])
    merged_count = (existing.merged_count or 1) + (extra.merged_count or 1)
    return existing.model_copy(
        update={
            "source_pages": pages,
            "keywords": keywords,
            "sub_requirements": sub_requirements,
            "merged_count": merged_count,
            "is_mandatory": bool(existing.is_mandatory or extra.is_mandatory),
        }
    )


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*left, *right]:
        key = str(value).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _ngram_jaccard(left: str, right: str, n: int = 3) -> float:
    left_key = normalize_dedup_key(left)
    right_key = normalize_dedup_key(right)
    if not left_key or not right_key:
        return 0.0
    left_grams = _ngrams(left_key, n)
    right_grams = _ngrams(right_key, n)
    if not left_grams or not right_grams:
        return 1.0 if left_key == right_key else 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _ngrams(text: str, n: int) -> set[str]:
    if len(text) < n:
        return {text} if text else set()
    return {text[idx:idx + n] for idx in range(len(text) - n + 1)}
