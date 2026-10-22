"""DocumentModel 构建: span 样式/归一化 bbox/旋转/标题识别/段落重建"""

from types import SimpleNamespace

import pytest

from app.core.document_qa.model_builder import build_document_model, build_paragraphs


@pytest.fixture()
def synthetic_pdf(tmp_path):
    """fitz 合成两页 PDF: 第1页标题+正文, 第2页旋转90度。"""
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text((72, 80), "3.1 施工方案", fontsize=16, fontname="china-s")
    page1.insert_text(
        (72, 120), "我方将按照总体部署组织施工。", fontsize=10.5, fontname="china-s",
    )
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((72, 80), "第2页内容", fontsize=10.5, fontname="china-s")
    page2.set_rotation(90)
    doc.save(str(path))
    doc.close()
    return path


def _parsed_stub(texts: dict[int, str]):
    pages = [
        SimpleNamespace(
            page_num=num, text=text, images=[], tables=[], width=595.0, height=842.0,
        )
        for num, text in sorted(texts.items())
    ]
    return SimpleNamespace(pages=pages, page_count=len(pages), filename="sample.pdf")


def test_build_model_spans_and_bbox(synthetic_pdf):
    parsed = _parsed_stub({
        1: "3.1 施工方案\n我方将按照总体部署组织施工。",
        2: "第2页内容",
    })
    model = build_document_model(
        parsed, str(synthetic_pdf), document_id=1, bid_part="commercial",
    )
    assert model.degraded_reason is None
    assert model.blocks, "fitz 层应产出文本块"
    span = model.blocks[0].spans[0]
    assert span.size > 0 and span.font
    for block in model.blocks:
        for value in block.bbox:
            assert 0.0 <= value <= 1.0, "bbox 必须归一化到 0..1"
    # 旋转页捕获
    rotation_by_page = {p.page_num: p.rotation for p in model.pages}
    assert rotation_by_page[2] == 90
    # 标题识别(编号句式 3.1)
    assert any(h.number == "3.1" for h in model.headings)


def test_oversize_document_degrades(synthetic_pdf):
    parsed = _parsed_stub({1: "内容"})
    model = build_document_model(
        parsed, str(synthetic_pdf), document_id=1, bid_part=None, max_span_pages=0,
    )
    assert model.degraded_reason is not None
    assert model.blocks == []
    assert model.body_texts  # 文本级检查仍可用


def test_fold_raised_digits_keeps_same_size_digit():
    """上移的小数字并进前一字; 同行同号数字保持原样。第二实体是另一位数字。"""
    from app.core.document_qa.contracts import QaSpan
    from app.core.document_qa.model_builder import _fold_raised_digits

    def span(text: str, x: float, y: float, size: float) -> QaSpan:
        return QaSpan(
            page=1, text=text, bbox=(x, y, x + 0.02, y + 0.03),
            font="STSong", size=size, flags=0, color=0,
        )

    cubic = _fold_raised_digits([
        span("m", 0.10, 0.40, 10.5),
        span("3", 0.12, 0.36, 6.0),
        span("/d", 0.14, 0.40, 10.5),
    ])
    squared = _fold_raised_digits([
        span("H", 0.10, 0.40, 10.5),
        span("2", 0.12, 0.36, 6.0),
    ])
    inline = _fold_raised_digits([
        span("m", 0.10, 0.40, 10.5),
        span("3", 0.12, 0.40, 10.5),
    ])
    assert [item.text for item in cubic] == ["m³", "/d"]
    assert [item.text for item in squared] == ["H²"]
    assert [item.text for item in inline] == ["m", "3"]


def test_build_paragraphs_merges_soft_lines():
    text = "我方承诺严格按照\n招标文件要求执行。\n下一段内容开始。"
    paragraphs = build_paragraphs(text)
    assert paragraphs[0] == "我方承诺严格按照招标文件要求执行。"
    assert paragraphs[1] == "下一段内容开始。"
