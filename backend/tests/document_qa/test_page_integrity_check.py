"""页面完整性: 空白页 + 方向离群(第二形态) + 单页不误报"""

from app.core.document_qa.checks.page_integrity_check import check_page_integrity
from app.core.document_qa.contracts import QaPage
from tests.document_qa.conftest import make_model


def _page(num, *, w=595.0, h=842.0, chars=100, images=0, tables=0):
    return QaPage(
        page_num=num, width=w, height=h, rotation=0,
        char_count=chars, image_count=images, table_count=tables,
    )


def test_blank_page_reported(qa_rules):
    model = make_model(body_texts={i: "内容" for i in range(1, 7)})
    model.pages = [_page(i) for i in (1, 2, 3, 5, 6)] + [
        _page(4, chars=0, images=0, tables=0)
    ]
    outcome = check_page_integrity(model, qa_rules)
    blanks = [f for f in outcome.findings if f.rule_code == "DOC-PAGE-001"]
    assert len(blanks) == 1
    assert "第4页" in blanks[0].message


def test_scan_page_with_images_not_blank(qa_rules):
    model = make_model(body_texts={1: "x", 2: ""})
    model.pages = [_page(1), _page(2, chars=0, images=1)]
    outcome = check_page_integrity(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-PAGE-001"]


def test_orientation_minority_reported(qa_rules):
    model = make_model(body_texts={i: "内容" for i in range(1, 9)})
    model.pages = [_page(i) for i in range(1, 8)] + [_page(8, w=842.0, h=595.0)]
    outcome = check_page_integrity(model, qa_rules)
    orient = [f for f in outcome.findings if f.rule_code == "DOC-PAGE-002"]
    assert len(orient) == 1
    assert orient[0].certainty.value == "possible"


def test_low_resolution_enlarged_image_not_icon(qa_rules):
    """放大的低像素图要报。小图标和像素足够的大图不报。第二张低清图是另一实体。"""
    from app.core.document_qa.contracts import QaImage

    model = make_model(body_texts={1: "插图页", 2: "另一页"})
    model.images = [
        QaImage(page=1, pixel_width=90, pixel_height=35, bbox=(0.15, 0.3, 0.85, 0.7)),
        QaImage(page=2, pixel_width=80, pixel_height=40, bbox=(0.2, 0.2, 0.7, 0.55)),
        QaImage(page=1, pixel_width=90, pixel_height=35, bbox=(0.01, 0.01, 0.06, 0.04)),
        QaImage(page=2, pixel_width=1200, pixel_height=800, bbox=(0.1, 0.1, 0.9, 0.8)),
    ]
    outcome = check_page_integrity(model, qa_rules)
    hits = [item for item in outcome.findings if item.rule_code == "DOC-PAGE-004"]
    assert {item.evidence.items[0].excerpt for item in hits} == {"90x35", "80x40"}


def test_single_page_no_outlier_noise(qa_rules):
    model = make_model(body_texts={1: "内容"})
    model.pages = [_page(1)]
    outcome = check_page_integrity(model, qa_rules)
    assert outcome.findings == []
