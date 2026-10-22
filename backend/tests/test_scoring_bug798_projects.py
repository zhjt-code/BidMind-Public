""" 四项目 RED 测试（Task 1 产物）。

失败原因必须是业务断言，非 fixture/导入错误。并行 Task3/Task5 完成后部分测试转绿。

根因来自 docs/bug_tracking.md ：
- 2497: 分值格噪声→'供货实施方案'15分漏提
- 2503: is_total_row 误杀商务6分；转置轴价格40分漏提
- 2499: 账本扁平化，两 lot 各100但全局不可比较
- 2502: chunk 横跨多模板边界，scheme_6 合并105项/1542分
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

import pytest

from app.core.extractor.scoring.ledger.extractor_bridge import (  # noqa: E402
    observe_scoring_ledger_shadow,
)
from app.core.extractor.scoring.score_totals import verify_score_totals  # noqa: E402
from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402
from app.core.parser import ChunkPlanner  # noqa: E402
from app.core.parser.pdf_parser import ParsedDocument, ParsedPage  # noqa: E402
from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks  # noqa: E402

pytestmark = pytest.mark.scoring

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "scoring" / "bug798"


def _load(name: str) -> dict[str, Any]:
    path = _FIXTURE_DIR / name
    assert path.exists(), f"Fixture 不存在: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 公共重放 helper
# ---------------------------------------------------------------------------


def _tbl(rows: list[list[str]], page_num: int = 1) -> SimpleNamespace:
    """构造鸭子类型 ExtractedTable（build_scoring_table_chunks 可消费）。"""
    return SimpleNamespace(
        data=rows, table_index=0, page_num=page_num,
        row_count=len(rows), col_count=max(len(r) for r in rows) if rows else 0,
        bbox=None, cell_grid=None,
    )


def _doc(pages: list[ParsedPage], total_pages: int | None = None, filename: str = "test.pdf") -> ParsedDocument:
    return ParsedDocument(
        file_path=filename, filename=filename,
        page_count=total_pages or max((p.page_num for p in pages), default=0),
        pages=pages,
    )


def _parse_chunks(chunks: list) -> tuple[list, dict]:
    """提取 scoring_table chunks 中的所有评分项和卷定义。

 Task4B: 传递 section_path 供 parse_scoring_table 正确推断卷别（商务/技术/价格）。
 """
    items: list = []
    vdefs: dict = {}
    for c in chunks:
        sp = getattr(c, "metadata", {}).get("section_path", "") or ""
        p = parse_scoring_table(c.content, section_path=sp)
        items.extend(p.items)
        vdefs.update(p.volume_defs)
    return items, vdefs


class _FakeItem:
    def __init__(self, name: str, weight: float, volume_label: str = "",
                 scheme: str | None = None, lot: str | None = None) -> None:
        self.score_item_name = name
        self.score_weight = weight
        self.volume_label = volume_label
        self.scheme = scheme
        self.lot = lot
        self.score_rules: list[str] = []
        self.deduction_rules: list[str] = []


class _FakeChunk:
    def __init__(self, page_num: int, content: str, grid: list[list[str]], lot: str | None = None) -> None:
        self.page_num = page_num
        self.content = content
        self.chunk_metadata = {"scoring_payload": {"lot": lot, "grid": grid}}


# ---------------------------------------------------------------------------
# 2497 — 恢复 7 项 / 100 分（供货实施方案 15 分）
# ---------------------------------------------------------------------------


def test_project_2497_recovers_seven_items_and_total_100() -> None:
    """RED→可转绿: 从 fixture chunk.grid 构造 ParsedDocument，走 build_scoring_table_chunks→parse。

    目标: item_count==7, total==100.0, 供货实施方案==15.0
    """
    data = _load("project_2497_grid.json")
    cd = data["chunk"]

    page = ParsedPage(page_num=cd["page_num"], text=cd["content"],
                      tables=[_tbl(cd["grid"], page_num=cd["page_num"])])
    doc = _doc([page], total_pages=cd["page_end"])

    chunks = build_scoring_table_chunks(doc, ChunkPlanner(), 0)
    all_items, vdefs = _parse_chunks(chunks)
    # 区段未检测到时回退直接解析 content
    if not all_items:
        p = parse_scoring_table(cd["content"])
        all_items, vdefs = p.items, p.volume_defs

    report = verify_score_totals(all_items, volume_defs=vdefs)
    supply = next((it for it in all_items if "供货实施方案" in (it.score_item_name or "")), None)

    assert report.item_count == 7, (
        f"期望7项，实际 {report.item_count}（缺'供货实施方案'，分值格噪声导致漏提）"
    )
    assert report.total == 100.0, f"期望100.0，实际 {report.total}"
    assert supply is not None, "期望存在'供货实施方案'条目，当前因噪声未提取"
    assert supply.score_weight == 15.0, f"'供货实施方案'应为15.0，实际 {supply.score_weight}"


# ---------------------------------------------------------------------------
# 2503 — 恢复 8 项 / 100 分（商务 20 / 技术 40 / 价格 40）
# ---------------------------------------------------------------------------


def test_project_2503_recovers_final_axis_and_total_100() -> None:
    """GREEN(Task4B): 全流程 build_scoring_table_chunks 走转置轴预扫描 + 价格公式识别。

    fixture: p1商务14 / p2商务6(+合计行) / p3技术5项40 / p8价格公式文本 / p9转置轴表
    链路:  build_scoring_table_chunks
           → scan_transposed_axis_chunks (p9 → 声明chunk + PriceFormulaContext)
           → collect_unclaimed / assembled (p1/p2/p3)
           → collect_text_formula (p8 + price_context=40 → 价格chunk)
    断言:
      - 8父项 / 100分
      - by_volume == {commercial:20, technical:40, price:40}
      - price item rule 含 "基准价"
      - p9 声明 chunk zero items
    """
    data = _load("project_2503_grid.json")

    pages = [
        ParsedPage(
            page_num=c["page_num"],
            text=c.get("content", ""),
            tables=[_tbl(c["grid"], page_num=c["page_num"])] if c.get("grid") else [],
        )
        for c in data["chunks"]
    ]
    doc = _doc(pages)

    scoring_chunks = build_scoring_table_chunks(doc, ChunkPlanner(), 0)
    all_items, vdefs = _parse_chunks(scoring_chunks)

    report = verify_score_totals(all_items, volume_defs=vdefs)

    item_names = [getattr(it, "score_item_name", "?") for it in all_items]
    assert report.item_count == 8, (
        f"期望8项，实际 {report.item_count}\n"
        f"  已提取: {item_names}\n"
        f"  期望: 企业综合实力14/企业业绩6/技术5项40/价格40"
    )
    assert report.total == 100.0, f"期望100.0，实际 {report.total}"

    expected_vol = {"commercial": 20.0, "technical": 40.0, "price": 40.0}
    actual_vol = {k: v for k, v in report.by_volume.items() if v > 0}
    assert actual_vol == expected_vol, f"期望卷分布 {expected_vol}，实际 {actual_vol}"

    # price item rule 含 "基准价"
    price_items = [it for it in all_items if getattr(it, "volume", None) == "price"]
    assert price_items, f"未找到价格评分项，items: {item_names}"
    price_rules = getattr(price_items[0], "score_rules", []) or []
    description = getattr(price_items[0], "description", "") or ""
    has_base_price = any("基准价" in (r or "") for r in price_rules) or "基准价" in description
    assert has_base_price, f"price item 应含'基准价'，rule={price_rules}, desc={description}"

    # p9 声明 chunk 零 items
    axis_chunks = [
        c for c in scoring_chunks
        if getattr(c, "metadata", {}).get("axis_declaration")
    ]
    assert axis_chunks, "应有 p9 声明 chunk"
    for ac in axis_chunks:
        parsed_ax = parse_scoring_table(ac.content)
        assert len(parsed_ax.items) == 0, (
            f"声明 chunk 不应含评分item，实际: {[getattr(i,'score_item_name','?') for i in parsed_ax.items]}"
        )


# ---------------------------------------------------------------------------
# 2499 — 账本两 scope 各 100，全局不可比较
# ---------------------------------------------------------------------------


def test_project_2499_ledger_two_lots_each_100() -> None:
    """RED: 期望 LedgerReport 携带 scope_reports（尚未实现，当前为空），且全局对比被禁止。

    断言层次：
    A) 两 lot items 各合计 100（factual，先通过）
    B) report.scope_reports 含 2 条 scope 报告（当前缺失 → RED 业务失败）
    C) 每个 scope: scope_id 匹配 lot、expected_total=100、extracted_total=100、gap≤1
    D) document 级 global_comparison_allowed=False
    E) 顶层 expected_total=None、total_gap=None（禁止全局200对100/1的错误比较）
    """
    data = _load("project_2499_scopes.json")

    fake_chunks = [
        _FakeChunk(c["page_num"], c.get("content", c.get("content_head", "")),
                   c.get("grid", []), c.get("lot"))
        for c in data["chunks"]
    ]
    fake_items = [
        _FakeItem(i["name"], i["weight"], i.get("volume_label", ""), i.get("scheme"), i.get("lot"))
        for i in data["items"]
    ]

    # 断言 A：两 lot items 各合计 100（factual，此层先通过）
    lot_totals: dict[str, float] = {}
    for item in data["items"]:
        if item.get("lot"):
            lot = item["lot"]
            lot_totals[lot] = round(lot_totals.get(lot, 0.0) + item["weight"], 4)
    assert len(lot_totals) == 2, f"期望2个lot scope，实际 {list(lot_totals.keys())}"
    for lot_name, lot_total in lot_totals.items():
        # Fixture 有效性守门：确认输入样本每个 lot 为 100 分，非被测生产代码行为。
        assert abs(lot_total - 100.0) < 0.01, (
            f"Scope '{lot_name}' items 合计 = {lot_total}，期望 = 100.0"
        )

    report = observe_scoring_ledger_shadow(fake_chunks, fake_items, document_id=0)
    assert report is not None, "observe_scoring_ledger_shadow 返回 None"

    # 断言 B：scope_reports 必须存在且含 2 条 —— 当前 LedgerReport 无此字段 → RED
    scope_reports = getattr(report, "scope_reports", ())
    assert len(scope_reports) == 2, (
        f"期望2个scope报告（采购包1/采购包2各100），实际 {len(scope_reports)}\n"
        f"  当前 LedgerReport 无 scope_reports 字段，账本仍以扁平模式运行\n"
        f"  全局 expected={report.expected_total}, extracted={report.extracted_total}, "
        f"gap={report.total_gap}\n"
        f"  两个 lot 各需独立对账（expected=100, extracted=100, gap≈0）"
    )

    # 断言 C：各 scope 的 expected/extracted/gap 正确（修复后验证）
    expected_lots = set(lot_totals.keys())
    for sr in scope_reports:
        scope_id = getattr(sr, "scope_id", None)
        assert scope_id in expected_lots, (
            f"scope_id='{scope_id}' 不在预期 lot 集合 {expected_lots}"
        )
        sr_expected = getattr(sr, "expected_total", None)
        sr_extracted = getattr(sr, "extracted_total", None)
        sr_gap = getattr(sr, "total_gap", None)
        assert sr_expected is not None and abs(sr_expected - 100.0) < 0.01, (
            f"Scope '{scope_id}' expected_total={sr_expected}，期望=100.0"
        )
        assert sr_extracted is not None and abs(sr_extracted - 100.0) < 0.01, (
            f"Scope '{scope_id}' extracted_total={sr_extracted}，期望=100.0"
        )
        assert sr_gap is not None and abs(sr_gap) <= 1.0, (
            f"Scope '{scope_id}' gap={sr_gap}，期望在容差 ≤1.0 内"
        )

    # 断言 D：document 级 global_comparison_allowed=False（多 scope 下禁止全局比较）
    global_ok = getattr(report, "global_comparison_allowed", True)
    assert global_ok is False, (
        f"global_comparison_allowed={global_ok}，两个并行 scope 时必须为 False\n"
        f"  禁止用全局 extracted≈200 与任意单一 expected（100或1）直接比较"
    )

    # 断言 E：顶层 expected_total 和 total_gap 必须为 None（禁止全局200对100/1的错误比较）
    assert report.expected_total is None, (
        f"顶层 expected_total={report.expected_total} 必须为 None\n"
        f"  多 scope 场景下全局期望无意义，禁止出现 expected=1 或 expected=100 的误导值"
    )
    assert report.total_gap is None, (
        f"顶层 total_gap={report.total_gap} 必须为 None\n"
        f"  多 scope 场景下全局差额无意义，禁止出现 gap=199 或 gap=100 的误导值"
    )


# ---------------------------------------------------------------------------
# 2502 — 模板边界：p24 与 p25-p26 不得合并，各 chunk 总分 ≤ 100
# ---------------------------------------------------------------------------


def test_project_2502_chunk_boundary_and_scheme_totals() -> None:
    """RED→可转绿: 从 physical_pages 构造 ParsedDocument，走真实 build_scoring_table_chunks。

    目标（修复后 GREEN）：
    - 无 chunk 同时横跨 p24 AND p25（模板边界）
    - p24 独立 chunk，p25-p26 独立 chunk
    - parser 计算每个逻辑表总分 ≤ 100
    """
    data = _load("project_2502_templates.json")

    pages = [
        ParsedPage(
            page_num=pp["page_num"],
            text=pp["text"],
            tables=[
                SimpleNamespace(
                    data=t["rows"], table_index=t["table_index"], page_num=pp["page_num"],
                    row_count=t["row_count"], col_count=t["col_count"],
                    bbox=tuple(t["bbox"]) if t.get("bbox") else None, cell_grid=None,
                )
                for t in pp.get("tables", [])
            ],
        )
        for pp in data["physical_pages"]
    ]
    # page_count=76 与 filename 来自 fixture physical_pages_note
    doc = _doc(pages, total_pages=76, filename="验证评分表结构-11.pdf")

    scoring_chunks = build_scoring_table_chunks(doc, ChunkPlanner(), 0)

    # 断言 A：不允许任何 chunk 同时横跨 p24 AND p25（跨模板边界禁止）
    cross = [
        c for c in scoring_chunks
        if c.metadata.get("page_num", 0) <= 24
        and c.metadata.get("page_end", c.metadata.get("page_num", 0)) >= 25
    ]
    assert not cross, (
        f"存在横跨 p24→p25 模板边界的 chunk（{len(cross)} 个，合并bug）：\n"
        + "\n".join(
            f"  chunk_idx={c.chunk_index} p{c.metadata.get('page_num')}"
            f"→p{c.metadata.get('page_end')}"
            for c in cross
        )
        + "\n  p24='技术方案(38分)'，p25='勘察设计方案(63分)'——不同模板"
    )

    # 断言 B：p24 与 p25-p26 各有独立 chunk（边界两侧分开）
    p24_only = [
        c for c in scoring_chunks
        if c.metadata.get("page_num") == 24
        and c.metadata.get("page_end", 24) < 25
    ]
    p25_plus = [
        c for c in scoring_chunks
        if c.metadata.get("page_num", 0) in (25, 26)
        or (c.metadata.get("page_num", 0) <= 25 < (c.metadata.get("page_end", 0) or 0)
            and c.metadata.get("page_num", 0) > 24)
    ]
    assert p24_only, "p24 应有独立 chunk（page_end<25）"
    assert p25_plus, "p25/p26 应有独立 chunk（与 p24 分开）"

    # 断言 C：用 parser 计算各 chunk 总分，不得超过 100（不从 fixture 摘要直接读）
    for chunk in scoring_chunks:
        parsed = parse_scoring_table(chunk.content)
        if not parsed.items:
            continue
        chunk_total = round(sum(getattr(it, "score_weight", 0) or 0.0 for it in parsed.items), 2)
        ps = chunk.metadata.get("page_num", "?")
        pe = chunk.metadata.get("page_end", ps)
        assert chunk_total <= 100.0, (
            f"chunk p{ps}-p{pe}: {len(parsed.items)}项 / {chunk_total}分 > 100"
            f"（跨模板合并导致超额）"
        )
