"""评标办法/评分表区段定位器（P2）。

职责: 在招标文件中定位"评标办法/详细评审/初步评审/技术标评分/商务评分/计分表"等
 评分相关区段，输出页范围，供跨页拼表与评分项提取聚焦。
不做: 不拼表、不提取评分项、不调用 LLM/DB。

定位策略（参考 教训，避免子串误判）:
1. 优先用 PdfStructureAnalyzer 的章节切分，章节标题命中评分关键词才纳入；
2. 章节切分不可用时，回退扫描页首标题行命中评分关键词，按"下一章节边界或窗口"定界。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.parser.pdf_parser import ParsedDocument
from app.core.parser.pdf_structure_analyzer import PdfStructureAnalyzer

_SCORING_KEYWORDS: tuple[str, ...] = (
    "评标办法",
    "评标方法",
    "详细评审",
    "初步评审",
    "技术标评分",
    "商务标评分",
    "技术评分",
    "商务评分",
    "评分标准",
    "评分细则",
    "评分表",
    "计分表",
    "评审标准",
    "评审表",
    "打分表",
    "评分办法",
)

_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零\d]+[章节部分篇卷]")
_FALLBACK_WINDOW = 20
_TOP_LINES = 6


@dataclass(frozen=True)
class ScoringSection:
    """评分相关区段。"""

    title: str
    page_start: int
    page_end: int
    detection: str  # structure | fallback


def _title_is_scoring(text: str) -> bool:
    return any(kw in (text or "") for kw in _SCORING_KEYWORDS)


def _top_lines(text: str | None, limit: int) -> list[str]:
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    return lines[:limit]


def locate_scoring_sections(parsed_doc: ParsedDocument) -> list[ScoringSection]:
    """定位评分相关区段。"""
    analysis = PdfStructureAnalyzer().analyze(parsed_doc)
    hits = [
        ScoringSection(s.title, s.page_start, s.page_end, "structure")
        for s in analysis.sections
        if _title_is_scoring(s.title)
    ]
    if hits:
        return _dedupe(hits)
    return _fallback_scan(parsed_doc)


def _fallback_scan(parsed_doc: ParsedDocument) -> list[ScoringSection]:
    pages = parsed_doc.pages
    if not pages:
        return []
    page_count = parsed_doc.page_count or max(p.page_num for p in pages)

    start_pages = [
        p.page_num
        for p in pages
        if any(_title_is_scoring(line) for line in _top_lines(p.text, _TOP_LINES))
    ]
    if not start_pages:
        return []

    chapter_pages = sorted(
        {
            p.page_num
            for p in pages
            if any(_CHAPTER_RE.match(line) for line in _top_lines(p.text, _TOP_LINES))
        }
    )

    results: list[ScoringSection] = []
    for start in sorted(set(start_pages)):
        next_chapter = next((cp for cp in chapter_pages if cp > start), None)
        if next_chapter:
            end = max(start, next_chapter - 1)
        else:
            end = min(start + _FALLBACK_WINDOW, page_count)
        results.append(ScoringSection("评分相关章节", start, end, "fallback"))

    return _merge_overlaps(results)


def _dedupe(sections: list[ScoringSection]) -> list[ScoringSection]:
    seen: set[tuple[int, int]] = set()
    out: list[ScoringSection] = []
    for s in sorted(sections, key=lambda x: (x.page_start, x.page_end)):
        key = (s.page_start, s.page_end)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _merge_overlaps(sections: list[ScoringSection]) -> list[ScoringSection]:
    ordered = sorted(sections, key=lambda x: (x.page_start, x.page_end))
    merged: list[ScoringSection] = []
    for s in ordered:
        if merged and s.page_start <= merged[-1].page_end + 1:
            prev = merged[-1]
            merged[-1] = ScoringSection(
                title=prev.title,
                page_start=prev.page_start,
                page_end=max(prev.page_end, s.page_end),
                detection=prev.detection,
            )
        else:
            merged.append(s)
    return merged


__all__ = ["ScoringSection", "locate_scoring_sections"]
