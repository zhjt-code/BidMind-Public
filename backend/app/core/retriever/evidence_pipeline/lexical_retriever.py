"""词项检索通道 

职责: 在单文档内按关键词/证号/表名做 trigram + 字面包含召回并按相关度排序
不做: 向量检索、RRF 融合、覆盖评估、LLM 调用
"""

from __future__ import annotations

from typing import Optional, Sequence

from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retriever.semantic_retriever import SearchResult
from app.models.chunk import Chunk
from app.models.document import DocumentType

MAX_LEXICAL_TERMS = 12


def normalize_lexical_terms(
    terms: Sequence[str],
    *,
    max_terms: int = MAX_LEXICAL_TERMS,
) -> list[str]:
    """去空白、去重保序, 丢掉不足 2 字的噪声词。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        term = (raw or "").strip()
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        out.append(term)
        if len(out) >= max_terms:
            break
    return out


def lexical_overlap_score(content: str, terms: Sequence[str]) -> float:
    """无 DB 时的词项相关度: 命中条数 + 词长加权, 供单测与 SQL 失败降级。"""
    if not content or not terms:
        return 0.0
    hits = 0.0
    for term in terms:
        if term and term in content:
            hits += 1.0 + min(len(term), 12) / 12.0
    return hits


async def search_lexical_in_document(
    db: AsyncSession,
    document_id: int,
    terms: Sequence[str],
    *,
    top_k: int = 8,
    chunk_types: Optional[tuple[str, ...]] = None,
    doc_type: Optional[DocumentType] = None,
) -> list[SearchResult]:
    """单文档词项召回; pg_trgm 不可用或查询失败时返回空列表, 不抛穿主流程。"""
    normalized = normalize_lexical_terms(terms)
    if not normalized or top_k <= 0:
        return []

    sim_exprs = [func.similarity(Chunk.content, term) for term in normalized]
    score_expr = sim_exprs[0] if len(sim_exprs) == 1 else func.greatest(*sim_exprs)

    term_filters = []
    for term in normalized:
        term_filters.append(Chunk.content.contains(term))
        term_filters.append(Chunk.content.op("%")(term))
        term_filters.append(
            (Chunk.section_path.isnot(None)) & (Chunk.section_path.contains(term))
        )

    conditions = [
        Chunk.document_id == document_id,
        or_(*term_filters),
    ]
    if chunk_types:
        conditions.append(Chunk.chunk_type.in_(chunk_types))

    pool_size = max(top_k, 1) * 4
    try:
        stmt = (
            select(Chunk)
            .where(*conditions)
            .order_by(score_expr.desc(), Chunk.page_num, Chunk.chunk_index)
            .limit(pool_size)
        )
        rows = list((await db.execute(stmt)).scalars().all())
    except Exception as err:
        logger.warning(f"[lexical_retriever] 词项检索降级为空: {err}")
        return []

    ranked = sorted(
        rows,
        key=lambda chunk: lexical_overlap_score(chunk.content or "", normalized),
        reverse=True,
    )
    results: list[SearchResult] = []
    for chunk in ranked[:top_k]:
        sim = lexical_overlap_score(chunk.content or "", normalized)
        results.append(
            SearchResult.from_chunk(
                chunk,
                similarity=min(sim / 4.0, 1.0),
                doc_type=doc_type,
                matched_keywords=list(normalized),
                role="lexical",
            )
        )
    return results
