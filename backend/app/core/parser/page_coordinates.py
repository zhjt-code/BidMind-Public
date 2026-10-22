"""目录页码坐标转换工具。

职责: 将目录印刷页码解析为可跳转的 PDF 物理页码。
不做: 不解析 PDF 文件、不查询数据库、不参与语义检索或审查判定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TOC_DOTTED_LINE_RE = re.compile(r"[\.·…—\-]{2,}\s*\d{1,4}\s*$")
_CN_SECTION_PREFIX_RE = re.compile(r"^[一二三四五六七八九十]+[、.．]\s*")
_NUM_SECTION_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*[、.．\s]*")
_NOISE_RE = re.compile(r"[\s\u3000·•・,，。.;；、:：\"'“”‘’()\[\]【】\-—_]+")


@dataclass(frozen=True)
class ResolvedTocEntry:
    """已解析坐标系的目录条目。"""

    title: str
    printed_page: int
    physical_page: int | None
    matched_physical_page: int | None

    def to_dict(self) -> dict[str, int | str | None]:
        """序列化为可存入 toc_map 的 JSON 对象。"""
        return {
            "title": self.title,
            "printed_page": self.printed_page,
            "physical_page": self.physical_page,
            "matched_physical_page": self.matched_physical_page,
        }


@dataclass(frozen=True)
class PageCoordinateResolution:
    """目录页码坐标解析结果。"""

    entries: list[ResolvedTocEntry]
    offset: int | None
    confidence: str
    matched_count: int
    total_entries: int

    @property
    def is_reliable(self) -> bool:
        """是否可把 physical_page 用作 chunk 查询或 PDF 跳转锚点。"""
        has_physical_page = any(entry.physical_page is not None for entry in self.entries)
        return (
            self.offset is not None
            and self.confidence in {"high", "medium"}
            and has_physical_page
        )


def resolve_toc_page_coordinates(
    pages: list[Any],
    entries: list[Any],
    *,
    max_toc_pages: int = 5,
) -> PageCoordinateResolution:
    """推断目录印刷页码到 PDF 物理页码的偏移。

    Args:
        pages: ParsedPage-like 对象列表，需具备 page_num/text。
        entries: TocEntry-like 对象列表，需具备 title/page_num。
        max_toc_pages: 仅用于目录页识别窗口，避免正文中点线页干扰。

    Returns:
        PageCoordinateResolution: 坐标解析结果。
    """
    if not pages or not entries:
        return PageCoordinateResolution([], None, "low", 0, 0)

    page_count = max((int(getattr(page, "page_num", 0) or 0) for page in pages), default=0)
    matched_offsets: list[int] = []
    matched_by_title: dict[tuple[str, int], int] = {}

    for entry in entries:
        title = str(getattr(entry, "title", "") or "").strip()
        printed_page = int(getattr(entry, "page_num", 0) or 0)
        if not title or printed_page < 1:
            continue

        matched_page = _find_title_physical_page(
            pages,
            title,
            max_toc_pages=max_toc_pages,
        )
        if matched_page is None:
            continue
        matched_by_title[(title, printed_page)] = matched_page
        matched_offsets.append(matched_page - printed_page)

    offset, confidence = _choose_offset(matched_offsets, len(entries))
    resolved_entries: list[ResolvedTocEntry] = []
    for entry in entries:
        title = str(getattr(entry, "title", "") or "").strip()
        printed_page = int(getattr(entry, "page_num", 0) or 0)
        matched_page = matched_by_title.get((title, printed_page))
        physical_page: int | None = None
        if offset is not None and printed_page >= 1:
            candidate = printed_page + offset
            if 1 <= candidate <= page_count:
                physical_page = candidate
        resolved_entries.append(
            ResolvedTocEntry(
                title=title,
                printed_page=printed_page,
                physical_page=physical_page,
                matched_physical_page=matched_page,
            )
        )

    return PageCoordinateResolution(
        entries=resolved_entries,
        offset=offset,
        confidence=confidence,
        matched_count=len(matched_offsets),
        total_entries=len(entries),
    )


def normalize_toc_title(title: str) -> str:
    """归一化目录标题，用于页内匹配与关键词查询。"""
    return _NOISE_RE.sub("", title or "").lower()


def strip_toc_numbering(title: str) -> str:
    """去掉常见章节编号，保留业务标题主体。"""
    stripped = (title or "").strip()
    stripped = _CN_SECTION_PREFIX_RE.sub("", stripped)
    stripped = _NUM_SECTION_PREFIX_RE.sub("", stripped)
    return stripped.strip()


def _find_title_physical_page(
    pages: list[Any],
    title: str,
    *,
    max_toc_pages: int,
) -> int | None:
    variants = _title_variants(title)
    if not variants:
        return None

    best: tuple[int, int] | None = None
    for page in pages:
        page_num = int(getattr(page, "page_num", 0) or 0)
        text = str(getattr(page, "text", "") or "")
        if not text.strip() or looks_like_toc_page(text, page_num, max_toc_pages):
            continue
        score = _match_score(text, variants)
        if score <= 0:
            continue
        if best is None or score > best[0] or (score == best[0] and page_num < best[1]):
            best = (score, page_num)
    return best[1] if best else None


def _title_variants(title: str) -> list[str]:
    stripped = strip_toc_numbering(title)
    raw = (title or "").strip()
    variants = [
        normalize_toc_title(raw),
        normalize_toc_title(stripped),
    ]
    return [variant for variant in dict.fromkeys(variants) if len(variant) >= 2]


def _match_score(text: str, variants: list[str]) -> int:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_lines = "\n".join(lines[:8])
    normalized_head = normalize_toc_title(first_lines)
    normalized_text = normalize_toc_title(text[:1500])
    for variant in variants:
        if variant and variant in normalized_head:
            return 2
    for variant in variants:
        if variant and variant in normalized_text:
            return 1
    return 0


def looks_like_toc_page(text: str, page_num: int, max_toc_pages: int = 6) -> bool:
    """前几页是否呈目录形态(点线行或「目录」标题)。"""
    if page_num > max_toc_pages:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    dotted_lines = sum(1 for line in lines if _TOC_DOTTED_LINE_RE.search(line))
    compact = normalize_toc_title(text)
    return dotted_lines >= 2 or ("目录" in text and dotted_lines >= 1) or compact in {"目录", "目錄"}


_looks_like_toc_page = looks_like_toc_page


def _choose_offset(offsets: list[int], total_entries: int) -> tuple[int | None, str]:
    if not offsets or total_entries <= 0:
        return None, "low"
    counts: dict[int, int] = {}
    for offset in offsets:
        counts[offset] = counts.get(offset, 0) + 1
    chosen, votes = max(counts.items(), key=lambda item: (item[1], -abs(item[0])))
    match_ratio = votes / max(total_entries, 1)
    if votes >= 2 and match_ratio >= 0.5:
        return chosen, "high"
    if total_entries >= 2 and votes >= 1 and match_ratio >= 0.5:
        return chosen, "medium"
    return None, "low"
