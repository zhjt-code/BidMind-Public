from types import SimpleNamespace

import pytest

from app.core.parser.image_extractor import ExtractedImage
from app.core.parser.pdf_parser import ParsedDocument, ParsedPage
from app.core.parser.pdf_structure_analyzer import PdfStructureAnalysis, PdfSection
from app.core.writer.material_content_extractor import ParseResult
from app.core.writer.material_titles import (
    is_poor_material_title,
    normalize_image_caption_title,
)
from app.core.writer.material_splitter import MaterialSplitter


def _page(page_num: int, text: str) -> ParsedPage:
    return ParsedPage(
        page_num=page_num,
        text=text,
        tables=[],
        images=[],
        width=595.0,
        height=842.0,
    )


def _parse_result(*texts: str) -> ParseResult:
    pages = [_page(idx + 1, text) for idx, text in enumerate(texts)]
    parsed_doc = ParsedDocument(
        file_path="synthetic.pdf",
        filename="综合素材.pdf",
        page_count=len(pages),
        pages=pages,
        metadata={},
    )
    return ParseResult(
        raw_text="\n\n".join(text for text in texts),
        images=[],
        tables=[],
        page_count=len(pages),
        parsed_pages=pages,
        parsed_document=parsed_doc,
        file_type="pdf",
    )


class _FakeDb:
    def __init__(self) -> None:
        self.added = []
        self._next_id = 100

    def add(self, obj) -> None:
        obj.id = self._next_id
        self._next_id += 1
        self.added.append(obj)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_material_splitter_generates_sub_material_drafts() -> None:
    parent = SimpleNamespace(
        id=1,
        project_id=10,
        document_id=None,
        material_type="text",
        title="综合素材.pdf",
        file_path="synthetic.pdf",
    )
    parse_result = _parse_result(
        "目录\n一、公司简介........2\n二、服务承诺........3",
        "公司简介\n" + ("企业基本情况与主营业务说明，涵盖发展历程、组织架构和综合实力。" * 12),
        "服务承诺\n" + ("提供7x24响应机制、售后保障与问题闭环服务，明确故障受理和到场承诺。" * 12),
    )

    drafts = await MaterialSplitter().analyze_and_split(parent, parse_result)

    assert len(drafts) == 2
    assert drafts[0].title == "一、公司简介"
    assert drafts[0].source_page_start == 2
    assert drafts[1].source_page_start == 3
    assert drafts[1].structure_hint_label == "服务承诺"


@pytest.mark.asyncio
async def test_material_splitter_uses_physical_pages_for_printed_toc_pages() -> None:
    """目录页码从正文起算时，子素材应裁切 PDF 物理页。"""
    parent = SimpleNamespace(
        id=1,
        project_id=10,
        document_id=None,
        material_type="text",
        title="综合素材.pdf",
        file_path="synthetic.pdf",
    )
    parse_result = _parse_result(
        "投 标 文 件\n封面",
        "目录\n一、公司简介........1\n二、服务承诺........2",
        "公司简介\n" + ("企业基本情况与主营业务说明，涵盖发展历程、组织架构和综合实力。" * 12),
        "服务承诺\n" + ("提供7x24响应机制、售后保障与问题闭环服务，明确故障受理和到场承诺。" * 12),
    )

    drafts = await MaterialSplitter().analyze_and_split(parent, parse_result)

    assert len(drafts) == 2
    assert drafts[0].title == "一、公司简介"
    assert drafts[0].source_page_start == 3
    assert "公司简介" in drafts[0].parse_result.raw_text
    assert "目录" not in drafts[0].parse_result.raw_text
    assert drafts[1].source_page_start == 4


@pytest.mark.asyncio
async def test_material_splitter_persists_split_children_with_parent_link() -> None:
    parent = SimpleNamespace(
        id=1,
        project_id=10,
        document_id=None,
        material_type="text",
        title="综合素材.pdf",
        file_path="synthetic.pdf",
    )
    parse_result = _parse_result(
        "目录\n一、公司简介........2\n二、项目业绩........3",
        "公司简介\n" + ("企业基本情况与主营业务说明，涵盖发展历程、组织架构和综合实力。" * 12),
        "项目业绩\n" + ("近三年完成多个大型系统集成项目，履约评价良好并形成典型案例。" * 12),
    )
    splitter = MaterialSplitter()
    drafts = await splitter.analyze_and_split(parent, parse_result)
    fake_db = _FakeDb()

    children = await splitter.create_sub_materials(parent, drafts, fake_db)

    assert len(children) == 2
    assert all(child.parent_material_id == 1 for child in children)
    assert all(child.material_role == "split_child" for child in children)
    assert children[0].source_page_start == 2
    assert children[1].source_page_end == 3


@pytest.mark.asyncio
async def test_material_splitter_adds_fallback_child_for_uncovered_image_page() -> None:
    parent = SimpleNamespace(
        id=1,
        project_id=10,
        document_id=None,
        material_type="text",
        title="综合素材.pdf",
        file_path="synthetic.pdf",
    )
    pages = [
        _page(1, "公司简介\n" + ("企业基本情况与主营业务说明，涵盖发展历程、组织架构和综合实力。" * 12)),
        _page(2, "统一社会信用代码：1234567890\n注册资本：5000万元"),
        _page(3, "项目业绩\n" + ("近三年完成多个大型系统集成项目，履约评价良好并形成典型案例。" * 12)),
    ]
    parsed_doc = ParsedDocument(
        file_path="synthetic.pdf",
        filename="综合素材.pdf",
        page_count=len(pages),
        pages=pages,
        metadata={},
    )
    parse_result = ParseResult(
        raw_text="\n\n".join(page.text for page in pages),
        images=[
            ExtractedImage(
                image_index=0,
                page_num=2,
                width=1200,
                height=900,
                image_format="jpeg",
                xref=1,
                file_path="fake.jpg",
            ),
        ],
        tables=[],
        page_count=4,
        parsed_pages=pages,
        parsed_document=parsed_doc,
        file_type="pdf",
    )

    splitter = MaterialSplitter()
    splitter._analyzer.analyze = lambda _doc: PdfStructureAnalysis(  # type: ignore[method-assign]
        sections=[
            PdfSection("公司简介", 1, 1, 0.8, "heading", "公司简介"),
            PdfSection("项目业绩", 3, 3, 0.8, "heading", "项目业绩综述"),
        ],
        is_composite=True,
        overall_confidence=0.8,
        evidence_summary="test",
    )

    drafts = await splitter.analyze_and_split(parent, parse_result)

    assert len(drafts) == 3
    assert drafts[-1].source_page_start == 2
    assert drafts[-1].source_page_end == 2
    assert drafts[-1].parse_result.images


@pytest.mark.asyncio
async def test_material_splitter_uses_section_title_without_parent_prefix() -> None:
    parent = SimpleNamespace(
        id=1,
        project_id=10,
        document_id=None,
        material_type="text",
        title="其它.pdf",
        file_path="synthetic.pdf",
    )
    parse_result = _parse_result(
        "目录\n180.pdf........2\n公司及团队简介.pdf........3",
        "180.pdf\n" + ("营业执照与基础证照扫描件说明。" * 20),
        "公司及团队简介.pdf\n" + ("公司及团队的组成、履历与综合实力说明。" * 20),
    )

    splitter = MaterialSplitter()
    splitter._analyzer.analyze = lambda _doc: PdfStructureAnalysis(  # type: ignore[method-assign]
        sections=[
            PdfSection("180.pdf", 2, 2, 0.8, "toc", "营业执照"),
            PdfSection("公司及团队简介.pdf", 3, 3, 0.8, "toc", "公司简介"),
        ],
        is_composite=True,
        overall_confidence=0.85,
        evidence_summary="test",
    )

    drafts = await splitter.analyze_and_split(parent, parse_result)

    assert [draft.title for draft in drafts] == ["180.pdf", "公司及团队简介.pdf"]


def test_is_poor_material_title_detects_numeric_and_attachment_titles() -> None:
    assert is_poor_material_title("195") is True
    assert is_poor_material_title("附件材料 P13") is True
    assert is_poor_material_title("公司简介") is False


def test_normalize_image_caption_title_trims_prefix_and_long_tail() -> None:
    assert normalize_image_caption_title("图1: 劳动合同扫描件") == "劳动合同扫描件"
    assert normalize_image_caption_title("图1：合同扫描件，签约地郑州，有效期长期。") == "合同扫描件"
