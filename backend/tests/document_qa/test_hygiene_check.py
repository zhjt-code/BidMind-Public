"""文档卫生: 占位符 + 乱码(第二形态实体) + 批注残留"""

from app.core.document_qa.checks.hygiene_check import check_hygiene
from app.core.document_qa.contracts import QaPage

from tests.document_qa.conftest import make_model


def test_placeholder_patterns(qa_rules):
    model = make_model(body_texts={
        2: "项目经理：XXXX，联系电话待补充。",
    })
    outcome = check_hygiene(model, qa_rules)
    placeholders = [f for f in outcome.findings if f.rule_code == "DOC-HYG-001"]
    assert len(placeholders) >= 2  # XXXX 与 待补充 各命中
    assert all(f.category.value == "hygiene" for f in placeholders)


def test_garbled_run_second_entity(qa_rules):
    model = make_model(body_texts={
        5: "本工程采用□□□工艺施工。",
    })
    outcome = check_hygiene(model, qa_rules)
    garbled = [f for f in outcome.findings if f.rule_code == "DOC-HYG-002"]
    assert len(garbled) == 1
    assert garbled[0].evidence.items[0].page == 5


def test_pdf_annotations_reported(qa_rules):
    model = make_model(body_texts={1: "正文内容。"})
    model.pages = [QaPage(
        page_num=1, width=595, height=842, rotation=0,
        char_count=5, image_count=0, table_count=0,
        annotation_texts=("这里需要老王确认",),
    )]
    outcome = check_hygiene(model, qa_rules)
    annotations = [f for f in outcome.findings if f.rule_code == "DOC-HYG-003"]
    assert len(annotations) == 1
    assert annotations[0].certainty.value == "certain"


def test_configured_typo_and_second_word_not_the_correct_form(qa_rules):
    """词表错词命中。正确用字不报。做为/作为是第二词条。"""
    model = make_model(body_texts={
        29: "模型在新增现场数据进入后进行既时更新，并记录样本范围。",
        30: "该附件做为补充材料提交。",
        31: "即时更新不应当被当成错词。作为正式表述也不应当被当成错词。",
    })
    outcome = check_hygiene(model, qa_rules)
    typos = [item for item in outcome.findings if item.rule_code == "DOC-HYG-004"]
    excerpts = [item.evidence.items[0].excerpt for item in typos]
    assert any("既时" in item for item in excerpts)
    assert any("做为" in item for item in excerpts)
    assert all("即时更新" not in item and "作为正式" not in item for item in excerpts)


def test_clean_document_no_findings(qa_rules):
    model = make_model(body_texts={
        1: "我公司承诺按期完成本项目全部施工内容。",
    })
    outcome = check_hygiene(model, qa_rules)
    assert outcome.findings == []
