"""门禁专用轻量目录检测。

职责: 从门禁已采样的页面文本识别可信目录证据，并兼容常见空格/制表符页码格式。
不做: 不读取PDF、不做OCR、不修改共享TocParser的目录导航语义。
"""

from dataclasses import dataclass
import re

from app.core.parser.pdf_parser import ParsedPage
from app.core.parser.toc_parser import TocEntry, TocParser

MIN_CONFIDENT_TOC_ENTRIES = 3

_TOC_HEADING = re.compile(
    r"^\s*"
    r"(?:(?:招标|投标|响应|采购)文件|技术标|商务标)?\s*"
    r"目\s*录"
    r"(?:\s*(?:CONTENTS|第?\s*\d{1,3}\s*页?|[（(](?:正本|副本)[）)]))?"
    r"\s*$",
    re.IGNORECASE,
)
_FLEX_TOC_LINES = (
    re.compile(
        r"^\s*(?P<title>.{2,60}?)\s{2,}"
        r"(?:第\s*)?(?P<page>\d{1,4})(?:\s*页)?\s*$"
    ),
    re.compile(
        r"^\s*(?P<title>.{2,60}?)\s+"
        r"(?:第\s*)?(?P<page>\d{1,4})\s*页\s*$"
    ),
)
_TITLE_SIGNAL = re.compile(r"[\u4e00-\u9fffA-Za-z]")


@dataclass(frozen=True)
class GateTocEvidence:
    """门禁目录证据及其可信度。"""

    entries: tuple[TocEntry, ...]
    is_confident: bool


def detect_gate_toc(page_texts: list[str]) -> GateTocEvidence:
    """识别门禁可使用的目录条目，不扩展共享目录导航结果。"""
    pages = [
        ParsedPage(page_num=index + 1, text=text)
        for index, text in enumerate(page_texts)
    ]
    standard_entries = TocParser().extract_toc_entries(
        pages,
        max_pages=len(pages),
    )
    flexible_candidates, has_heading = _collect_flexible_candidates(pages)
    flexible_confident = _flexible_candidates_are_confident(
        flexible_candidates,
        has_heading=has_heading,
    )
    standard_confident = (
        has_heading
        and len(standard_entries) >= MIN_CONFIDENT_TOC_ENTRIES
    )

    entries: list[TocEntry] = []
    if standard_confident:
        entries.extend(standard_entries)
    if flexible_confident:
        entries.extend(flexible_candidates)
    entries = _dedupe_entries(entries)

    is_confident = standard_confident or flexible_confident
    return GateTocEvidence(entries=tuple(entries), is_confident=is_confident)


def _collect_flexible_candidates(
    pages: list[ParsedPage],
) -> tuple[list[TocEntry], bool]:
    """收集空格/制表符目录行候选，暂不判定其可信度。"""
    candidates: list[TocEntry] = []
    has_heading = False

    for page in pages:
        for line in page.text.splitlines():
            if _TOC_HEADING.fullmatch(line):
                has_heading = True
                continue
            entry = _parse_flexible_toc_line(line)
            if entry is not None:
                candidates.append(entry)

    return candidates, has_heading


def _parse_flexible_toc_line(line: str) -> TocEntry | None:
    """解析单条灵活目录候选。"""
    for pattern in _FLEX_TOC_LINES:
        match = pattern.fullmatch(line)
        if match is None:
            continue
        title = match.group("title").strip()
        page_num = int(match.group("page"))
        if not _TITLE_SIGNAL.search(title) or not 1 <= page_num <= 999:
            return None
        return TocEntry(title=title, page_num=page_num)
    return None


def _flexible_candidates_are_confident(
    candidates: list[TocEntry],
    *,
    has_heading: bool,
) -> bool:
    """灵活格式必须有目录标题，避免编号评分表被误认。"""
    return (
        has_heading
        and len(candidates) >= MIN_CONFIDENT_TOC_ENTRIES
        and _page_numbers_are_nondecreasing(candidates)
    )


def _page_numbers_are_nondecreasing(entries: list[TocEntry]) -> bool:
    """目录印刷页码应按文档顺序大体非递减。"""
    return all(
        current.page_num <= following.page_num
        for current, following in zip(entries, entries[1:])
    )


def _dedupe_entries(entries: list[TocEntry]) -> list[TocEntry]:
    """按标题和印刷页码去重并稳定排序。"""
    unique = {
        (entry.title, entry.page_num): entry
        for entry in entries
    }
    return sorted(
        unique.values(),
        key=lambda entry: (entry.page_num, entry.title),
    )
