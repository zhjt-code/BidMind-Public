"""综合 PDF 素材拆分器。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.parser.pdf_structure_analyzer import PdfSection, PdfStructureAnalyzer
from app.core.writer.filename_classifier import classify_by_text_signal
from app.core.writer.material_content_extractor import (
    MaterialContentExtractor,
    ParseResult,
)
from app.models.writing_material import (
    MaterialRole,
    SplitReviewStatus,
    WritingMaterial,
)

_MIN_SPLIT_CONFIDENCE = 0.55
_NEEDS_REVIEW_CONFIDENCE = 0.7
_MIN_SPLIT_CHAR_COUNT = 200
_FALLBACK_IMAGE_PAGE_CONFIDENCE = 0.66
_FALLBACK_TEXT_PAGE_CONFIDENCE = 0.58


@dataclass
class SubMaterialDraft:
    """子素材草案。"""

    title: str
    parse_result: ParseResult
    source_page_start: int
    source_page_end: int
    split_confidence: float
    structure_hint_label: str | None
    detection_method: str


class MaterialSplitter:
    """综合 PDF 素材拆分器。"""

    def __init__(self) -> None:
        self._analyzer = PdfStructureAnalyzer()

    async def analyze_and_split(
        self,
        material: WritingMaterial,
        parse_result: ParseResult,
    ) -> list[SubMaterialDraft]:
        """分析 PDF 结构并生成子素材草案。"""
        parsed_doc = parse_result.parsed_document
        if parse_result.file_type != "pdf" or parsed_doc is None:
            return []

        analysis = self._analyzer.analyze(parsed_doc)
        if not analysis.is_composite or analysis.overall_confidence < _MIN_SPLIT_CONFIDENCE:
            return []

        drafts: list[SubMaterialDraft] = []
        for section in analysis.sections:
            draft = self._build_sub_material_draft(material, parse_result, section)
            if draft is not None:
                drafts.append(draft)
        drafts.extend(self._build_fallback_page_drafts(material, parse_result, drafts))
        return drafts if len(drafts) >= 2 else []

    async def create_sub_materials(
        self,
        parent: WritingMaterial,
        drafts: list[SubMaterialDraft],
        db: AsyncSession,
    ) -> list[WritingMaterial]:
        """持久化子素材。"""
        created: list[WritingMaterial] = []
        for draft in drafts:
            child = WritingMaterial(
                project_id=parent.project_id,
                document_id=parent.document_id,
                parent_material_id=parent.id,
                material_type=parent.material_type,
                title=draft.title,
                file_path=parent.file_path,
                source_page=draft.source_page_start,
                source_page_start=draft.source_page_start,
                source_page_end=draft.source_page_end,
                processing_status="processing",
                structure_hint_label=draft.structure_hint_label,
                split_confidence=draft.split_confidence,
                material_role=MaterialRole.split_child.value,
                split_review_status=(
                    SplitReviewStatus.confirmed.value
                    if draft.split_confidence >= _NEEDS_REVIEW_CONFIDENCE
                    else SplitReviewStatus.needs_review.value
                ),
            )
            db.add(child)
            created.append(child)

        await db.flush()
        return created

    def _build_sub_material_draft(
        self,
        material: WritingMaterial,
        parse_result: ParseResult,
        section: PdfSection,
    ) -> SubMaterialDraft | None:
        sliced = MaterialContentExtractor.slice_parse_result(
            parse_result,
            section.page_start,
            section.page_end,
        )
        text = sliced.raw_text or ""
        has_images = bool(sliced.images)
        if len(text.strip()) < _MIN_SPLIT_CHAR_COUNT and not has_images:
            return None

        title = self._build_child_title(material.title, section.title)
        return SubMaterialDraft(
            title=title,
            parse_result=sliced,
            source_page_start=section.page_start,
            source_page_end=section.page_end,
            split_confidence=section.confidence,
            structure_hint_label=section.suggested_label,
            detection_method=section.detection_method,
        )

    def _build_fallback_page_drafts(
        self,
        material: WritingMaterial,
        parse_result: ParseResult,
        drafts: list[SubMaterialDraft],
    ) -> list[SubMaterialDraft]:
        parsed_doc = parse_result.parsed_document
        if parsed_doc is None:
            return []

        covered_pages: set[int] = set()
        for draft in drafts:
            covered_pages.update(range(draft.source_page_start, draft.source_page_end + 1))

        fallback_drafts: list[SubMaterialDraft] = []
        pending_text_range: tuple[int, int] | None = None
        for page in parsed_doc.pages:
            if page.page_num in covered_pages:
                if pending_text_range is not None:
                    fallback_drafts.extend(
                        self._build_text_gap_drafts(material, parse_result, *pending_text_range)
                    )
                    pending_text_range = None
                continue

            page_has_images = any(
                getattr(img, "page_num", None) == page.page_num
                for img in parse_result.images
            )
            if page_has_images:
                if pending_text_range is not None:
                    fallback_drafts.extend(
                        self._build_text_gap_drafts(material, parse_result, *pending_text_range)
                    )
                    pending_text_range = None
                page_draft = self._build_single_page_fallback_draft(
                    material,
                    parse_result,
                    page.page_num,
                )
                if page_draft is not None:
                    fallback_drafts.append(page_draft)
                continue

            if pending_text_range is None:
                pending_text_range = (page.page_num, page.page_num)
            else:
                pending_text_range = (pending_text_range[0], page.page_num)

        if pending_text_range is not None:
            fallback_drafts.extend(
                self._build_text_gap_drafts(material, parse_result, *pending_text_range)
            )
        return fallback_drafts

    def _build_text_gap_drafts(
        self,
        material: WritingMaterial,
        parse_result: ParseResult,
        page_start: int,
        page_end: int,
    ) -> list[SubMaterialDraft]:
        section_title = self._derive_fallback_title(parse_result, page_start, page_end)
        section = PdfSection(
            title=section_title,
            page_start=page_start,
            page_end=page_end,
            confidence=_FALLBACK_TEXT_PAGE_CONFIDENCE,
            detection_method="gap_text",
            suggested_label=classify_by_text_signal(section_title)[1],
        )
        draft = self._build_sub_material_draft(material, parse_result, section)
        return [draft] if draft is not None else []

    def _build_single_page_fallback_draft(
        self,
        material: WritingMaterial,
        parse_result: ParseResult,
        page_num: int,
    ) -> SubMaterialDraft | None:
        section_title = self._derive_fallback_title(parse_result, page_num, page_num)
        section = PdfSection(
            title=section_title,
            page_start=page_num,
            page_end=page_num,
            confidence=_FALLBACK_IMAGE_PAGE_CONFIDENCE,
            detection_method="gap_image",
            suggested_label=classify_by_text_signal(section_title)[1],
        )
        return self._build_sub_material_draft(material, parse_result, section)

    @staticmethod
    def _derive_fallback_title(
        parse_result: ParseResult,
        page_start: int,
        page_end: int,
    ) -> str:
        pages = [
            page for page in parse_result.parsed_pages
            if page_start <= page.page_num <= page_end
        ]
        for page in pages:
            lines = [line.strip() for line in (page.text or "").split("\n") if line.strip()]
            for line in lines[:5]:
                if 2 <= len(line) <= 40:
                    return line
        if page_start == page_end:
            return f"附件材料 P{page_start}"
        return f"附件材料 P{page_start}-{page_end}"

    @staticmethod
    def _build_child_title(parent_title: str, section_title: str) -> str:
        normalized = (section_title or "").strip()
        return normalized or "附件材料"
