"""语言检查: 括号未闭合 + 重复标点(第二形态) + 干净文本零误报"""

from app.core.document_qa.checks.language_check import check_language

from tests.document_qa.conftest import make_model


def test_unbalanced_bracket(qa_rules):
    model = make_model(body_texts={4: "（详见附件资质证书复印件。"})
    outcome = check_language(model, qa_rules)
    unbalanced = [f for f in outcome.findings if f.rule_code == "DOC-LANG-001"]
    assert len(unbalanced) == 1


def test_repeated_punctuation_second_entity(qa_rules):
    model = make_model(body_texts={6: "我方承诺按期完工。。"})
    outcome = check_language(model, qa_rules)
    repeats = [f for f in outcome.findings if f.rule_code == "DOC-LANG-002"]
    assert len(repeats) == 1
    assert repeats[0].certainty.value == "certain"


def test_halfwidth_comma_between_cjk(qa_rules):
    model = make_model(body_texts={2: "我方承诺,保证工期。"})
    outcome = check_language(model, qa_rules)
    mixed = [f for f in outcome.findings if f.rule_code == "DOC-LANG-003"]
    assert len(mixed) == 1
    assert "承诺,保证" in mixed[0].message
    assert mixed[0].closed_measurement is False


def test_three_halfwidth_commas_stay_closed(qa_rules):
    """同一段里三处半角逗号按闭合度量。另一页单处分号不闭合。"""
    model = make_model(body_texts={
        4: "甲,乙丙,丁戊,己。",
        5: "另有一句;结束。",
    })
    outcome = check_language(model, qa_rules)
    mixed = [f for f in outcome.findings if f.rule_code == "DOC-LANG-003"]
    assert {item.closed_measurement for item in mixed} == {True, False}
    assert any("甲,乙" in item.message for item in mixed)
    assert any("一句;结束" in item.message for item in mixed)


def test_list_marker_close_paren_not_unbalanced(qa_rules):
    model = make_model(body_texts={3: "本人承诺：1）按招标文件履行义务。"})
    outcome = check_language(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-LANG-001"]


def test_list_marker_after_cjk_not_unbalanced(qa_rules):
    """同形态第二实体: 清单序号紧贴汉字时不把闭括号算进配对。"""
    model = make_model(body_texts={95: "性能测试3）数据分析并出具实验报告。"})
    outcome = check_language(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-LANG-001"]


def test_language_skipped_without_visual(qa_rules):
    from app.core.document_qa.contracts import DocumentModel

    model = DocumentModel(
        document_id=1, bid_part=None, filename="t.pdf", page_count=1,
        paragraphs={1: ["我方承诺,保证工期。"]},
    )
    outcome = check_language(model, qa_rules)
    assert outcome.status.value == "skipped"
    assert outcome.findings == []


def test_clean_text_no_findings(qa_rules):
    model = make_model(body_texts={
        1: "我方承诺（含全部子项）按期完成，保证质量。",
    })
    outcome = check_language(model, qa_rules)
    assert outcome.findings == []
