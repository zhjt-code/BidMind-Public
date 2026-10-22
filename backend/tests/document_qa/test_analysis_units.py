"""分析单元与作者单元对齐: 标题身份定位、跨页段落缝合、清单挂脊、长编号句式入树。"""

from app.core.document_qa.checks.language_check import check_language
from app.core.document_qa.checks.numbering_check import check_numbering
from app.core.document_qa.checks.structure_check import check_structure
from app.core.document_qa.contracts import QaBlock, QaHeading, QaPage
from app.core.document_qa.heading_detector import detect_headings
from tests.document_qa.conftest import make_model
from tests.document_qa.test_outline_scope import _block, _filled_model


def test_empty_section_not_closed_by_title_words_in_body(qa_rules):
    """下一标题用词出现在正文中, 不得把空章节区间塌到正文块。"""
    body = "我公司制定了完善的培训实施方案，系统构建培训实施体系并明确总体部署。"
    model = make_model(
        body_texts={295: f"第二节 实施方案\n{body}\n第二节 培训实施方案"},
        headings=[
            QaHeading(
                page=295, level=2, number="第二节", title="实施方案",
                kind="unit", bbox=(0.14, 0.59, 0.31, 0.61),
            ),
            QaHeading(
                page=295, level=2, number="第二节", title="培训实施方案",
                kind="unit", bbox=(0.14, 0.81, 0.35, 0.82),
            ),
        ],
    )
    model.pages = [
        QaPage(
            page_num=295, width=595, height=842, rotation=0,
            char_count=80, image_count=0, table_count=0,
        ),
    ]
    model.blocks = [
        QaBlock(page=295, text="第二节实施方案", bbox=(0.14, 0.59, 0.31, 0.61)),
        QaBlock(page=295, text=body, bbox=(0.14, 0.63, 0.90, 0.78)),
        QaBlock(page=295, text="第二节培训实施方案", bbox=(0.14, 0.81, 0.35, 0.82)),
    ]
    outcome = check_structure(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-STR-002"]


def test_empty_section_title_mention_second_entity(qa_rules):
    """同形态第二实体: 正文转述下一节标题词同样不报空章节。"""
    body = "详细进度计划见专章，本节只列施工方案要点与人员分工安排。"
    model = make_model(
        body_texts={10: f"3.1 施工方案\n{body}\n3.2 进度计划"},
        headings=[
            QaHeading(
                page=10, level=2, number="3.1", title="施工方案",
                kind="decimal", bbox=(0.1, 0.10, 0.4, 0.14),
            ),
            QaHeading(
                page=10, level=2, number="3.2", title="进度计划",
                kind="decimal", bbox=(0.1, 0.50, 0.4, 0.54),
            ),
        ],
    )
    model.blocks = [
        QaBlock(page=10, text="3.1 施工方案", bbox=(0.1, 0.10, 0.4, 0.14)),
        QaBlock(page=10, text=body, bbox=(0.1, 0.20, 0.9, 0.40)),
        QaBlock(page=10, text="3.2 进度计划", bbox=(0.1, 0.50, 0.4, 0.54)),
    ]
    outcome = check_structure(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-STR-002"]


def test_long_paren_heading_without_style_collected():
    """无粗体的长括号标题仍入树, 不被 30 字门槛丢掉。"""
    title = "（3）其他食品类（奶酪棒、鸡蛋馍丁、什锦坚果、黑巧曲奇）供应商"
    assert len(title) > 30
    model = _filled_model([_block(2, title, size=12.0, flags=0)])
    headings = detect_headings(model)
    assert any(h.kind == "paren" and h.number == "3" for h in headings)


def test_long_paren_heading_second_entity_collected():
    """同形态第二实体: 另一条无粗体长括号标题同样入树。"""
    title = "（5）冷链仓储设施（低温库、缓冲间、备用发电机组及监控）配置标准"
    assert len(title) > 30
    model = _filled_model([_block(2, title, size=12.0, flags=0)])
    headings = detect_headings(model)
    assert any(h.kind == "paren" and h.number == "5" for h in headings)


def test_cross_page_bracket_not_unbalanced(qa_rules):
    """开括号在页末、闭括号在次页首, 是同一段落, 不报未闭合。"""
    model = make_model(body_texts={
        4: "我公司严格按投标产品（含品种、名称、",
        5: "产地、规格、质量）按时按量送达。",
    })
    outcome = check_language(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-LANG-001"]


def test_cross_page_ascii_bracket_second_entity(qa_rules):
    """同形态第二实体: ASCII 括号跨页同样缝合成对。"""
    model = make_model(body_texts={
        46: "包装标识须符合标准(含净含量、厂名、",
        47: "厂址、生产日期)且与实物一致。",
    })
    outcome = check_language(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-LANG-001"]


def test_true_unbalanced_bracket_still_reported(qa_rules):
    """页内真正缺闭括号仍报。"""
    model = make_model(body_texts={
        8: "详见附件（资质证书复印件尚未装订。",
    })
    outcome = check_language(model, qa_rules)
    assert [f for f in outcome.findings if f.rule_code == "DOC-LANG-001"]


def test_arabic_paren_nested_under_chinese_not_glued(qa_rules):
    """中文括号节下的阿拉伯括号列表互不粘连, 不把另一节的 4 当成缺 3。"""
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=1, level=1, number="四", title="应急预案", kind="cn_digit"),
            QaHeading(page=222, level=2, number="二", title="预警识别", kind="paren"),
            QaHeading(page=222, level=2, number="1", title="信息传达渠道", kind="paren"),
            QaHeading(page=222, level=2, number="2", title="针对性与时效性", kind="paren"),
            QaHeading(page=224, level=2, number="三", title="事故分级", kind="paren"),
            # 下一列表的（1）（2）（3）因过长未入树, 只采到（4）——不能和上一列表粘成缺 3
            QaHeading(page=225, level=2, number="4", title="质量争议", kind="paren"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    missing = [f for f in outcome.findings if f.rule_code == "DOC-NUM-001"]
    assert missing == [], [f.message for f in missing]


def test_arabic_paren_real_gap_under_same_parent_still_reported(qa_rules):
    """同形态第二实体: 同一中文括号父级下阿拉伯序号真缺 2 仍报。"""
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=1, level=1, number="五", title="供货方案", kind="cn_digit"),
            QaHeading(page=10, level=2, number="一", title="准入标准", kind="paren"),
            QaHeading(page=10, level=2, number="1", title="学生奶标准", kind="paren"),
            QaHeading(page=11, level=2, number="3", title="小麦粉标准", kind="paren"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    missing = [f for f in outcome.findings if f.rule_code == "DOC-NUM-001"]
    assert len(missing) == 1
    assert "2" in missing[0].message


def test_same_page_two_arabic_paren_still_duplicate(qa_rules):
    """同页两个不同「（2）」仍报重号, 不得被挂脊误伤。"""
    model = make_model(
        body_texts={84: "正文"},
        headings=[
            QaHeading(page=80, level=1, number="三", title="配送组织", kind="cn_digit"),
            QaHeading(page=84, level=2, number="1", title="干线路由", kind="paren"),
            QaHeading(page=84, level=2, number="2", title="差异化配送策略", kind="paren"),
            QaHeading(page=84, level=2, number="2", title="动态调度系统", kind="paren"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    duplicates = [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]
    assert len(duplicates) == 1
    assert "84" in duplicates[0].message


def test_paren_heading_with_sentence_break_keeps_prefix():
    """句读切开的括号行: 余量足够时只取句号前标题, 不丢编号。"""
    line = "（2）现场核实与检测。由专职食品安全管理员组织现场取样，委托第三方"
    assert "。" in line
    model = _filled_model([_block(2, line, size=12.0, flags=0)])
    headings = detect_headings(model)
    matched = [h for h in headings if h.kind == "paren" and h.number == "2"]
    assert len(matched) == 1
    assert matched[0].title == "现场核实与检测"


def test_paren_heading_sentence_break_second_entity():
    """同形态第二实体: 另一条句号切开的括号行同样只保留前缀。"""
    line = "（4）应急响应启动。接到报告后立即通知现场负责人并封锁通道等待指令"
    assert "。" in line
    model = _filled_model([_block(2, line, size=12.0, flags=0)])
    headings = detect_headings(model)
    matched = [h for h in headings if h.kind == "paren" and h.number == "4"]
    assert len(matched) == 1
    assert matched[0].title == "应急响应启动"


def test_paren_heading_short_sentence_tail_not_collected():
    """句号后余量不足, 整行仍是句子不是标题。"""
    line = "（1）各方及时沟通。出现问题，"
    model = _filled_model([_block(2, line, size=12.0, flags=0)])
    headings = detect_headings(model)
    assert not any(h.kind == "paren" and h.number == "1" for h in headings)


def test_comma_clause_not_heading():
    """含逗号的长句不是标题, 避免把正文行收成同号。"""
    line = "（2）不同食品品类的仓储要求存在差异，我方将优化冷链仓储区布局"
    model = _filled_model([_block(2, line, size=12.0, flags=0)])
    headings = detect_headings(model)
    assert not any(h.kind == "paren" and h.number == "2" for h in headings)


def test_comma_clause_second_entity():
    """同形态第二实体: 逗号切开的另一条正文行同样不入树。"""
    line = "（5）采用导航定位系统实时监测配送车辆的行驶轨迹，配合冷链记录"
    model = _filled_model([_block(2, line, size=12.0, flags=0)])
    headings = detect_headings(model)
    assert not any(h.kind == "paren" and h.number == "5" for h in headings)

