"""交叉引用: 章节引用缺失 + 附件引用缺失(第二实体) + 页码引用越界 + 存在目标不误报"""

from app.core.document_qa.checks.reference_check import check_reference
from app.core.document_qa.contracts import QaHeading

from tests.document_qa.conftest import make_model


def test_chapter_reference_unresolved(qa_rules):
    model = make_model(
        body_texts={2: "具体施工工艺详见第六章相关内容。"},
        headings=[
            QaHeading(page=1, level=1, number="第一章", title="总体概述"),
            QaHeading(page=3, level=1, number="第二章", title="施工方案"),
        ],
    )
    outcome = check_reference(model, qa_rules)
    assert len(outcome.findings) == 1
    assert "第六章" in outcome.findings[0].message or "6" in outcome.findings[0].message


def test_attachment_reference_unresolved_second_entity(qa_rules):
    model = make_model(body_texts={
        1: "附件1：营业执照",
        4: "相关资质证明详见附件5。",
    })
    outcome = check_reference(model, qa_rules)
    unresolved = [f for f in outcome.findings if "附件" in f.message]
    assert len(unresolved) == 1


def test_dotted_section_reference_without_di_prefix(qa_rules):
    """"详见3.4节"(无"第"字)引用缺失目标必须命中; 第二实体"参见2.5节"同链路。"""
    model = make_model(
        body_texts={
            18: "具体阈值的来源与校核过程详见3.4节。",
            25: "评价口径参见2.5节相关说明。",
        },
        headings=[
            QaHeading(page=15, level=2, number="3.1", title="总体技术路线"),
            QaHeading(page=16, level=2, number="3.2", title="剩余油动态表征"),
            QaHeading(page=17, level=2, number="3.3", title="井组响应识别"),
            QaHeading(page=19, level=2, number="3.5", title="方案集成与迭代"),
            QaHeading(page=11, level=2, number="2.1", title="低渗油藏注采调控现状"),
        ],
    )
    outcome = check_reference(model, qa_rules)
    messages = " | ".join(f.message for f in outcome.findings)
    assert len(outcome.findings) == 2
    assert "3.4" in messages and "2.5" in messages


def test_dotted_section_reference_existing_not_reported(qa_rules):
    model = make_model(
        body_texts={5: "详见3.2节的表征方法。"},
        headings=[
            QaHeading(page=16, level=2, number="3.2", title="剩余油动态表征"),
        ],
    )
    outcome = check_reference(model, qa_rules)
    assert outcome.findings == []


def test_page_reference_beyond_page_count(qa_rules):
    """「见第N页」按物理页数判存在: 超出页数报缺失, 页内的不报。"""
    model = make_model(body_texts={
        1: "资质文件原件详见第12页。",
        2: "人员证书扫描件见第3页。",
        3: "设备清单。",
    })
    outcome = check_reference(model, qa_rules)
    assert [f.anchor for f in outcome.findings] == ["ref:page:12"]


def test_existing_targets_not_reported(qa_rules):
    model = make_model(
        body_texts={
            1: "表2：设备清单",
            3: "主要设备配置见表2。施工总体安排详见第2章。",
        },
        headings=[
            QaHeading(page=1, level=1, number="第一章", title="概述"),
            QaHeading(page=2, level=1, number="第二章", title="施工部署"),
        ],
    )
    outcome = check_reference(model, qa_rules)
    assert outcome.findings == []
