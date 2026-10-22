"""大纲作用域: 异族节点不参与空章节/跳跃/小数分组; 长小数标题与无空格图题可采集。"""

from app.core.document_qa.checks.language_check import check_language
from app.core.document_qa.checks.numbering_check import check_numbering
from app.core.document_qa.checks.reference_check import check_reference
from app.core.document_qa.checks.structure_check import check_structure
from app.core.document_qa.contracts import QaBlock, QaHeading, QaPage, QaSpan
from app.core.document_qa.heading_detector import detect_headings
from tests.document_qa.conftest import make_model

_BODY = "本节给出实施方案、人员分工与进度安排，确保按期完成各项研究任务。"
_BOLD = 1 << 4


def _span(page: int, text: str, *, size: float = 12.0, flags: int = 0) -> QaSpan:
    return QaSpan(
        page=page, text=text, bbox=(0.1, 0.2, 0.9, 0.24),
        font="SimSun", size=size, flags=flags, color=0,
    )


def _block(page: int, text: str, *, size: float = 12.0, flags: int = 0, y: float = 0.2) -> QaBlock:
    return QaBlock(
        page=page, text=text, bbox=(0.1, y, 0.9, y + 0.05),
        spans=(_span(page, text, size=size, flags=flags),),
    )


def _filled_model(blocks: list[QaBlock], pages: int = 3) -> object:
    fillers = [
        _block(1, f"这是用于建立正文字号基准的填充句子第{i}条，内容无实际意义。")
        for i in range(21)
    ]
    from app.core.document_qa.contracts import DocumentModel

    return DocumentModel(
        document_id=1, bid_part=None, filename="t.pdf", page_count=pages,
        blocks=fillers + blocks,
    )


def test_list_item_does_not_close_decimal_section(qa_rules):
    """小数节后的括号清单不是同级, 中间无正文不报空章节。"""
    model = make_model(
        body_texts={133: "8.5.2 相关设备证明资料\n（1）设备照片\n照片说明文字足够长。"},
        headings=[
            QaHeading(page=133, level=3, number="8.5.2", title="相关设备证明资料", kind="decimal"),
            QaHeading(page=133, level=2, number="1", title="设备照片", kind="paren"),
            QaHeading(page=140, level=3, number="8.6.1", title="进度计划", kind="decimal"),
        ],
    )
    model.blocks = [
        QaBlock(page=133, text="8.5.2 相关设备证明资料", bbox=(0.1, 0.80, 0.5, 0.84)),
        QaBlock(page=133, text="（1）设备照片", bbox=(0.1, 0.85, 0.4, 0.89)),
        QaBlock(page=134, text=_BODY, bbox=(0.1, 0.20, 0.9, 0.40)),
        QaBlock(page=140, text="8.6.1 进度计划", bbox=(0.1, 0.10, 0.5, 0.14)),
    ]
    model.pages = [
        QaPage(page_num=133, width=595, height=842, rotation=0, char_count=40, image_count=0, table_count=0),
        QaPage(page_num=134, width=595, height=842, rotation=0, char_count=40, image_count=2, table_count=0),
        QaPage(page_num=140, width=595, height=842, rotation=0, char_count=20, image_count=0, table_count=0),
    ]
    outcome = check_structure(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-STR-002"]


def test_same_family_empty_section_still_reported(qa_rules):
    """同形态第二实体: 同一小数家族同级之间无正文仍报空章节。"""
    model = make_model(
        body_texts={10: "3.1 施工方案\n3.2 进度计划"},
        headings=[
            QaHeading(page=10, level=2, number="3.1", title="施工方案", kind="decimal"),
            QaHeading(page=10, level=2, number="3.2", title="进度计划", kind="decimal"),
        ],
    )
    model.blocks = [
        QaBlock(page=10, text="3.1 施工方案", bbox=(0.1, 0.10, 0.5, 0.14)),
        QaBlock(page=10, text="3.2 进度计划", bbox=(0.1, 0.20, 0.5, 0.24)),
    ]
    outcome = check_structure(model, qa_rules)
    assert [f for f in outcome.findings if f.rule_code == "DOC-STR-002"]


def test_cross_family_level_not_jump(qa_rules):
    """括号模板小节与后续小数标题不是同一大纲, 不报层级跳跃。"""
    model = make_model(
        body_texts={127: "（4）预期输出成果\n8.4.4.2 站场油水分离工艺适应性分析"},
        headings=[
            QaHeading(page=127, level=2, number="4", title="预期输出成果", kind="paren"),
            QaHeading(page=128, level=4, number="8.4.4.2", title="站场油水分离工艺适应性分析", kind="decimal"),
        ],
    )
    outcome = check_structure(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-STR-001"]


def test_same_family_level_jump_still_reported(qa_rules):
    """同形态第二实体: 同一小数家族 2 级直接到 4 级仍报跳跃。"""
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=1, level=2, number="9.1", title="现场准备", kind="decimal"),
            QaHeading(page=2, level=4, number="9.1.1.1", title="机具配置", kind="decimal"),
        ],
    )
    outcome = check_structure(model, qa_rules)
    assert [f for f in outcome.findings if f.rule_code == "DOC-STR-001"]


def test_decimal_gap_not_split_by_list_ancestors(qa_rules):
    """证书清单祖先不同(劳动合同/劳务合同)不把 8.2.x 拆桶报缺号。"""
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=14, level=1, number="八", title="与评分有关资料", kind="cn_digit"),
            QaHeading(page=19, level=2, number="4", title="劳动合同", kind="paren"),
            QaHeading(page=22, level=3, number="8.2.2", title="技术首席-常泰", kind="decimal"),
            QaHeading(page=25, level=2, number="4", title="劳务合同", kind="paren"),
            QaHeading(page=28, level=3, number="8.2.3", title="技术专家-张少华", kind="decimal"),
            QaHeading(page=33, level=3, number="8.2.4", title="技术专家-武鹏鹏", kind="decimal"),
            QaHeading(page=39, level=3, number="8.2.5", title="主要研究人员-毛洪超", kind="decimal"),
            QaHeading(page=42, level=2, number="4", title="劳动合同", kind="paren"),
            QaHeading(page=45, level=3, number="8.2.6", title="主要研究人员-张津滔", kind="decimal"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    missing = [f for f in outcome.findings if f.rule_code == "DOC-NUM-001" and "章节" in f.title]
    assert missing == [], [f.message for f in missing]


def test_decimal_gap_same_family_still_reported(qa_rules):
    """同形态第二实体: 同一父号下真缺 9.3.3 仍报, 文案是可读编号。"""
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=1, level=3, number="9.3.2", title="质量保证", kind="decimal"),
            QaHeading(page=2, level=3, number="9.3.4", title="安全措施", kind="decimal"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    missing = [f for f in outcome.findings if f.rule_code == "DOC-NUM-001"]
    assert len(missing) == 1
    assert "9.3.3" in missing[0].message
    assert "decimal|" not in missing[0].message


def test_long_decimal_heading_without_style_collected():
    """无粗体长小数标题仍入树, 不被 30 字门槛丢掉。"""
    title = "8.6.2.3 沉降罐油-水分离效率敏感性因素分析及工况模拟阶段"
    assert len(title) > 30
    model = _filled_model([_block(2, title, size=12.0, flags=4)])
    headings = detect_headings(model)
    assert any(h.number == "8.6.2.3" for h in headings)


def test_long_decimal_second_entity_collected():
    """同形态第二实体: 另一条无粗体长小数标题同样入树。"""
    title = "9.4.1.2 高效低成本常压油水分离技术研究优化方案与设计手册编制阶段"
    assert len(title) > 30
    model = _filled_model([_block(2, title, size=12.0, flags=4)])
    headings = detect_headings(model)
    assert any(h.number == "9.4.1.2" for h in headings)


def test_figure_caption_without_space_resolves_reference(qa_rules):
    """图题编号后直接接汉字仍能入索引, 「见图8-2」不报缺失。"""
    model = make_model(body_texts={
        126: "流程示意图见图8-2。\n图8-2方案三流程示意图",
    })
    outcome = check_reference(model, qa_rules)
    assert not [f for f in outcome.findings if "8-2" in f.message or "8.2" in f.message]
    numbered = check_numbering(model, qa_rules)
    assert not [f for f in numbered.findings if f.rule_code == "DOC-NUM-001" and "图" in f.title]


def test_same_page_caption_two_channels_not_duplicate(qa_rules):
    """同页图题: 页文本无空格与块首行有空格是同一 caption, 不报重号。"""
    model = make_model(body_texts={126: "图8-2方案三流程示意图"})
    model.blocks = [
        QaBlock(page=126, text="图8-2 方案三流程示意图", bbox=(0.3, 0.7, 0.7, 0.74)),
    ]
    outcome = check_numbering(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]


def test_table_caption_without_space_second_entity(qa_rules):
    """同形态第二实体: 表题无空格仍能解析引用。"""
    model = make_model(body_texts={
        40: "主要参数见表3-1。\n表3-1设备清单",
    })
    outcome = check_reference(model, qa_rules)
    assert not [f for f in outcome.findings if "3-1" in f.message or "3.1" in f.message]


def test_parenthetical_number_not_unbalanced(qa_rules):
    """括注内「并联1）」是闭合括号, 不是清单序号。"""
    model = make_model(body_texts={
        125: "①方案一（二级沉降分离——多沉降罐并联1）：工艺流程如下所述。",
    })
    outcome = check_language(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-LANG-001"]


def test_parenthetical_number_second_entity(qa_rules):
    """同形态第二实体: 「串联2）」同样保持成对。"""
    model = make_model(body_texts={
        126: "③方案三（二级沉降分离——多沉降罐串联2）：后续说明。",
    })
    outcome = check_language(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-LANG-001"]


def test_glued_digit_unit_title_not_heading(qa_rules):
    """数字与单位粘连的标题仍按计量否决, 不入小数大纲。"""
    model = _filled_model([_block(2, "26.46 82万元", size=12.0, flags=4)])
    headings = detect_headings(model, qa_rules)
    assert not any(h.number == "26.46" for h in headings)


def test_glued_digit_unit_second_entity(qa_rules):
    """同形态第二实体: 数字+台 同样否决。"""
    model = _filled_model([_block(2, "12.8 6台", size=12.0, flags=4)])
    headings = detect_headings(model, qa_rules)
    assert not any(h.number == "12.8" for h in headings)
