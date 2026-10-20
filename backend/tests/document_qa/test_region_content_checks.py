"""内容类检查器按版面区域取字 (表格隔离未完成部分)。

形态: 不同身份的文字被混成一个检查单元——正文跨区拼接把下一区吞进取值、表格文字被当正文比重复或被整体丢弃、
页眉页脚与公式混进正文。机制: 一致性/卫生/重复/语言检查与跨卷特征统一经 region_text 取数。
每条改动的规则都有版面页夹具与同形态第二实体; 对照模型(同样页面文字、无版面区域)证明夹具确实复现旧问题。
"""

from __future__ import annotations

from app.core.document_qa.analyzer import _build_features
from app.core.document_qa.checks.consistency_check import check_consistency, collect_slot_ledger
from app.core.document_qa.checks.duplicate_check import check_duplicate
from app.core.document_qa.checks.hygiene_check import check_hygiene
from app.core.document_qa.checks.language_check import check_language
from app.core.document_qa.contracts import DocumentModel, Finding, QaRegion, normalize_for_hash
from app.core.document_qa.region_text import (
    KIND_BODY,
    KIND_CAPTION,
    KIND_FORMULA,
    KIND_FURNITURE,
    KIND_TABLE,
)

from tests.document_qa.conftest import make_line, make_model, make_region, with_regions

_ROW_PITCH = 0.03
_LINE_HEIGHT = 0.02

_LONG_BODY = (
    "本项目质量管理实行项目经理负责制，建立覆盖原材料进场、工序交接、"
    "隐蔽验收和竣工验收的全过程质量控制体系，确保每道工序质量责任可追溯到人。"
)
_TABLE_NOTE = (
    "注：以上检测项目均按国家现行标准取样送检，检测报告由具备相应资质的第三方检测机构"
    "出具并加盖检测专用章，原件留存备查，复印件随投标文件提交。"
)
_PAGE_BODY = [
    "本章阐述施工总体部署，包括施工区段划分、主要施工顺序、关键线路控制节点以及各专业之间的交叉作业协调原则。",
    "施工准备阶段完成图纸会审、测量控制网复核、临时设施搭设和主要材料设备进场报验，为全面开工创造条件。",
    "主体施工阶段按先地下后地上、先结构后装修的顺序组织流水作业，每个流水段配置固定班组并实行工序交接检查。",
    "收尾阶段组织分部分项工程验收与资料整理，编制竣工图并移交运维培训资料，确保工程按期顺利通过竣工验收。",
]


def _stack(page: int, kind: str, top: float, texts: list[str]) -> QaRegion:
    """一个区域, 每段文字自成一行, 自上而下排列。"""
    lines = [
        make_line(page, text, (0.1, top + i * _ROW_PITCH, 0.9, top + i * _ROW_PITCH + _LINE_HEIGHT))
        for i, text in enumerate(texts)
    ]
    return make_region(page, kind, (0.08, top - 0.005, 0.92, top + len(texts) * _ROW_PITCH), lines)


def _table(page: int, top: float, *rows: tuple[str, ...]) -> QaRegion:
    """表格区: 每行若干单元格片段, 同一行的片段落在同一视觉行。"""
    lines = []
    for r, cells in enumerate(rows):
        y0 = top + r * _ROW_PITCH
        width = 0.8 / len(cells)
        for c, cell in enumerate(cells):
            x0 = 0.1 + c * width
            lines.append(make_line(page, cell, (x0, y0, x0 + width - 0.02, y0 + _LINE_HEIGHT)))
    return make_region(page, KIND_TABLE, (0.08, top - 0.005, 0.92, top + len(rows) * _ROW_PITCH), lines)


def _legacy_texts(pages: dict[int, list[QaRegion]]) -> dict[int, str]:
    """旧页文本: 页上全部区域的行各占一行(旧来源把表格、页眉页脚、公式混在正文里)。"""
    return {
        page: "\n".join(line.text for region in regions for line in region.lines)
        for page, regions in pages.items()
    }


def _layout_model(pages: dict[int, list[QaRegion]]) -> DocumentModel:
    return with_regions(make_model(body_texts=_legacy_texts(pages)), pages)


def _legacy_model(pages: dict[int, list[QaRegion]]) -> DocumentModel:
    return make_model(body_texts=_legacy_texts(pages))


def _of(findings: list[Finding], rule_code: str) -> list[Finding]:
    return [finding for finding in findings if finding.rule_code == rule_code]


def _slot_values(model: DocumentModel, qa_rules: dict, slot: str) -> list[str]:
    ledger = collect_slot_ledger(model, qa_rules)
    return [occ["value"] for occ in (ledger.get(slot) or {}).get("occurrences", [])]


def test_long_fixtures_exceed_duplicate_threshold() -> None:
    assert len(normalize_for_hash(_LONG_BODY)) > 60
    assert len(normalize_for_hash(_TABLE_NOTE)) > 60
    assert len(normalize_for_hash("".join(_PAGE_BODY))) > 200


# ── 一致性: 正文按区、表格逐行 ────────────────────────────────────────────────


def test_bidder_name_stops_at_its_own_body_region(qa_rules) -> None:
    """复现误报: 旧段落把下一区「投标人法定代表人…」拼进投标人名称取值。"""
    pages = {1: [
        _stack(1, KIND_BODY, 0.20, ['投标人名称：<ORG_B>']),
        _stack(1, KIND_BODY, 0.30, ["投标人法定代表人或受委托人（签名或盖私章）："]),
    ]}
    assert _slot_values(_layout_model(pages), qa_rules, "bidder_name") == ['<ORG_B>']
    assert _slot_values(_legacy_model(pages), qa_rules, "bidder_name") == ['<ORG_B>投标人法定代表人或受委托人']


def test_project_no_stops_at_its_own_body_region_second_entity(qa_rules) -> None:
    """同形态第二实体: 编号取值字符类含数字, 下一区以数字开头时旧段落同样吞进取值。"""
    for follower in ('项目负责人：<PERSON_C>', "2026年9月27日"):
        pages = {1: [
            _stack(1, KIND_BODY, 0.20, ["项目编号：TP-2026-018"]),
            _stack(1, KIND_BODY, 0.30, [follower]),
        ]}
        assert _slot_values(_layout_model(pages), qa_rules, "project_no") == ["TP-2026-018"], follower
    assert _slot_values(_legacy_model(pages), qa_rules, "project_no") == ["TP-2026-0182026"]


def test_table_row_price_conflicts_with_body_price(qa_rules) -> None:
    model = _layout_model({
        2: [_table(2, 0.30, ("投标报价：人民币82.5万元",))],
        3: [_stack(3, KIND_BODY, 0.20, ["投标报价：人民币85万元"])],
    })
    conflicts = _of(check_consistency(model, qa_rules).findings, "DOC-CONS-001")
    assert len(conflicts) == 1
    assert "投标报价" in conflicts[0].title
    assert {item.page for item in conflicts[0].evidence.items} == {2, 3}


def test_table_row_duration_conflicts_with_body_second_entity(qa_rules) -> None:
    """同形态第二实体: 标签与取值分在两个单元格, 同一视觉行拼成一行后进台账。"""
    model = _layout_model({
        4: [_table(4, 0.30, ("总工期", "180日历天"), ("质量标准", "合格"))],
        6: [_stack(6, KIND_BODY, 0.20, ["总工期：150日历天。"])],
    })
    conflicts = [f for f in check_consistency(model, qa_rules).findings if "工期" in f.title]
    assert len(conflicts) == 1
    assert {item.page for item in conflicts[0].evidence.items} == {4, 6}


def test_table_row_value_not_glued_to_next_row(qa_rules) -> None:
    pages = {5: [_table(5, 0.30, ("投标人名称：", '<ORG_B>'), ("法定代表人", '<PERSON_A>'))]}
    assert _slot_values(_layout_model(pages), qa_rules, "bidder_name") == ['<ORG_B>']
    assert _slot_values(_legacy_model(pages), qa_rules, "bidder_name") == ['<ORG_B>法定代表人<PERSON_A>']


# ── 卫生: 占位扫正文与表格行, 乱码扫页面文字 ──────────────────────────────────


def test_placeholder_in_table_row_reported(qa_rules) -> None:
    model = _layout_model({2: [
        _stack(2, KIND_BODY, 0.10, ["项目组织机构如下表。"]),
        _table(2, 0.30, ("项目经理", "XXXX"), ("技术负责人", '<PERSON_B>')),
    ]})
    found = _of(check_hygiene(model, qa_rules).findings, "DOC-HYG-001")
    assert [f.title for f in found] == ["占位符 XXX"]
    assert found[0].evidence.items[0].page == 2


def test_year_placeholder_in_table_row_reported_second_entity(qa_rules) -> None:
    model = _layout_model({3: [_table(3, 0.30, ("签署日期", "20XX年X月X日"), ("投标人", '<ORG_B>'))]})
    found = _of(check_hygiene(model, qa_rules).findings, "DOC-HYG-001")
    assert [f.title for f in found] == ["年份占位"]
    assert found[0].evidence.items[0].page == 3


def test_placeholder_only_in_header_or_footer_not_reported(qa_rules) -> None:
    pages = {4: [
        _stack(4, KIND_FURNITURE, 0.02, ["XXXX项目投标文件"]),
        _stack(4, KIND_BODY, 0.30, ["我公司承诺按期完成本项目全部施工内容。"]),
        _stack(4, KIND_FURNITURE, 0.94, ["联系人：待补充"]),
    ]}
    assert _of(check_hygiene(_layout_model(pages), qa_rules).findings, "DOC-HYG-001") == []
    assert len(_of(check_hygiene(_legacy_model(pages), qa_rules).findings, "DOC-HYG-001")) == 2


def test_garbled_run_in_table_reported_formula_glyphs_not(qa_rules) -> None:
    pages = {5: [
        _table(5, 0.20, ("主要材料", "□□□型钢")),
        _stack(5, KIND_FORMULA, 0.60, ["E=\uFFFD\uFFFD×100%"]),
    ]}
    garbled = _of(check_hygiene(_layout_model(pages), qa_rules).findings, "DOC-HYG-002")
    assert len(garbled) == 1
    assert "□□□" in garbled[0].message
    assert len(_of(check_hygiene(_legacy_model(pages), qa_rules).findings, "DOC-HYG-002")) == 2


def test_garbled_run_in_caption_reported_footer_not_second_entity(qa_rules) -> None:
    model = _layout_model({6: [
        _stack(6, KIND_CAPTION, 0.40, ["图3 □□分布图"]),
        _stack(6, KIND_FURNITURE, 0.94, ["第□□页"]),
    ]})
    garbled = _of(check_hygiene(model, qa_rules).findings, "DOC-HYG-002")
    assert len(garbled) == 1
    assert "分布图" in garbled[0].evidence.items[0].excerpt


# ── 重复: 段落层只比正文, 整页层只比文字类区域 ──────────────────────────────


def test_repeated_table_text_not_reported_as_duplicate_paragraph(qa_rules) -> None:
    """续表的表头与表注天然逐页重复, 不是冗余段落。"""
    pages = {
        page: [
            _stack(page, KIND_BODY, 0.10, [f"第{page}页检测安排说明。"]),
            _table(page, 0.30, ("序号", "检测项目", "执行标准"), (_TABLE_NOTE,)),
        ]
        for page in (2, 3)
    }
    codes = {f.rule_code for f in check_duplicate(_layout_model(pages), qa_rules).findings}
    assert not codes & {"DOC-DUP-001", "DOC-DUP-003", "DOC-DUP-004"}
    assert _of(check_duplicate(_legacy_model(pages), qa_rules).findings, "DOC-DUP-001")


def test_near_identical_table_rows_not_reported_second_entity(qa_rules) -> None:
    tweaked = _TABLE_NOTE.replace("原件留存备查", "原件存档备查")
    pages = {
        6: [_table(6, 0.30, ("序号", "检测项目"), (_TABLE_NOTE,))],
        7: [_table(7, 0.30, ("序号", "检测项目"), (tweaked,))],
    }
    codes = {f.rule_code for f in check_duplicate(_layout_model(pages), qa_rules).findings}
    assert not codes & {"DOC-DUP-001", "DOC-DUP-004"}
    assert _of(check_duplicate(_legacy_model(pages), qa_rules).findings, "DOC-DUP-004")


def test_repeated_body_region_text_reported(qa_rules) -> None:
    model = _layout_model({
        page: [_stack(page, KIND_BODY, 0.20, [_LONG_BODY]), _table(page, 0.50, ("序号", f"第{page}页"))]
        for page in (4, 9)
    })
    dups = _of(check_duplicate(model, qa_rules).findings, "DOC-DUP-001")
    assert len(dups) == 1
    assert {item.page for item in dups[0].evidence.items} == {4, 9}


def test_near_duplicate_body_regions_second_entity(qa_rules) -> None:
    base = (
        "施工现场设置专职安全员三名，每日开工前进行安全技术交底并留存签字记录，"
        "每周组织一次安全隐患排查，发现问题限期整改并复查闭环。"
    )
    model = _layout_model({
        5: [_stack(5, KIND_BODY, 0.20, [base])],
        8: [_stack(8, KIND_BODY, 0.20, [base.replace("三名", "四名")])],
    })
    findings = check_duplicate(model, qa_rules).findings
    near = _of(findings, "DOC-DUP-004")
    assert len(near) == 1
    assert {item.page for item in near[0].evidence.items} == {5, 8}
    assert _of(findings, "DOC-DUP-001") == []


def _page_with_furniture(page: int, header: str, footer: str) -> list[QaRegion]:
    return [
        _stack(page, KIND_FURNITURE, 0.02, [header]),
        _stack(page, KIND_BODY, 0.20, _PAGE_BODY),
        _stack(page, KIND_FURNITURE, 0.94, [footer]),
    ]


def test_whole_page_duplicate_ignores_footer_page_number(qa_rules) -> None:
    pages = {
        6: _page_with_furniture(6, "第三章 施工方案", "第 6 页"),
        7: _page_with_furniture(7, "第三章 施工方案", "第 7 页"),
    }
    dup_pages = _of(check_duplicate(_layout_model(pages), qa_rules).findings, "DOC-DUP-002")
    assert len(dup_pages) == 1
    assert dup_pages[0].evidence.items[0].excerpt == "\n".join(_PAGE_BODY)[:120]
    assert _of(check_duplicate(_legacy_model(pages), qa_rules).findings, "DOC-DUP-002") == []


def test_whole_page_duplicate_ignores_running_header_second_entity(qa_rules) -> None:
    pages = {
        10: _page_with_furniture(10, "第三章 施工方案", "投标文件"),
        31: _page_with_furniture(31, "第五章 质量保证", "投标文件"),
    }
    dup_pages = _of(check_duplicate(_layout_model(pages), qa_rules).findings, "DOC-DUP-002")
    assert len(dup_pages) == 1
    assert {item.page for item in dup_pages[0].evidence.items} == {10, 31}
    assert _of(check_duplicate(_legacy_model(pages), qa_rules).findings, "DOC-DUP-002") == []


# ── 语言: 正文跨页缝合; 表格逐行查标点、括号按页结算; 公式/页眉页脚不读 ─────


def test_halfwidth_semicolon_in_table_row_reported(qa_rules) -> None:
    """找回真问题: 旧字形通道剔除了表格块, 表内半角分号漏检。"""
    model = _layout_model({3: [
        _stack(3, KIND_BODY, 0.10, ["涂层性能要求见下表。"]),
        _table(3, 0.30, ("耐腐蚀性", "涂层具有良好的抗盐雾腐蚀的能力;该涂层附着力不低于5MPa")),
    ]})
    mixed = _of(check_language(model, qa_rules).findings, "DOC-LANG-003")
    assert len(mixed) == 1
    assert mixed[0].evidence.items[0].page == 3
    assert "能力;该涂层" in mixed[0].evidence.items[0].excerpt


def test_repeated_comma_in_table_row_reported_second_entity(qa_rules) -> None:
    model = _layout_model({4: [_table(4, 0.30, ("售后服务", "接到报修后2小时内响应，，24小时内到场处理"))]})
    repeats = _of(check_language(model, qa_rules).findings, "DOC-LANG-002")
    assert len(repeats) == 1
    assert "「，，」" in repeats[0].message


def test_formula_region_brackets_not_checked(qa_rules) -> None:
    pages = {5: [
        _stack(5, KIND_BODY, 0.10, ["离子平衡误差按下式计算。"]),
        _stack(5, KIND_FORMULA, 0.30, ["(%)=[(Σmeq⁺-Σmeq⁻)", "Σmeq⁺+Σmeq⁻)]×100"]),
    ]}
    assert _of(check_language(_layout_model(pages), qa_rules).findings, "DOC-LANG-001") == []
    assert _of(check_language(_legacy_model(pages), qa_rules).findings, "DOC-LANG-001")


def test_header_region_brackets_not_checked_second_entity(qa_rules) -> None:
    pages = {6: [
        _stack(6, KIND_FURNITURE, 0.02, ["某污水处理厂提标改造项目（技术标"]),
        _stack(6, KIND_BODY, 0.20, ["本章说明施工总体部署。"]),
    ]}
    assert _of(check_language(_layout_model(pages), qa_rules).findings, "DOC-LANG-001") == []
    assert _of(check_language(_legacy_model(pages), qa_rules).findings, "DOC-LANG-001")


def test_bracket_wrapping_across_table_rows_not_unbalanced(qa_rules) -> None:
    """单元格文字折行跨表格行: 括号在下一行才闭合, 按页结算不报。"""
    model = _layout_model({7: [_table(7, 0.30, ("1", "水质检测（含pH值、"), ("电导率、溶解氧）",))]})
    assert _of(check_language(model, qa_rules).findings, "DOC-LANG-001") == []


def test_ascii_bracket_wrapping_across_table_rows_second_entity(qa_rules) -> None:
    model = _layout_model({8: [_table(8, 0.30, ("2", "包装标识(含净含量、厂名、"), ("厂址、生产日期)",))]})
    assert _of(check_language(model, qa_rules).findings, "DOC-LANG-001") == []


def test_unclosed_bracket_in_table_still_reported(qa_rules) -> None:
    model = _layout_model({9: [_table(9, 0.30, ("备注", "详见附件（资质证书复印件"), ("联系人", '<PERSON_A>'))]})
    unbalanced = _of(check_language(model, qa_rules).findings, "DOC-LANG-001")
    assert len(unbalanced) == 1
    assert unbalanced[0].evidence.items[0].page == 9


def test_layout_pages_checked_without_visual_paragraphs(qa_rules) -> None:
    model = _layout_model({10: [_table(10, 0.30, ("承诺", "我方承诺,保证工期"))]})
    model.visual_paragraphs = {}
    outcome = check_language(model, qa_rules)
    assert outcome.status.value == "ok"
    assert len(_of(outcome.findings, "DOC-LANG-003")) == 1


def test_rule_cap_shared_by_body_and_table_units(qa_rules) -> None:
    body = [f"第{i}段承诺,按期完工。" for i in range(6)]
    rows = [(f"第{i}项", f"按期完成,保证质量第{i}条") for i in range(6)]
    model = _layout_model({11: [_stack(11, KIND_BODY, 0.05, body), _table(11, 0.40, *rows)]})
    mixed = _of(check_language(model, qa_rules).findings, "DOC-LANG-003")
    assert len(mixed) == 8
    assert sum("按期完成" in f.evidence.items[0].excerpt for f in mixed) == 2


# ── 跨卷特征: 段落哈希只取正文段落 ─────────────────────────────────────────


def test_paragraph_hash_features_skip_table_rows(qa_rules) -> None:
    model = _layout_model({2: [
        _stack(2, KIND_BODY, 0.10, [_LONG_BODY]),
        _table(2, 0.40, (_TABLE_NOTE,)),
    ]})
    hashes = _build_features(model, qa_rules)["paragraph_hashes"]
    assert [entry["excerpt"] for entry in hashes.values()] == [_LONG_BODY[:120]]


def test_paragraph_hash_features_skip_furniture_second_entity(qa_rules) -> None:
    footer = (
        "本投标文件所载技术方案、报价及人员信息仅供本次招标评审使用，未经投标人书面许可，"
        "任何单位和个人不得复制、摘录或向第三方披露。"
    )
    assert len(normalize_for_hash(footer)) > 60
    model = _layout_model({3: [
        _stack(3, KIND_BODY, 0.10, [_LONG_BODY]),
        _stack(3, KIND_FURNITURE, 0.94, [footer]),
    ]})
    hashes = _build_features(model, qa_rules)["paragraph_hashes"]
    assert [entry["excerpt"] for entry in hashes.values()] == [_LONG_BODY[:120]]
