"""
职责: 编排「各自由论述章检索认领 → 未认领的需求纲要按亲和挂载，否则落入兜底章」。
不做: 不落库 writing_outline_nodes、不改写约束、不替代骨架丰富 LLM、不把建设内容固定塞进其他资料。
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.writer.directory_spec_context import load_document_toc_map
from app.core.writer.outline_scope.attacher import (
    attach_titles_to_parent,
    iter_enrichable_parents,
    pick_affinity_parent,
)
from app.core.writer.outline_scope.chapter_retriever import (
    is_catchall_chapter_title,
    select_related_chunks_for_chapter,
)
from app.core.writer.outline_scope.contracts import CATCHALL_EXTRACT_TOPIC, MAX_TITLES_TOTAL
from app.core.writer.outline_scope.extractor import extract_scope_titles, render_scope_text
from app.models.chunk import Chunk
from app.models.document import Document, DocumentType
from app.schemas.writing import OutlineNodeGenerated

_MAX_ENRICH_PARENTS = 8
_RETRIEVE_TOP_K = 8
_RETRIEVE_MIN_SIMILARITY = 0.32


async def attach_scope_titles_to_outline(
    *,
    tree: list[OutlineNodeGenerated],
    db: AsyncSession,
    llm_client: Any,
    project_id: int,
) -> int:
    """返回实际挂上的标题数；失败返回 0 且不中断目录生成。"""

    try:
        document = await _load_tender_document(db, project_id)
        if document is None:
            return 0
        chunks = await _load_chunks(db, document.id)
        if not chunks:
            return 0
        toc_map = await load_document_toc_map(db, document.id)
        parents = iter_enrichable_parents(tree)[:_MAX_ENRICH_PARENTS]
        if not parents:
            return 0

        specific = [
            node for node in parents
            if not is_catchall_chapter_title(node.title or "")
        ]
        catchalls = [
            node for node in parents
            if is_catchall_chapter_title(node.title or "")
        ]

        used_norms: set[str] = set()
        attached = 0
        parent_windows: dict[int, str] = {}
        for parent in specific:
            if attached >= MAX_TITLES_TOTAL:
                break
            title = parent.title or ""
            retrieved = await _retrieve_related_chunks(
                db, document.id, title, chunks,
            )
            related = select_related_chunks_for_chapter(
                chapter_title=title,
                chunks=chunks,
                toc_map=toc_map,
                retrieved=retrieved,
            )
            parent_windows[id(parent)] = render_scope_text(related)
            if not related:
                continue
            titles = await extract_scope_titles(
                related,
                llm_client,
                chapter_title=title,
            )
            attached += attach_titles_to_parent(
                parent,
                titles,
                used_norms=used_norms,
                remaining_global=MAX_TITLES_TOTAL - attached,
            )

        if catchalls and attached < MAX_TITLES_TOTAL:
            demand_related = select_related_chunks_for_chapter(
                chapter_title=catchalls[0].title or "",
                chunks=chunks,
                toc_map=toc_map,
            )
            if demand_related:
                leftovers = await extract_scope_titles(
                    demand_related,
                    llm_client,
                    chapter_title=CATCHALL_EXTRACT_TOPIC,
                )
                for item in leftovers:
                    if attached >= MAX_TITLES_TOTAL:
                        break
                    home = pick_affinity_parent(
                        specific,
                        item.title,
                        parent_windows=parent_windows,
                    ) or catchalls[0]
                    attached += attach_titles_to_parent(
                        home,
                        [item],
                        used_norms=used_norms,
                        remaining_global=MAX_TITLES_TOTAL - attached,
                    )
        if attached:
            logger.info(
                f"实质内容标题挂载: project={project_id}, titles={attached}"
            )
        return attached
    except Exception as exc:
        logger.warning(f"实质内容标题挂载失败(project={project_id}): {exc}")
        return 0


async def _retrieve_related_chunks(
    db: AsyncSession,
    document_id: int,
    chapter_title: str,
    chunks: list[Chunk],
) -> list[Chunk]:
    """语义检索；失败则返回空，由标题词字面检索兜底。"""

    if not chapter_title.strip():
        return []
    try:
        from app.core.retriever.semantic_retriever import SemanticRetriever

        hits = await SemanticRetriever(db).search_in_document(
            document_id,
            chapter_title,
            top_k=_RETRIEVE_TOP_K,
            min_similarity=_RETRIEVE_MIN_SIMILARITY,
        )
    except Exception as exc:
        logger.warning(f"章节实质内容检索失败({chapter_title}): {exc}")
        return []

    by_id = {
        getattr(chunk, "id"): chunk
        for chunk in chunks
        if getattr(chunk, "id", None) is not None
    }
    related: list[Chunk] = []
    seen: set[int] = set()
    for hit in hits:
        chunk = by_id.get(hit.chunk_id)
        if chunk is None or hit.chunk_id in seen:
            continue
        seen.add(hit.chunk_id)
        related.append(chunk)
    return related


async def _load_tender_document(
    db: AsyncSession,
    project_id: int,
) -> Document | None:
    result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.doc_type == DocumentType.TENDER,
            Document.parsed == 1,
        )
    )
    return result.scalar_one_or_none()


async def _load_chunks(db: AsyncSession, document_id: int) -> list[Chunk]:
    result = await db.execute(
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index)
    )
    return list(result.scalars().all())


__all__ = ["attach_scope_titles_to_outline"]
