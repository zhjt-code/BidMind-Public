"""固定模板布局保真通道防御测试。

覆盖:
- 词级两遍行聚类: 半行偏移编号碎片回拼宿主行, 相邻真实文本行不串联
- 碎片水平槽位适配: 与宿主行既有词相撞的候选被降级
- 行内零风险前缀归一: "1） ." -> "1）"
- 模板定界: 标题行起点 + 下一模板标题终点
- 快照增补: raw_text_verbatim 写入与失败降级
- 契约优先级: verbatim > chunk 文本
- 填充直通: verbatim 模式跳过 normalize/表格重建, 原编号原样保留
- 渲染保真: Word/预览不重排编号, 表格原样透传
- rule_fallback 粘连长行标题前缀识别
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.writer.fixed_template.rule_fallback import build_rule_fallback_entries
from app.core.writer.fixed_template.section_locator import SectionRange
from app.core.writer.fixed_template.verbatim_extractor import (
    SnapshotVerbatimEnricher,
    _cluster_words_by_overlap,
    _find_next_template_heading,
    _find_title_line_index,
    _render_line_text,
    _table_to_html,
)
from app.core.writer.fixed_template_contract import (
    has_verbatim_source,
    resolve_fixed_template_source,
)
from app.core.writer.verbatim_section_renderer import (
    is_fixed_template_node,
    iter_verbatim_blocks,
    render_verbatim_preview_html,
    write_verbatim_docx_section,
)


def _word(text: str, x0: float, top: float, *, width: float = 10.0, height: float = 12.0) -> dict:
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + width,
        "top": top,
        "bottom": top + height,
    }


# ---------------------------------------------------------------------------
# 行聚类
# ---------------------------------------------------------------------------


def test_cluster_reattaches_offset_number_stub_to_host_line() -> None:
    """1559 页41 实测版式: 编号 "1 ." 半行下偏, 必须回拼宿主行而非独立成行。"""
    words = [
        _word("）", 103.6, 280.8),
        _word("我方将按规定履行义务；承诺按", 121.6, 280.8, width=300),
        _word("1", 97.6, 290.7),
        _word(".", 115.6, 290.7, width=4),
        _word("本招标文件须知前附表第", 76.6, 303.8, width=140),
        _word("条的要求签订合同。", 226.6, 303.8, width=120),
        _word("10", 211.6, 313.7, width=12),
    ]
    lines = [_render_line_text(row) for row in _cluster_words_by_overlap(words)]

    assert len(lines) == 2
    assert lines[0].startswith("1）我方将按")
    assert "第 10 条的要求签订合同" in lines[1].replace("  ", " ")


def test_cluster_keeps_adjacent_real_text_lines_apart() -> None:
    """相邻真实文本行(间距一整行)不允许被碎片桥接串联。"""
    words = [
        _word("地址：", 100.0, 100.0, width=40),
        _word("电子邮箱：", 100.0, 123.0, width=60),
        _word("电话：", 100.0, 146.0, width=40),
    ]
    lines = [_render_line_text(row) for row in _cluster_words_by_overlap(words)]

    assert lines == ["地址：", "电子邮箱：", "电话："]


def test_cluster_fragment_prefers_host_without_horizontal_collision() -> None:
    """1559 页43 实测版式: "1"/"2" 与上行文字水平相撞、与下行空隙适配,
    必须归入下行(注释正文行)而非上行。"""
    words = [
        # 上行: "注：采用转账方式递交投标保证金的，仅需提供第 、 项资料。采用"
        _word("注：采用转账方式递交投标保证金的，仅需提供第", 103.6, 608.1, width=300, height=13.4),
        _word("、", 410.6, 608.1, width=8, height=13.4),
        _word("项资料。采用", 434.3, 608.1, width=80, height=13.4),
        # 半行偏移碎片: 1 落在 "第" 与 "、" 之间的空隙, 2 落在 "、" 与 "项" 之间
        _word("1", 403.9, 619.2, width=6, height=13.4),
        _word("2", 424.2, 619.2, width=6, height=13.4),
        # 下行正文
        _word("保函递交投标保证金的，仅需提供第", 76.6, 630.0, width=220, height=13.5),
        _word("项资料，并在开标现场递交保函原件。", 301.0, 630.0, width=230, height=13.5),
        _word("3", 290.9, 641.1, width=6, height=13.5),
    ]
    lines = [_render_line_text(row) for row in _cluster_words_by_overlap(words)]

    assert len(lines) == 2
    assert "第 1 、 2 项资料" in lines[0]
    assert "第 3 项资料" in lines[1]


def test_render_line_normalizes_stub_prefix_only() -> None:
    assert _render_line_text([
        _word("1", 97.6, 290.7, width=6),
        _word("）", 103.6, 290.7, width=8),
        _word(".", 115.6, 290.7, width=4),
        _word("我方同意", 121.6, 290.7, width=48),
    ]).startswith("1）我方同意")


# ---------------------------------------------------------------------------
# 模板定界
# ---------------------------------------------------------------------------

_SAMPLE_LINES = [
    "一、投标函",
    "致：招标人",
    "1）我方将按规定履行义务；",
    "投标方名称： （盖章）",
    "二、开标一览表",
    "（招标人名称）：招标人",
]


def test_find_title_line_index_hits_numbered_title() -> None:
    assert _find_title_line_index(_SAMPLE_LINES, "投标函", "bid_letter") == 0


def test_find_next_template_heading_stops_at_other_template() -> None:
    body = _SAMPLE_LINES[1:]
    assert _find_next_template_heading(body, "bid_letter") == 3


def test_find_next_template_heading_ignores_same_key_and_plain_text() -> None:
    body = ["致：招标人", "1）我方将按规定履行义务；递交投标函一份"]
    assert _find_next_template_heading(body, "bid_letter") is None


def test_table_to_html_builds_regular_grid() -> None:
    """规则网格(无合并): 每个物理 cell 输出一个无跨度 <td>。"""
    cells = [
        (0, 0, 50, 20), (50, 0, 100, 20), (100, 0, 150, 20),
        (0, 20, 50, 40), (50, 20, 100, 40), (100, 20, 150, 40),
    ]
    table = SimpleNamespace(
        bbox=(0, 0, 150, 40),
        cells=cells,
        extract=lambda: [
            ["项目名称", "报价", "备注"],
            ["测试项目", "下浮 %", "税率 %"],
        ],
    )
    html = _table_to_html(table)
    assert html is not None
    assert "<td>项目名称</td>" in html
    assert "<td>下浮 %</td>" in html
    assert "rowspan" not in html and "colspan" not in html


def test_table_to_html_rebuilds_merged_cells() -> None:
    """: 合并单元格按网格边线重建 rowspan/colspan, 不摊成空格。

 版式(3列2行网格, 3个物理cell):
 [A(1x2竖跨) | B(2x1横跨)]
 [ | C | D ]
 """
    cells = [
        (0, 0, 50, 40),     # A: 竖跨2行
        (50, 0, 150, 20),   # B: 横跨2列
        (50, 20, 100, 40),  # C
        (100, 20, 150, 40), # D
    ]
    table = SimpleNamespace(
        bbox=(0, 0, 150, 40),
        cells=cells,
        extract=lambda: [
            ["职工人数", "职称等级", None],
            [None, "高级", "中级"],
        ],
    )
    html = _table_to_html(table)
    assert html is not None
    assert '<td rowspan="2">职工人数</td>' in html
    assert '<td colspan="2">职称等级</td>' in html
    assert "<td>高级</td>" in html and "<td>中级</td>" in html


# ---------------------------------------------------------------------------
# 快照增补
# ---------------------------------------------------------------------------


def test_enricher_writes_verbatim_and_recomputes_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    verbatim_text = (
        "致：招标人\n1）我方将按规定履行义务；\n"
        "投标方名称： （盖章）\n日期： 年 月 日"
    )

    class _FakeSource:
        def __init__(self, path: str) -> None:
            assert path == "fake.pdf"

        def extract_template_text(self, **kwargs) -> str:
            assert kwargs["title"] == "投标函"
            assert kwargs["page_start"] == 41
            return verbatim_text

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "app.core.writer.fixed_template.verbatim_extractor.VerbatimTemplateSource",
        _FakeSource,
    )
    metadata = {
        "fixed_template_snapshot": {
            "title": "投标函",
            "template_key": "bid_letter",
            "source_page_start": 41,
            "source_page_end": 41,
            "raw_text_from_chunks": "碎文本",
        }
    }
    enricher = SnapshotVerbatimEnricher("fake.pdf")
    assert enricher.enrich_snapshot(metadata) is True
    snapshot = metadata["fixed_template_snapshot"]
    assert snapshot["raw_text_verbatim"] == verbatim_text
    assert snapshot["verbatim_source"] == "layout_lines"
    assert "盖章" in snapshot["signature_slots"]
    enricher.close()


def test_enricher_degrades_silently_when_pdf_missing() -> None:
    metadata = {
        "fixed_template_snapshot": {
            "title": "投标函",
            "source_page_start": 41,
            "source_page_end": 41,
        }
    }
    enricher = SnapshotVerbatimEnricher("Z:/not_exists/nope.pdf")
    assert enricher.enrich_snapshot(metadata) is False
    assert "raw_text_verbatim" not in metadata["fixed_template_snapshot"]
    enricher.close()


# ---------------------------------------------------------------------------
# 契约优先级
# ---------------------------------------------------------------------------


def test_resolve_source_prefers_verbatim_over_chunks() -> None:
    clause = SimpleNamespace(
        raw_text="约束raw",
        metadata_json={
            "fixed_template_snapshot": {
                "raw_text_from_chunks": "chunk碎文本",
                "raw_text_verbatim": "保真文本",
            }
        },
    )
    template, snapshot = resolve_fixed_template_source(clause)
    assert template == "保真文本"
    assert has_verbatim_source(snapshot)


def test_resolve_source_falls_back_to_chunks_without_verbatim() -> None:
    clause = SimpleNamespace(
        raw_text="约束raw",
        metadata_json={
            "fixed_template_snapshot": {"raw_text_from_chunks": "chunk碎文本"}
        },
    )
    template, snapshot = resolve_fixed_template_source(clause)
    assert template == "chunk碎文本"
    assert not has_verbatim_source(snapshot)


# ---------------------------------------------------------------------------
# 填充直通
# ---------------------------------------------------------------------------

_VERBATIM_BID_LETTER = (
    "致：招标人\n"
    "1）我方将按“招标文件”的规定履行义务；承诺按\n"
    "本招标文件须知前附表第 10 条的要求签订合同。\n"
    "2）我方已详细审查全部“招标文件”；\n"
    "3）本投标自开标日起有效期为 60 个日历日；\n"
    "地址： 电子邮箱：\n"
    "投标方名称： （盖章）\n"
    "投标方法定代表人或授权委托人：（签字） 职务：\n"
    "日期： 年 月 日"
)


def _verbatim_clause() -> SimpleNamespace:
    return SimpleNamespace(
        id=101,
        content="投标函固定模板",
        raw_text="旧碎文本",
        metadata_json={
            "template_key": "bid_letter",
            "fixed_template_snapshot": {
                "title": "投标函",
                "template_key": "bid_letter",
                "source": "llm_only",
                "is_complete_candidate": True,
                "raw_text_from_chunks": "旧碎文本",
                "raw_text_verbatim": _VERBATIM_BID_LETTER,
            },
        },
    )


def _fill_composer(clause: SimpleNamespace) -> MagicMock:
    composer = MagicMock()
    composer._find_bound_clause = AsyncMock(return_value=clause)
    composer._find_fallback_template_clause = AsyncMock(return_value=None)
    composer._load_bound_materials = AsyncMock(return_value=[])
    composer._load_material_images = AsyncMock(return_value=[])
    composer._load_bound_clauses = AsyncMock(return_value=[])
    composer._replace_placeholders = MagicMock(side_effect=lambda t: t)
    composer._autofill_structured_fields = MagicMock(side_effect=lambda t, *_: t)
    composer._dedupe_ids = MagicMock(
        side_effect=lambda ids: [i for i in dict.fromkeys(ids) if i is not None]
    )
    composer._normalize_fixed_template_text = MagicMock(
        side_effect=lambda t: (_ for _ in ()).throw(
            AssertionError("verbatim 模式禁止调用 normalize_fixed_template_text")
        )
    )
    composer._is_tabular_fixed_template = MagicMock(return_value=False)
    composer._rebuild_tabular_fixed_template = MagicMock(side_effect=lambda *_: "")
    return composer


@pytest.mark.asyncio
async def test_fill_template_verbatim_passthrough_keeps_original_numbering() -> None:
    from app.core.writer.composer.template_route import fill_template

    clause = _verbatim_clause()
    composer = _fill_composer(clause)
    node = SimpleNamespace(id=1, project_id=1, title="投标函", volume_type="technical")

    payload = await fill_template(composer, node)

    assert "1）我方将按" in payload.content
    assert "第 10 条" in payload.content
    assert "60 个日历日" in payload.content
    composer._normalize_fixed_template_text.assert_not_called()
    composer._rebuild_tabular_fixed_template.assert_not_called()


@pytest.mark.asyncio
async def test_fill_template_chunk_source_still_normalizes() -> None:
    """无 verbatim 字段的旧快照必须维持原净化链路(向后兼容锁)。"""
    from app.core.writer.composer.template_route import fill_template

    clause = _verbatim_clause()
    snapshot = clause.metadata_json["fixed_template_snapshot"]
    del snapshot["raw_text_verbatim"]
    snapshot["raw_text_from_chunks"] = _VERBATIM_BID_LETTER
    composer = _fill_composer(clause)
    composer._normalize_fixed_template_text = MagicMock(side_effect=lambda t: t)
    node = SimpleNamespace(id=1, project_id=1, title="投标函", volume_type="technical")

    await fill_template(composer, node)

    composer._normalize_fixed_template_text.assert_called_once()


# ---------------------------------------------------------------------------
# 渲染保真
# ---------------------------------------------------------------------------


def test_is_fixed_template_node_matches_enum_value() -> None:
    node = SimpleNamespace(node_type=SimpleNamespace(value="fixed_template"))
    assert is_fixed_template_node(node)
    assert not is_fixed_template_node(SimpleNamespace(node_type="free_writing"))


def test_iter_verbatim_blocks_handles_text_table_and_paragraphs() -> None:
    content = (
        "致：招标人\n1）我方将按规定履行义务；\n"
        "<table><tr><td>项目</td></tr></table>\n"
        "<p>2）我方已详细审查；</p>"
    )
    blocks = iter_verbatim_blocks(content)
    kinds = [kind for kind, _ in blocks]
    assert kinds == ["text", "text", "html", "text"]
    assert blocks[1][1] == "1）我方将按规定履行义务；"
    assert blocks[3][1] == "2）我方已详细审查；"


def test_write_verbatim_docx_section_preserves_numbering() -> None:
    from app.core.writer.doc_renderer import DocRenderer

    doc = SimpleNamespace(paragraphs=[])
    captured_texts: list[str] = []
    captured_html: list[str] = []

    renderer = SimpleNamespace(
        _preprocess_html_for_bid=DocRenderer._preprocess_html_for_bid,
        _render_body_paragraph=lambda d, text: captured_texts.append(text),
        _append_html_fragment_to_doc=lambda fragment, d: captured_html.append(fragment),
        _insert_figure_images=lambda *args, **kwargs: None,
    )
    content = _VERBATIM_BID_LETTER + "\n<table><tr><td>项目名称</td><td>报价</td></tr></table>"

    write_verbatim_docx_section(renderer, doc, content, float, None, "投标函", None)

    joined = "\n".join(captured_texts)
    assert "1）我方将按" in joined
    assert "3）本投标自开标日起有效期为 60 个日历日；" in joined
    # 原编号原样保留, 不得被重排为系统层级编号
    assert "（一）" not in joined
    assert captured_html and "<table" in captured_html[0]


def test_render_verbatim_preview_html_preserves_numbering() -> None:
    from app.core.writer.doc_renderer import HtmlPreviewBuilder

    rendered = render_verbatim_preview_html(HtmlPreviewBuilder, _VERBATIM_BID_LETTER)

    assert "1）我方将按" in rendered
    assert "（一）" not in rendered
    assert rendered.count("<p>") >= 8


# ---------------------------------------------------------------------------
# rule_fallback 粘连长行
# ---------------------------------------------------------------------------


def test_rule_fallback_detects_glued_title_long_line() -> None:
    """: 标题被 chunk 化粘连成超长行时, 兜底仍应识别该模板。"""
    glued = (
        "一、投标函致:<ORG_ENERGY_1>根据贵方 ,"
        "本人正式代表投标方 (投标方名称)\n"
        "提交下述投标文件,内容包括:\n"
        "商务标;\n"
        "投标方名称: (盖章)\n"
        "投标方法定代表人或授权委托人:(签字) 职务:\n"
        "日期: 年 月 日"
    )
    chunks = [SimpleNamespace(page_num=52, chunk_index=182, content=glued)]
    ranges = [SectionRange(page_start=50, page_end=56, volume_type="commercial")]

    entries = build_rule_fallback_entries(chunks, ranges, min_page=1, max_page=100)

    assert any(
        entry.template_key == "bid_letter" and entry.volume_type == "commercial"
        for entry in entries
    )


# ---------------------------------------------------------------------------
# 1559 真实 PDF 黄金集成（文件缺失时跳过）
# ---------------------------------------------------------------------------

_PDF_1559 = sorted(
    (Path(__file__).resolve().parents[2] / "storage" / "uploads" / "1559").glob(
        "tender_*.pdf"
    )
) if (Path(__file__).resolve().parents[2] / "storage" / "uploads" / "1559").exists() else []


@pytest.mark.skipif(not _PDF_1559, reason="1559 招标 PDF 不存在")
def test_golden_1559_bid_letter_verbatim() -> None:
    from app.core.writer.fixed_template.verbatim_extractor import VerbatimTemplateSource

    with VerbatimTemplateSource(str(_PDF_1559[0])) as source:
        text = source.extract_template_text(
            title="投标函", page_start=41, page_end=41, template_key="bid_letter",
        )

    assert text is not None
    lines = text.splitlines()
    # 序号完整且顺序正确
    ordinals = [line[:2] for line in lines if line[:1].isdigit() and line[1:2] == "）"]
    assert ordinals == ["1）", "2）", "3）", "4）", "5）", "6）"]
    # 填空值归位
    assert "第 10 条" in text
    assert "有效期为 60 个日历日" in text
    # 页眉/页码不进入正文
    assert not any(line == "40" for line in lines)
    assert not any(line.startswith("宁东油田防腐药剂") for line in lines)


@pytest.mark.skipif(not _PDF_1559, reason="1559 招标 PDF 不存在")
def test_golden_1559_bid_opening_table_verbatim() -> None:
    from app.core.writer.fixed_template.verbatim_extractor import VerbatimTemplateSource

    with VerbatimTemplateSource(str(_PDF_1559[0])) as source:
        text = source.extract_template_text(
            title="开标一览表",
            page_start=53,
            page_end=53,
            template_key="bid_opening_table",
        )

    assert text is not None
    assert "<table>" in text
    assert "<td>项目名称</td>" in text and "<td>报价</td>" in text and "<td>备注</td>" in text
    assert "注： 1 、投标单位如果需要对报价或其它内容加以说明" in text


# ---------------------------------------------------------------------------
# 公共降噪策略接入（水印字符过滤 + 页眉页脚签名过滤, 旁路收口）
# ---------------------------------------------------------------------------


def _make_noisy_pdf(path: Path) -> None:
    """合成 5 页 PDF: 灰色大字水印 + 固定页眉 + 变数字页脚 + 纯页码 + 正文。"""
    import fitz

    doc = fitz.open()
    for page_num in range(1, 6):
        page = doc.new_page(width=600, height=800)
        # 固定页眉(顶部区带, 跨页重复)
        page.insert_text((150, 30), "NINGDONG-HEADER", fontsize=10)
        # 正文(页面中部, 必须完整保留)
        page.insert_text((60, 300), f"1) BODY-LINE-{page_num} keep me", fontsize=12)
        page.insert_text((60, 330), "second body sentence stays", fontsize=12)
        # 灰色大字水印(每页同文, 弱显示属性 low_ink, 位于页面中部)
        page.insert_text(
            (120, 420), "CONFIDENTIAL", fontsize=48, color=(0.85, 0.85, 0.85)
        )
        # 页脚: 数字随页变化的重复行(旧精确匹配的盲区) + 纯页码
        page.insert_text((60, 772), f"PAGE {page_num} FOOTER", fontsize=9)
        page.insert_text((300, 788), str(page_num + 40), fontsize=10)
    doc.save(path)
    doc.close()


def test_verbatim_filters_watermark_and_chrome_via_shared_policies(
    tmp_path: Path,
) -> None:
    """verbatim 通道复用 core/parser 公共降噪: 水印/页眉/页脚被滤, 正文零损失。"""
    from app.core.writer.fixed_template.verbatim_extractor import VerbatimTemplateSource

    pdf_path = tmp_path / "noisy.pdf"
    _make_noisy_pdf(pdf_path)

    with VerbatimTemplateSource(str(pdf_path)) as source:
        lines = source.extract_range_lines(1, 5)

    joined = "\n".join(lines)
    # 水印字符不得进入布局保真行流(本通道历史上无水印过滤, 为本次补齐点)
    assert "CONFIDENTIAL" not in joined
    # 固定页眉被公共签名过滤
    assert "NINGDONG" not in joined
    # 变数字页脚: normalize_text_signature 数字归一后可命中(旧精确匹配无法过滤)
    assert "FOOTER" not in joined
    # 纯页码兜底
    assert not any(line.strip() == "41" for line in lines)
    # 正文完整保留(防误删闸门: 黑色常规字号正文永不命中水印组)
    for page_num in range(1, 6):
        assert f"BODY-LINE-{page_num}" in joined
    assert "second body sentence stays" in joined


def test_verbatim_degrades_to_unfiltered_when_noise_learning_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """策略学习失败必须降级为不过滤, 不得阻断 verbatim 提取(零阻断契约)。"""
    from app.core.writer.fixed_template.verbatim_extractor import VerbatimTemplateSource

    pdf_path = tmp_path / "noisy_degrade.pdf"
    _make_noisy_pdf(pdf_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("noise learning exploded")

    monkeypatch.setattr(
        "app.core.writer.fixed_template.verbatim_extractor.learn_watermark_policy",
        _boom,
    )
    monkeypatch.setattr(
        "app.core.writer.fixed_template.verbatim_extractor.learn_page_chrome",
        _boom,
    )

    with VerbatimTemplateSource(str(pdf_path)) as source:
        lines = source.extract_range_lines(1, 5)

    joined = "\n".join(lines)
    # 学习失败不阻断提取, 正文仍在
    assert "BODY-LINE-1" in joined
    # 降级语义: 不再过滤水印/页眉(而非崩溃)
    assert "CONFIDENTIAL" in joined
    # 纯页码兜底不依赖策略学习, 仍生效
    assert not any(line.strip() == "41" for line in lines)
