"""无文字层页不在质检范围。

同形态两例: 扫描页(页上有图片) 与 文字转曲页(无图片, 版面框出正文区)。
只剩印刷页码的页不算有内容, 仍按空白页报出。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.document_qa.analyzer import analyze_document
from app.core.document_qa.checks.page_integrity_check import check_page_integrity
from app.core.document_qa.checks.toc_check import check_toc
from app.core.document_qa.contracts import QaHeading, QaPage, QaTocEntry
from app.core.document_qa.model_builder import (
    build_document_model,
    image_page_max_chars,
    is_image_only_page,
)
from app.core.document_qa.region_text import build_page_regions, layout_content_pages
from tests.document_qa.conftest import make_model


def _page(num: int, *, chars: int = 100, images: int = 0, image_only: bool = False) -> QaPage:
    return QaPage(
        page_num=num, width=595.0, height=842.0, rotation=0,
        char_count=chars, image_count=images, table_count=0, image_only=image_only,
    )


def test_scan_and_outlined_pages_are_image_only(qa_rules) -> None:
    limit = image_page_max_chars(qa_rules)
    assert limit == 5
    # 扫描页: 无文字, 有图片
    assert is_image_only_page("", 1, False, limit)
    # 文字转曲页: 无文字, 无图片, 版面在页上看到正文区
    assert is_image_only_page(" \n ", 0, True, limit)
    # 只剩印刷页码的空白页: 版面没有页眉页脚页码以外的内容
    assert not is_image_only_page("", 0, False, limit)
    # 有文件自带文字的页不论有无图片都要查
    assert not is_image_only_page("营业执照副本复印件", 1, True, limit)


def test_layout_content_ignores_header_footer_and_page_number(qa_rules) -> None:
    top, bottom, body = [0.1, 0.02, 0.9, 0.05], [0.45, 0.93, 0.55, 0.96], [0.1, 0.2, 0.9, 0.6]
    pages = {
        1: [{"label": "header", "bbox": top}, {"label": "number", "bbox": bottom}],
        2: [{"label": "text", "bbox": body}],
        3: [{"label": "footer", "bbox": bottom}, {"label": "image", "bbox": body}],
        4: [],
    }
    regions, _ = build_page_regions([], pages, qa_rules)
    assert layout_content_pages(regions) == {2, 3}


@pytest.fixture()
def four_page_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "scope.pdf"
    doc = fitz.open()
    first = doc.new_page(width=595, height=842)
    first.insert_text((72, 100), "第一章 总则", fontsize=20, fontname="china-s")
    for _ in range(3):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


def _src(num: int, text: str, images: int) -> SimpleNamespace:
    return SimpleNamespace(
        page_num=num, text=text, images=[object()] * images, tables=[],
        width=595.0, height=842.0,
    )


def test_builder_marks_scan_and_outlined_pages_and_keeps_blank(four_page_pdf, qa_rules) -> None:
    parsed = SimpleNamespace(
        pages=[
            _src(1, "第一章 总则\n本章说明投标文件的组成与编制要求。", 0),
            _src(2, "", 1),
            _src(3, "", 0),
            _src(4, "", 0),
        ],
        page_count=4,
        filename="scope.pdf",
    )
    structure_pages = {
        1: [{"label": "doc_title", "text": "第一章 总则", "bbox": [0.1, 0.08, 0.6, 0.14], "level": 1}],
        2: [{"label": "image", "text": "", "bbox": [0.05, 0.05, 0.95, 0.95], "level": None}],
        3: [{"label": "text", "text": "", "bbox": [0.1, 0.2, 0.9, 0.6], "level": None}],
        4: [{"label": "number", "text": "", "bbox": [0.45, 0.93, 0.55, 0.96], "level": None}],
    }

    model = build_document_model(
        parsed, str(four_page_pdf), document_id=7, bid_part=None,
        structure_pages=structure_pages,
    )

    assert [page.page_num for page in model.pages if page.image_only] == [2, 3]
    analysis = analyze_document(model, qa_rules)
    assert analysis.features["image_pages"] == [2, 3]
    blanks = [f for f in analysis.findings if f.rule_code == "DOC-PAGE-001"]
    assert len(blanks) == 1
    assert "第4页" in blanks[0].message
    assert "第2页" not in blanks[0].message and "第3页" not in blanks[0].message


def test_outlined_page_is_not_blank_but_real_blank_is(qa_rules) -> None:
    model = make_model(body_texts={i: "内容" for i in range(1, 7)})
    model.pages = [_page(i) for i in (1, 2, 5, 6)] + [
        _page(3, chars=0, image_only=True),
        _page(4, chars=0),
    ]
    blanks = [
        f for f in check_page_integrity(model, qa_rules).findings
        if f.rule_code == "DOC-PAGE-001"
    ]
    assert len(blanks) == 1
    assert "第4页" in blanks[0].message
    assert "第3页" not in blanks[0].message


_HEADINGS = [
    QaHeading(page=3, level=1, number="一", title="投标函"),
    QaHeading(page=5, level=1, number="二", title="施工组织方案"),
    QaHeading(page=9, level=1, number="三", title="资质证书"),
]


def test_toc_entries_pointing_to_scan_or_outlined_pages_are_not_missing(qa_rules) -> None:
    entries = [
        QaTocEntry(title="投标函", printed_page=1, physical_page=3, order_index=0),
        QaTocEntry(title="施工组织方案", printed_page=3, physical_page=5, order_index=1),
        QaTocEntry(title="营业执照", printed_page=5, physical_page=7, order_index=2),
        QaTocEntry(title="业绩合同", printed_page=6, physical_page=8, order_index=3),
        QaTocEntry(title="商务报价表", printed_page=10, physical_page=12, order_index=4),
    ]
    model = make_model(body_texts={1: "目录页"}, headings=_HEADINGS)
    model.pages = [_page(i) for i in range(1, 13) if i not in (7, 8)] + [
        _page(7, chars=0, images=1, image_only=True),
        _page(8, chars=0, image_only=True),
    ]
    model.toc_entries = entries

    missing = [f for f in check_toc(model, qa_rules).findings if f.rule_code == "DOC-TOC-001"]

    assert len(missing) == 1
    assert "商务报价表" in missing[0].message
