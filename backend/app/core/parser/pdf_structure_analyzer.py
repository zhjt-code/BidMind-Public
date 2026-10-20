"""PDF结构分析器

用于识别 PDF 内部的逻辑章节边界，供标书编写等场景拆分综合素材。
首版采用纯规则方案：
1. 目录页条目提取
2. 标题行检测
3. 业务关键词建议标签
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from app.core.parser.pdf_parser import ParsedDocument
from app.core.parser.page_coordinates import resolve_toc_page_coordinates
from app.core.parser.toc_parser import TocEntry, TocParser
from app.core.writer.filename_classifier import classify_by_text_signal


_HEADING_PATTERNS = [
    re.compile(r"^[一二三四五六七八九十]+[、.]\s*(.{2,30})\s*$"),
    re.compile(r"^第[一二三四五六七八九十\d]+[章节部分卷编]\s*(.{2,30})\s*$"),
    re.compile(r"^[（(][一二三四五六七八九十\d]+[）)]\s*(.{2,40})\s*$"),
    re.compile(
        r"^(公司简介|企业简介|企业概况|企业介绍|项目业绩|项目业绩综述|服务承诺|售后服务|响应承诺|管理制度|制度汇编|质量管理|安全管理|人员简历|设备清单|资质证书)\s*$"
    ),
]

_MIN_SECTION_PAGES = 1
_MIN_SECTION_CHARS = 120
_HEADING_EXCLUDE_TERMS = ("续篇", "续页", "下篇", "附表", "附录")


@dataclass(frozen=True)
class PdfSection:
    """PDF 内识别出的逻辑段落。"""

    title: str
    page_start: int
    page_end: int
    confidence: float
    detection_method: str
    suggested_label: str | None = None


@dataclass(frozen=True)
class PdfStructureAnalysis:
    """PDF 结构分析结果。"""

    sections: list[PdfSection] = field(default_factory=list)
    is_composite: bool = False
    overall_confidence: float = 0.0
    evidence_summary: str = ""


class PdfStructureAnalyzer:
    """PDF 内部结构分析器。"""

    def __init__(self) -> None:
        self._toc_parser = TocParser()

    def analyze(self, parsed_doc: ParsedDocument) -> PdfStructureAnalysis:
        """分析 PDF 内部结构。"""
        toc_sections = self._build_sections_from_toc(parsed_doc)
        heading_sections = self._build_sections_from_headings(parsed_doc)

        sections: list[PdfSection]
        evidence_summary: str
        if len(toc_sections) >= 2:
            sections = toc_sections
            evidence_summary = f"toc:{len(toc_sections)}"
        elif len(heading_sections) >= 2:
            sections = heading_sections
            evidence_summary = f"heading:{len(heading_sections)}"
        else:
            sections = []
            evidence_summary = "single_or_unknown"

        overall_confidence = (
            round(sum(section.confidence for section in sections) / len(sections), 3)
            if sections else 0.0
        )
        is_composite = len(sections) >= 2
        return PdfStructureAnalysis(
            sections=sections,
            is_composite=is_composite,
            overall_confidence=overall_confidence,
            evidence_summary=evidence_summary,
        )

    def is_composite_pdf(self, parsed_doc: ParsedDocument) -> bool:
        """快速判断是否为多素材混装 PDF。"""
        return self.analyze(parsed_doc).is_composite

    def _build_sections_from_toc(
        self,
        parsed_doc: ParsedDocument,
    ) -> list[PdfSection]:
        entries = self._toc_parser.extract_toc_entries(parsed_doc.pages)
        if len(entries) < 2:
            return []
        resolution = resolve_toc_page_coordinates(parsed_doc.pages, entries)
        if not resolution.is_reliable:
            return []
        physical_entries = [
            TocEntry(title=entry.title, page_num=entry.physical_page)
            for entry in resolution.entries
            if entry.physical_page is not None
        ]
        if len(physical_entries) < 2:
            return []
        sections = self._entries_to_sections(physical_entries, parsed_doc)
        return self._filter_sections(sections, parsed_doc)

    def _build_sections_from_headings(
        self,
        parsed_doc: ParsedDocument,
    ) -> list[PdfSection]:
        candidates: list[tuple[int, str, float]] = []
        seen_pages: set[int] = set()
        for page in parsed_doc.pages:
            lines = [line.strip() for line in (page.text or "").split("\n") if line.strip()]
            for line in lines[:6]:
                candidate = self._extract_heading_title(line)
                if not candidate:
                    continue
                title, strength = candidate
                if page.page_num in seen_pages:
                    break
                seen_pages.add(page.page_num)
                candidates.append((page.page_num, title, strength))
                break

        if len(candidates) < 2:
            return []

        entries = [TocEntry(title=title, page_num=page_num) for page_num, title, _ in candidates]
        score_hints = {
            (page_num, title): strength
            for page_num, title, strength in candidates
        }
        sections = self._entries_to_sections(
            entries,
            parsed_doc,
            detection_method="heading",
            base_confidence=0.56,
            score_hints=score_hints,
        )
        return self._filter_sections(sections, parsed_doc)

    @staticmethod
    def _extract_heading_title(line: str) -> tuple[str, float] | None:
        cleaned_line = line.strip()
        if any(term in cleaned_line for term in _HEADING_EXCLUDE_TERMS):
            return None
        for index, pattern in enumerate(_HEADING_PATTERNS):
            matched = pattern.match(cleaned_line)
            if not matched:
                continue
            title = (matched.group(1) if matched.groups() else line).strip()
            title = re.sub(r"^[（(【\[]?\d+[）)\]】.\s-]*", "", title).strip()
            if 2 <= len(title) <= 30:
                if index == 0:
                    return title, 0.14
                if index == 1:
                    return title, 0.12
                if index == 2:
                    return title, 0.1
                return title, 0.08
        _, suggested_label = classify_by_text_signal(cleaned_line)
        if suggested_label and 2 <= len(cleaned_line) <= 40:
            return cleaned_line, 0.04
        return None

    def _entries_to_sections(
        self,
        entries: list[TocEntry],
        parsed_doc: ParsedDocument,
        *,
        detection_method: str = "toc",
        base_confidence: float = 0.88,
        score_hints: dict[tuple[int, str], float] | None = None,
    ) -> list[PdfSection]:
        if not entries:
            return []
        sorted_entries = sorted(entries, key=lambda entry: (entry.page_num, entry.title))
        page_count = max(parsed_doc.page_count, 1)
        sections: list[PdfSection] = []

        for index, entry in enumerate(sorted_entries):
            next_entry = sorted_entries[index + 1] if index + 1 < len(sorted_entries) else None
            page_start = max(1, min(entry.page_num, page_count))
            if next_entry:
                page_end = max(page_start, min(next_entry.page_num - 1, page_count))
            else:
                page_end = page_count
            _, suggested_label = classify_by_text_signal(entry.title)
            confidence = self._score_section_confidence(
                title=entry.title,
                page_start=page_start,
                page_end=page_end,
                parsed_doc=parsed_doc,
                detection_method=detection_method,
                base_confidence=base_confidence,
                score_hint=(score_hints or {}).get((entry.page_num, entry.title), 0.0),
                suggested_label=suggested_label,
            )
            sections.append(
                PdfSection(
                    title=entry.title,
                    page_start=page_start,
                    page_end=page_end,
                    confidence=confidence,
                    detection_method=detection_method,
                    suggested_label=suggested_label,
                )
            )
        return sections

    @staticmethod
    def _score_section_confidence(
        *,
        title: str,
        page_start: int,
        page_end: int,
        parsed_doc: ParsedDocument,
        detection_method: str,
        base_confidence: float,
        score_hint: float,
        suggested_label: str | None,
    ) -> float:
        text = parsed_doc.get_text_by_page_range(page_start, page_end)
        compact_len = len(re.sub(r"\s+", "", text))
        page_span = page_end - page_start + 1

        score = base_confidence + score_hint
        if suggested_label:
            score += 0.04
        if detection_method == "toc":
            score += 0.02
        if page_span >= 4:
            score += 0.08
        elif page_span >= 2:
            score += 0.05
        else:
            score += 0.02

        if compact_len >= 1200:
            score += 0.08
        elif compact_len >= 600:
            score += 0.05
        elif compact_len >= 300:
            score += 0.03
        else:
            score += 0.01

        return round(min(max(score, 0.0), 0.97), 3)

    def _filter_sections(
        self,
        sections: list[PdfSection],
        parsed_doc: ParsedDocument,
    ) -> list[PdfSection]:
        filtered: list[PdfSection] = []
        seen_ranges: set[tuple[int, int, str]] = set()
        for section in sections:
            key = (section.page_start, section.page_end, section.title)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            page_span = section.page_end - section.page_start + 1
            text = parsed_doc.get_text_by_page_range(section.page_start, section.page_end)
            if page_span < _MIN_SECTION_PAGES:
                continue
            if len(re.sub(r"\s+", "", text)) < _MIN_SECTION_CHARS:
                continue
            filtered.append(section)
        return filtered
