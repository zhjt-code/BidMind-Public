"""
职责: 在 LLM 合并前压缩低风险完全重复的招标要求候选。
不做: 不跳过 chunk、不删除否决项/评分项/重点评审项、不做语义相似推断。
"""

from __future__ import annotations

from loguru import logger

from app.core.extractor.tender_extractor_parts.dedup_normalize import normalize_dedup_key
from app.schemas.requirement import ExtractedRequirement

_HIGH_RISK_TERMS = (
    "评分",
    "评审",
    "分值",
    "得分",
    "否决",
    "废标",
    "资格",
    "不得",
    "必须",
    "★",
    "▲",
)


def _normalize_description(description: str) -> str:
    return normalize_dedup_key(description)


def _is_protected_requirement(item: ExtractedRequirement) -> bool:
    text = item.description or ""
    return (
        item.is_mandatory is True
        or item.requirement_kind == "scoring_item"
        or item.is_key_review is True
        or item.score_weight is not None
        or any(term in text for term in _HIGH_RISK_TERMS)
    )


def _merge_unique_text(left: list[str], right: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*left, *right]:
        if not isinstance(value, str):
            continue
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(value)
    return merged


def _merge_low_risk_duplicate(
    existing: ExtractedRequirement,
    extra: ExtractedRequirement,
) -> ExtractedRequirement:
    pages = sorted(set([*(existing.source_pages or []), *(extra.source_pages or [])]))
    merged_count = (existing.merged_count or 1) + (extra.merged_count or 1)
    return existing.model_copy(
        update={
            "source_pages": pages,
            "keywords": _merge_unique_text(existing.keywords or [], extra.keywords or []),
            "sub_requirements": _merge_unique_text(
                existing.sub_requirements or [],
                extra.sub_requirements or [],
            ),
            "merged_count": merged_count,
        }
    )


def compress_low_risk_duplicate_requirements(
    items: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    """只压缩低风险、完全相同描述的重复项。"""
    if len(items) <= 1:
        return items

    result: list[ExtractedRequirement] = []
    index_by_key: dict[tuple[str, str], int] = {}
    removed = 0

    for item in items:
        if _is_protected_requirement(item):
            result.append(item)
            continue

        normalized = _normalize_description(item.description)
        if not normalized:
            result.append(item)
            continue

        key = (item.category, normalized)
        existing_idx = index_by_key.get(key)
        if existing_idx is None:
            index_by_key[key] = len(result)
            result.append(item)
            continue

        result[existing_idx] = _merge_low_risk_duplicate(result[existing_idx], item)
        removed += 1

    if removed:
        logger.info(
            "受保护预合并去重: {} -> {} (压缩低风险完全重复 {} 条)",
            len(items),
            len(result),
            removed,
        )
    return result
