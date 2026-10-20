"""跨表副本行判重防御测试（换轴重构 P1）。

覆盖必测场景: 跨表全等副本命中、同表对称并列因素不误判(反例)、
同表重复行不判、短行不参与; 另补三表连环副本与空表边界。
"""
import pytest

from app.core.extractor.scoring.ledger.duplicate_exclusion import (
    detect_duplicate_grid_rows,
)

pytestmark = pytest.mark.scoring


class TestCrossTableDuplicates:
    """跨表整行归一全等 -> 判副本(后现者视为副本)。"""

    def test_identical_rows_across_tables_detected(self):
        """副本表跨页切分点不同, 但行级归一全等即命中(探针指标4结论)。"""
        grid_a = [
            ["序号", "评分项", "分值"],
            ["1", "项目实施 方案", "10"],
            ["2", "质量管理体系与措施", "5"],
        ]
        grid_b = [
            ["序号", "评分项", "分值"],
            ["1", "项目实施方案", "10"],
        ]
        keys, diagnostics = detect_duplicate_grid_rows([(3, grid_a), (7, grid_b)])

        # 表头行与"项目实施方案"行跨表全等(含空白差异归一); 质量行仅出现一次。
        assert len(keys) == 2
        assert "项目实施方案\x1f10" in keys[1] or "项目实施方案" in keys[1]
        assert all("质量管理体系与措施" not in key for key in keys)
        # 诊断给出页位置对照: 后现者(页7)对照先现者(页3)。
        assert any("页7" in d and "页3" in d for d in diagnostics)

    def test_key_repeated_in_origin_table_then_cross_table_still_detected(self):
        """键先在原表内重复(不判), 再跨表出现 -> 仍判副本且仅去重记录一次。"""
        grid_a = [
            ["项目实施方案", "10"],
            ["项目实施方案", "10"],
        ]
        grid_b = [["项目实施方案", "10"]]
        keys, diagnostics = detect_duplicate_grid_rows([(1, grid_a), (2, grid_b)])

        assert keys == ("项目实施方案\x1f10",)
        assert len(diagnostics) == 1
        assert "表1" in diagnostics[0] and "表0" in diagnostics[0]

    def test_three_tables_chain_duplicate_key_deduped(self):
        """同一行出现在三张表 -> 键去重仅记一次, 诊断逐次对照首现位置。"""
        grid = [["技术负责人具有高级职称", "5"]]
        keys, diagnostics = detect_duplicate_grid_rows([(1, grid), (2, grid), (3, grid)])

        assert keys == ("技术负责人具有高级职称\x1f5",)
        assert len(diagnostics) == 2


class TestBug541Protection:
    """ 反例: 对称并列因素与同表重复禁止误判。"""

    def test_symmetric_factors_same_table_not_flagged(self):
        """同表对称并列因素(两行不同名同分)绝不能被误判为副本。"""
        grid = [
            ["质量管理体系与措施", "5"],
            ["安全管理体系与措施", "5"],
        ]
        keys, diagnostics = detect_duplicate_grid_rows([(4, grid)])

        assert keys == ()
        assert diagnostics == ()

    def test_symmetric_factors_across_tables_not_flagged(self):
        """跨表出现的对称因素(行文本不同)也不判——判重必须整行全等,
        禁止相似度模糊匹配。"""
        grid_a = [["质量管理体系与措施", "5"]]
        grid_b = [["安全管理体系与措施", "5"]]
        keys, _diagnostics = detect_duplicate_grid_rows([(1, grid_a), (2, grid_b)])

        assert keys == ()

    def test_same_table_exact_repeat_not_flagged(self):
        """同表内完全相同的行不判(由既有身份键去重管辖)。"""
        grid = [
            ["项目实施方案", "10"],
            ["项目实施方案", "10"],
        ]
        keys, diagnostics = detect_duplicate_grid_rows([(5, grid)])

        assert keys == ()
        assert diagnostics == ()


class TestNoiseGuards:
    """空行/单格短行(归一后<4字符)不参与判重。"""

    def test_short_rows_not_flagged_across_tables(self):
        """跨表相同的短行(序号/小计类)与空行不参与判重。"""
        grid_a = [
            ["1"],
            ["小计", "5"],
            ["", ""],
        ]
        grid_b = [
            ["1"],
            ["小计", "5"],
            ["", ""],
        ]
        keys, diagnostics = detect_duplicate_grid_rows([(1, grid_a), (2, grid_b)])

        assert keys == ()
        assert diagnostics == ()

    def test_boundary_four_chars_participates(self):
        """归一后恰好4字符的行参与判重(边界含4)。"""
        grid = [["小计", "55"]]
        keys, _diagnostics = detect_duplicate_grid_rows([(1, grid), (2, grid)])

        assert keys == ("小计\x1f55",)

    def test_empty_tables_and_empty_input(self):
        """空表/空输入 -> 空结果, 不抛错。"""
        assert detect_duplicate_grid_rows([]) == ((), ())
        assert detect_duplicate_grid_rows([(1, [])]) == ((), ())
