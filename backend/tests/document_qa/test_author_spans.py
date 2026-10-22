"""作者对象: 关系型检查只消费表外大纲、正文跨度、网格单行。

每条形态都有第二个实体, 避免只守住某一种编号或括号。
"""

from __future__ import annotations

from app.core.document_qa.author_spans import outline_nodes
from app.core.document_qa.checks.consistency_check import check_consistency
from app.core.document_qa.checks.duplicate_check import check_duplicate
from app.core.document_qa.checks.hygiene_check import check_hygiene
from app.core.document_qa.checks.language_check import check_language
from app.core.document_qa.checks.layout_check import check_layout
from app.core.document_qa.checks.numbering_check import check_numbering
from app.core.document_qa.checks.reference_check import check_reference
from app.core.document_qa.checks.structure_check import check_structure
from app.core.document_qa.checks.toc_check import check_toc
from app.core.document_qa.contracts import (
    Evidence,
    EvidenceItem,
    Finding,
    QaCategory,
    QaCertainty,
    QaHeading,
    QaSeverity,
    QaTocEntry,
)
from app.core.document_qa.region_text import KIND_BODY, KIND_CAPTION, KIND_TABLE, KIND_TITLE
from app.core.document_qa.verify.evidence_pack import confirmed_by_measurement
from app.core.document_qa.verify.finding_verifier import apply_verdicts
from tests.document_qa.conftest import make_line, make_model, make_region, with_regions


def _codes(findings, code: str) -> list:
    return [item for item in findings if item.rule_code == code]


def test_decimal_inside_table_is_not_a_chapter_gap(qa_rules) -> None:
    """表内小数编号不进入章节序列, 也不把前后章节切成空章。"""
    table = make_region(
        2, KIND_TABLE, (0.08, 0.25, 0.92, 0.7),
        [
            make_line(2, "1.1 设备名称", (0.10, 0.30, 0.40, 0.33)),
            make_line(2, "1.3 规格型号", (0.10, 0.40, 0.40, 0.43)),
            make_line(2, "离心泵一台", (0.45, 0.30, 0.80, 0.33)),
        ],
        label="table",
    )
    body = make_region(
        2, KIND_BODY, (0.08, 0.72, 0.92, 0.8),
        [make_line(2, "表下还有设备说明正文, 这一节不是空的。", (0.10, 0.74, 0.80, 0.78))],
        label="text",
    )
    headings = [
        QaHeading(page=2, level=2, number="1.1", title="设备名称", bbox=(0.10, 0.30, 0.40, 0.33), kind="decimal"),
        QaHeading(page=2, level=2, number="1.3", title="规格型号", bbox=(0.10, 0.40, 0.40, 0.43), kind="decimal"),
        QaHeading(page=2, level=1, number="8.1", title="设备配置", bbox=(0.10, 0.08, 0.70, 0.12), kind="decimal"),
        QaHeading(page=2, level=1, number="8.2", title="安装调试", bbox=(0.10, 0.84, 0.70, 0.88), kind="decimal"),
    ]
    title_a = make_region(
        2, KIND_TITLE, (0.10, 0.08, 0.70, 0.12),
        [make_line(2, "8.1 设备配置", (0.10, 0.08, 0.70, 0.12))],
        label="paragraph_title",
    )
    title_b = make_region(
        2, KIND_TITLE, (0.10, 0.84, 0.70, 0.88),
        [make_line(2, "8.2 安装调试", (0.10, 0.84, 0.70, 0.88))],
        label="paragraph_title",
    )
    model = with_regions(
        make_model(body_texts={2: "页"}, headings=headings),
        {2: [title_a, table, body, title_b]},
    )
    numbering = check_numbering(model, qa_rules).findings
    assert not any("1.2" in item.message for item in _codes(numbering, "DOC-NUM-001"))
    assert _codes(check_structure(model, qa_rules).findings, "DOC-STR-002") == []


def test_paren_inside_table_is_not_a_list_gap(qa_rules) -> None:
    """同形态第二实体: 表内括号编号同样不进入章节序列。"""
    table = make_region(
        3, KIND_TABLE, (0.08, 0.2, 0.92, 0.6),
        [
            make_line(3, "（1）进场验收", (0.10, 0.25, 0.42, 0.28)),
            make_line(3, "（3）竣工验收", (0.10, 0.36, 0.42, 0.39)),
        ],
        label="table",
    )
    headings = [
        QaHeading(page=3, level=2, number="1", title="进场验收", bbox=(0.10, 0.25, 0.42, 0.28), kind="paren"),
        QaHeading(page=3, level=2, number="3", title="竣工验收", bbox=(0.10, 0.36, 0.42, 0.39), kind="paren"),
    ]
    model = with_regions(make_model(body_texts={3: "页"}, headings=headings), {3: [table]})
    numbering = check_numbering(model, qa_rules).findings
    assert _codes(numbering, "DOC-NUM-001") == []


def test_full_width_table_banner_stays_outline(qa_rules) -> None:
    line = make_line(4, "8.1 项目组织机构", (0.10, 0.20, 0.90, 0.24))
    table = make_region(
        4, KIND_TABLE, (0.08, 0.19, 0.92, 0.7),
        [line, make_line(4, '项目经理 <PERSON_A>', (0.10, 0.30, 0.40, 0.33))],
        label="table",
    )
    model = with_regions(make_model(body_texts={4: "页"}), {4: [table]})
    nodes = outline_nodes(model, qa_rules)
    assert any(item.number == "8.1" and "项目组织机构" in item.title for item in nodes)


def test_full_width_unit_banner_stays_outline(qa_rules) -> None:
    """同形态第二实体: 横跨表首的「第X章」仍是大纲。"""
    line = make_line(5, "第一章 总体部署", (0.10, 0.20, 0.90, 0.24))
    table = make_region(5, KIND_TABLE, (0.08, 0.19, 0.92, 0.6), [line], label="table")
    model = with_regions(make_model(body_texts={5: "页"}), {5: [table]})
    nodes = outline_nodes(model, qa_rules)
    assert any(item.kind == "unit" and "总体部署" in item.title for item in nodes)


def test_bracket_across_paragraphs_is_closed(qa_rules) -> None:
    model = make_model(body_texts={
        1: "说明如下（含品种、名称、\n\n产地及合格证）。",
    })
    assert _codes(check_language(model, qa_rules).findings, "DOC-LANG-001") == []


def test_corner_bracket_across_pages_is_closed(qa_rules) -> None:
    """同形态第二实体: 【】跨页仍闭合。"""
    model = make_model(body_texts={
        6: "见【附件清单",
        7: "及承诺书】后提交。",
    })
    assert _codes(check_language(model, qa_rules).findings, "DOC-LANG-001") == []


def test_body_stem_is_not_title_variant_but_two_outline_titles_are(qa_rules) -> None:
    headings = [
        QaHeading(page=1, level=1, number="8.1", title="海上低渗油藏智能注采优化关键技术研究", kind="decimal"),
        QaHeading(page=4, level=1, number="8.2", title="海上低渗油藏智能注采优化技术研究", kind="decimal"),
    ]
    model = make_model(
        body_texts={2: "正文写了海上低渗油藏智能注采优化技术研究, 这只是词干。"},
        headings=headings,
    )
    variants = _codes(check_consistency(model, qa_rules).findings, "DOC-CONS-001")
    assert len(variants) == 1
    assert "关键" not in variants[0].evidence.actual
    assert variants[0].evidence.items[1].page == 4


def test_attachment_titles_with_a_short_cut_are_variants(qa_rules) -> None:
    """同形态第二实体: 附件标题之间的连续缺字也报, 不只章节标题。"""
    headings = [
        QaHeading(page=2, level=1, number="附件1", title="试验检测设备校准证书清单", kind="attachment"),
        QaHeading(page=6, level=1, number="附件2", title="试验检测设备证书清单", kind="attachment"),
    ]
    model = make_model(body_texts={3: "正文没有复述这组附件名。"}, headings=headings)
    variants = _codes(check_consistency(model, qa_rules).findings, "DOC-CONS-001")
    assert len(variants) == 1
    assert "证书清单" in variants[0].evidence.actual


def test_placeholder_split_across_lines_reports_cell_glue_does_not(qa_rules) -> None:
    body = make_region(
        1, KIND_BODY, (0.08, 0.1, 0.92, 0.2),
        [
            make_line(1, "项目管理人员中有待补", (0.10, 0.10, 0.80, 0.13)),
            make_line(1, "充岗位尚未确定。", (0.10, 0.14, 0.80, 0.17)),
        ],
        label="text",
    )
    table = make_region(
        1, KIND_TABLE, (0.08, 0.4, 0.92, 0.6),
        [
            make_line(1, "待补", (0.10, 0.42, 0.30, 0.45)),
            make_line(1, "充说明", (0.40, 0.42, 0.70, 0.45)),
        ],
        label="table",
    )
    model = with_regions(make_model(body_texts={1: "页"}), {1: [body, table]})
    found = _codes(check_hygiene(model, qa_rules).findings, "DOC-HYG-001")
    assert len(found) == 1
    assert found[0].evidence.items[0].excerpt.find("待补充") >= 0


def test_typo_split_across_lines_reports_cell_glue_does_not(qa_rules) -> None:
    """同形态第二实体: 错词跨行仍报, 分在两个单元格里不报。"""
    body = make_region(
        2, KIND_BODY, (0.08, 0.1, 0.92, 0.2),
        [
            make_line(2, "系统应既", (0.10, 0.10, 0.50, 0.13)),
            make_line(2, "时回传报警。", (0.10, 0.14, 0.50, 0.17)),
        ],
        label="text",
    )
    table = make_region(
        2, KIND_TABLE, (0.08, 0.4, 0.92, 0.55),
        [
            make_line(2, "既", (0.10, 0.42, 0.20, 0.45)),
            make_line(2, "时", (0.30, 0.42, 0.40, 0.45)),
        ],
        label="table",
    )
    model = with_regions(make_model(body_texts={2: "页"}), {2: [body, table]})
    found = _codes(check_hygiene(model, qa_rules).findings, "DOC-HYG-004")
    assert len(found) == 1


def test_cross_page_paragraph_is_not_near_duplicate(qa_rules) -> None:
    opening = (
        "本项目质量管理实行项目经理负责制，建立覆盖原材料进场、工序交接和隐蔽验收的"
        "全过程质量控制体系，确保责任可追溯"
    )
    closing = opening + "到人"
    model = make_model(body_texts={1: opening, 2: closing})
    assert _codes(check_duplicate(model, qa_rules).findings, "DOC-DUP-004") == []


def test_repeated_heading_inside_table_is_ignored(qa_rules) -> None:
    table_a = make_region(
        3, KIND_TABLE, (0.08, 0.2, 0.92, 0.5),
        [make_line(3, "6.2 现场实施流程", (0.10, 0.25, 0.45, 0.28))],
        label="table",
    )
    table_b = make_region(
        4, KIND_TABLE, (0.08, 0.2, 0.92, 0.5),
        [make_line(4, "6.2 现场实施流程", (0.10, 0.25, 0.45, 0.28))],
        label="table",
    )
    headings = [
        QaHeading(page=3, level=2, number="6.2", title="现场实施流程", bbox=(0.10, 0.25, 0.45, 0.28), kind="decimal"),
        QaHeading(page=4, level=2, number="6.2", title="现场实施流程", bbox=(0.10, 0.25, 0.45, 0.28), kind="decimal"),
        QaHeading(page=8, level=2, number="7.1", title="质量保证措施", kind="decimal"),
        QaHeading(page=9, level=2, number="7.1", title="质量保证措施", kind="decimal"),
    ]
    body = "质量保证这一节的正文和其他页不同，避免被当成整页复制。" * 6
    model = with_regions(
        make_model(body_texts={3: "表", 4: "表", 8: body, 9: body + "另一页内容。"}, headings=headings),
        {3: [table_a], 4: [table_b]},
    )
    repeated = _codes(check_duplicate(model, qa_rules).findings, "DOC-DUP-006")
    assert len(repeated) == 1
    assert "质量保证措施" in repeated[0].message
    assert "现场实施流程" not in repeated[0].message


def test_toc_uses_joined_outline_not_table_cell(qa_rules) -> None:
    title = make_region(
        5, KIND_TITLE, (0.10, 0.10, 0.80, 0.18),
        [
            make_line(5, "8.1 项目组织管理机构", (0.10, 0.10, 0.70, 0.13)),
            make_line(5, "及职责分工", (0.10, 0.14, 0.40, 0.17)),
        ],
        label="paragraph_title",
    )
    table = make_region(
        9, KIND_TABLE, (0.08, 0.2, 0.92, 0.5),
        [make_line(9, "项目组织管理机构及职责分工", (0.10, 0.25, 0.50, 0.28))],
        label="table",
    )
    cell = QaHeading(
        page=9, level=2, number=None, title="项目组织管理机构及职责分工",
        bbox=(0.10, 0.25, 0.50, 0.28), kind="decimal",
    )
    entries = [
        QaTocEntry(title="投标函", printed_page=1, physical_page=3, order_index=0),
        QaTocEntry(title="项目组织管理机构及职责分工", printed_page=5, physical_page=5, order_index=1),
        QaTocEntry(title="资质证书", printed_page=7, physical_page=11, order_index=2),
    ]
    headings = [
        QaHeading(page=3, level=1, number="一", title="投标函", kind="cn_digit"),
        cell,
        QaHeading(page=11, level=1, number="三", title="资质证书", kind="cn_digit"),
    ]
    model = with_regions(
        make_model(body_texts={3: "甲", 5: "乙", 9: "丙", 11: "丁"}, headings=headings),
        {5: [title], 9: [table]},
    )
    model.toc_entries = entries
    model.toc_resolution_reliable = True
    mismatch = _codes(check_toc(model, qa_rules).findings, "DOC-TOC-004")
    assert not any("项目组织管理机构" in item.message for item in mismatch)


def test_figure_caption_is_not_a_chapter_toc_target(qa_rules) -> None:
    """同形态第二实体: 图题不是章节目录的匹配目标。"""
    caption = make_region(
        4, KIND_CAPTION, (0.10, 0.40, 0.70, 0.46),
        [make_line(4, "图2-1 工艺流程", (0.10, 0.40, 0.50, 0.44))],
        label="figure_title",
    )
    model = with_regions(
        make_model(
            body_texts={4: "图"},
            headings=[QaHeading(page=4, level=1, number="二", title="工艺流程", kind="cn_digit")],
        ),
        {4: [caption]},
    )
    nodes = outline_nodes(model, qa_rules)
    assert all("图2-1" not in item.display for item in nodes)
    assert any(item.title == "工艺流程" for item in nodes)


def test_split_citation_resolves_outline_not_table_cell(qa_rules) -> None:
    body = make_region(
        2, KIND_BODY, (0.08, 0.1, 0.9, 0.25),
        [
            make_line(2, "详见第3.2", (0.10, 0.12, 0.40, 0.15)),
            make_line(2, "节的人员安排。", (0.10, 0.16, 0.50, 0.19)),
        ],
        label="text",
    )
    table = make_region(
        6, KIND_TABLE, (0.08, 0.2, 0.92, 0.5),
        [make_line(6, "3.2 设备参数", (0.10, 0.25, 0.40, 0.28))],
        label="table",
    )
    headings = [
        QaHeading(page=4, level=2, number="3.2", title="人员安排", bbox=(0.10, 0.10, 0.60, 0.14), kind="decimal"),
        QaHeading(page=6, level=2, number="3.2", title="设备参数", bbox=(0.10, 0.25, 0.40, 0.28), kind="decimal"),
    ]
    model = with_regions(
        make_model(body_texts={2: "引", 4: "章", 6: "表"}, headings=headings),
        {2: [body], 6: [table]},
    )
    assert _codes(check_reference(model, qa_rules).findings, "DOC-REF-001") == []


def test_table_citation_uses_caption_not_grid_text(qa_rules) -> None:
    """同形态第二实体: 「见表2-1」的目标只来自题注。"""
    caption = make_region(
        3, KIND_CAPTION, (0.10, 0.18, 0.70, 0.24),
        [make_line(3, "表2-1 设备清单", (0.10, 0.18, 0.55, 0.22))],
        label="table_title",
    )
    body = make_region(
        3, KIND_BODY, (0.08, 0.05, 0.9, 0.12),
        [make_line(3, "参数见表2-1。", (0.10, 0.06, 0.40, 0.09))],
        label="text",
    )
    cited = with_regions(make_model(body_texts={3: "见"}), {3: [body, caption]})
    assert _codes(check_reference(cited, qa_rules).findings, "DOC-REF-001") == []

    grid_only = make_region(
        3, KIND_TABLE, (0.08, 0.3, 0.92, 0.6),
        [make_line(3, "表2-1 设备清单", (0.10, 0.35, 0.45, 0.38))],
        label="table",
    )
    missing = with_regions(make_model(body_texts={3: "见"}), {3: [body, grid_only]})
    refs = _codes(check_reference(missing, qa_rules).findings, "DOC-REF-001")
    assert any("2-1" in item.message for item in refs)


def test_wrapped_indent_is_not_an_outlier(qa_rules) -> None:
    first = make_line(1, "本段正文从左边距起写并在行末折到下一行继续叙述具体安排", (0.12, 0.20, 0.88, 0.24))
    wrapped = make_line(1, "续行向右缩进但仍然是同一段正文不应当报成缩进离群。", (0.20, 0.25, 0.88, 0.29))
    body = make_region(1, KIND_BODY, (0.08, 0.18, 0.92, 0.34), [first, wrapped], label="text")
    model = with_regions(make_model(body_texts={1: "段"}), {1: [body]})
    messages = [item.message for item in check_layout(model, qa_rules).findings]
    assert not any("缩进" in item for item in messages)


def test_table_color_is_not_a_body_outlier(qa_rules) -> None:
    """同形态第二实体: 表内色块不进入正文颜色。"""
    body = make_region(
        1, KIND_BODY, (0.08, 0.1, 0.92, 0.2),
        [make_line(1, "我公司将严格按照招标文件组织实施本项目并保证工程质量合格。", (0.12, 0.12, 0.88, 0.16))],
        label="text",
    )
    colored = make_line(1, "表内蓝色说明文字很长但是不应当计入正文颜色离群。", (0.10, 0.40, 0.80, 0.44), color=255)
    table = make_region(1, KIND_TABLE, (0.08, 0.3, 0.92, 0.6), [colored], label="table")
    model = with_regions(make_model(body_texts={1: "页"}), {1: [body, table]})
    messages = [item.message for item in check_layout(model, qa_rules).findings]
    assert not any("颜色" in item for item in messages)


def test_prefix_excerpt_does_not_block_a_high_confidence_rejection() -> None:
    prefix = Finding(
        rule_code="DOC-LANG-001",
        category=QaCategory.LANGUAGE,
        severity=QaSeverity.P2,
        certainty=QaCertainty.PROBABLE,
        title="括号未闭合",
        message="前缀摘录不平衡",
        evidence=Evidence(kind="single", items=(EvidenceItem(excerpt="详见附件（资质", page=4),)),
        anchor="bracket:prefix",
        closed_measurement=False,
    )
    kept, counts = apply_verdicts(
        [prefix],
        {"p4-0": (False, 0.95)},
        [type("C", (), {"candidate_id": "p4-0", "finding_index": 0})()],
        threshold=0.8,
    )
    assert counts["dropped"] == 1
    assert kept == []
    assert confirmed_by_measurement(make_model(body_texts={4: "页"}), prefix) is False


def test_complete_span_stays_closed_measurement() -> None:
    """同形态第二实体: 证据等于完整跨度时仍免复核。编号缺号是另一类闭合对象。"""
    bracket = Finding(
        rule_code="DOC-LANG-001",
        category=QaCategory.LANGUAGE,
        severity=QaSeverity.P2,
        certainty=QaCertainty.PROBABLE,
        title="括号未闭合",
        message="完整跨度仍未闭合",
        evidence=Evidence(
            kind="single",
            items=(EvidenceItem(excerpt="详见附件（资质证书复印件尚未装订。", page=8),),
        ),
        anchor="bracket:full",
        closed_measurement=True,
    )
    numbering = Finding(
        rule_code="DOC-NUM-001",
        category=QaCategory.NUMBERING,
        severity=QaSeverity.P1,
        certainty=QaCertainty.CERTAIN,
        title="章节编号不连续",
        message="缺少 1.3",
        evidence=Evidence(kind="sequence", items=(EvidenceItem(excerpt="1.2 目标", page=2),)),
        anchor="num:1.3",
        closed_measurement=True,
    )
    model = make_model(body_texts={2: "一", 8: "二"})
    assert confirmed_by_measurement(model, bracket) is True
    assert confirmed_by_measurement(model, numbering) is True


def _body(page: int, y: float, text: str):
    line = make_line(page, text, (0.10, y, 0.80, y + 0.03))
    return make_region(page, KIND_BODY, (0.08, y, 0.92, y + 0.05), [line], label="text")


def _table_barrier(page: int, y: float, text: str):
    line = make_line(page, text, (0.10, y, 0.40, y + 0.03))
    return make_region(page, KIND_TABLE, (0.08, y, 0.92, y + 0.2), [line], label="table")


def test_identifier_does_not_cross_a_skipped_table(qa_rules) -> None:
    """编号行和后面的数字之间隔着表, 不能拼成另一个编号。"""
    from app.core.document_qa.checks.consistency_check import collect_slot_ledger

    model = with_regions(
        make_model(body_texts={10: "甲", 11: "乙"}),
        {
            10: [
                _body(10, 0.20, "招标编号：FWFA00000114459"),
                _table_barrier(10, 0.40, "序号"),
            ],
            11: [
                _body(11, 0.20, "5"),
                _body(11, 0.30, "填写说明："),
            ],
        },
    )
    occurrences = collect_slot_ledger(model, qa_rules)["project_no"]["occurrences"]
    assert [item["normalized"] for item in occurrences] == ["FWFA00000114459"]
    assert all("填写说明" not in item["excerpt"] for item in occurrences)


def test_money_does_not_absorb_a_digit_across_a_table(qa_rules) -> None:
    """同形态第二例: 金额和后页数字之间隔着表, 不能拼成另一个金额。"""
    from app.core.document_qa.checks.consistency_check import collect_slot_ledger

    model = with_regions(
        make_model(body_texts={7: "甲", 8: "乙", 9: "丙"}),
        {
            7: [_body(7, 0.20, "投标报价：80万元")],
            8: [
                _body(8, 0.20, "投标报价：120"),
                _table_barrier(8, 0.40, "栏目"),
            ],
            9: [_body(9, 0.20, "5万元")],
        },
    )
    occurrences = collect_slot_ledger(model, qa_rules)["bid_price"]["occurrences"]
    assert [item["normalized"] for item in occurrences] == ["800000.00"]


def test_adjacent_next_page_still_joins_a_split_identifier(qa_rules) -> None:
    """紧邻的下一页仍接上。中间只有页眉页脚。"""
    from app.core.document_qa.checks.consistency_check import collect_slot_ledger
    from app.core.document_qa.region_text import KIND_FURNITURE

    footer = make_region(
        1, KIND_FURNITURE, (0.30, 0.94, 0.70, 0.98),
        [make_line(1, "1", (0.45, 0.95, 0.50, 0.97))],
        label="footer",
    )
    header = make_region(
        2, KIND_FURNITURE, (0.10, 0.02, 0.40, 0.05),
        [make_line(2, "页眉", (0.10, 0.02, 0.30, 0.04))],
        label="header",
    )
    model = with_regions(
        make_model(body_texts={1: "甲", 2: "乙"}),
        {
            1: [_body(1, 0.20, "招标编号：FWFA0000011445"), footer],
            2: [header, _body(2, 0.20, '9招标人：<ORG_A>有限公司')],
        },
    )
    occurrences = collect_slot_ledger(model, qa_rules)["project_no"]["occurrences"]
    assert [item["normalized"] for item in occurrences] == ["FWFA00000114459"]
    assert occurrences[0]["page"] == 1
    assert "招标人" not in occurrences[0]["excerpt"]


def test_brackets_close_across_line_paragraph_and_page(qa_rules) -> None:
    """紧邻的换行、句号后和下一页都能配平。全角和半角同一形状也配平。"""
    model = with_regions(
        make_model(body_texts={3: "甲", 4: "乙"}),
        {
            3: [
                make_region(3, KIND_BODY, (0.08, 0.15, 0.92, 0.55), [
                    make_line(3, "详见（附件", (0.10, 0.20, 0.50, 0.23)),
                    make_line(3, "清单）。", (0.10, 0.24, 0.50, 0.27)),
                    make_line(3, "承诺（如下。", (0.10, 0.40, 0.60, 0.43)),
                    make_line(3, "执行）。", (0.10, 0.44, 0.40, 0.47)),
                ], label="text"),
            ],
            4: [
                make_region(4, KIND_BODY, (0.08, 0.15, 0.92, 0.55), [
                    make_line(4, "另见（跨页", (0.10, 0.20, 0.50, 0.23)),
                    make_line(4, "条款）。", (0.10, 0.24, 0.40, 0.27)),
                    make_line(4, "见【栏目", (0.10, 0.36, 0.40, 0.39)),
                    make_line(4, "名称】。", (0.10, 0.40, 0.40, 0.43)),
                ], label="text"),
            ],
        },
    )
    assert _codes(check_language(model, qa_rules).findings, "DOC-LANG-001") == []


def test_bracket_does_not_close_across_a_table(qa_rules) -> None:
    """同形态第二例: 中间隔着表, 圆括号和方括号都不能配上。证据停在未配那一行。"""
    model = with_regions(
        make_model(body_texts={5: "甲", 6: "乙", 8: "丙", 9: "丁"}),
        {
            5: [_body(5, 0.20, "详见（附件"), _table_barrier(5, 0.40, "序号")],
            6: [_body(6, 0.20, "清单）。")],
            8: [_body(8, 0.20, "见【栏目"), _table_barrier(8, 0.40, "栏")],
            9: [_body(9, 0.20, "名称】。")],
        },
    )
    found = _codes(check_language(model, qa_rules).findings, "DOC-LANG-001")
    assert {item.evidence.items[0].page for item in found} == {5, 6, 8, 9}
    assert all("序号" not in item.evidence.items[0].excerpt for item in found)
    assert all(len(item.evidence.items[0].excerpt) <= 41 for item in found)


def test_mixed_width_brackets_are_closed(qa_rules) -> None:
    model = make_model(body_texts={
        9: "投标人（投标单位全称)已盖章。",
        12: "栏目【栏目标题]已填写。",
    })
    assert _codes(check_language(model, qa_rules).findings, "DOC-LANG-001") == []
