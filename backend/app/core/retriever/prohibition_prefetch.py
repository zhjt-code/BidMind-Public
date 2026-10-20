"""
职责: 按禁止项契约的检索词召回投标文档候选页，供禁区泄漏扫描。
不做: 不符合判定、LLM 调用、must_have 正向检索。
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.gates.review.prohibition_policy import (
    ProhibitionLeakHit,
    ProhibitionSpec,
    scan_prohibition_leaks,
)
from app.models.chunk import Chunk

_FETCH_LIMIT = 48


async def collect_prohibition_leaks(
    db: AsyncSession,
    document_id: int,
    spec: ProhibitionSpec,
) -> tuple[ProhibitionLeakHit, ...]:
    """按 search_terms 召回 chunk 后做区位扫描。"""
    if not document_id or not spec.search_terms:
        return ()
    try:
        term_filters = []
        for term in spec.search_terms:
            term_filters.append(
                (Chunk.section_path.isnot(None)) & (Chunk.section_path.contains(term))
            )
            term_filters.append(Chunk.content.contains(term))
        stmt = (
            select(Chunk.page_num, Chunk.section_path, Chunk.content)
            .where(Chunk.document_id == document_id, or_(*term_filters))
            .order_by(Chunk.page_num, Chunk.chunk_index)
            .limit(_FETCH_LIMIT)
        )
        rows = (await db.execute(stmt)).all()
    except Exception:
        return ()
    payloads = [
        {
            "page": page,
            "section": section or "",
            "content": content or "",
        }
        for page, section, content in rows
    ]
    return scan_prohibition_leaks(payloads, spec)
