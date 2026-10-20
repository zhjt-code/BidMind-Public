"""证据包组装 

职责: 为单条审查项组装可回放证据包（多通道召回、融合、覆盖快照）
不做: 符合性打分、串标定性、LLM 调用、ORM 持久化
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.retriever.evidence_coverage import evaluate_facet_coverage
from app.core.retriever.evidence_pipeline.contracts import (
    ChannelHits,
    EvidencePackage,
    RetrievalPlan,
    RetrievalQuery,
)
from app.core.retriever.evidence_pipeline.lexical_retriever import (
    search_lexical_in_document,
)
from app.core.retriever.evidence_pipeline.query_planner import plan_retrieval_channels
from app.core.retriever.evidence_pipeline.rrf import fuse_channel_rankings
from app.core.retriever.evidence_ranker import apply_low_info_penalty, rerank_results_by_query
from app.core.retriever.semantic_retriever import SemanticRetriever


async def _collect_dense_hits(
    retriever: SemanticRetriever,
    query: RetrievalQuery,
    plan: RetrievalPlan,
) -> list[SearchResult]:
    if not plan.use_dense:
        return []
    sub_queries = list(plan.dense_queries) or [query.description]
    top_k = max(1, query.top_k)
    min_sim = query.min_similarity
    if len(sub_queries) <= 1:
        results = await retriever.search_in_document(
            document_id=query.document_id,
            query=sub_queries[0],
            top_k=top_k,
            min_similarity=min_sim,
        )
        for item in results:
            item.role = item.role or "primary"
            item.matched_sub_query = None
        return results

    per_query_k = max(5, top_k // len(sub_queries) + 2)
    seen: dict[int, SearchResult] = {}
    for sub_query in sub_queries:
        hits = await retriever.search_in_document(
            document_id=query.document_id,
            query=sub_query,
            top_k=per_query_k,
            min_similarity=max(0.0, min_sim - 0.05),
        )
        for item in hits:
            if item.chunk_id not in seen or item.similarity > seen[item.chunk_id].similarity:
                item.role = item.role or "primary"
                item.matched_sub_query = sub_query
                seen[item.chunk_id] = item
    logger.info(
        f"多路检索: {len(sub_queries)}个子查询, 合并去重后{len(seen)}个chunk"
    )
    return sorted(seen.values(), key=lambda item: item.similarity, reverse=True)


async def assemble_evidence_package(
    db: AsyncSession,
    query: RetrievalQuery,
    *,
    retriever: Optional[SemanticRetriever] = None,
) -> EvidencePackage:
    """按计划召回 dense/lexical(及已实现的结构通道), RRF 融合后做覆盖快照。"""
    retriever = retriever or SemanticRetriever(db)
    plan = plan_retrieval_channels(query)
    channels: list[ChannelHits] = []
    used: list[str] = []

    if plan.use_dense:
        dense_hits = await _collect_dense_hits(retriever, query, plan)
        channels.append(ChannelHits("dense", dense_hits[: query.top_k * 2]))
        used.append("dense")

    if plan.use_lexical and plan.lexical_terms:
        lexical_top_k = int(getattr(settings, "RETRIEVAL_LEXICAL_TOP_K", query.top_k) or query.top_k)
        lexical_hits = await search_lexical_in_document(
            db,
            query.document_id,
            plan.lexical_terms,
            top_k=lexical_top_k,
        )
        if lexical_hits:
            channels.append(ChannelHits("lexical", lexical_hits))
            used.append("lexical")

    if plan.use_section:
        from app.core.retriever.evidence_pipeline.section_retriever import (
            search_section_chunks,
        )

        section_hits = await search_section_chunks(db, query, plan)
        if section_hits:
            channels.append(ChannelHits("section", section_hits))
            used.append("section")

    if plan.use_entity:
        from app.core.retriever.evidence_pipeline.entity_retriever import (
            search_entity_chunks,
        )

        entity_hits = await search_entity_chunks(db, query, plan)
        if entity_hits:
            channels.append(ChannelHits("entity", entity_hits))
            used.append("entity")

    fused = fuse_channel_rankings(channels, limit=query.top_k * 2)
    fused = apply_low_info_penalty(list(fused))
    if fused and query.description:
        fused = await rerank_results_by_query(
            db, fused, query.description, limit=query.top_k * 2, min_rerank_size=2
        )

    starvation = False
    uncovered_pages: tuple[int, ...] = ()
    try:
        from app.core.retriever.evidence_pipeline.starvation_expand import (
            expand_uncovered_pages,
            merge_unique_hits,
        )
        from app.core.retriever.evidence_priors import detect_retrieval_starvation

        covered_pages = {item.page_num for item in fused if item.page_num}
        finding = await detect_retrieval_starvation(
            db,
            query.document_id,
            query.category,
            covered_pages=covered_pages,
            requirement_keywords=list(plan.lexical_terms),
        )
        if finding and finding.uncovered_pages:
            extra = await expand_uncovered_pages(
                db,
                query.document_id,
                finding.uncovered_pages,
                existing_ids={item.chunk_id for item in fused},
            )
            if extra:
                fused = merge_unique_hits(fused, extra)
                used.append("starvation")
            starvation = True
            uncovered_pages = tuple(finding.uncovered_pages)
    except Exception as err:
        logger.warning(f"[assemble_package] 饥饿页进包降级: {err}")

    coverage = evaluate_facet_coverage(query.facets, fused) if query.facets else None
    return EvidencePackage(
        results=fused,
        image_results=[],
        coverage=coverage,
        channels_used=tuple(used),
        starvation=starvation,
        uncovered_pages=uncovered_pages,
    )
