"""章节通道 

职责: 用目录映射与 section_path trigram 召回章节页 chunk
不做: 向量检索、RRF、覆盖评估、LLM 调用、ORM 持久化
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retriever.evidence_pipeline.contracts import RetrievalPlan, RetrievalQuery
from app.core.retriever.semantic_retriever import SearchResult
from app.models.chunk import Chunk
from app.models.document import Document

_TOC_PAGE_CHUNK_LIMIT = 15


async def search_section_chunks(
    db: AsyncSession,
    query: RetrievalQuery,
    plan: RetrievalPlan,
    *,
    existing_ids: Optional[set[int]] = None,
) -> list[SearchResult]:
    """按 toc_map 页码与 section_path 词项召回; 失败返回空列表。"""
    if not plan.use_section:
        return []
    existing = set(existing_ids or ())
    results: list[SearchResult] = []
    try:
        doc = (
            await db.execute(select(Document).where(Document.id == query.document_id))
        ).scalar_one_or_none()
    except Exception as err:
        logger.warning(f"[section_retriever] 读文档降级: {err}")
        return []
    if doc is None:
        return []

    toc_hits = await _fetch_toc_page_chunks(
        db, query, doc, existing_ids=existing
    )
    for hit in toc_hits:
        existing.add(hit.chunk_id)
        results.append(hit)

    path_terms = [term for term in plan.section_terms if term]
    if path_terms:
        from app.core.retriever.evidence_pipeline.lexical_retriever import (
            search_lexical_in_document,
        )

        lexical_hits = await search_lexical_in_document(
            db, query.document_id, path_terms, top_k=8
        )
        for hit in lexical_hits:
            if hit.chunk_id in existing:
                continue
            hit.role = "section"
            existing.add(hit.chunk_id)
            results.append(hit)
    return results


async def _fetch_toc_page_chunks(
    db: AsyncSession,
    query: RetrievalQuery,
    doc: Document,
    *,
    existing_ids: set[int],
) -> list[SearchResult]:
    from app.core.parser.toc_parser import TocParser, deserialize_toc_map

    toc_raw = getattr(doc, "toc_map", None)
    if not toc_raw:
        return []
    toc_map = deserialize_toc_map(toc_raw)
    if not toc_map:
        return []
    parser = TocParser()
    target_pages: set[int] = set(parser.lookup_physical_pages(query.description or "", toc_map))
    for facet in query.facets or ():
        target_pages.update(parser.lookup_physical_pages(facet.key or "", toc_map))
        if facet.query and facet.query != facet.key:
            target_pages.update(parser.lookup_physical_pages(facet.query, toc_map))
    if not target_pages:
        return []
    try:
        stmt = (
            select(Chunk)
            .where(
                Chunk.document_id == query.document_id,
                Chunk.page_num.in_(target_pages),
            )
            .order_by(Chunk.page_num, Chunk.chunk_index)
            .limit(_TOC_PAGE_CHUNK_LIMIT)
        )
        chunks = list((await db.execute(stmt)).scalars().all())
    except Exception as err:
        logger.warning(f"[section_retriever] toc 页召回降级: {err}")
        return []

    results: list[SearchResult] = []
    for chunk in chunks:
        if chunk.id in existing_ids:
            continue
        results.append(
            SearchResult.from_chunk(
                chunk,
                similarity=0.0,
                doc_type=getattr(doc, "doc_type", None),
                role="toc",
            )
        )
    if results:
        logger.info(f"目录导航: 页码{target_pages}, 补充{len(results)}个chunk")
    return results
