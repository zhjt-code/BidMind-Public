"""结构类检查器按版面区域取字(表格隔离后续): 空章节、编号家族、印刷页码、交叉引用。

机制: 有版面的页经 region_text 取数, 没有版面的页仍走旧来源(各检查器既有测试覆盖)。
每条规则至少两个同形态、不同实体的夹具, 外加仍须命中的对照。
夹具按生产口径构造: 旧块已剔除表格区; 旧页文本按最坏情况含全部文字(表格、漏剔的页眉页脚)。
"""

from __future__ import annotations

from typing import Optional

import pytest

from app.core.document_qa.checks.numbering_check import check_numbering
from app.core.document_qa.checks.reference_check import check_reference
from app.core.document_qa.checks.structure_check import check_structure
from app.core.document_qa.contracts import BBox, DocumentModel, QaBlock, QaHeading, QaRegion
from app.core.document_qa.region_text import (
    KIND_BODY,
    KIND_CAPTION,
    KIND_FURNITURE,
    KIND_TABLE,
    KIND_TITLE,
    KIND_VISUAL,
)
from tests.document_qa.conftest import make_line, make_model, make_region, with_regions

_ROW = 0.025


def _region(page: int, kind: str, bbox: BBox, *texts: str, label: str = "") -> QaRegion:
    """区域内自上而下逐行排字, 每行一个视觉行。"""
    lines = [
        make_line(page, text, (bbox[0], bbox[1] + i * _ROW, bbox[2], bbox[1] + i * _ROW + 0.02))
        for i, text in enumerate(texts)
    ]
    return make_region(page, kind, bbox, lines, label=label)


def _layout_model(
    regions: dict[int, list[QaRegion]], headings: Optional[list[QaHeading]] = None,
) -> DocumentModel:
    model = make_model(
        body_texts={
            page: "\n".join(region.text for region in items if region.lines)
            for page, items in regions.items()
        },
        headings=headings,
    )
    model.blocks = [
        QaBlock(
            page=region.page, text=region.text, bbox=region.bbox,
            spans=tuple(span for line in region.lines for span in line.spans), lines=region.lines,
        )
        for items in regions.values() for region in items
        if region.lines and region.kind != KIND_TABLE
    ]
    return with_regions(model, regions)


def _outline_model(regions: dict[int, list[QaRegion]]) -> DocumentModel:
    """标题取自标题区(同 headings_from_pages): 标题框即标题区框。"""
    headings: list[QaHeading] = []
    for page in sorted(regions):
        for region in regions[page]:
            if region.kind != KIND_TITLE:
                continue
            number, title = region.text.split(" ", 1)
            headings.append(QaHeading(
                page=page, level=number.count(".") + 1, number=number, title=title,
                bbox=region.bbox, kind="decimal",
            ))
    return _layout_model(regions, headings)


# ── 空章节: 表格/图片区算内容, 页眉页脚不算 ─────────────────────────────────


def _empty_section_anchors(model: DocumentModel, rules: dict) -> list[str]:
    return [f.anchor for f in check_structure(model, rules).findings if f.rule_code == "DOC-STR-002"]


@pytest.mark.parametrize("regions", [
    pytest.param({6: [
        _region(6, KIND_TITLE, (0.10, 0.05, 0.60, 0.08), "7.2 主要人员配置表"),
        _region(6, KIND_TABLE, (0.08, 0.10, 0.92, 0.55), "姓名 职称 专业 从业年限", '<PERSON_A> 高级工程师 石油工程 15年'),
        _region(6, KIND_TITLE, (0.10, 0.60, 0.50, 0.63), "7.3 公司简介"),
        _region(6, KIND_BODY, (0.08, 0.66, 0.92, 0.80), "我公司成立于2005年，专注油田地面工程技术服务。"),
    ]}, id="staff_table"),
    pytest.param({
        20: [
            _region(20, KIND_BODY, (0.08, 0.10, 0.92, 0.70), "本节按阶段分解进度目标，明确各阶段起止时间与交付物。"),
            _region(20, KIND_TITLE, (0.10, 0.80, 0.60, 0.83), "8.6.1.1 分阶段进度表"),
        ],
        21: [
            _region(21, KIND_TABLE, (0.08, 0.05, 0.92, 0.50), "阶段 起止时间 主要工作", "第一阶段 1-3月 现场踏勘与方案设计"),
            _region(21, KIND_TITLE, (0.10, 0.55, 0.70, 0.58), "8.6.1.2 关键里程碑节点控制"),
            _region(21, KIND_BODY, (0.08, 0.60, 0.92, 0.80), "里程碑节点按合同工期倒排，逐月考核。"),
        ],
    }, id="schedule_table_next_page"),
])
def test_section_with_only_table_is_not_empty(qa_rules, regions):
    """章节下只有表格(旧块里表格已剔除)不报空章节: 「7.2 主要人员配置表」「8.6.1.1 分阶段进度表」。"""
    assert _empty_section_anchors(_outline_model(regions), qa_rules) == []


@pytest.mark.parametrize(("label", "first", "second"), [
    ("image", "9.1 现场照片", "9.2 设备清单"),
    ("seal", "10.3 授权委托书", "10.4 资格声明"),
])
def test_section_with_only_visual_region_is_not_empty(qa_rules, label, first, second):
    """图片/印章区没有文件文字, 单独出现也算章节内容(页面未统计到图片时同样成立)。"""
    regions = {9: [
        _region(9, KIND_TITLE, (0.10, 0.05, 0.50, 0.08), first),
        make_region(9, KIND_VISUAL, (0.10, 0.10, 0.90, 0.60), [], label=label),
        _region(9, KIND_TITLE, (0.10, 0.65, 0.50, 0.68), second),
    ]}
    assert _empty_section_anchors(_outline_model(regions), qa_rules) == []


def test_layout_heading_followed_by_same_level_heading_still_empty(qa_rules):
    """对照: 版面页上标题紧跟同级标题仍报空章节(标题按标题区框定位)。"""
    regions = {30: [
        _region(30, KIND_TITLE, (0.10, 0.10, 0.50, 0.13), "5.1 质量目标"),
        _region(30, KIND_TITLE, (0.10, 0.15, 0.60, 0.18), "5.2 质量保证体系"),
        _region(30, KIND_BODY, (0.08, 0.20, 0.92, 0.40), "我公司建立了覆盖设计、采购、施工全过程的质量保证体系。"),
    ]}
    assert _empty_section_anchors(_outline_model(regions), qa_rules) == ["empty_section:5.1 质量目标"]


def test_page_furniture_between_headings_is_not_content(qa_rules):
    """对照第二实体: 跨页标题之间只有页码与页眉仍报空章节(页眉页脚不算内容)。"""
    regions = {
        10: [
            _region(10, KIND_BODY, (0.08, 0.10, 0.92, 0.70), "本章说明施工部署与资源配置。"),
            _region(10, KIND_TITLE, (0.10, 0.80, 0.50, 0.83), "3.1 施工方案"),
            _region(10, KIND_FURNITURE, (0.45, 0.94, 0.55, 0.96), "10", label="number"),
        ],
        11: [
            _region(11, KIND_FURNITURE, (0.10, 0.02, 0.90, 0.04), "<ORG_7>投标文件", label="header"),
            _region(11, KIND_TITLE, (0.10, 0.08, 0.50, 0.11), "3.2 进度计划"),
            _region(11, KIND_BODY, (0.08, 0.14, 0.92, 0.40), "总工期120日历天，分三个阶段组织实施。"),
        ],
    }
    assert _empty_section_anchors(_outline_model(regions), qa_rules) == ["empty_section:3.1 施工方案"]


# ── 编号家族: 表格/正文里同形的文字不是题注 ─────────────────────────────────


def _family_findings(model: DocumentModel, rules: dict, label: str) -> list:
    return [f for f in check_numbering(model, rules).findings if f.title.startswith(f"{label}编号")]


def test_table_cell_figure_label_is_not_caption(qa_rules):
    """表格里的「图5 示意」不是图题: 图1-图3 连续, 不报缺图4。"""
    regions = {
        3: [_region(3, KIND_CAPTION, (0.30, 0.60, 0.70, 0.63), "图1 施工流程图", label="figure_title")],
        4: [_region(4, KIND_CAPTION, (0.30, 0.60, 0.70, 0.63), "图2 组织机构图", label="figure_title")],
        5: [
            _region(5, KIND_CAPTION, (0.30, 0.10, 0.70, 0.13), "图3 进度网络图", label="figure_title"),
            _region(5, KIND_TABLE, (0.08, 0.20, 0.92, 0.60), "序号 附图说明", "图5 示意"),
        ],
    }
    assert _family_findings(_layout_model(regions), qa_rules, "图") == []


def test_body_line_table_label_is_not_caption_second_entity(qa_rules):
    """同形态第二实体: 正文区行首的「表4 汇总」不是表题, 表1、表2 连续不报缺表3。"""
    regions = {
        7: [
            _region(7, KIND_CAPTION, (0.30, 0.10, 0.70, 0.13), "表1 主要人员配置表", label="figure_title"),
            _region(7, KIND_TABLE, (0.08, 0.15, 0.92, 0.50), "姓名 职称", '<PERSON_A> 高级工程师'),
            _region(7, KIND_CAPTION, (0.30, 0.55, 0.70, 0.58), "表2 主要设备配置表", label="figure_title"),
        ],
        8: [_region(8, KIND_BODY, (0.08, 0.10, 0.92, 0.30), "各阶段投入按月统计如下。", "表4 汇总")],
    }
    assert _family_findings(_layout_model(regions), qa_rules, "表") == []


@pytest.mark.parametrize(("kind", "label", "texts", "actual"), [
    (KIND_CAPTION, "图", ("图1 施工流程图", "图2 组织机构图", "图4 应急流程图"), "缺 3"),
    (KIND_CAPTION, "表", ("表1 主要人员配置表", "表3 主要设备配置表"), "缺 2"),
    (KIND_TITLE, "附件", ("附件1：营业执照", "附件2：资质证书", "附件4：业绩合同"), "缺 3"),
    (KIND_TITLE, "附录", ("附录A 术语定义", "附录C 检测方法"), "缺 2"),
])
def test_labeled_regions_still_feed_family_gaps(qa_rules, kind, label, texts, actual):
    """图表题区的图/表号、标题区的附件/附录号仍入家族序列, 真缺号照报。"""
    regions = {
        page: [_region(page, kind, (0.30, 0.10, 0.70, 0.13), text)]
        for page, text in enumerate(texts, start=1)
    }
    findings = _family_findings(_layout_model(regions), qa_rules, label)
    assert [f.evidence.actual for f in findings if f.rule_code == "DOC-NUM-001"] == [actual]


# ── 印刷页码: 只取页眉页脚页码区 ───────────────────────────────────────────


def _paged_model(numbers: dict[int, str], bottom_body: dict[int, str]) -> DocumentModel:
    """numbers 为页码区(版面 number 标签)文字; bottom_body 为落在页底部带的正文区行。"""
    regions: dict[int, list[QaRegion]] = {}
    for page in sorted(set(numbers) | set(bottom_body)):
        items = [_region(page, KIND_BODY, (0.08, 0.10, 0.92, 0.50), "本页说明施工组织安排与资源配置。")]
        if page in bottom_body:
            items.append(_region(page, KIND_BODY, (0.40, 0.90, 0.60, 0.93), bottom_body[page]))
        if page in numbers:
            items.append(_region(page, KIND_FURNITURE, (0.40, 0.95, 0.60, 0.97), numbers[page], label="number"))
        regions[page] = items
    return _layout_model(regions)


def _printed_findings(model: DocumentModel, rules: dict) -> list:
    return [f for f in check_numbering(model, rules).findings if f.title.startswith("印刷页码")]


def test_layout_page_numbers_come_from_furniture_regions(qa_rules):
    """页码区两种形态(「12」「第 13 页」)都采到: 11、12、13、15 只缺 14。"""
    model = _paged_model({1: "11", 2: "12", 3: "第 13 页", 4: "15"}, {})
    model.blocks = []  # 旧块里没有页码, 只能从页码区采到
    missing = [f for f in _printed_findings(model, qa_rules) if f.rule_code == "DOC-NUM-001"]
    assert [f.evidence.actual for f in missing] == ["缺 14"]


@pytest.mark.parametrize(("numbers", "bottom_body"), [
    pytest.param({1: "11", 2: "12", 3: "13", 4: "14"}, {5: "20"}, id="digits_false_gap"),
    pytest.param({1: "第 3 页", 2: "第 4 页", 3: "第 5 页"}, {4: "4"}, id="cn_page_false_duplicate"),
])
def test_body_number_in_bottom_band_is_not_printed_page(qa_rules, numbers, bottom_body):
    """页底部带里正文区的短数字不是印刷页码: 采进来会造成假缺号(11-14 接 20)或假重号(第4页 与 4)。"""
    assert _printed_findings(_paged_model(numbers, bottom_body), qa_rules) == []


# ── 交叉引用: 扫正文与表格行, 不扫页眉页脚 ──────────────────────────────────


def _reference_findings(model: DocumentModel, rules: dict) -> list:
    return [f for f in check_reference(model, rules).findings if f.rule_code == "DOC-REF-001"]


@pytest.mark.parametrize(("row", "target"), [
    ("离心泵 详见表9", "表9"),
    ("安全生产许可证 详见附件5", "附件5"),
])
def test_table_row_citation_is_scanned(qa_rules, row, target):
    """表格行里的引用照样扫描: 文中没有表9/附件5 时报引用目标未找到。"""
    regions = {12: [
        _region(12, KIND_CAPTION, (0.30, 0.05, 0.70, 0.08), "表8 主要设备参数", label="figure_title"),
        _region(12, KIND_TABLE, (0.08, 0.10, 0.92, 0.60), "名称 说明", row),
    ]}
    findings = _reference_findings(_layout_model(regions), qa_rules)
    assert len(findings) == 1
    assert target in findings[0].message
    assert findings[0].evidence.items[0].page == 12


@pytest.mark.parametrize(("label", "bbox", "text"), [
    ("header", (0.10, 0.02, 0.90, 0.04), "技术标（详见附件8）"),
    ("footer", (0.10, 0.94, 0.90, 0.96), "设备参数参见表7"),
])
def test_citation_only_in_page_furniture_is_not_scanned(qa_rules, label, bbox, text):
    """页眉/页脚里的引用句不扫描(旧页文本漏剔页眉页脚时也不报)。"""
    regions = {14: [
        _region(14, KIND_BODY, (0.08, 0.10, 0.92, 0.50), "本章说明施工组织安排与资源配置。"),
        _region(14, KIND_FURNITURE, bbox, text, label=label),
    ]}
    assert _reference_findings(_layout_model(regions), qa_rules) == []


@pytest.mark.parametrize(("title", "citation"), [
    ("附件1：营业执照", "营业执照复印件详见附件1。"),
    ("附录A 术语定义", "相关术语参见附录A。"),
])
def test_attachment_title_region_resolves_reference(qa_rules, title, citation):
    """附件/附录标签在标题区(不在图表题区), 仍是引用目标。"""
    regions = {
        2: [_region(2, KIND_BODY, (0.08, 0.10, 0.92, 0.30), citation)],
        30: [_region(30, KIND_TITLE, (0.30, 0.05, 0.70, 0.08), title, label="paragraph_title")],
    }
    assert _reference_findings(_layout_model(regions), qa_rules) == []
