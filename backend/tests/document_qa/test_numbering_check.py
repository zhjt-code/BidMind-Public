"""编号连续性: 章节缺号 + 附件缺号(同形态第二实体) + 重号 + 零误报"""

from app.core.document_qa.checks.numbering_check import check_numbering
from app.core.document_qa.contracts import QaBlock, QaHeading, QaSpan

from tests.document_qa.conftest import make_model


def _rule_codes(findings):
    return {f.rule_code for f in findings}


def test_chapter_missing_number(qa_rules):
    """章节 3.1 -> 3.2 -> 3.4 缺 3.3(标题树家族)。"""
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=1, level=2, number="3.1", title="施工方案"),
            QaHeading(page=2, level=2, number="3.2", title="进度计划"),
            QaHeading(page=3, level=2, number="3.4", title="质量保证"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    missing = [f for f in outcome.findings if f.rule_code == "DOC-NUM-001"]
    assert len(missing) == 1
    assert "3.3" in missing[0].message
    assert missing[0].certainty.value == "certain"


def test_attachment_missing_number_second_entity(qa_rules):
    """附件 1/2/4 缺附件3 —— 同形态第二实体走同一序列差分链。"""
    model = make_model(body_texts={
        1: "附件1：营业执照\n附件2：资质证书",
        2: "附件4：业绩合同",
    })
    outcome = check_numbering(model, qa_rules)
    missing = [f for f in outcome.findings if f.rule_code == "DOC-NUM-001"]
    assert len(missing) == 1
    assert "附件" in missing[0].title
    assert "3" in missing[0].message


def test_figure_duplicate_number(qa_rules):
    """图2 在邻近页(间隔<=2)重复出现 -> 重号(possible)。"""
    model = make_model(body_texts={
        1: "图1 网络拓扑\n图2 部署架构",
        3: "图2 组织结构",
        4: "图3 流程图",
    })
    outcome = check_numbering(model, qa_rules)
    duplicates = [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]
    assert len(duplicates) == 1
    assert duplicates[0].certainty.value == "possible"


def test_decimal_far_apart_not_duplicate(qa_rules):
    """稳定小数同号但页距>2: 分章复用, 不报重号。"""
    model = make_model(
        body_texts={4: "正文", 69: "正文"},
        headings=[
            QaHeading(page=4, level=2, number="1.2", title="分项报价明细表"),
            QaHeading(page=69, level=2, number="1.2", title="总体目标"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]


def test_figure_far_apart_second_entity_not_duplicate(qa_rules):
    """同形态第二实体: 图2 远距分章复用不报重号。"""
    model = make_model(body_texts={
        1: "图2 网络拓扑",
        80: "图2 应急流程",
    })
    outcome = check_numbering(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]


def test_decimal_nearby_still_duplicate(qa_rules):
    """页距<=2 的稳定小数同号仍报。"""
    model = make_model(
        body_texts={4: "正文", 6: "正文"},
        headings=[
            QaHeading(page=4, level=2, number="1.2", title="分项报价明细表"),
            QaHeading(page=6, level=2, number="1.2", title="总体目标"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    duplicates = [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]
    assert len(duplicates) == 1
    assert "1.2" in duplicates[0].message
    assert duplicates[0].certainty.value == "possible"


def test_restartable_cn_digit_across_chapters_not_duplicate(qa_rules):
    """可重启中文序号跨章「一」不报重号。"""
    model = make_model(
        body_texts={1: "正文", 2: "正文"},
        headings=[
            QaHeading(page=3, level=1, number="一", title="投标函", kind="cn_digit"),
            QaHeading(page=4, level=1, number="二", title="方案", kind="cn_digit"),
            QaHeading(page=10, level=1, number="一", title="质量承诺", kind="cn_digit"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]


def test_restartable_one_then_one_not_duplicate(qa_rules):
    """同形态: 新章节直接从「一」再数, 中间不必出现更大号。"""
    model = make_model(
        body_texts={1: "正文", 2: "正文"},
        headings=[
            QaHeading(page=3, level=1, number="一", title="施工方案", kind="cn_digit"),
            QaHeading(page=8, level=1, number="一", title="人员配置", kind="cn_digit"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]


def test_restartable_paren_second_entity(qa_rules):
    """同形态第二实体: （一）在不同父级下不报重号。"""
    model = make_model(
        body_texts={1: "正文"},
        headings=[
            QaHeading(page=3, level=1, number="一", title="人员", kind="cn_digit"),
            QaHeading(page=3, level=2, number="一", title="简历", kind="paren"),
            QaHeading(page=8, level=1, number="二", title="设备", kind="cn_digit"),
            QaHeading(page=8, level=2, number="一", title="清单", kind="paren"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]


def test_restartable_same_parent_mid_start_cross_page_not_duplicate(qa_rules):
    """同父可重启列表: 第一段 1/2/3 后第二段从 3 起跳(中间缺采), 跨页不报重号。"""
    model = make_model(
        body_texts={1: "正文", 37: "正文", 67: "正文", 76: "正文"},
        headings=[
            QaHeading(page=1, level=1, number="八", title="与评分有关资料", kind="cn_digit"),
            QaHeading(page=37, level=2, number="1", title="身份证", kind="paren"),
            QaHeading(page=37, level=2, number="2", title="职称证", kind="paren"),
            QaHeading(page=37, level=2, number="3", title="学历学位证", kind="paren"),
            QaHeading(page=67, level=2, number="3", title="业绩3油井施工", kind="paren"),
            QaHeading(page=76, level=2, number="4", title="业绩4管线钢", kind="paren"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]


def test_restartable_top_digit_mid_start_second_entity(qa_rules):
    """同形态第二实体: top_digit 同作用域、第二段从 3 起跳跨页不报重号。"""
    model = make_model(
        body_texts={10: "正文", 12: "正文", 40: "正文", 41: "正文"},
        headings=[
            QaHeading(page=10, level=1, number="1", title="现场准备", kind="top_digit"),
            QaHeading(page=11, level=1, number="2", title="人员进场", kind="top_digit"),
            QaHeading(page=12, level=1, number="3", title="材料验收", kind="top_digit"),
            QaHeading(page=40, level=1, number="3", title="设备进场", kind="top_digit"),
            QaHeading(page=41, level=1, number="4", title="安全交底", kind="top_digit"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]


def test_restartable_same_page_distinct_still_duplicate(qa_rules):
    """同页两个不同「二」仍报重号(真阳不得被跨页豁免误伤)。"""
    model = make_model(
        body_texts={110: "正文"},
        headings=[
            QaHeading(page=110, level=1, number="三", title="安全承诺", kind="cn_digit"),
            QaHeading(page=110, level=2, number="一", title="杜绝安全事故", kind="paren"),
            QaHeading(page=110, level=2, number="二", title="加强安全教育培训", kind="paren"),
            QaHeading(page=110, level=2, number="二", title="落实安全防护措施", kind="paren"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    duplicates = [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]
    assert len(duplicates) == 1
    assert duplicates[0].certainty.value == "probable"
    assert "110" in duplicates[0].message


def test_plain_document_no_false_positive(qa_rules):
    """无编号文档零误报。"""
    model = make_model(body_texts={
        1: "本公司承诺严格按照招标文件要求组织实施。",
        2: "我方具备完善的质量管理体系与安全保障措施。",
    })
    outcome = check_numbering(model, qa_rules)
    assert outcome.findings == []


# ── 金标3638 修复: 目录行排除 + 同页重号 ─────────────────────────────────


def test_index_lines_excluded_from_families(qa_rules):
    """目录行(行尾独立页码)不入编号家族: 不再误报"附件1 重号"。"""
    model = make_model(body_texts={
        3: "附件1 项目组主要成员 50\n附件2 数据字段说明 51",
        50: "附件1 项目组主要成员",
        51: "附件2 数据字段说明",
    })
    outcome = check_numbering(model, qa_rules)
    assert outcome.findings == []


def test_same_page_duplicate_number_distinct_titles(qa_rules):
    """同页两个不同对象共用同一编号(表7-2 x2)必须报; 第二实体图号同链路。"""
    model = make_model(body_texts={
        42: "表7-2 文档归档检查表\n表7-2 文件命名规则检查表",
        9: "图2 网络拓扑\n图2 组织结构",
    })
    outcome = check_numbering(model, qa_rules)
    duplicates = [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]
    assert len(duplicates) == 2
    assert all(f.certainty.value == "probable" for f in duplicates)


def test_same_page_same_text_not_reported(qa_rules):
    """同页同文本重复行(解析副作用)不算重号。"""
    model = make_model(body_texts={
        8: "表3 设备清单\n表3 设备清单",
    })
    outcome = check_numbering(model, qa_rules)
    assert not [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]


def _footer(page: int, text: str, y: float = 0.91) -> QaBlock:
    return QaBlock(
        page=page, text=text, bbox=(0.4, y, 0.6, y + 0.03),
        spans=(QaSpan(
            page=page, text=text, bbox=(0.4, y, 0.6, y + 0.03),
            font="SimSun", size=9.0, flags=0, color=0,
        ),),
    )


def test_printed_page_gap_in_middle(qa_rules):
    """物理页中段缺印刷页码 -> 序列差分缺号。"""
    model = make_model(body_texts={i: f"第{i}页正文。" for i in range(1, 7)})
    model.blocks = [
        _footer(3, "1"), _footer(4, "2"), _footer(5, "4"), _footer(6, "5"),
    ]
    outcome = check_numbering(model, qa_rules)
    missing = [
        f for f in outcome.findings
        if f.rule_code == "DOC-NUM-001" and "印刷页码" in f.title
    ]
    assert len(missing) == 1
    assert "3" in missing[0].message


def test_printed_page_cn_duplicate_second_entity(qa_rules):
    """同形态第二实体: 「第N页」跨页重号走同一序列差分链。"""
    model = make_model(body_texts={i: f"正文{i}。" for i in range(1, 8)})
    model.blocks = [
        _footer(3, "第4页"),
        _footer(4, "第5页"),
        _footer(6, "第5页"),
        _footer(7, "第6页"),
    ]
    outcome = check_numbering(model, qa_rules)
    duplicates = [
        f for f in outcome.findings
        if f.rule_code == "DOC-NUM-002" and "印刷页码" in f.title
    ]
    assert len(duplicates) == 1


def test_printed_page_far_duplicate_still_reported(qa_rules):
    """印刷页码是整本唯一序列, 远距同号仍报, 不走章节页距阈值。"""
    model = make_model(body_texts={i: f"正文{i}。" for i in range(1, 12)})
    model.blocks = [
        _footer(3, "第4页"),
        _footer(4, "第5页"),
        _footer(10, "第5页"),
        _footer(11, "第6页"),
    ]
    outcome = check_numbering(model, qa_rules)
    duplicates = [
        f for f in outcome.findings
        if f.rule_code == "DOC-NUM-002" and "印刷页码" in f.title
    ]
    assert len(duplicates) == 1


def test_copied_page_skips_number_duplicate_but_distinct_pages_do_not(qa_rules):
    """复制页上的同号不另报; 正文不同的邻近重号仍报(图号为第二实体)。"""
    copied = "现场实施流程的正文需要足够长才能视为整页复制。" * 12
    model = make_model(
        body_texts={
            10: copied,
            11: copied,
            20: "图2 甲方案部署说明" + "甲" * 80,
            21: "图2 乙方案应急说明" + "乙" * 80,
        },
        headings=[
            QaHeading(page=10, level=2, number="5.6", title="现场实施", kind="decimal"),
            QaHeading(page=11, level=2, number="5.6", title="现场实施", kind="decimal"),
        ],
    )
    outcome = check_numbering(model, qa_rules)
    chapter_dups = [
        item for item in outcome.findings
        if item.rule_code == "DOC-NUM-002" and "章节" in item.title
    ]
    figure_dups = [
        item for item in outcome.findings
        if item.rule_code == "DOC-NUM-002" and "图" in item.title
    ]
    assert chapter_dups == []
    assert len(figure_dups) == 1


def test_blank_and_image_pages_explain_printed_gaps(qa_rules):
    """空白页与图片页上的印刷缺号不报; 正文页缺号仍报。"""
    from dataclasses import replace

    texts = {i: f"本页是正常正文{i}。" * 12 for i in range(1, 8)}
    model = make_model(body_texts=texts)
    model.pages[3] = replace(model.pages[3], char_count=0, image_count=0, table_count=0)
    model.pages[4] = replace(model.pages[4], char_count=0, image_only=True)
    model.blocks = [
        _footer(1, "1"), _footer(2, "2"), _footer(3, "3"),
        _footer(6, "6"), _footer(7, "7"),
    ]
    explained = check_numbering(model, qa_rules)
    printed_missing = [
        item for item in explained.findings
        if item.rule_code == "DOC-NUM-001" and "印刷页码" in item.title
    ]
    assert printed_missing == []

    real = make_model(body_texts=texts)
    real.blocks = [
        _footer(1, "1"), _footer(2, "2"), _footer(3, "3"),
        _footer(5, "5"), _footer(6, "6"), _footer(7, "7"),
    ]
    outcome = check_numbering(real, qa_rules)
    missing = [
        item for item in outcome.findings
        if item.rule_code == "DOC-NUM-001" and "印刷页码" in item.title
    ]
    assert len(missing) == 1
    assert "4" in missing[0].message


def test_cover_without_page_number_not_reported(qa_rules):
    """封面/目录头部无印刷页码的空窗不报缺号。"""
    model = make_model(body_texts={i: f"正文{i}。" for i in range(1, 6)})
    model.blocks = [_footer(3, "1"), _footer(4, "2"), _footer(5, "3")]
    outcome = check_numbering(model, qa_rules)
    printed = [f for f in outcome.findings if "印刷页码" in f.title]
    assert printed == []
