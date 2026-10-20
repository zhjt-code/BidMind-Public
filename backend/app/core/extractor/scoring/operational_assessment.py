"""履约/运营考核表软标记。

职责: 按 chunk 文本签名收集考核表页，并判断评分项是否落在这些页内。
不做: 不拒绝评分候选、不改分值、不调用 LLM/DB。
"""
from __future__ import annotations

from typing import Any, Iterable

from app.core.extractor.scoring.scoring_pipeline_core import chunk_page_range
from app.core.parser.scoring_table_detector import looks_like_operational_assessment

SUSPECTED_ASSESSMENT_LABEL = "疑似履约考核，建议排除"


def assessment_pages_from_chunks(chunks: Iterable[Any]) -> frozenset[int]:
    """从评分表 chunk 中收集履约考核表覆盖的页码。

    表头页范围与 pdfplumber page_num 可能差一页（3422 考核项落在 54/65，
    chunk 头写第55-56/66-67页），故并入表头下界-1 与 chunk.page_num。
    """
    pages: set[int] = set()
    for chunk in chunks:
        content = getattr(chunk, "content", "") or ""
        if not looks_like_operational_assessment(content):
            continue
        ranged = chunk_page_range(chunk)
        pages.update(ranged)
        if ranged:
            pages.add(min(ranged) - 1)
        try:
            pn = int(getattr(chunk, "page_num", 0) or 0)
        except (TypeError, ValueError):
            pn = 0
        if pn >= 1:
            pages.add(pn)
    return frozenset(p for p in pages if p >= 1)


def item_is_suspected_assessment(item: Any, assessment_pages: frozenset[int]) -> bool:
    """评分项来源页完全落在考核表页内时视为疑似履约考核。"""
    if not assessment_pages:
        return False
    pages: set[int] = set()
    for page in getattr(item, "source_pages", None) or []:
        try:
            pages.add(int(page))
        except (TypeError, ValueError):
            continue
    source_page = getattr(item, "source_page", None)
    try:
        if source_page is not None:
            pages.add(int(source_page))
    except (TypeError, ValueError):
        pass
    for attr in ("source_page_start", "source_page_end"):
        try:
            val = getattr(item, attr, None)
            if val is not None:
                pages.add(int(val))
        except (TypeError, ValueError):
            continue
    return bool(pages) and pages <= set(assessment_pages)


def is_suspected_assessment_meta(meta: dict | None) -> bool:
    """落库 metadata 是否带疑似履约考核标记。"""
    return bool(meta and meta.get("suspected_assessment_form") is True)


def suspected_assessment_notice(count: int, score: float) -> str:
    """确认页提示文案。count<=0 时返回空串。"""
    if count <= 0:
        return ""
    weight = int(score) if float(score) == int(score) else round(float(score), 4)
    return f"另有 {count} 项疑似履约考核（合计 {weight} 分），建议排除"


def attach_suspected_assessment_payload(
    payload: dict[str, Any] | None,
    *,
    count: int,
    score: float,
) -> dict[str, Any] | None:
    """把疑似考核计数写入核计 payload；不改变 needs_review。"""
    if payload is None:
        return None
    payload["suspected_assessment_count"] = int(count)
    payload["suspected_assessment_score"] = float(score)
    return payload


async def stamp_requirement_assessment_flags(
    db: Any,
    document_id: int,
    saved: list[Any],
) -> None:
    """审查落库后回写疑似履约考核标记（避免向超阈值 extractor 追加方法）。"""
    if not saved:
        return
    from sqlalchemy import select

    from app.models.chunk import Chunk

    result = await db.execute(
        select(Chunk).where(
            Chunk.document_id == document_id,
            Chunk.chunk_type == "scoring_table",
        )
    )
    pages = assessment_pages_from_chunks(result.scalars().all())
    changed = False
    for req in saved:
        if not item_is_suspected_assessment(req, pages):
            continue
        meta = dict(getattr(req, "scoring_metadata", None) or {})
        meta["suspected_assessment_form"] = True
        req.scoring_metadata = meta
        changed = True
    if changed:
        await db.commit()


def stamp_clause_assessment_flags(clauses: list[Any], chunks: Iterable[Any]) -> None:
    """写标权威评分约束就地标记疑似履约考核。"""
    pages = assessment_pages_from_chunks(chunks)
    for clause in clauses:
        if not item_is_suspected_assessment(clause, pages):
            continue
        meta = dict(getattr(clause, "metadata_json", None) or {})
        meta["suspected_assessment_form"] = True
        clause.metadata_json = meta


__all__ = [
    "SUSPECTED_ASSESSMENT_LABEL",
    "assessment_pages_from_chunks",
    "attach_suspected_assessment_payload",
    "is_suspected_assessment_meta",
    "item_is_suspected_assessment",
    "stamp_clause_assessment_flags",
    "stamp_requirement_assessment_flags",
    "suspected_assessment_notice",
]
