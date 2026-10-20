from app.core.parser.pdf_parser import ParsedDocument, ParsedPage
from app.core.parser.pdf_structure_analyzer import PdfStructureAnalyzer


def _page(page_num: int, text: str) -> ParsedPage:
    return ParsedPage(
        page_num=page_num,
        text=text,
        tables=[],
        images=[],
        width=595.0,
        height=842.0,
    )


def _doc(*texts: str) -> ParsedDocument:
    return ParsedDocument(
        file_path="synthetic.pdf",
        filename="synthetic.pdf",
        page_count=len(texts),
        pages=[_page(idx + 1, text) for idx, text in enumerate(texts)],
        metadata={},
    )


def test_analyzer_prefers_toc_entries_for_composite_pdf() -> None:
    company = "公司简介\n" + ("本公司成立于2010年，主营业务覆盖系统集成与运维服务。" * 8)
    performance = "项目业绩\n" + ("近三年完成多个大型系统集成项目，履约评价良好。" * 8)
    doc = _doc(
        "目录\n一、公司简介........2\n二、项目业绩........3",
        company,
        performance,
    )

    analysis = PdfStructureAnalyzer().analyze(doc)

    assert analysis.is_composite is True
    assert [section.title for section in analysis.sections] == ["一、公司简介", "二、项目业绩"]
    assert analysis.sections[0].page_start == 2
    assert analysis.sections[0].page_end == 2
    assert analysis.sections[1].page_start == 3
    assert analysis.sections[1].suggested_label == "项目业绩综述"


def test_analyzer_converts_printed_toc_pages_to_physical_pages() -> None:
    """封面/目录不计页时，目录切分必须落到 PDF 物理页。"""
    company = "公司简介\n" + ("本公司成立于2010年，主营业务覆盖系统集成与运维服务。" * 8)
    performance = "项目业绩\n" + ("近三年完成多个大型系统集成项目，履约评价良好。" * 8)
    doc = _doc(
        "投 标 文 件\n封面",
        "目录\n一、公司简介........1\n二、项目业绩........2",
        company,
        performance,
    )

    analysis = PdfStructureAnalyzer().analyze(doc)

    assert analysis.is_composite is True
    assert [section.title for section in analysis.sections] == ["一、公司简介", "二、项目业绩"]
    assert analysis.sections[0].page_start == 3
    assert analysis.sections[0].page_end == 3
    assert analysis.sections[1].page_start == 4


def test_analyzer_falls_back_to_headings_when_toc_missing() -> None:
    doc = _doc(
        "公司简介\n" + ("企业基本情况、主营业务与发展历程说明。" * 12),
        "服务承诺\n" + ("提供7x24响应机制、售后保障与问题闭环服务。" * 12),
    )

    analysis = PdfStructureAnalyzer().analyze(doc)

    assert analysis.is_composite is True
    assert [section.detection_method for section in analysis.sections] == ["heading", "heading"]
    assert analysis.sections[0].title == "公司简介"
    assert analysis.sections[1].title == "服务承诺"
    assert 0.56 < analysis.sections[0].confidence < 0.9
    assert analysis.sections[0].confidence != 0.72


def test_analyzer_keeps_single_theme_pdf_unsplit() -> None:
    doc = _doc(
        "公司简介\n" + ("企业基本情况与主营业务说明。" * 20),
        "企业简介续篇\n" + ("组织架构、履约能力与服务体系说明。" * 20),
    )

    analysis = PdfStructureAnalyzer().analyze(doc)

    assert analysis.is_composite is False
    assert analysis.sections == []
