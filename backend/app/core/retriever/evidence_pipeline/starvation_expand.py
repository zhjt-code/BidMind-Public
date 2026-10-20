"""饥饿页有界进包 

职责: 把检索饥饿检出的未覆盖页有界注入证据包
不做: 全文灌模型、符合性打分、LLM 调用
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional, Sequence

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.retriever.semantic_retriever import SearchResult
from app.models.chunk import Chunk


def merge_unique_hits(
    existing: Sequence[SearchResult],
    extra: Sequence[SearchResult],
) -> list[SearchResult]:
    seen = {item.chunk_id for item in existing if item.chunk_id is not None}
    merged = list(existing)
    for item in extra:
        if item.chunk_id is None or item.chunk_id in seen:
            continue
        seen.add(item.chunk_id)
        merged.append(item)
    return merged


async def fetch_chunks_on_pages(
    db: AsyncSession,
    document_id: int,
    pages: Sequence[int],
    *,
    existing_ids: Optional[set[int]] = None,
    per_page: Optional[int] = None,
    max_pages: Optional[int] = None,
    role: str = "starvation",
) -> list[SearchResult]:
    """按页有界取 chunk; SQL 失败返回空列表。"""
    page_limit = int(
        max_pages
        if max_pages is not None
        else getattr(settings, "RETRIEVAL_STARVATION_EXPAND_MAX_PAGES", 8)
        or 8
    )
    chunk_limit = int(
        per_page
        if per_page is not None
        else getattr(settings, "RETRIEVAL_STARVATION_EXPAND_CHUNKS_PER_PAGE", 3)
        or 3
    )
    unique_pages = []
    seen_page: set[int] = set()
    for page in pages:
        if page is None or page in seen_page:
            continue
        seen_page.add(page)
        unique_pages.append(page)
        if len(unique_pages) >= page_limit:
            break
    if not unique_pages or chunk_limit <= 0:
        return []
    existing = set(existing_ids or ())
    try:
        stmt = (
            select(Chunk)
            .where(
                Chunk.document_id == document_id,
                Chunk.page_num.in_(unique_pages),
            )
            .order_by(Chunk.page_num, Chunk.chunk_index)
        )
        rows = list((await db.execute(stmt)).scalars().all())
    except Exception as err:
        logger.warning(f"[starvation_expand] 按页召回降级: {err}")
        return []

    grouped: dict[int, list[Chunk]] = defaultdict(list)
    for chunk in rows:
        if chunk.id in existing:
            continue
        page_num = chunk.page_num
        if page_num is None:
            continue
        bucket = grouped[page_num]
        if len(bucket) >= chunk_limit:
            continue
        bucket.append(chunk)

    results: list[SearchResult] = []
    for page_num in unique_pages:
        for chunk in grouped.get(page_num, ()):
            results.append(
                SearchResult.from_chunk(chunk, similarity=0.05, role=role)
            )
            existing.add(chunk.id)
    return results


async def expand_uncovered_pages(
    db: AsyncSession,
    document_id: int,
    uncovered_pages: Sequence[int],
    *,
    existing_ids: Optional[set[int]] = None,
) -> list[SearchResult]:
    """饥饿未覆盖页进包; 上限走 settings, 禁止全文灌入。"""
    return await fetch_chunks_on_pages(
        db,
        document_id,
        uncovered_pages,
        existing_ids=existing_ids,
        role="starvation",
    )
