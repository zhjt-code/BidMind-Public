"""文档级投标评分表存在性门禁。

职责: 判定文档是否存在投标评分表，给出 present/absent/uncertain 与采购方式先验。
不做: 不提取评分项、不拼表、不判绿、不调用 LLM。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

from loguru import logger

from app.core.extractor.scoring.ledger.declaration_capture import (
    capture_scoring_declarations,
)
from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk
from app.core.extractor.scoring.scoring_pipeline_core import chunk_page_range
from app.core.extractor.scoring.vlm.scoring_page_locator import locate_scoring_pages
from app.core.parser.scoring_table_detector import (
    BID_EVAL_ANCHORS,
    analyze_scoring_table_rows,
    looks_like_operational_assessment,
)

PresenceVerdict = Literal["present", "absent", "uncertain"]
ProcurementPrior = Literal["negotiation_like", "competitive"]

_NEGOTIATION_TERMS = ("竞争性谈判", "谈判采购", "询价", "单一来源")
_CONSULTATION_TERM = "竞争性磋商"
_ABSENT_NOTICE = "未检出投标评分表，未执行评分项提取；如确有评分表可在确认页手动补录"


@dataclass(frozen=True)
class PresenceDecision:
    """文档级评分表存在性判定。"""

    verdict: PresenceVerdict
    prior: ProcurementPrior
    reasons: tuple[str, ...]
    candidate_pages: tuple[int, ...]

    @property
    def blocks_extraction(self) -> bool:
        """absent，或谈判先验下的 uncertain，均阻断昂贵抽取。"""
        if self.verdict == "absent":
            return True
        return self.verdict == "uncertain" and self.prior == "negotiation_like"


def decide_scoring_table_presence(
    page_texts: dict[int, str],
    chunk_grids: Sequence[list[list[str]] | None],
    chunk_pages: Sequence[set[int]],
    chunk_contents: Sequence[str],
    *,
    title: str = "",
) -> PresenceDecision:
    """纯函数：按三元候选、声明、评标锚点作用域合取判定存在性。"""
    prior = _detect_prior(page_texts, title)
    if not any(str(text or "").strip() for text in page_texts.values()):
        return PresenceDecision("uncertain", prior, ("no_text_layer",), ())

    candidate_pages, table_grids = _collect_candidates(
        chunk_grids, chunk_pages, chunk_contents
    )
    pages = [(page, page_texts.get(page, "")) for page in sorted(page_texts)]
    capture = capture_scoring_declarations(pages, table_grids)
    has_declaration = bool(capture.pairs or capture.total_candidates)
    declaration_pages = {
        int(pair.page)
        for pair in capture.pairs
        if getattr(pair, "page", None) and int(pair.page) >= 1
    }
    located_pages = locate_scoring_pages(page_texts)
    scope = _expand_scope(candidate_pages | declaration_pages | located_pages)
    anchor_in_scope = any(
        _has_bid_eval_anchor(page_texts.get(page, "")) for page in scope
    )

    reasons: list[str] = []
    if candidate_pages:
        reasons.append("candidate_table")
    if has_declaration:
        reasons.append("declaration")
    if located_pages:
        reasons.append("located_pages")
    if anchor_in_scope:
        reasons.append("anchor_in_scope")
    if prior == "negotiation_like":
        reasons.append("negotiation_prior")

    has_signal = bool(candidate_pages) or has_declaration
    located_with_anchor = bool(located_pages) and anchor_in_scope
    if has_signal and anchor_in_scope:
        verdict: PresenceVerdict = "present"
    elif not has_signal and not located_with_anchor:
        verdict = "absent"
        if not reasons:
            reasons.append("no_scoring_structure")
    else:
        verdict = "uncertain"

    logger.info(
        "[评分存在性门禁] verdict={} prior={} reasons={}",
        verdict, prior, ",".join(reasons),
    )
    return PresenceDecision(
        verdict, prior, tuple(reasons), tuple(sorted(candidate_pages))
    )


async def evaluate_document_scoring_presence(
    db: Any,
    document_id: int,
    scoring_chunks: Sequence[Any],
) -> PresenceDecision:
    """从 DB/原 PDF 装配页文本与 chunk 信号后判定存在性；失败 fail-open。"""
    try:
        page_texts, title = await _load_page_texts(db, document_id)
        grids, pages, contents = _chunks_to_signals(scoring_chunks)
        return decide_scoring_table_presence(
            page_texts, grids, pages, contents, title=title
        )
    except Exception as exc:  # noqa: BLE001 - 门禁失败不得阻断抽取
        logger.warning("[评分存在性门禁] 判定失败 doc={} err={}", document_id, exc)
        return PresenceDecision("uncertain", "competitive", ("gate_error",), ())


def _detect_prior(page_texts: dict[int, str], title: str) -> ProcurementPrior:
    blob = str(title or "") + "".join(
        str(page_texts.get(page, "") or "") for page in (1, 2, 3)
    )
    if _CONSULTATION_TERM in blob:
        return "competitive"
    if any(term in blob for term in _NEGOTIATION_TERMS):
        return "negotiation_like"
    return "competitive"


def _collect_candidates(
    chunk_grids: Sequence[list[list[str]] | None],
    chunk_pages: Sequence[set[int]],
    chunk_contents: Sequence[str],
) -> tuple[set[int], list[tuple[int, list[list[str]]]]]:
    candidate_pages: set[int] = set()
    table_grids: list[tuple[int, list[list[str]]]] = []
    for grid, pages, content in zip(chunk_grids, chunk_pages, chunk_contents):
        page_set = {int(page) for page in pages if int(page) >= 1}
        if looks_like_operational_assessment(content):
            continue
        if grid:
            analysis = analyze_scoring_table_rows(grid)
            if "assessment_form" in analysis.reasons:
                continue
            table_grids.append((min(page_set) if page_set else 0, grid))
            if analysis.is_scoring_candidate:
                candidate_pages.update(page_set)
            continue
        if page_set:
            candidate_pages.update(page_set)
    return candidate_pages, table_grids


def _expand_scope(pages: set[int]) -> set[int]:
    scope: set[int] = set()
    for page in pages:
        scope.update((page - 1, page, page + 1))
    return {page for page in scope if page >= 1}


def _has_bid_eval_anchor(text: str) -> bool:
    blob = str(text or "")
    return any(anchor in blob for anchor in BID_EVAL_ANCHORS)


def _chunks_to_signals(
    scoring_chunks: Sequence[Any],
) -> tuple[list[list[list[str]] | None], list[set[int]], list[str]]:
    grids: list[list[list[str]] | None] = []
    pages: list[set[int]] = []
    contents: list[str] = []
    for chunk in scoring_chunks or ():
        payload = payload_from_chunk(chunk)
        grid = payload.get("grid") if payload else None
        grids.append(grid if isinstance(grid, list) else None)
        pages.append(set(chunk_page_range(chunk)))
        contents.append(str(getattr(chunk, "content", "") or ""))
    return grids, pages, contents


async def _load_page_texts(
    db: Any, document_id: int
) -> tuple[dict[int, str], str]:
    if db is None:
        return {}, ""
    from sqlalchemy import select

    from app.models.document import Document

    doc = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    title = str(getattr(doc, "filename", "") or "") if doc else ""
    file_path = getattr(doc, "file_path", None) if doc else None
    if not isinstance(file_path, str) or not Path(file_path).exists():
        return {}, title
    return await asyncio.to_thread(_read_pdf_page_texts, file_path), title


def _read_pdf_page_texts(file_path: str) -> dict[int, str]:
    import fitz

    with fitz.open(file_path) as pdf:
        return {
            index + 1: pdf[index].get_text() or ""
            for index in range(pdf.page_count)
        }


__all__ = [
    "PresenceDecision",
    "ABSENT_NOTICE",
    "decide_scoring_table_presence",
    "evaluate_document_scoring_presence",
]

ABSENT_NOTICE = _ABSENT_NOTICE
