""" Task4B 防御测试：转置轴预扫描 + 价格公式上下文 + 负例。

映射真实问题:
- 项目2503: p9 转置轴未被识别，价格卷40分漏提；p8 价格公式段落无满分来源
- Task4B 新增: scan_transposed_axis_chunks 声明 chunk / PriceFormulaContext /
 price_formula_recognizer 支持"价格标评审方法"标题
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser import ChunkPlanner  # noqa: E402
from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # noqa: E402
from app.core.parser.price_formula_recognizer import (  # noqa: E402
    recognize_price_formula_sections,
)
from app.core.parser.scoring_axis_chunk_source import (  # noqa: E402
    AxisScanResult,
    PriceFormulaContext,
    scan_transposed_axis_chunks,
)
from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402
from app.core.parser.scoring_section_locator import ScoringSection  # noqa: E402

import pytest

pytestmark = pytest.mark.scoring

# ---------------------------------------------------------------------------
# Fixture 加载 (M4: P9 grid 从 project_2503 fixture 单源加载，删双存常量)
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "scoring" / "bug798"


def _load(name: str) -> dict[str, Any]:
    path = _FIXTURE_DIR / name
    assert path.exists(), f"Fixture 不存在: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_p9_grid() -> list[list[str]]:
    """M4: 从 project_2503_grid.json fixture 加载 p9 转置轴网格（单一来源）。"""
    data = _load("project_2503_grid.json")
    chunk = next(c for c in data["chunks"] if c["page_num"] == 9 and "grid" in c)
    return chunk["grid"]


_P8_PRICE_TEXT = (
    "价格标评审方法\n"
    "一、基准价确定方法：将所有有效投标人的投标加权总价算术平均值作为评标基准价。\n"
    "二、价格评分公式：价格得分=(评标基准价/投标加权总价)×40×100\n"
    "三、当投标加权总价不高于评标基准价时，得满分。"
)


def _tbl(data: list[list[str]], page_num: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        data=data, table_index=0, page_num=page_num,
        row_count=len(data), col_count=max(len(r) for r in data) if data else 0,
        bbox=None, cell_grid=None,
    )


def _doc(pages: list[ParsedPage]) -> ParsedDocument:
    return ParsedDocument(
        file_path="test.pdf",
        filename="test.pdf",
        page_count=max(p.page_num for p in pages),
        pages=pages,
    )


# ---------------------------------------------------------------------------
# scan_transposed_axis_chunks: 正例
# ---------------------------------------------------------------------------


def test_scan_finds_p9_axis_creates_declaration_chunk() -> None:
    """p9 转置轴被预扫描，产生声明 chunk、预占表键、构造 PriceFormulaContext。"""
    page9 = ParsedPage(page_num=9, text="权重表", tables=[_tbl(_load_p9_grid(), page_num=9)])
    doc = _doc([page9])

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0)

    assert len(result.chunks) == 1, "应产生1个声明chunk"
    assert (9, 0) in result.claimed_keys, "p9 table_idx=0 应被预占"
    assert len(result.price_contexts) == 1
    assert result.price_contexts[0].price_score == 40.0
    assert result.price_contexts[0].lot_label is None
    assert not result.conflict


def test_declaration_chunk_has_zero_items_and_correct_volume_defs() -> None:
    """声明 chunk markdown 解析: zero items, volume_defs = {commercial:20, technical:40, price:40}。"""
    page9 = ParsedPage(page_num=9, text="权重表", tables=[_tbl(_load_p9_grid(), page_num=9)])
    doc = _doc([page9])

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0)
    assert result.chunks

    parsed = parse_scoring_table(result.chunks[0].content)
    assert parsed.items == [], f"声明chunk不应含评分item，实际: {parsed.items}"
    vdefs = parsed.volume_defs
    assert "commercial" in vdefs, "应有 commercial 卷定义"
    assert "technical" in vdefs, "应有 technical 卷定义"
    assert "price" in vdefs, "应有 price 卷定义"
    assert abs(vdefs["commercial"].volume_max - 20.0) < 0.01, f"commercial=20，实际={vdefs['commercial'].volume_max}"
    assert abs(vdefs["technical"].volume_max - 40.0) < 0.01, f"technical=40，实际={vdefs['technical'].volume_max}"
    assert abs(vdefs["price"].volume_max - 40.0) < 0.01, f"price=40，实际={vdefs['price'].volume_max}"


def test_declaration_chunk_metadata_flags() -> None:
    """声明 chunk metadata 含 axis_declaration=True 和 recovered_from=transposed_final_axis。"""
    page9 = ParsedPage(page_num=9, text="权重表", tables=[_tbl(_load_p9_grid(), page_num=9)])
    doc = _doc([page9])

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0)
    chunk = result.chunks[0]

    assert getattr(chunk, "metadata", {}).get("axis_declaration") is True
    assert getattr(chunk, "metadata", {}).get("recovered_from") == "transposed_final_axis"


def test_scan_deduplicates_identical_fingerprint() -> None:
    """两页相同网格（相同指纹）只产出一个声明 chunk。"""
    grid = _load_p9_grid()
    tbl = _tbl(grid, page_num=9)
    tbl2 = _tbl(grid, page_num=10)
    doc = _doc([
        ParsedPage(page_num=9, text="", tables=[tbl]),
        ParsedPage(page_num=10, text="", tables=[tbl2]),
    ])

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0)
    assert len(result.chunks) == 1, "相同指纹应去重为1个chunk"


def test_scan_returns_empty_when_no_axis() -> None:
    """页面无转置轴表时返回空结果。"""
    normal_grid = [
        ["评分因素", "分值"],
        ["技术方案", "20分"],
    ]
    page = ParsedPage(page_num=1, text="评分表", tables=[_tbl(normal_grid, page_num=1)])
    doc = _doc([page])

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0)
    assert result.chunks == []
    assert result.claimed_keys == set()
    assert result.price_contexts == []


def test_scan_conflict_detection_clears_price_contexts() -> None:
    """两个不同 price 期望的轴 → conflict=True，price_contexts 清空。"""
    # 轴1: 价格40 (从 fixture 加载)
    grid1 = _load_p9_grid()
    # 轴2: 价格60（不同权重分布）
    grid2 = [
        ["权重汇总表", "", "", ""],
        ["权重名称", "商务标权重", "技术标权重", "价格标权重"],
        ["权重", "10%", "30%", "60%"],
        ["综合得分", "商务标得分+技术标得分+价格标得分=100", "", ""],
    ]
    doc = _doc([
        ParsedPage(page_num=5, text="", tables=[_tbl(grid1, page_num=5)]),
        ParsedPage(page_num=9, text="", tables=[_tbl(grid2, page_num=9)]),
    ])

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0)
    assert result.conflict is True
    assert result.price_contexts == [], "冲突时 price_contexts 应清空"
    assert len(result.chunks) == 2, "声明 chunk 仍产出（只清空 price_contexts）"


# ---------------------------------------------------------------------------
# price_formula_recognizer: 支持"价格标评审方法"标题
# ---------------------------------------------------------------------------


def test_recognizes_price_formula_with_jiage_biao_title() -> None:
    """'价格标评审方法' 标题 + 公式 + 基准价 → 正确识别，score 来自 PriceFormulaContext。"""
    pages = [ParsedPage(page_num=8, text=_P8_PRICE_TEXT)]
    doc = _doc(pages)
    section = ScoringSection("评标办法", 1, 7, "fallback")
    ctx = PriceFormulaContext(price_score=40.0)

    result = recognize_price_formula_sections(doc, [section], "", price_contexts=[ctx])

    assert len(result) == 1, "应识别1个价格公式段落"
    item = result[0]
    assert item.page_num == 8
    assert item.score == 40.0, f"score 应来自 PriceFormulaContext=40，实际 {item.score}"
    assert "基准价" in item.text


def test_recognizes_old_jiage_pingfen_biaozhun_title() -> None:
    """保留既有'价格评分标准'标题兼容性，不回归。"""
    text = "价格评分标准价格部分得分=100-100×n×|投标人评标总价-基准价|/基准价。"
    pages = [ParsedPage(page_num=29, text=text)]
    doc = _doc(pages)
    section = ScoringSection("评分区段", 21, 28, "fallback")
    ctx = PriceFormulaContext(price_score=100.0)

    result = recognize_price_formula_sections(doc, [section], "", price_contexts=[ctx])
    assert len(result) == 1
    assert result[0].score == 100.0


def test_no_price_chunk_without_p9_axis() -> None:
    """无 p9 轴（price_contexts=[]）且 p8 无行内显式满分声明 → 不生成 price chunk。"""
    pages = [ParsedPage(page_num=8, text=_P8_PRICE_TEXT)]
    doc = _doc(pages)
    section = ScoringSection("评标办法", 1, 7, "fallback")

    result = recognize_price_formula_sections(doc, [section], "", price_contexts=[])
    assert result == [], "无满分来源时不应生成价格公式 chunk"


def test_no_price_chunk_title_without_formula() -> None:
    """有价格标评审方法标题但无公式 → 不识别。"""
    text = "价格标评审方法\n一、基准价确定方法：算术平均值。\n二、说明性文字，无得分公式。"
    pages = [ParsedPage(page_num=8, text=text)]
    doc = _doc(pages)
    section = ScoringSection("评标办法", 1, 7, "fallback")
    ctx = PriceFormulaContext(price_score=40.0)

    result = recognize_price_formula_sections(doc, [section], "", price_contexts=[ctx])
    assert result == [], "无得分公式时不应识别"


def test_no_price_chunk_formula_without_base_price() -> None:
    """有公式但无基准价语义 → 不识别（避免纯系数形态误识别）。"""
    text = "价格标评审方法\n价格得分=投标价格/最低价格×标准分值。"
    pages = [ParsedPage(page_num=8, text=text)]
    doc = _doc(pages)
    section = ScoringSection("评标办法", 1, 7, "fallback")
    ctx = PriceFormulaContext(price_score=40.0)

    result = recognize_price_formula_sections(doc, [section], "", price_contexts=[ctx])
    assert result == [], "无基准价语义时不应识别"


def test_recognized_price_formula_carries_lot_label() -> None:
    """识别结果应携带 lot_label 字段（Task4B payload写lot）。"""
    from app.core.parser.lot_boundary_locator import LotBoundary

    text = "价格标评审方法\n价格得分=(评标基准价/报价)×10×100\n达到基准价得满分。"
    pages = [ParsedPage(page_num=5, text=text)]
    doc = _doc(pages)
    ctx = PriceFormulaContext(price_score=10.0, lot_label="采购包1")
    lots = [LotBoundary("采购包1", 5, 5)]

    result = recognize_price_formula_sections(doc, lots, "", price_contexts=[ctx])
    assert result, "应识别到价格公式段落"
    assert result[0].lot_label == "采购包1"


# ---------------------------------------------------------------------------
# volume_labeling: 商务标/技术标/价格标评审标准(表) 支持
# ---------------------------------------------------------------------------


def test_detect_volume_title_commercial_biao() -> None:
    """商务标评审标准 → 返回匹配标题。"""
    from app.core.extractor.scoring.volume_labeling import (
        detect_table_volume_title,
        infer_initial_table_volume,
    )

    text = "《商务标评审标准》\n本表共20分"
    title = detect_table_volume_title(text)
    assert title is not None, "应检测到表级标题"
    vol = infer_initial_table_volume(title or "")
    assert vol == "commercial", f"应推断为 commercial，实际 {vol}"


def test_detect_volume_title_technical_biao() -> None:
    """技术标评审标准表 → technical。"""
    from app.core.extractor.scoring.volume_labeling import (
        detect_table_volume_title,
        infer_initial_table_volume,
    )

    text = "技术标评审标准表\n本表共40分"
    title = detect_table_volume_title(text)
    assert title is not None
    vol = infer_initial_table_volume(title or "")
    assert vol == "technical", f"应推断为 technical，实际 {vol}"


def test_detect_volume_title_price_biao_method() -> None:
    """价格标评审方法 → price。"""
    from app.core.extractor.scoring.volume_labeling import (
        detect_table_volume_title,
        infer_initial_table_volume,
    )

    text = "价格标评审方法\n价格得分公式..."
    title = detect_table_volume_title(text)
    assert title is not None
    vol = infer_initial_table_volume(title or "")
    assert vol == "price", f"应推断为 price，实际 {vol}"


def test_detect_volume_title_ambiguity_returns_none() -> None:
    """同页多卷标题歧义 → None。"""
    from app.core.extractor.scoring.volume_labeling import detect_table_volume_title

    text = "商务标评审标准\n技术标评审标准"
    title = detect_table_volume_title(text)
    assert title is None, "同页多卷歧义应返回 None"


# ---------------------------------------------------------------------------
# C1: AST 参数护栏 —— collect_text_formula_scoring_chunks 参数 ≤6
# ---------------------------------------------------------------------------


def test_c1_collect_text_formula_chunk_config_parameter_count() -> None:
    """C1: collect_text_formula_scoring_chunks 参数数量 ≤ 6（TextFormulaChunkConfig 收敛）。"""
    import inspect
    from app.core.parser.scoring_chunk_sources import collect_text_formula_scoring_chunks

    sig = inspect.signature(collect_text_formula_scoring_chunks)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    assert len(params) <= 6, (
        f"collect_text_formula_scoring_chunks 参数超限: {len(params)} > 6，"
        f"参数列表: {[p.name for p in params]}"
    )


# ---------------------------------------------------------------------------
# I1: axis scan 在 lots 解析后执行，按 lot_for_page 精确绑定 lot
# ---------------------------------------------------------------------------


def test_i1_scan_with_lots_binds_lot_label() -> None:
    """I1: axis 所在页属于某 lot，PriceFormulaContext.lot_label 应精确绑定该标段。"""
    from app.core.parser.lot_boundary_locator import LotBoundary

    page9 = ParsedPage(page_num=9, text="权重表", tables=[_tbl(_load_p9_grid(), page_num=9)])
    doc = _doc([page9])
    lots = [LotBoundary("采购包一", 8, 9)]

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0, lots=lots)

    assert len(result.price_contexts) == 1
    assert result.price_contexts[0].lot_label == "采购包一", (
        f"lot_label 应为 '采购包一'，实际: {result.price_contexts[0].lot_label}"
    )
    assert result.price_contexts[0].price_score == 40.0


def test_i1_scan_page_not_in_any_lot_no_price_context() -> None:
    """I1: 多 lot 文档中 axis 所在页不属于任何 lot → 不生成 PriceFormulaContext（标诊断）。"""
    from app.core.parser.lot_boundary_locator import LotBoundary

    page9 = ParsedPage(page_num=9, text="权重表", tables=[_tbl(_load_p9_grid(), page_num=9)])
    doc = _doc([page9])
    # lots 存在但不覆盖 p9
    lots = [LotBoundary("采购包一", 1, 7), LotBoundary("采购包二", 8, 8)]

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0, lots=lots)

    assert len(result.chunks) == 1, "声明 chunk 仍应产出"
    assert result.price_contexts == [], "p9 不在任何 lot 内，不应生成 PriceFormulaContext"


def test_i1_scan_single_doc_no_lots_lot_label_is_none() -> None:
    """I1: 无 lots（单一 scope 文档）→ PriceFormulaContext.lot_label=None。"""
    page9 = ParsedPage(page_num=9, text="权重表", tables=[_tbl(_load_p9_grid(), page_num=9)])
    doc = _doc([page9])

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0, lots=[])

    assert len(result.price_contexts) == 1
    assert result.price_contexts[0].lot_label is None


# ---------------------------------------------------------------------------
# I2: 三来源冲突 fail-closed；相同值去重后可用
# ---------------------------------------------------------------------------


def test_i2_inline_vs_axis_conflict_fails_closed() -> None:
    """I2: 行内显式满分(30) 与轴期望(40) 不一致 → fail-closed，不生成价格公式 chunk。"""
    text = (
        "价格标评审方法\n"
        "价格项部分满分为30分\n"
        "价格得分=(评标基准价/投标加权总价)×30×100\n"
        "达到基准价时得满分。"
    )
    pages = [ParsedPage(page_num=8, text=text)]
    doc = _doc(pages)
    section = ScoringSection("评标办法", 1, 7, "fallback")
    ctx = PriceFormulaContext(price_score=40.0)

    result = recognize_price_formula_sections(doc, [section], "", price_contexts=[ctx])
    assert result == [], "inline(30) 与 axis(40) 冲突，不应生成价格公式 chunk"


def test_i2_inline_equals_axis_deduplicates_ok() -> None:
    """I2: 行内满分与轴期望相同 → 去重后可用，正常生成 chunk，score=40。"""
    text = (
        "价格标评审方法\n"
        "价格项部分满分为40分\n"
        "价格得分=(评标基准价/投标加权总价)×40×100\n"
        "当达到基准价时得满分。"
    )
    pages = [ParsedPage(page_num=8, text=text)]
    doc = _doc(pages)
    section = ScoringSection("评标办法", 1, 7, "fallback")
    ctx = PriceFormulaContext(price_score=40.0)

    result = recognize_price_formula_sections(doc, [section], "", price_contexts=[ctx])
    assert len(result) == 1, "inline==axis(40) 应去重后正常生成"
    assert result[0].score == 40.0


# ---------------------------------------------------------------------------
# M2: price_contexts 按 (lot_label, price_score) 稳定去重
# ---------------------------------------------------------------------------


def test_m2_price_contexts_dedup_by_lot_and_score() -> None:
    """M2: 两个不同物理表但相同 (lot_label=None, price_score=40) → price_contexts 去重为 1。"""
    grid1 = _load_p9_grid()
    # 不同首行(不同指纹)，但相同权重分布 40%
    grid2 = [
        ["综合得分权重汇总表（备用）", "", "", ""],
        [grid1[1][0], grid1[1][1], grid1[1][2], grid1[1][3]],
        ["权重", "20%", "40%", "40%"],
        ["投标人综合得分", "商务标得分+技术标得分+价格标得分=100", "", ""],
    ]
    doc = _doc([
        ParsedPage(page_num=5, text="", tables=[_tbl(grid1, page_num=5)]),
        ParsedPage(page_num=9, text="", tables=[_tbl(grid2, page_num=9)]),
    ])

    result = scan_transposed_axis_chunks(doc, ChunkPlanner(), 0, lots=[])

    assert len(result.chunks) == 2, "两个不同指纹 → 应产出 2 个声明 chunk"
    assert len(result.price_contexts) == 1, (
        f"(lot_label=None, price_score=40) 应去重为 1 个，实际: {result.price_contexts}"
    )
    assert result.price_contexts[0].price_score == 40.0
    assert not result.conflict, "相同值去重后无冲突"


# ---------------------------------------------------------------------------
# I4: 声明 chunk 直接集成测试（不经 builder，直接 parse_scoring_table）
# ---------------------------------------------------------------------------


def test_i4_declaration_chunk_direct_parse_from_fixture() -> None:
    """I4: 从 fixture 加载 p9 grid，_render_axis_declaration_markdown 渲染后
    parse_scoring_table 直接返回 0 items + volume_defs {commercial:20, technical:40, price:40}。
    不依赖 builder，验证声明 chunk 的解析路由正确。
    """
    from app.core.parser.scoring_axis_chunk_source import _render_axis_declaration_markdown

    grid = _load_p9_grid()
    markdown = _render_axis_declaration_markdown(grid, page_num=9)

    parsed = parse_scoring_table(markdown)
    assert parsed.items == [], f"应 0 items，实际: {parsed.items}"
    vdefs = parsed.volume_defs
    for vol, expected in [("commercial", 20.0), ("technical", 40.0), ("price", 40.0)]:
        actual = vdefs.get(vol)
        assert actual is not None, f"volume_defs 应含 '{vol}'"
        assert abs(actual.volume_max - expected) < 0.01, (
            f"{vol}: 期望 {expected}，实际 {actual.volume_max}"
        )
