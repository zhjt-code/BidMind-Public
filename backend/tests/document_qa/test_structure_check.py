"""结构检查: 层级跳跃(两组实体) + 正常层级不误报"""

from app.core.document_qa.checks.structure_check import check_structure
from app.core.document_qa.contracts import QaHeading
from tests.document_qa.conftest import make_model


def test_level_jump_reported(qa_rules):
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=1, level=1, number="第一章", title="总述"),
            QaHeading(page=2, level=3, number="1.1.1", title="细项"),
        ],
    )
    outcome = check_structure(model, qa_rules)
    jumps = [f for f in outcome.findings if f.rule_code == "DOC-STR-001"]
    assert len(jumps) == 1
    assert jumps[0].evidence.kind == "pair"


def test_second_entity_jump_from_level_two(qa_rules):
    """同形态第二实体: 2 级直接跳 4 级同样命中同一规则。"""
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=1, level=2, number="3.1", title="方案"),
            QaHeading(page=2, level=4, number="3.1.1.1", title="细则"),
        ],
    )
    outcome = check_structure(model, qa_rules)
    assert [f for f in outcome.findings if f.rule_code == "DOC-STR-001"]


def test_scan_section_not_empty(qa_rules):
    """扫描件章节(区间内有图)不报空章节。"""
    from app.core.document_qa.contracts import QaPage

    model = make_model(
        body_texts={8: "六、营业执照", 9: "七、人员"},
        headings=[
            QaHeading(page=8, level=1, number="六", title="营业执照"),
            QaHeading(page=9, level=1, number="七", title="人员"),
        ],
    )
    model.pages = [
        QaPage(page_num=8, width=595, height=842, rotation=0, char_count=6, image_count=2, table_count=0),
        QaPage(page_num=9, width=595, height=842, rotation=0, char_count=4, image_count=0, table_count=0),
    ]
    model.blocks = []
    # 无 blocks 时空章节直接跳过; 补两个标题块且中间几乎无正文
    from app.core.document_qa.contracts import QaBlock

    model.blocks = [
        QaBlock(page=8, text="六 营业执照", bbox=(0.1, 0.1, 0.9, 0.15)),
        QaBlock(page=9, text="七 人员", bbox=(0.1, 0.1, 0.9, 0.15)),
    ]
    outcome = check_structure(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-STR-002"]


def test_normal_hierarchy_no_findings(qa_rules):
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=1, level=1, number="第一章", title="总述"),
            QaHeading(page=2, level=2, number="1.1", title="概况"),
            QaHeading(page=3, level=3, number="1.1.1", title="细项"),
        ],
    )
    outcome = check_structure(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-STR-001"]


def test_shallow_bookmark_and_second_heading(qa_rules):
    """书签浅于正文才报。同级不报; 另一小节被抬成一级是第二实体。"""
    from app.core.document_qa.contracts import QaBookmark

    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=1, level=2, number="3.1", title="总体技术路线", kind="decimal"),
            QaHeading(page=2, level=2, number="4.2", title="设备校核", kind="decimal"),
            QaHeading(page=3, level=1, number="5", title="风险识别", kind="top_digit"),
        ],
    )
    model.bookmarks = [
        QaBookmark(level=1, title="3.1 总体技术路线", page=1),
        QaBookmark(level=1, title="4.2 设备校核", page=2),
        QaBookmark(level=1, title="5 风险识别", page=3),
    ]
    outcome = check_structure(model, qa_rules)
    hits = [item for item in outcome.findings if item.rule_code == "DOC-STR-003"]
    titles = {item.evidence.items[0].excerpt for item in hits}
    assert "3.1 总体技术路线" in titles
    assert "4.2 设备校核" in titles
    assert "5 风险识别" not in titles


def test_no_bookmarks_no_level_finding(qa_rules):
    model = make_model(
        body_texts={1: "正文"},
        headings=[QaHeading(page=1, level=2, number="3.1", title="总体技术路线", kind="decimal")],
    )
    outcome = check_structure(model, qa_rules)
    assert not [item for item in outcome.findings if item.rule_code == "DOC-STR-003"]
