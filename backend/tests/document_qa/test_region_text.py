"""按版面分块取字 (表格隔离未完成部分)。

机制: 文件文字行按中心点归入版面区域, 检查器只经 region_text 取数; 没有版面结果的页退回旧来源。
夹具覆盖同形态多实体: 表格/页眉/公式/页脚/图表题都不进正文; 两种水印(灰色平铺、旋转黑字)都剔除。
"""

from __future__ import annotations

from app.core.document_qa.contracts import QaBlock, QaLine
from app.core.document_qa.evidence_geom import locate_bbox
from app.core.document_qa.region_text import (
    KIND_BODY,
    KIND_CAPTION,
    KIND_TABLE,
    KIND_VISUAL,
    body_blocks,
    body_paragraphs,
    build_page_regions,
    page_texts,
    section_units,
    table_rows,
)
from app.core.document_qa.text_layer_watermark import strip_text_layer_watermark
from tests.document_qa.conftest import make_line, make_model, make_region, with_regions


def _block(page: int, lines: list[QaLine]) -> QaBlock:
    return QaBlock(
        page=page,
        text="\n".join(line.text for line in lines),
        bbox=(
            min(line.bbox[0] for line in lines), min(line.bbox[1] for line in lines),
            max(line.bbox[2] for line in lines), max(line.bbox[3] for line in lines),
        ),
        spans=tuple(span for line in lines for span in line.spans),
        lines=tuple(lines),
    )


def _layout_model(qa_rules, blocks: list[QaBlock], structure: dict) -> object:
    regions, unknown = build_page_regions(blocks, structure, qa_rules)
    model = make_model(body_texts={page: "旧页文本" for page in structure})
    model.unknown_region_labels = unknown
    return with_regions(model, regions)


def test_table_header_formula_lines_stay_out_of_body(qa_rules) -> None:
    lines = [
        make_line(1, '<ORG_B>投标文件', (0.1, 0.02, 0.6, 0.04)),
        make_line(1, "本项目实施周期为12个月。", (0.1, 0.20, 0.8, 0.22)),
        make_line(1, '投标人名称 <ORG_B>', (0.1, 0.40, 0.8, 0.42)),
        make_line(1, "(%)=[(Σmeq⁺-Σmeq⁻)]×100%", (0.2, 0.60, 0.7, 0.62)),
    ]
    structure = {1: [
        {"label": "header", "bbox": [0.08, 0.01, 0.9, 0.05]},
        {"label": "text", "bbox": [0.08, 0.18, 0.9, 0.24]},
        {"label": "table", "bbox": [0.08, 0.38, 0.9, 0.50]},
        {"label": "formula", "bbox": [0.15, 0.58, 0.8, 0.64]},
    ]}
    model = _layout_model(qa_rules, [_block(1, lines)], structure)

    assert body_paragraphs(model)[1] == ["本项目实施周期为12个月。"]
    assert table_rows(model)[1] == ['投标人名称 <ORG_B>']
    assert '<ORG_B>投标文件' not in page_texts(model)[1]
    assert "Σmeq" not in page_texts(model)[1]


def test_footer_and_caption_lines_stay_out_of_body(qa_rules) -> None:
    lines = [
        make_line(2, "表3 主要人员配置表", (0.3, 0.10, 0.7, 0.12)),
        make_line(2, "项目组由三名技术人员组成。", (0.1, 0.30, 0.8, 0.32)),
        make_line(2, "第 18 页", (0.45, 0.94, 0.55, 0.96)),
    ]
    structure = {2: [
        {"label": "figure_title", "bbox": [0.28, 0.09, 0.72, 0.13]},
        {"label": "text", "bbox": [0.08, 0.28, 0.9, 0.34]},
        {"label": "number", "bbox": [0.42, 0.93, 0.58, 0.97]},
    ]}
    model = _layout_model(qa_rules, [_block(2, lines)], structure)

    assert body_paragraphs(model)[2] == ["项目组由三名技术人员组成。"]
    assert [region.kind for region in model.regions[2]] == [KIND_CAPTION, KIND_BODY, "furniture"]


def test_lines_outside_every_region_are_kept_as_body(qa_rules) -> None:
    lines = [
        make_line(3, "版面漏框的一行正文，", (0.1, 0.70, 0.8, 0.72)),
        make_line(3, "续行在同一文字块里。", (0.1, 0.72, 0.8, 0.74)),
    ]
    structure = {3: [{"label": "text", "bbox": [0.08, 0.1, 0.9, 0.2]}]}
    model = _layout_model(qa_rules, [_block(3, lines)], structure)

    assert body_paragraphs(model)[3] == ["版面漏框的一行正文，续行在同一文字块里。"]


def test_caption_inside_table_box_goes_to_smaller_region(qa_rules) -> None:
    lines = [
        make_line(4, "表5 分阶段进度表", (0.3, 0.21, 0.7, 0.23)),
        make_line(4, "第一阶段 1-3月", (0.1, 0.40, 0.8, 0.42)),
    ]
    structure = {4: [
        {"label": "table", "bbox": [0.05, 0.20, 0.95, 0.60]},
        {"label": "figure_title", "bbox": [0.28, 0.205, 0.72, 0.235]},
    ]}
    model = _layout_model(qa_rules, [_block(4, lines)], structure)

    kinds = {region.kind: region.text for region in model.regions[4]}
    assert kinds[KIND_CAPTION] == "表5 分阶段进度表"
    assert kinds[KIND_TABLE] == "第一阶段 1-3月"


def test_inline_formula_box_does_not_swallow_the_text_line(qa_rules) -> None:
    """行内公式小框压着行中心(CK P61 形态): 整行仍归正文, 段落不缺行、括号不失配。"""
    lines = [
        make_line(9, "可对最表面几个纳米的成", (0.159, 0.197, 0.881, 0.212)),
        make_line(9, "分进行定性分析（如区分Fe₂O₃、Fe₃O₄）。通过深度剖析（结", (0.119, 0.224, 0.881, 0.240)),
        make_line(9, "合离子溅射）可以获得成分随深度的变化。", (0.119, 0.250, 0.881, 0.264)),
    ]
    structure = {9: [
        {"label": "text", "bbox": [0.114, 0.196, 0.881, 0.265]},
        {"label": "formula", "bbox": [0.498, 0.225, 0.544, 0.238]},
    ]}
    model = _layout_model(qa_rules, [_block(9, lines)], structure)

    [paragraph] = body_paragraphs(model)[9]
    assert "通过深度剖析（结合离子溅射）" in paragraph


def test_small_seal_box_on_a_line_keeps_line_in_body_second_entity(qa_rules) -> None:
    lines = [make_line(10, '投标人：<ORG_B>（公章）日期：2025年9月', (0.10, 0.45, 0.80, 0.47))]
    structure = {10: [
        {"label": "text", "bbox": [0.08, 0.44, 0.82, 0.48]},
        {"label": "seal", "bbox": [0.40, 0.40, 0.55, 0.52]},
    ]}
    model = _layout_model(qa_rules, [_block(10, lines)], structure)

    assert body_paragraphs(model)[10] == ['投标人：<ORG_B>（公章）日期：2025年9月']


def test_blank_lines_and_unboxed_page_number_are_not_layout_content(qa_rules) -> None:
    """只剩印刷页码的空白页(CK P18 形态): 空白行不参与对齐, 未入框的页码不算版面内容。"""
    from app.core.document_qa.region_text import layout_content_pages

    page_18 = [
        make_line(18, " ", (0.1, 0.05, 0.12, 0.06)),
        make_line(18, "16", (0.485, 0.918, 0.515, 0.935)),
    ]
    page_19 = [make_line(19, "·", (0.1, 0.5, 0.11, 0.51))]
    structure = {
        18: [{"label": "number", "bbox": [0.49, 0.92, 0.51, 0.93]}],
        19: [{"label": "footer", "bbox": [0.1, 0.92, 0.9, 0.95]}],
    }
    regions, _ = build_page_regions([_block(18, page_18), _block(19, page_19)], structure, qa_rules)

    assert layout_content_pages(regions) == set()
    assert all(line.text.strip() for region in regions[18] for line in region.lines)


def test_pages_without_layout_use_legacy_sources(qa_rules) -> None:
    model = make_model(body_texts={1: "第一页旧文本。", 2: "第二页旧文本。\n第二段。"})
    legacy_block = QaBlock(page=2, text="第二页旧文本。", bbox=(0.1, 0.1, 0.9, 0.2))
    model.blocks = [legacy_block]
    with_regions(model, {1: [make_region(
        1, KIND_BODY, (0.1, 0.1, 0.9, 0.2), [make_line(1, "第一页版面正文。", (0.1, 0.1, 0.9, 0.12))],
    )]})

    paragraphs = body_paragraphs(model)
    assert paragraphs[1] == ["第一页版面正文。"]
    assert paragraphs[2] == model.paragraphs[2]
    assert body_paragraphs(model, visual=True)[2] == model.visual_paragraphs[2]
    assert page_texts(model)[2] == model.body_texts[2]
    assert [block.page for block in body_blocks(model)] == [1, 2]
    assert body_blocks(model)[1] is legacy_block


def test_table_rows_join_cells_of_one_visual_row_only(qa_rules) -> None:
    table = make_region(5, KIND_TABLE, (0.1, 0.1, 0.9, 0.5), [
        make_line(5, '<ORG_B>', (0.40, 0.201, 0.80, 0.219)),
        make_line(5, "投标人名称", (0.10, 0.200, 0.35, 0.220)),
        make_line(5, "项目编号", (0.10, 0.250, 0.35, 0.270)),
        make_line(5, "TP-2026-018", (0.40, 0.250, 0.80, 0.270)),
    ])
    model = with_regions(make_model(body_texts={5: ""}), {5: [table]})

    assert table_rows(model)[5] == ['投标人名称 <ORG_B>', "项目编号 TP-2026-018"]


def test_section_units_count_tables_and_images_but_not_furniture(qa_rules) -> None:
    model = with_regions(make_model(body_texts={6: ""}), {6: [
        make_region(6, "title", (0.1, 0.05, 0.6, 0.08), [make_line(6, "7.2 主要人员配置表", (0.1, 0.05, 0.6, 0.08))]),
        make_region(6, KIND_TABLE, (0.1, 0.1, 0.9, 0.8), [make_line(6, "姓名 职称", (0.1, 0.12, 0.9, 0.14))]),
        make_region(6, KIND_VISUAL, (0.1, 0.8, 0.9, 0.9), []),
        make_region(6, "furniture", (0.45, 0.93, 0.55, 0.96), [make_line(6, "12", (0.45, 0.93, 0.55, 0.96))]),
    ]})
    assert [unit.kind for unit in section_units(model)] == ["title", KIND_TABLE, KIND_VISUAL]


def test_unknown_layout_label_is_body_and_counted(qa_rules) -> None:
    lines = [make_line(7, "新标签框里的正文。", (0.1, 0.3, 0.8, 0.32))]
    structure = {7: [{"label": "marginalia", "bbox": [0.08, 0.28, 0.9, 0.34]}]}
    model = _layout_model(qa_rules, [_block(7, lines)], structure)

    assert body_paragraphs(model)[7] == ["新标签框里的正文。"]
    assert model.unknown_region_labels == {"marginalia": 1}


def test_table_excerpt_is_located_through_region_lines(qa_rules) -> None:
    model = make_model(body_texts={8: ""})
    model.blocks = []  # 表格块已从旧块剔除
    row = make_line(8, "投标报价 97.2万元", (0.1, 0.5, 0.9, 0.52))
    with_regions(model, {8: [make_region(8, KIND_TABLE, (0.05, 0.4, 0.95, 0.7), [row])]})

    assert locate_bbox(model, 8, "投标报价 97.2万元") == (0.1, 0.5, 0.9, 0.52)


def _watermarked_pages(watermark: QaLine) -> list[QaBlock]:
    blocks: list[QaBlock] = []
    for page in range(1, 6):
        blocks.append(_block(page, [make_line(page, "本项目机密资料按合同约定管理。", (0.1, 0.2, 0.9, 0.22))]))
        mark = QaLine(
            page=page, text=watermark.text, bbox=watermark.bbox,
            spans=tuple(span.__class__(**{**span.__dict__, "page": page}) for span in watermark.spans),
            rotated=watermark.rotated,
        )
        blocks.append(_block(page, [mark]))
    return blocks


def test_gray_tiled_watermark_is_stripped_but_same_chars_in_body_stay() -> None:
    watermark = make_line(1, "机密文件", (0.2, 0.5, 0.8, 0.6), font="SimHei", size=40.0, color=0x999999)
    blocks, removed = strip_text_layer_watermark(_watermarked_pages(watermark), 5)

    assert removed == 20
    assert [block.text for block in blocks] == ["本项目机密资料按合同约定管理。"] * 5


def test_rotated_black_watermark_is_stripped_second_entity() -> None:
    watermark = make_line(1, "内部资料", (0.2, 0.5, 0.8, 0.6), font="SimHei", size=12.0, rotated=True)
    blocks, removed = strip_text_layer_watermark(_watermarked_pages(watermark), 5)

    assert removed == 20
    assert all("内部" not in block.text for block in blocks)
    assert len(blocks) == 5
