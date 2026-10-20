"""
职责: 按投标目录章节标题，从招标 chunks 中检索相关章节窗口。
不做: 不调用 LLM、不挂目录树、不写库。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.core.writer.format_section_markers import is_format_section_title
from app.core.writer.outline_scope.selector import (
    _chunk_has_scope_heading,
    _collect_from_start,
    _is_next_top_level_chapter,
    _order_chunks_by_page,
    select_demand_chunks,
)
from app.models.chunk import Chunk

_CATCHALL_KEYWORDS = ("其他资料", "其他证明", "认为必要")
_STOP_TERMS = frozenset({
    "其他", "资料", "投标人", "招标人", "需要", "补充", "格式", "自拟",
    "材料", "文件", "章节", "目录", "如下", "部分", "内容",
})
# 二字切分后过泛，会把「施工资质」检索打成整本「施工/建设」正文
_GENERIC_AFFIX_STOP = frozenset({
    "施工", "技术", "工程", "项目", "服务", "管理", "质量", "安全",
    "方案", "工作", "分析", "测试", "研究", "建设", "采购", "招标",
    "投标", "合同", "要求",
})
_HEADING_LINES = 3
_HEADING_SCORE = 10
_CHAPTER_WINDOW_PAGES = 8


def is_catchall_chapter_title(title: str) -> bool:
    return any(keyword in (title or "") for keyword in _CATCHALL_KEYWORDS)


def chapter_query_terms(title: str) -> list[str]:
    """从章节标题抽出检索词，不用领域词表。"""

    stripped = re.sub(r"[（(][^）)]*[）)]", "", title or "")
    parts = re.split(r"[/、，,;；\s]+", stripped)
    terms: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> bool:
        if len(term) < 2 or term in _STOP_TERMS or term in seen:
            return False
        terms.append(term)
        seen.add(term)
        return True

    for part in parts:
        part = part.strip()
        _add(part)
        if len(part) >= 4:
            prefix, suffix = part[:2], part[-2:]
            if prefix not in _GENERIC_AFFIX_STOP:
                _add(prefix)
            if suffix not in _GENERIC_AFFIX_STOP:
                _add(suffix)
        for token in re.findall(r"[\u4e00-\u9fff]{2,6}", part):
            if token in _GENERIC_AFFIX_STOP:
                continue
            _add(token)
    if "资质" in seen:
        _add("资格")
    if "资格" in seen:
        _add("资质")
    return terms


def select_related_chunks_for_chapter(
    *,
    chapter_title: str,
    chunks: list[Chunk],
    toc_map: Optional[dict[str, Any]] = None,
    retrieved: Optional[list[Chunk]] = None,
) -> list[Chunk]:
    """为单个投标目录章节选出招标相关原文窗口。"""

    if not chunks or not (chapter_title or "").strip():
        return []

    if is_catchall_chapter_title(chapter_title):
        return select_demand_chunks(chunks, toc_map)

    ordered = _order_chunks_by_page(chunks)
    terms = chapter_query_terms(chapter_title)
    from_hits = _windows_from_hits(ordered, retrieved or [], terms)
    if from_hits:
        return from_hits

    return _windows_from_title_terms(ordered, terms)


def _term_score(text: str, terms: list[str]) -> tuple[int, int]:
    if not text or not terms:
        return 0, 0
    heading = "\n".join(text.split("\n")[:_HEADING_LINES])
    heading_hits = sum(1 for term in terms if term in heading)
    body_hits = sum(1 for term in terms if term in text)
    return heading_hits, body_hits


def _rank(heading_hits: int, body_hits: int) -> int:
    return heading_hits * _HEADING_SCORE + body_hits


def _pick_start_indexes(
    scored: list[tuple[int, int, int]],
    *,
    require_heading: bool,
) -> list[int]:
    """scored: (heading_hits, body_hits, index)。有章首命中时只保留章首窗。"""

    if require_heading:
        scored = [item for item in scored if item[0] > 0]
    if not scored:
        return []
    if not require_heading and any(item[0] > 0 for item in scored):
        scored = [item for item in scored if item[0] > 0]
    best = max(_rank(heading, body) for heading, body, _index in scored)
    return [index for heading, body, index in scored if _rank(heading, body) == best]


def _windows_from_hits(
    ordered: list[Chunk],
    retrieved: list[Chunk],
    terms: list[str],
) -> list[Chunk]:
    if not retrieved or not terms:
        return []
    scored: list[tuple[int, int, int]] = []
    for hit in retrieved:
        index = _index_of_chunk(ordered, hit)
        if index is None:
            continue
        text = getattr(ordered[index], "content", None) or ""
        if is_format_section_title(text):
            continue
        heading_hits, body_hits = _term_score(text, terms)
        if body_hits == 0:
            continue
        scored.append((heading_hits, body_hits, index))
    start_indexes = _pick_start_indexes(scored, require_heading=True)
    return _merge_windows_by_index(ordered, start_indexes, terms)


def _windows_from_title_terms(ordered: list[Chunk], terms: list[str]) -> list[Chunk]:
    if not terms:
        return []
    scored: list[tuple[int, int, int]] = []
    for index, chunk in enumerate(ordered):
        text = getattr(chunk, "content", None) or ""
        if is_format_section_title(text):
            continue
        heading_hits, body_hits = _term_score(text, terms)
        if body_hits == 0:
            continue
        if _is_off_topic_heading(text, terms):
            continue
        scored.append((heading_hits, body_hits, index))
    start_indexes = _pick_start_indexes(scored, require_heading=False)
    return _merge_windows_by_index(ordered, start_indexes, terms)


def _is_off_topic_heading(text: str, terms: list[str]) -> bool:
    """章首是招标另一章、且章首不含当前检索词时，不当作起点。"""

    heading = "\n".join((text or "").split("\n")[:_HEADING_LINES])
    looks_like_heading = (
        _is_next_top_level_chapter(text) or _chunk_has_scope_heading(heading)
    )
    if not looks_like_heading:
        return False
    compact = re.sub(r"\s+", "", heading)
    return not any(term in compact for term in terms)


def _merge_windows_by_index(
    ordered: list[Chunk],
    start_indexes: list[int],
    terms: list[str],
) -> list[Chunk]:
    seen: set[tuple[object, object]] = set()
    merged: list[Chunk] = []
    for start_index in sorted(set(start_indexes)):
        for chunk in _collect_from_start(
            ordered,
            start_index=start_index,
            max_pages=_CHAPTER_WINDOW_PAGES,
            stop_at_any_chapter=True,
        ):
            key = (getattr(chunk, "page_num", None), getattr(chunk, "chunk_index", None))
            if key in seen:
                continue
            text = getattr(chunk, "content", None) or ""
            if is_format_section_title(text) and not merged:
                break
            seen.add(key)
            merged.append(chunk)
    return _keep_chunks_with_terms(merged, terms)


def _keep_chunks_with_terms(
    window: list[Chunk],
    terms: list[str],
) -> list[Chunk]:
    """丢掉同章后半段里不再含检索词的报价表/工作清单。"""

    if not window or not terms:
        return window
    kept = [
        chunk
        for chunk in window
        if any(term in (getattr(chunk, "content", None) or "") for term in terms)
        and not _is_price_list_noise(getattr(chunk, "content", None) or "", terms)
    ]
    return kept


def _is_price_list_noise(text: str, terms: list[str]) -> bool:
    """报价/限价表里会夹一句资格用语，但不能把测试清单当资质章正文。"""

    if "单价" not in text and "最高限价" not in text:
        return False
    inventory = text.count("测试")
    term_hits = sum(text.count(term) for term in terms)
    return inventory >= 3 and inventory > term_hits


def _index_of_chunk(ordered: list[Chunk], target: Chunk) -> int | None:
    target_id = getattr(target, "id", None)
    if target_id is not None:
        for index, chunk in enumerate(ordered):
            if getattr(chunk, "id", None) == target_id:
                return index
    for index, chunk in enumerate(ordered):
        if chunk is target:
            return index
        if (
            getattr(chunk, "page_num", None) == getattr(target, "page_num", None)
            and getattr(chunk, "chunk_index", None) == getattr(target, "chunk_index", None)
        ):
            return index
    return None
