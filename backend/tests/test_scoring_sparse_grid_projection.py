"""超宽稀疏表几何投影与装配接线的防御测试（归档 /Task4 移植 +）。

护栏对象:
- 投影门禁: 非稀疏表/无坐标/跨越边界时必须拒绝，不得改写既有 legacy 路径行为
- 装配接线: project 后 none_slots 必须清空（cell_grid 的 rowspan 槽已失效）
-: 卷标题判据必须校验"首格独占"位置，右侧单格属规则正文
"""
from __future__ import annotations

import pytest

from app.core.extractor.scoring.scoring_table_parser import _infer_volume_heading
from app.core.parser.scoring_sparse_assembly_gate import apply_sparse_projection
from app.core.parser.scoring_sparse_grid_projection import (
    SparseGridProjectionSchema,
    decide_sparse_grid_action,
    discover_sparse_scoring_projection,
)

pytestmark = pytest.mark.scoring


def _sparse_table(rows: int = 4) -> tuple[list[list[str]], list[list[tuple]]]:
    """构造 16 物理列 / 4 视觉列的稀疏表：序号 | 评分因素 | 分值 | 评分标准。

    视觉列边界 x: [10,60) [60,200) [200,240) [240,560)
    每个视觉列内放 4 个物理槽，其余为 None。
    """
    spans = [(10.0, 60.0), (60.0, 200.0), (200.0, 240.0), (240.0, 560.0)]
    header = ["序号", "评分因素", "分值", "评分标准"]
    data: list[list[str]] = []
    grid: list[list[tuple | None]] = []
    for r in range(rows):
        cells: list[str] = []
        boxes: list[tuple | None] = []
        texts = header if r == 0 else [str(r), f"因素{r}", "10", f"规则正文{r}"]
        for col, (x0, x1) in enumerate(spans):
            for slot in range(4):
                if slot == 0:
                    cells.append(texts[col])
                    boxes.append((x0, r * 20.0, x1, r * 20.0 + 18.0))
                else:
                    cells.append("")
                    boxes.append(None)
        data.append(cells)
        grid.append(boxes)
    return data, grid


class TestProjectionGating:
    def test_discovers_schema_on_wide_sparse_scoring_table(self):
        data, grid = _sparse_table()
        schema = discover_sparse_scoring_projection(data, grid)
        assert schema is not None
        assert schema.column_count == 4
        assert schema.score_column_index == 2

    def test_rejects_when_cell_grid_absent(self):
        """无坐标证据时必须拒绝：几何投影没有可用输入，禁止猜测列边界。"""
        data, _ = _sparse_table()
        assert discover_sparse_scoring_projection(data, None) is None

    def test_rejects_narrow_table(self):
        """常规窄表不得进入投影路径，否则会改写全量既有样本行为。"""
        data = [["序号", "评分因素", "分值", "评分标准"], ["1", "因素", "10", "规则"]]
        grid = [[(0.0, 0.0, 10.0, 10.0)] * 4 for _ in data]
        assert discover_sparse_scoring_projection(data, grid) is None

    def test_rejects_non_scoring_header(self):
        data, grid = _sparse_table()
        data[0] = ["项目名称" if c else "" for c in data[0]]
        assert discover_sparse_scoring_projection(data, grid) is None


class TestAssemblyGate:
    def test_project_clears_none_slots(self):
        """投影后 cell_grid 的 rowspan 槽证据失效，none_slots 必须整体清空。

 沿用旧槽会让规则列 rowspan 继承按错误列号生效（的边界）。
 """
        data, grid = _sparse_table()
        rows = [[str(c) for c in row] for row in data]
        stale = [frozenset({3, 7}) for _ in rows]
        outcome = apply_sparse_projection(data, grid, rows, stale, len(rows[0]), None)

        assert outcome.claim_without_rows is False
        assert outcome.width == 4
        assert outcome.schema is not None
        assert all(slots == frozenset() for slots in outcome.none_slots)
        assert len(outcome.none_slots) == len(outcome.rows)

    def test_fallthrough_preserves_caller_state(self):
        """未命中投影时必须原样返回，禁止对 legacy 路径产生任何副作用。"""
        rows = [["序号", "评分因素", "分值"], ["1", "因素", "10"]]
        slots = [frozenset({1}), frozenset()]
        outcome = apply_sparse_projection(rows, None, rows, slots, 3, None)

        assert outcome.rows is rows
        assert outcome.none_slots is slots
        assert outcome.width == 3
        assert outcome.claim_without_rows is False

    def test_schema_reused_for_continuation_table(self):
        """续表自身无表头，须复用段内已确认 schema 才能正确投影。"""
        data, grid = _sparse_table()
        schema = discover_sparse_scoring_projection(data, grid)
        cont_data, cont_grid = _sparse_table(rows=3)
        cont_data = cont_data[1:]
        cont_grid = cont_grid[1:]

        assert discover_sparse_scoring_projection(cont_data, cont_grid) is None
        decision = decide_sparse_grid_action(cont_data, cont_grid, schema)
        assert decision.action == "project"
        assert decision.projected_rows is not None
        assert len(decision.projected_rows[0]) == 4


class TestVolumeHeadingPositionGuard:
    """: 卷标题判据的"首格独占"必须校验位置。"""

    def test_rejects_single_filled_cell_in_right_column(self):
        """规则列独占的续写文本含"报价"字样，不得被判为价格卷标题。

        投影把宽表压到 4-5 列后，规则续行恰好成为"整行仅一格有值"，
        旧实现不校验列位置，会把后续所有评分项改判为 price 卷。
        """
        row = ["", "", "", "最后报价出现并列最低时各列供应商均得价"]
        assert _infer_volume_heading(row) is None

    def test_accepts_single_filled_cell_in_first_column(self):
        """首格独占的真实卷标题行仍须识别（原有能力不得回退）。"""
        assert _infer_volume_heading(["价格评审", "", "", ""]) == "price"
        assert _infer_volume_heading(["商务技术评审", "", "", ""]) == "commercial"

    def test_explicit_part_marker_unaffected_by_position(self):
        """显式"XX部分"标记走独立分支，不受首格约束影响。"""
        assert _infer_volume_heading(["", "报价部分（30分）", "", ""]) == "price"
