"""实体通道 

职责: 从要求文本或已有标识符抽取角色/证号, 词项回捞 chunk
不做: 新建实体表或图数据库、符合性打分、LLM 调用
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retriever.evidence_pipeline.contracts import RetrievalPlan, RetrievalQuery
from app.core.retriever.evidence_pipeline.lexical_retriever import (
    normalize_lexical_terms,
    search_lexical_in_document,
)
from app.core.retriever.semantic_retriever import SearchResult

_ROLE_TERMS: tuple[str, ...] = (
    "项目经理",
    "技术负责人",
    "安全员",
    "质量员",
    "法定代表人",
    "授权委托人",
    "注册建造师",
)

_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:证书|证号|编号)[:：]?\s*([A-Za-z0-9\-\[\]()]{6,})"),
    re.compile(r"\b[0-9A-Z]{18}\b"),
    re.compile(r"[（(][京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼].{4,}[)）]"),
)


def extract_entity_terms(text: str) -> list[str]:
    """从招标要求/维度描述抽取可检索实体词, 不含图数据库。"""
    raw = text or ""
    found: list[str] = []
    for role in _ROLE_TERMS:
        if role in raw:
            found.append(role)
    for pattern in _ID_PATTERNS:
        for match in pattern.finditer(raw):
            token = (match.group(1) if match.lastindex else match.group(0) or "").strip()
            if len(token) >= 2:
                found.append(token)
    return normalize_lexical_terms(found)


def terms_and_pages_from_identifier_index(index: object) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """从串标 IdentifierIndex 取出归一化值与 FieldRef.page, 供回捞 chunk。"""
    terms: list[str] = []
    pages: list[int] = []
    buckets = (
        getattr(index, "company_names", None),
        getattr(index, "person_names", None),
        getattr(index, "legal_representatives", None),
        getattr(index, "phones", None),
        getattr(index, "credit_codes", None),
        getattr(index, "cert_numbers", None),
        getattr(index, "bank_accounts", None),
        getattr(index, "subcontractors", None),
    )
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        for key, refs in bucket.items():
            if key:
                terms.append(str(key))
            for ref in refs or ():
                raw = str(getattr(ref, "raw_value", "") or "").strip()
                if raw:
                    terms.append(raw)
                page_raw = getattr(ref, "page", None)
                if page_raw is None:
                    continue
                try:
                    pages.append(int(str(page_raw).split(",")[0].strip()))
                except ValueError:
                    continue
    return tuple(normalize_lexical_terms(terms, max_terms=20)), tuple(dict.fromkeys(pages))


async def search_entity_chunks(
    db: AsyncSession,
    query: RetrievalQuery,
    plan: RetrievalPlan,
    *,
    existing_ids: Optional[set[int]] = None,
) -> list[SearchResult]:
    """按实体词项召回; 有 identifier_pages 时再按页有界补捞。"""
    if not plan.use_entity:
        return []
    terms: Sequence[str] = plan.entity_terms or extract_entity_terms(query.description or "")
    if not terms:
        return []
    existing = set(existing_ids or ())
    hits = await search_lexical_in_document(db, query.document_id, terms, top_k=8)
    results: list[SearchResult] = []
    for hit in hits:
        if hit.chunk_id in existing:
            continue
        hit.role = "entity"
        existing.add(hit.chunk_id)
        results.append(hit)
    if query.identifier_pages:
        from app.core.retriever.evidence_pipeline.starvation_expand import (
            fetch_chunks_on_pages,
        )

        page_hits = await fetch_chunks_on_pages(
            db,
            query.document_id,
            query.identifier_pages,
            existing_ids=existing,
            role="entity",
        )
        results.extend(page_hits)
    if not results:
        logger.debug("[entity_retriever] 实体通道无命中")
    return results
