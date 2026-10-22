"""串标维度上下文召回 

职责: 按维度组用 dense/lexical/entity/table/image 组装截断后的上下文字符串
不做: LLM 对比、串标定性、ORM 持久化
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.collusion.analyzer_parts.constants import _DIMENSION_RETRIEVAL
from app.core.retriever.evidence_pipeline.contracts import RetrievalPlan, RetrievalQuery
from app.core.retriever.evidence_pipeline.entity_retriever import search_entity_chunks
from app.core.retriever.evidence_pipeline.lexical_retriever import (
    search_lexical_in_document,
)
from app.core.retriever.semantic_retriever import SearchResult, SemanticRetriever

_DEFAULT_CHANNELS: tuple[str, ...] = ("dense", "lexical", "image")
_TABLE_TERMS: tuple[str, ...] = (
    "报价表", "工程量清单", "单价分析表", "一览表", "汇总表", "情况表",
)


@dataclass(frozen=True)
class DimensionRetrieveSpec:
    """串标维度检索选项, 避免 retrieve 参数超过 6 个。"""

    channels: tuple[str, ...] = ()
    identifier_terms: tuple[str, ...] = ()
    identifier_pages: tuple[int, ...] = ()


def _resolve_channels(dim_group: str, spec: DimensionRetrieveSpec) -> tuple[str, ...]:
    if spec.channels:
        return spec.channels
    extra: list[str] = []
    if dim_group == "entity" or spec.identifier_terms:
        extra.append("entity")
    if dim_group == "business":
        extra.append("table")
    ordered: list[str] = []
    for name in (*_DEFAULT_CHANNELS, *extra):
        if name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def _format_context(
    scored: Sequence[tuple[float, int, str, int | None]],
    max_chars: int,
) -> str:
    ordered = sorted(scored, key=lambda item: item[0], reverse=True)
    parts: list[str] = []
    total = 0
    for _, _, content, page_num in ordered:
        tag = f"[第{page_num}页] " if page_num else ""
        line = f"{tag}{content}"
        if total + len(line) > max_chars:
            break
        parts.append(line)
        total += len(line)
    return "\n".join(parts)


def _append_hits(
    scored: list[tuple[float, int, str, int | None]],
    seen_ids: set[int],
    hits: Sequence[SearchResult],
    fallback_score: float,
) -> None:
    for hit in hits:
        if hit.chunk_id in seen_ids:
            continue
        seen_ids.add(hit.chunk_id)
        score = hit.similarity if hit.similarity else fallback_score
        scored.append((score, hit.chunk_id, hit.content, hit.page_num))


async def collect_dimension_context_text(
    session: AsyncSession,
    doc_id: int,
    dim_group: str,
    spec: DimensionRetrieveSpec,
    *,
    min_similarity: float = 0.3,
    top_k: int = 8,
    max_chars: int = 10000,
) -> str:
    """单文档维度上下文; 词项失败降级为空通道, 不抛穿。"""
    config = _DIMENSION_RETRIEVAL.get(dim_group)
    if not config:
        return ""
    channels = _resolve_channels(dim_group, spec)
    retriever = SemanticRetriever(session)
    seen_ids: set[int] = set()
    scored: list[tuple[float, int, str, int | None]] = []
    keywords = list(config.get("keywords") or ())
    queries = list(config.get("queries") or ())
    lexical_terms = list(dict.fromkeys([*keywords, *spec.identifier_terms]))

    if "dense" in channels:
        for query_text in queries:
            hits = await retriever.search_in_document(
                document_id=doc_id,
                query=query_text,
                top_k=top_k,
                min_similarity=min_similarity,
            )
            _append_hits(scored, seen_ids, hits, min_similarity)

    if "lexical" in channels and lexical_terms:
        lexical_hits = await search_lexical_in_document(
            session, doc_id, lexical_terms, top_k=top_k
        )
        _append_hits(scored, seen_ids, lexical_hits, 0.15)

    if "table" in channels:
        table_terms = list(dict.fromkeys([*lexical_terms, *_TABLE_TERMS]))
        table_hits = await search_lexical_in_document(
            session, doc_id, table_terms, top_k=top_k
        )
        _append_hits(scored, seen_ids, table_hits, 0.12)

    if "entity" in channels:
        entity_terms = spec.identifier_terms or tuple(keywords)
        entity_query = RetrievalQuery(
            document_id=doc_id,
            description=" ".join(queries[:1] or keywords[:3]),
            identifier_terms=tuple(entity_terms),
            identifier_pages=spec.identifier_pages,
            top_k=top_k,
        )
        entity_plan = RetrievalPlan(
            use_entity=True,
            entity_terms=tuple(entity_terms),
        )
        entity_hits = await search_entity_chunks(
            session, entity_query, entity_plan, existing_ids=seen_ids
        )
        _append_hits(scored, seen_ids, entity_hits, 0.18)

    if "image" in channels:
        page_nums = {page for _, _, _, page in scored if page is not None}
        if page_nums:
            image_results = await retriever.fetch_image_chunks_by_pages(
                document_id=doc_id,
                page_nums=page_nums,
                expand_range=1,
                existing_chunk_ids=seen_ids,
            )
            _append_hits(scored, seen_ids, image_results, 0.05)

    text = _format_context(scored, max_chars)
    logger.debug(
        f"[dimension_context] doc={doc_id} group={dim_group} "
        f"channels={channels} chars={len(text)}"
    )
    return text
