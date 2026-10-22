"""目录页码坐标转换回归测试。"""

from app.core.parser.pdf_parser import ParsedPage
from app.core.parser.toc_parser import TocParser


def _page(page_num: int, text: str) -> ParsedPage:
    return ParsedPage(
        page_num=page_num,
        text=text,
        tables=[],
        images=[],
        width=595.0,
        height=842.0,
    )


def test_resolver_converts_printed_pages_to_physical_pages() -> None:
    """目录从正文重新计页时，应推断 printed_page -> physical_page 的偏移。"""
    from app.core.parser.page_coordinates import resolve_toc_page_coordinates

    pages = [
        _page(1, "投 标 文 件\n封面"),
        _page(2, "目 录\n一、公司简介........1\n二、项目业绩........2"),
        _page(3, "公司简介\n" + ("企业基本情况说明。" * 20)),
        _page(4, "项目业绩\n" + ("近三年类似项目经验。" * 20)),
    ]
    entries = TocParser().extract_toc_entries(pages)

    resolution = resolve_toc_page_coordinates(pages, entries)

    assert resolution.offset == 2
    assert resolution.is_reliable is True
    assert [(entry.printed_page, entry.physical_page) for entry in resolution.entries] == [
        (1, 3),
        (2, 4),
    ]


def test_resolver_marks_low_confidence_when_titles_do_not_match_body() -> None:
    """目录标题无法在正文页锚定时，不能启用目录导航。"""
    from app.core.parser.page_coordinates import resolve_toc_page_coordinates

    pages = [
        _page(1, "目 录\n一、公司简介........1\n二、项目业绩........2"),
        _page(2, "完全无关内容\n" + ("其它材料说明。" * 20)),
        _page(3, "仍然无关内容\n" + ("其它材料说明。" * 20)),
    ]
    entries = TocParser().extract_toc_entries(pages)

    resolution = resolve_toc_page_coordinates(pages, entries)

    assert resolution.is_reliable is False
    assert resolution.confidence == "low"


def test_resolver_does_not_trust_single_toc_anchor() -> None:
    """单个目录锚点不足以排伪，不能启用物理页导航。"""
    from app.core.parser.page_coordinates import resolve_toc_page_coordinates

    pages = [
        _page(1, "目 录\n一、公司简介........1"),
        _page(2, "公司简介\n" + ("企业基本情况说明。" * 20)),
    ]
    entries = TocParser().extract_toc_entries(pages)

    resolution = resolve_toc_page_coordinates(pages, entries)

    assert resolution.confidence == "low"
    assert resolution.is_reliable is False


def test_toc_parser_v2_lookup_returns_only_physical_pages() -> None:
    """v2 toc_map 返回物理页；legacy toc_map 不再作为物理页参与查询。"""
    parser = TocParser()
    pages = [
        _page(1, "投 标 文 件\n封面"),
        _page(2, "目 录\n一、公司简介........1\n二、项目业绩........2"),
        _page(3, "公司简介\n" + ("企业基本情况说明。" * 20)),
        _page(4, "项目业绩\n" + ("近三年类似项目经验。" * 20)),
    ]

    toc_map = parser.parse_resolved(pages)
    legacy_map = parser.parse(pages)

    assert parser.lookup_physical_pages("投标人项目业绩证明", toc_map) == [4]
    assert parser.lookup_physical_pages("投标人项目业绩证明", legacy_map) == []
