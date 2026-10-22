"""目录检查: 目录有正文无 / 正文有目录无 / 页码不符 / 模糊匹配不误报"""

from app.core.document_qa.checks.toc_check import check_toc
from app.core.document_qa.contracts import QaHeading, QaTocEntry
from tests.document_qa.conftest import make_model


def _model_with_toc(entries, headings, reliable=False):
    model = make_model(body_texts={1: "目录页"}, headings=headings)
    model.toc_entries = entries
    model.toc_resolution_reliable = reliable
    return model


_HEADINGS = [
    QaHeading(page=3, level=1, number="一", title="投标函"),
    QaHeading(page=5, level=1, number="二", title="施工组织方案"),
    QaHeading(page=9, level=1, number="三", title="资质证书"),
]


def test_toc_entry_missing_in_body(qa_rules):
    entries = [
        QaTocEntry(title="投标函", printed_page=1, physical_page=3, order_index=0),
        QaTocEntry(title="施工组织方案", printed_page=3, physical_page=5, order_index=1),
        QaTocEntry(title="商务报价表", printed_page=7, physical_page=7, order_index=2),
    ]
    outcome = check_toc(_model_with_toc(entries, _HEADINGS), qa_rules)
    missing = [f for f in outcome.findings if f.rule_code == "DOC-TOC-001"]
    assert len(missing) == 1
    assert "商务报价表" in missing[0].message


def test_heading_not_listed_in_toc(qa_rules):
    entries = [
        QaTocEntry(title="投标函", printed_page=1, physical_page=3, order_index=0),
        QaTocEntry(title="施工组织方案", printed_page=3, physical_page=5, order_index=1),
        QaTocEntry(title="资质证书", printed_page=7, physical_page=9, order_index=2),
    ]
    headings = _HEADINGS + [
        QaHeading(page=12, level=1, number="四", title="售后服务承诺"),
    ]
    outcome = check_toc(_model_with_toc(entries, headings), qa_rules)
    unlisted = [f for f in outcome.findings if f.rule_code == "DOC-TOC-002"]
    assert len(unlisted) == 1
    assert "售后服务承诺" in unlisted[0].message
    assert unlisted[0].certainty.value == "possible"


def test_toc_page_mismatch_only_when_reliable(qa_rules):
    entries = [
        QaTocEntry(title="投标函", printed_page=1, physical_page=3, order_index=0),
        QaTocEntry(title="施工组织方案", printed_page=3, physical_page=8, order_index=1),
        QaTocEntry(title="资质证书", printed_page=7, physical_page=9, order_index=2),
    ]
    # 坐标解析不可靠: 不产出页码不符
    outcome = check_toc(_model_with_toc(entries, _HEADINGS, reliable=False), qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-TOC-004"]
    # 可靠时: 施工组织方案 标注物理页8 vs 实际5(差3页), probable
    outcome = check_toc(_model_with_toc(entries, _HEADINGS, reliable=True), qa_rules)
    mismatch = [f for f in outcome.findings if f.rule_code == "DOC-TOC-004"]
    assert len(mismatch) == 1
    assert mismatch[0].evidence.kind == "pair"
    assert mismatch[0].certainty.value == "probable"


def test_toc_page_off_by_one_reported_as_possible(qa_rules):
    """页码差1页(金标3638 埋点形态)报 possible, 不再被容差放行。"""
    entries = [
        QaTocEntry(title="投标函", printed_page=1, physical_page=3, order_index=0),
        QaTocEntry(title="施工组织方案", printed_page=3, physical_page=5, order_index=1),
        QaTocEntry(title="资质证书", printed_page=7, physical_page=8, order_index=2),
    ]
    outcome = check_toc(_model_with_toc(entries, _HEADINGS, reliable=True), qa_rules)
    mismatch = [f for f in outcome.findings if f.rule_code == "DOC-TOC-004"]
    assert len(mismatch) == 1
    assert "资质证书" in mismatch[0].message
    assert mismatch[0].certainty.value == "possible"


def test_fuzzy_title_match_not_reported(qa_rules):
    """目录「施工组织方案」与正文「施工组织设计方案」应模糊匹配, 不报缺失。"""
    entries = [
        QaTocEntry(title="投标函", printed_page=1, physical_page=3, order_index=0),
        QaTocEntry(title="施工组织设计方案", printed_page=3, physical_page=5, order_index=1),
        QaTocEntry(title="资质证书", printed_page=7, physical_page=9, order_index=2),
    ]
    outcome = check_toc(_model_with_toc(entries, _HEADINGS), qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-TOC-001"]
