"""
职责: 从招标文件 chunks 中选取采购/技术需求与资格/资质/准入章节窗口。
不做: 不调用 LLM、不写库、不生成 writing_outline_nodes。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.core.writer.format_section_markers import is_format_section_title
from app.models.chunk import Chunk

_DEFAULT_MAX_PAGES = 80
_TOP_LEVEL_CHAPTER_RE = re.compile(r"^\s*第[一二三四五六七八九十\d]+[章节]")
_INLINE_INCLUDE_RE = re.compile(
    r"(研究内容|建设内容|服务内容|资格要求|资质要求|项目内容|工作内容)包括"
)
_DEMAND_INCLUDE_RE = re.compile(
    r"(研究内容|建设内容|服务内容|项目内容|工作内容)包括"
)

DEMAND_CHAPTER_KEYWORDS = (
    "采购需求",
    "采购内容",
    "采购范围",
    "技术需求",
    "技术规格",
    "研究内容",
    "建设内容",
    "服务内容",
    "服务范围",
    "项目需求",
    "工作内容",
    "项目内容",
)
QUAL_CHAPTER_KEYWORDS = (
    "投标人资格",
    "资格要求",
    "资质要求",
    "资格审查",
    "准入条件",
    "资格条件",
    "申请人资格",
    "投标资格",
)
SCOPE_CHAPTER_KEYWORDS = DEMAND_CHAPTER_KEYWORDS + QUAL_CHAPTER_KEYWORDS


def select_demand_chunks(
    chunks: list[Chunk],
    toc_map: Optional[dict[str, Any]],
    *,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> list[Chunk]:
    """兜底章用的需求窗口：建设/研究/项目内容，不含资格章。"""

    return select_scope_chunks(
        chunks,
        toc_map,
        max_pages=max_pages,
        chapter_keywords=DEMAND_CHAPTER_KEYWORDS,
        include_re=_DEMAND_INCLUDE_RE,
    )


def select_scope_chunks(
    chunks: list[Chunk],
    toc_map: Optional[dict[str, Any]],
    *,
    max_pages: int = _DEFAULT_MAX_PAGES,
    chapter_keywords: tuple[str, ...] = SCOPE_CHAPTER_KEYWORDS,
    include_re: re.Pattern[str] = _INLINE_INCLUDE_RE,
) -> list[Chunk]:
    """按 toc → 行首需求章标题 → 正文「包括」窗口 的优先级选取需求章。"""

    if max_pages <= 0 or not chunks:
        return []

    ordered = _order_chunks_by_page(chunks)
    selected = _select_by_toc(
        ordered, toc_map, max_pages=max_pages, chapter_keywords=chapter_keywords,
    )
    if selected:
        return selected
    selected = _select_by_heading(
        ordered, max_pages=max_pages, chapter_keywords=chapter_keywords,
    )
    if selected:
        return selected
    return _select_by_inline_include(
        ordered, max_pages=max_pages, include_re=include_re,
    )


def _order_chunks_by_page(chunks: list[Chunk]) -> list[Chunk]:
    return sorted(
        chunks,
        key=lambda c: (
            getattr(c, "page_num", None) is None,
            getattr(c, "page_num", None) or 0,
            getattr(c, "chunk_index", 0),
        ),
    )


def _is_reliable_v2_toc_map(toc_map: Optional[dict[str, Any]]) -> bool:
    return bool(
        isinstance(toc_map, dict)
        and toc_map.get("version") == 2
        and toc_map.get("coordinate_system") == "physical_page"
        and toc_map.get("enabled")
    )


def _title_is_scope_chapter(
    title: str,
    chapter_keywords: tuple[str, ...] = SCOPE_CHAPTER_KEYWORDS,
) -> bool:
    compact = re.sub(r"\s+", "", title or "")
    if not compact or is_format_section_title(compact):
        return False
    return any(keyword in compact for keyword in chapter_keywords)


def _select_by_toc(
    ordered: list[Chunk],
    toc_map: Optional[dict[str, Any]],
    *,
    max_pages: int,
    chapter_keywords: tuple[str, ...] = SCOPE_CHAPTER_KEYWORDS,
) -> list[Chunk]:
    if not _is_reliable_v2_toc_map(toc_map):
        return []
    start_pages = sorted({
        int(entry["physical_page"])
        for entry in toc_map.get("entries", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("title"), str)
        and isinstance(entry.get("physical_page"), int)
        and entry["physical_page"] > 0
        and _title_is_scope_chapter(str(entry["title"]), chapter_keywords)
    })
    return _merge_windows(ordered, start_pages, max_pages=max_pages)


def _select_by_heading(
    ordered: list[Chunk],
    *,
    max_pages: int,
    chapter_keywords: tuple[str, ...] = SCOPE_CHAPTER_KEYWORDS,
) -> list[Chunk]:
    start_indexes = [
        index
        for index, chunk in enumerate(ordered)
        if _chunk_has_keywords(getattr(chunk, "content", None) or "", chapter_keywords)
        and not is_format_section_title(getattr(chunk, "content", None) or "")
    ]
    start_pages = []
    for index in start_indexes:
        page = getattr(ordered[index], "page_num", None)
        if isinstance(page, int) and page > 0:
            start_pages.append(page)
        else:
            window = _collect_from_start(
                ordered, start_index=index, max_pages=max_pages,
            )
            if window:
                return window
    return _merge_windows(ordered, start_pages, max_pages=max_pages)


def _select_by_inline_include(
    ordered: list[Chunk],
    *,
    max_pages: int,
    include_re: re.Pattern[str] = _INLINE_INCLUDE_RE,
) -> list[Chunk]:
    for index, chunk in enumerate(ordered):
        text = getattr(chunk, "content", None) or ""
        if not include_re.search(text):
            continue
        if is_format_section_title(text):
            continue
        return _collect_from_start(
            ordered, start_index=index, max_pages=max_pages,
        )
    return []


def _merge_windows(
    ordered: list[Chunk],
    start_pages: list[int],
    *,
    max_pages: int,
) -> list[Chunk]:
    if not start_pages:
        return []
    seen: set[tuple[object, object]] = set()
    merged: list[Chunk] = []
    for start_page in start_pages:
        start_index = next(
            (
                index
                for index, chunk in enumerate(ordered)
                if getattr(chunk, "page_num", None) is not None
                and int(chunk.page_num) >= start_page
            ),
            None,
        )
        if start_index is None:
            continue
        for chunk in _collect_from_start(
            ordered, start_index=start_index, max_pages=max_pages,
        ):
            key = (getattr(chunk, "page_num", None), getattr(chunk, "chunk_index", None))
            if key in seen:
                continue
            seen.add(key)
            merged.append(chunk)
    return merged


def _chunk_has_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    for line in (text or "").split("\n")[:8]:
        stripped = line.strip()
        if not stripped or len(stripped) > 40:
            continue
        if is_format_section_title(stripped):
            continue
        compact = re.sub(r"\s+", "", stripped)
        if any(keyword in compact for keyword in keywords):
            return True
    return False


def _chunk_has_scope_heading(text: str) -> bool:
    return _chunk_has_keywords(text, SCOPE_CHAPTER_KEYWORDS)


def _collect_from_start(
    ordered: list[Chunk],
    *,
    start_index: int,
    max_pages: int,
    stop_at_any_chapter: bool = False,
) -> list[Chunk]:
    relevant: list[Chunk] = []
    start_chunk = ordered[start_index] if ordered else None
    start_page: int | None = getattr(start_chunk, "page_num", None) if start_chunk else None

    for chunk in ordered[start_index:]:
        text = getattr(chunk, "content", None) or ""
        if relevant and _is_next_top_level_chapter(text):
            if stop_at_any_chapter or not _chunk_has_scope_heading(text):
                break
        page = getattr(chunk, "page_num", None)
        # ponytail: 80-page ceiling for demand-chapter windows; upgrade to TOC-derived end if a chapter exceeds this.
        if page is not None and start_page is not None and page > start_page + max_pages - 1:
            break
        relevant.append(chunk)
    return relevant


def _is_next_top_level_chapter(text: str) -> bool:
    if not any(token in text for token in ("第", "章", "附件")):
        return False
    return any(
        _TOP_LEVEL_CHAPTER_RE.match(line.strip())
        for line in text.split("\n")[:3]
    )
