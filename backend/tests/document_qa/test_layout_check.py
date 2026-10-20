"""版式离群: 异字体 + 异字号(第二实体) 报出; 图注小字不误报; 无正文块跳过;
版面页只看正文区: 公式区、表格区(第二实体)不误报, 表格字数不改主流样式。"""

from app.core.document_qa.checks.layout_check import check_layout
from app.core.document_qa.contracts import (
    CheckerStatus,
    DocumentModel,
    QaBlock,
    QaHeading,
    QaLine,
    QaRegion,
    QaSpan,
)
from app.core.document_qa.model_builder import _build_style_profile
from app.core.document_qa.region_text import KIND_BODY, KIND_FORMULA, KIND_TABLE

from tests.document_qa.conftest import make_line, make_model, make_region, with_regions

_BODY = (
    "我公司将严格按照招标文件及施工组织设计的总体部署组织实施本项目，"
    "确保工程质量达到合格标准。"
)
_OUTLIER_FONT = (
    "本段改用黑体排版以突出所谓重点内容，但属于正文叙述而非标题，应当被版式检查捕获。"
)
_OUTLIER_SIZE = (
    "本段仍用宋体但字号明显大于正文，用于夹具覆盖字号离群这一同形态第二实体，请核对。"
)
_CAPTION = (
    "图3 现场平面布置示意图（仅供示意，不作为施工依据）以及图例说明补足长度避免被短块跳过。"
)


def _span(page: int, text: str, *, font: str = "SimSun", size: float = 10.5, y: float = 0.3) -> QaSpan:
    return QaSpan(
        page=page, text=text, bbox=(0.1, y, 0.9, y + 0.04),
        font=font, size=size, flags=0, color=0,
    )


def _block(page: int, text: str, *, font: str = "SimSun", size: float = 10.5, y: float = 0.3) -> QaBlock:
    return QaBlock(
        page=page, text=text, bbox=(0.1, y, 0.9, y + 0.05),
        spans=(_span(page, text, font=font, size=size, y=y),),
    )


def _model(blocks: list[QaBlock], headings: list[QaHeading] | None = None) -> DocumentModel:
    return DocumentModel(
        document_id=1, bid_part=None, filename="t.pdf", page_count=4,
        blocks=blocks,
        headings=list(headings or []),
        style_profile=_build_style_profile(blocks),
    )


def test_outlier_font_reported(qa_rules):
    blocks = [_block(1, _BODY, y=0.2 + i * 0.05) for i in range(6)]
    blocks.append(_block(2, _OUTLIER_FONT, font="SimHei", y=0.4))
    outcome = check_layout(_model(blocks), qa_rules)
    hits = [f for f in outcome.findings if f.rule_code == "DOC-LAY-001"]
    assert len(hits) == 1
    assert hits[0].evidence.items[0].page == 2
    assert hits[0].evidence.items[0].bbox is not None


def test_outlier_size_second_entity(qa_rules):
    """同形态第二实体: 主流字体但字号离群同样命中 DOC-LAY-001。"""
    blocks = [_block(1, _BODY, y=0.2 + i * 0.05) for i in range(6)]
    blocks.append(_block(3, _OUTLIER_SIZE, size=14.0, y=0.4))
    outcome = check_layout(_model(blocks), qa_rules)
    hits = [f for f in outcome.findings if f.rule_code == "DOC-LAY-001"]
    assert len(hits) == 1
    assert hits[0].evidence.items[0].page == 3


def test_same_family_near_threshold_not_reported(qa_rules):
    """同家族字号差 1.0pt 落在容差带内, 不报离群。"""
    blocks = [_block(1, _BODY, y=0.2 + i * 0.05) for i in range(6)]
    blocks.append(_block(2, _BODY + "十一磅补充足够长度避免被短块跳过。", size=11.0, y=0.4))
    outcome = check_layout(_model(blocks), qa_rules)
    assert outcome.findings == []


def test_toc_zone_hei_not_reported(qa_rules):
    blocks = [_block(1, _BODY, y=0.2 + i * 0.05) for i in range(6)]
    blocks.append(_block(2, _OUTLIER_FONT, font="SimHei", y=0.4))
    model = _model(blocks)
    model.toc_zone = {2}
    outcome = check_layout(model, qa_rules)
    assert outcome.findings == []


def test_caption_small_size_not_reported(qa_rules):
    blocks = [_block(1, _BODY, y=0.2 + i * 0.05) for i in range(6)]
    blocks.append(_block(2, _CAPTION, size=8.0, y=0.85))
    outcome = check_layout(_model(blocks), qa_rules)
    assert outcome.findings == []


def test_layout_skipped_without_blocks(qa_rules):
    model = DocumentModel(document_id=1, bid_part=None, filename="t.pdf", page_count=1)
    outcome = check_layout(model, qa_rules)
    assert outcome.status.value == "skipped"
    assert outcome.findings == []


# ── 版面页: 只有正文区参与主流样式与离群 ─────────────────────────────────────

_FORMULA = "(%)=[(Σmeq⁺-Σmeq⁻)/(Σmeq⁺+Σmeq⁻)]×100%，Na⁺,K⁺,Ca²⁺,Mg²⁺,Cl⁻,SO₄²⁻,HCO₃⁻"
_TABLE_ROW = "TP-2026-018 Water quality analyzer 12 pcs 97200.00 1166400.00"
_OUTLIER_KAI = "本段改用楷体排版以突出所谓重点内容，但属于正文叙述而非标题，应当被捕获。"


def _line(text: str, top: float, *, font: str = "SimSun") -> QaLine:
    return make_line(1, text, (0.1, top, 0.9, top + 0.02), font=font)


def _region(kind: str, lines: list[QaLine]) -> QaRegion:
    bbox = (
        min(line.bbox[0] for line in lines), min(line.bbox[1] for line in lines),
        max(line.bbox[2] for line in lines), max(line.bbox[3] for line in lines),
    )
    return make_region(1, kind, bbox, lines)


def _body_regions(count: int = 4) -> list[QaRegion]:
    return [_region(KIND_BODY, [_line(_BODY, 0.05 + i * 0.03)]) for i in range(count)]


def _layout_model(regions: list[QaRegion]) -> DocumentModel:
    """版面页模型; 旧块仍含该页全部文字行(同生产: 公式行、未整块剔除的表格行都留在 blocks 里)。"""
    model = make_model(body_texts={1: ""})
    model.blocks = [
        QaBlock(
            page=1, text=region.text, bbox=region.bbox,
            spans=tuple(span for line in region.lines for span in line.spans), lines=region.lines,
        )
        for region in regions
    ]
    return with_regions(model, {1: regions})


def test_formula_region_font_not_reported(qa_rules):
    """公式区的 Times New Roman 化学式不是正文, 不报离群。"""
    formula = _region(KIND_FORMULA, [_line(_FORMULA, 0.5, font="TimesNewRomanPSMT")])
    outcome = check_layout(_layout_model([*_body_regions(), formula]), qa_rules)
    assert outcome.status == CheckerStatus.OK
    assert outcome.findings == []


def test_table_region_font_not_reported_second_entity(qa_rules):
    """同形态第二实体: 表格区的 Arial 编号/金额行同样不报离群。"""
    table = _region(KIND_TABLE, [_line(_TABLE_ROW, 0.5 + i * 0.03, font="Arial") for i in range(2)])
    outcome = check_layout(_layout_model([*_body_regions(), table]), qa_rules)
    assert outcome.status == CheckerStatus.OK
    assert outcome.findings == []


def test_body_region_outlier_still_reported_on_layout_page(qa_rules):
    """对照: 版面页正文区里连续楷体长段仍报 DOC-LAY-001。"""
    outlier = _line(_OUTLIER_KAI, 0.53, font="KaiTi")
    body = _region(KIND_BODY, [_line(_BODY, 0.5), outlier])
    outcome = check_layout(_layout_model([*_body_regions(), body]), qa_rules)
    hits = [f for f in outcome.findings if f.rule_code == "DOC-LAY-001"]
    assert len(hits) == 1
    assert hits[0].evidence.items[0].bbox == outlier.bbox
    assert (hits[0].evidence.expected, hits[0].evidence.actual) == ("song|12.0", "kai|12.0")


def test_table_chars_not_counted_in_majority_style(qa_rules):
    """表格区 Arial 字数多于正文时主流样式仍是正文宋体: 楷体段相对宋体报出, 宋体正文不被反报。"""
    table = _region(KIND_TABLE, [_line(_TABLE_ROW, 0.2 + i * 0.03, font="Arial") for i in range(12)])
    body_regions = [
        *_body_regions(2),
        _region(KIND_BODY, [_line(_BODY, 0.6), _line(_OUTLIER_KAI, 0.63, font="KaiTi")]),
    ]
    assert len(table.text) > sum(len(region.text) for region in body_regions)
    outcome = check_layout(_layout_model([*body_regions, table]), qa_rules)
    hits = [f for f in outcome.findings if f.rule_code == "DOC-LAY-001"]
    assert len(hits) == 1
    assert (hits[0].evidence.expected, hits[0].evidence.actual) == ("song|12.0", "kai|12.0")
    assert "主流 song|12.0" in hits[0].message


def test_color_indent_margin_and_their_negatives(qa_rules):
    """颜色、缩进、边距离群。单字着色、正常首行缩进、边距接近主流都不报。"""
    from app.core.document_qa.contracts import QaBlock

    def block(page: int, text: str, *, x0: float = 0.12, x1: float = 0.88, color: int = 0, y: float = 0.3):
        span = QaSpan(
            page=page, text=text, bbox=(x0, y, x1, y + 0.04),
            font="SimSun", size=10.5, flags=0, color=color,
        )
        return QaBlock(page=page, text=text, bbox=(x0, y, x1, y + 0.05), spans=(span,))

    body = "我公司将严格按照招标文件及施工组织设计的总体部署组织实施本项目，确保工程质量达到合格标准。"
    blue = "本段正文改为蓝色以示强调但是仍属于正文叙述应当被颜色检查捕获。" * 2
    red = "本段正文改为红色用于覆盖颜色离群的同形态第二实体请核对版式。" * 2
    wide = "本段左缩进明显大于同页其他正文应当被缩进检查捕获而不是当成标题。" * 2
    pages = {}
    blocks = []
    for page in range(1, 7):
        pages[page] = body
        blocks.append(block(page, body, y=0.2))
    blocks.append(block(1, blue, color=255, y=0.4))
    blocks.append(block(2, red, color=0xFF0000, y=0.45))
    blocks.append(block(3, "蓝", color=255, y=0.5))
    blocks.append(block(4, wide, x0=0.20, x1=0.9, y=0.55))
    blocks.append(block(
        5, "首行略缩进的正文仍应视为正常段落不应当报缩进异常。" * 2,
        x0=0.15, x1=0.88, y=0.6,
    ))
    blocks.append(block(6, body, x0=0.02, x1=0.98, y=0.3))
    model = make_model(body_texts=pages)
    model.blocks = blocks
    outcome = check_layout(model, qa_rules)
    messages = [item.message for item in outcome.findings if item.rule_code == "DOC-LAY-001"]
    assert any("颜色" in item and "第1页" in item for item in messages)
    assert any("颜色" in item and "第2页" in item for item in messages)
    assert any("缩进" in item for item in messages)
    assert any("边距" in item and "第6页" in item for item in messages)
    assert not any("第3页" in item and "颜色" in item for item in messages)


def test_layout_page_without_body_region_skipped(qa_rules):
    """版面页只有公式/表格区: 没有正文可比即跳过, 不看旧块。"""
    formula = _region(KIND_FORMULA, [_line(_FORMULA, 0.2, font="TimesNewRomanPSMT")])
    table = _region(KIND_TABLE, [_line(_TABLE_ROW, 0.5, font="Arial")])
    outcome = check_layout(_layout_model([formula, table]), qa_rules)
    assert outcome.status == CheckerStatus.SKIPPED
    assert outcome.findings == []
