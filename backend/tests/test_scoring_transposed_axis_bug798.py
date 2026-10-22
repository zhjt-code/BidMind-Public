# -*- coding: utf-8 -*-
"""防御测试 转置最终分值轴纯解析。

TDD 流程：从 project_2503_grid.json 读取 physical_pages.p9_weight_table.grid，
 先 RED（实现前断言失败），实现后全部 GREEN。

测试范围：
- parse_transposed_final_score_axis: 正例（项目2503 p9 网格）+ 全门禁负例
- final_score_axis_to_volume_defs: FinalScoreAxis → VolumeDefinition 转换
- framework_table_parser 薄调用入口

不测：scoring_table_parser、chunk_builder、chunk_sources、
 price_formula、volume_labeling、ledger/frontend/docs。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.framework_table_parser import (  # noqa: E402
    parse_weight_summary_defs,
    try_parse_transposed_axis_volume_defs,
)
from app.core.extractor.scoring.transposed_score_axis import (  # noqa: E402
    FinalScoreAxis,
    final_score_axis_to_volume_defs,
    parse_transposed_final_score_axis,
)

pytestmark = pytest.mark.scoring

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "scoring" / "bug798"


def _load_p9_grid() -> list[list[str]]:
    """从 project_2503_grid.json 读取 physical_pages.p9_weight_table.grid。"""
    path = _FIXTURE_DIR / "project_2503_grid.json"
    assert path.exists(), f"Fixture 不存在: {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["physical_pages"]["p9_weight_table"]["grid"]


# ---------------------------------------------------------------------------
# 正例: 项目2503 p9 转置权重汇总表
# ---------------------------------------------------------------------------


class TestParseTransposedFinalScoreAxisPositive:
    """项目2503 附表4-1《商务标/技术标/价格标权重表和综合得分汇总》正例。"""

    def test_returns_final_score_axis_not_none(self) -> None:
        grid = _load_p9_grid()
        result = parse_transposed_final_score_axis(grid, source_page=9)
        assert result is not None, (
            "期望解析成功，返回 FinalScoreAxis，实际 None\n"
            "  检查: transposed_score_axis.py 是否已实现 parse_transposed_final_score_axis"
        )

    def test_expected_total_is_100(self) -> None:
        grid = _load_p9_grid()
        result = parse_transposed_final_score_axis(grid, source_page=9)
        assert result is not None
        assert result.expected_total == 100.0, (
            f"期望 expected_total=100.0，实际 {result.expected_total}"
        )

    def test_three_volumes_detected(self) -> None:
        grid = _load_p9_grid()
        result = parse_transposed_final_score_axis(grid, source_page=9)
        assert result is not None
        assert set(result.weights.keys()) == {"commercial", "technical", "price"}, (
            f"期望三卷 {{commercial,technical,price}}，实际 {set(result.weights.keys())}"
        )

    def test_weights_are_correct(self) -> None:
        grid = _load_p9_grid()
        result = parse_transposed_final_score_axis(grid, source_page=9)
        assert result is not None
        assert abs(result.weights["commercial"] - 0.20) < 0.001, (
            f"commercial 权重期望 0.20，实际 {result.weights['commercial']}"
        )
        assert abs(result.weights["technical"] - 0.40) < 0.001, (
            f"technical 权重期望 0.40，实际 {result.weights['technical']}"
        )
        assert abs(result.weights["price"] - 0.40) < 0.001, (
            f"price 权重期望 0.40，实际 {result.weights['price']}"
        )

    def test_expected_by_volume_is_weight_times_total(self) -> None:
        """最终分值制: expected_by_volume = weight × 100，非各卷100再折算。"""
        grid = _load_p9_grid()
        result = parse_transposed_final_score_axis(grid, source_page=9)
        assert result is not None
        assert abs(result.expected_by_volume["commercial"] - 20.0) < 0.01, (
            f"commercial 期望分 20.0，实际 {result.expected_by_volume['commercial']}"
        )
        assert abs(result.expected_by_volume["technical"] - 40.0) < 0.01, (
            f"technical 期望分 40.0，实际 {result.expected_by_volume['technical']}"
        )
        assert abs(result.expected_by_volume["price"] - 40.0) < 0.01, (
            f"price 期望分 40.0，实际 {result.expected_by_volume['price']}"
        )

    def test_source_page_preserved(self) -> None:
        grid = _load_p9_grid()
        result = parse_transposed_final_score_axis(grid, source_page=9)
        assert result is not None
        assert result.source_page == 9, (
            f"source_page 期望 9，实际 {result.source_page}"
        )

    def test_is_frozen_dataclass(self) -> None:
        grid = _load_p9_grid()
        result = parse_transposed_final_score_axis(grid, source_page=9)
        assert result is not None
        assert isinstance(result, FinalScoreAxis)
        with pytest.raises((AttributeError, TypeError)):
            result.expected_total = 99.0  # type: ignore[misc]

    def test_weight_sum_equals_one(self) -> None:
        grid = _load_p9_grid()
        result = parse_transposed_final_score_axis(grid, source_page=9)
        assert result is not None
        total = sum(result.weights.values())
        assert abs(total - 1.0) <= 0.01, (
            f"权重之和应为 1.0±0.01，实际 {total}"
        )


# ---------------------------------------------------------------------------
# 两卷形态（非固定三卷）
# ---------------------------------------------------------------------------


class TestTwoVolumeVariant:
    """不固定三卷数量：技术+价格两卷形态也应正常解析。"""

    _GRID: list[list[str]] = [
        ["权重名称", "技术标权重", "价格标权重"],
        ["权重", "60%", "40%"],
        ["投标人综合得分", "技术标得分+价格标得分=100", ""],
    ]

    def test_two_volume_axis_parsed(self) -> None:
        result = parse_transposed_final_score_axis(self._GRID)
        assert result is not None, "双卷形态应能解析，实际返回 None"
        assert set(result.weights.keys()) == {"technical", "price"}, (
            f"期望 {{technical, price}}，实际 {set(result.weights.keys())}"
        )

    def test_two_volume_weights(self) -> None:
        result = parse_transposed_final_score_axis(self._GRID)
        assert result is not None
        assert abs(result.weights["technical"] - 0.60) < 0.001
        assert abs(result.weights["price"] - 0.40) < 0.001

    def test_two_volume_expected_by_volume(self) -> None:
        result = parse_transposed_final_score_axis(self._GRID)
        assert result is not None
        assert abs(result.expected_by_volume["technical"] - 60.0) < 0.01
        assert abs(result.expected_by_volume["price"] - 40.0) < 0.01


# ---------------------------------------------------------------------------
# 门禁负例（任一不满足应返回 None）
# ---------------------------------------------------------------------------


class TestGateRejections:
    """所有门禁负例: parse_transposed_final_score_axis 必须返回 None。"""

    def test_empty_grid_rejected(self) -> None:
        assert parse_transposed_final_score_axis([]) is None

    def test_no_summary_signal_rejected(self) -> None:
        """无汇总信号词的表格（普通百分比统计表）应被拒收。"""
        grid = [
            ["指标", "A占比", "B占比", "C占比"],
            ["数据", "25%", "35%", "40%"],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "无'权重表'/'综合得分汇总'等信号词的普通统计表应被拒收"
        )

    def test_single_volume_rejected(self) -> None:
        """只有一个可识别卷标签时不满足'至少2个不同卷标签'门禁。"""
        grid = [
            ["权重名称", "技术标权重"],
            ["权重", "100%"],
            ["综合得分", "技术标得分=100"],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "单卷不构成最终分值轴，应被拒收"
        )

    def test_duplicate_volume_label_rejected(self) -> None:
        """重复卷标签（同一卷名出现两次作为列标题）应被拒收。"""
        grid = [
            ["权重名称", "商务标权重", "商务标权重", "价格标权重"],
            ["权重", "20%", "40%", "40%"],
            ["投标人综合得分", "商务标得分+商务标得分+价格标得分=100", "", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "重复卷标签（commercial 出现两次）应被拒收"
        )

    def test_orphan_pct_column_rejected(self) -> None:
        """权重行含孤儿百分比列（无对应卷标签）应被拒收（未知空标签）。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重"],  # 只有2个卷列
            ["权重", "20%", "40%", "40%"],  # 第3列有百分比但标签行无对应卷
            ["投标人综合得分", "商务标得分+技术标得分=100", "", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "权重行含孤儿百分比（无对应卷标签列）应被拒收"
        )

    def test_weight_sum_not_100pct_rejected(self) -> None:
        """百分比之和显著偏离100%（90%）应被拒收。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重", "价格标权重"],
            ["权重", "20%", "30%", "40%"],  # 合计 90%
            ["投标人综合得分", "商务标得分+技术标得分+价格标得分=100", "", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "权重和 90% 显著偏离 100%，应被拒收"
        )

    def test_missing_formula_row_rejected(self) -> None:
        """缺少综合公式行（无 '=100'）时应被拒收。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重", "价格标权重"],
            ["权重", "20%", "40%", "40%"],
            ["说明", "仅供参考", "", ""],  # 无公式
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "缺公式行（无'=100'）应被拒收"
        )

    def test_plain_pct_stats_table_rejected(self) -> None:
        """普通百分比统计表（有 '=100' 但无卷标签行）应被拒收。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重", "价格标权重"],
            ["权重", "20%", "40%", "40%"],
            ["合计", "=100", "", ""],  # 有 =100 但仅含数字不含卷名
        ]
        # 这个特殊形态公式行无卷关键词，门禁5应拒收
        assert parse_transposed_final_score_axis(grid) is None, (
            "公式行仅含'=100'但不列出卷名，应被拒收"
        )

    def test_formula_missing_a_volume_rejected(self) -> None:
        """公式行遗漏一个声明卷（formula_vols ⊂ declared_vols），应被拒收。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重", "价格标权重"],
            ["权重", "20%", "40%", "40%"],
            # 公式只列出2卷，遗漏价格
            ["投标人综合得分", "商务标得分+技术标得分=100", "", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "公式行遗漏'价格'卷，声明与公式不一致，应被拒收"
        )


# ---------------------------------------------------------------------------
# VolumeDefinition 转换验证
# ---------------------------------------------------------------------------


class TestFinalScoreAxisToVolumeDefs:
    """final_score_axis_to_volume_defs: 最终分值制 VolumeDefinition。

    volume_max = weight × expected_total（如 20/40/40），
    不代表各卷100分再按百分比折算。
    """

    def test_returns_dict_for_all_volumes(self) -> None:
        grid = _load_p9_grid()
        axis = parse_transposed_final_score_axis(grid, source_page=9)
        assert axis is not None
        vdefs = final_score_axis_to_volume_defs(axis)
        assert set(vdefs.keys()) == {"commercial", "technical", "price"}, (
            f"期望三卷键，实际 {set(vdefs.keys())}"
        )

    def test_volume_max_is_final_score_not_100(self) -> None:
        """volume_max 必须是最终分值（20/40/40），不是各卷内部满分100。"""
        grid = _load_p9_grid()
        axis = parse_transposed_final_score_axis(grid, source_page=9)
        assert axis is not None
        vdefs = final_score_axis_to_volume_defs(axis)
        assert abs(vdefs["commercial"].volume_max - 20.0) < 0.01, (
            f"commercial.volume_max 应为 20.0（最终分值），实际 {vdefs['commercial'].volume_max}\n"
            "  重要: 转置最终分值轴不做折算，volume_max 直接是分值"
        )
        assert abs(vdefs["technical"].volume_max - 40.0) < 0.01, (
            f"technical.volume_max 应为 40.0，实际 {vdefs['technical'].volume_max}"
        )
        assert abs(vdefs["price"].volume_max - 40.0) < 0.01, (
            f"price.volume_max 应为 40.0，实际 {vdefs['price'].volume_max}"
        )

    def test_weight_is_fractional(self) -> None:
        grid = _load_p9_grid()
        axis = parse_transposed_final_score_axis(grid, source_page=9)
        assert axis is not None
        vdefs = final_score_axis_to_volume_defs(axis)
        assert abs(vdefs["commercial"].weight - 0.20) < 0.001
        assert abs(vdefs["technical"].weight - 0.40) < 0.001
        assert abs(vdefs["price"].weight - 0.40) < 0.001

    def test_volume_field_correct(self) -> None:
        grid = _load_p9_grid()
        axis = parse_transposed_final_score_axis(grid, source_page=9)
        assert axis is not None
        vdefs = final_score_axis_to_volume_defs(axis)
        for vol_key, vdef in vdefs.items():
            assert vdef.volume == vol_key, (
                f"vdef.volume='{vdef.volume}' 与键 '{vol_key}' 不一致"
            )


# ---------------------------------------------------------------------------
# framework_table_parser 薄调用入口
# ---------------------------------------------------------------------------


class TestFrameworkTableParserThinCall:
    """try_parse_transposed_axis_volume_defs 薄调用入口。"""

    def test_positive_case_returns_volume_defs(self) -> None:
        grid = _load_p9_grid()
        vdefs = try_parse_transposed_axis_volume_defs(grid, source_page=9)
        assert vdefs is not None, (
            "framework_table_parser 薄调用入口期望返回 volume_defs，实际 None"
        )
        assert set(vdefs.keys()) == {"commercial", "technical", "price"}

    def test_negative_case_returns_none(self) -> None:
        non_axis_grid = [
            ["评分因素", "技术方案", "价格", "商务"],
            ["A单位", "85", "92", "88"],
        ]
        result = try_parse_transposed_axis_volume_defs(non_axis_grid)
        assert result is None, "非转置轴表格应返回 None"


# ===========================================================================
# C1: parse_weight_summary_defs 生产入口必须接入转置轴路径
# ===========================================================================


class TestProductionEntryC1:
    """C1: parse_weight_summary_defs 须优先尝试转置轴，命中则返回最终分值制卷定义。

    当前 RED: parse_weight_summary_defs 以"加权得分"为第一道门，
              p9 fixture 无"加权得分" → 返回 {}。
    GREEN 后: 转置轴识别先于旧逻辑，p9 fixture 被识别并返回三卷定义。
    """

    def test_parse_weight_summary_defs_with_transposed_fixture_not_empty(self) -> None:
        """调用生产入口 parse_weight_summary_defs，不直调新模块。"""
        grid = _load_p9_grid()
        vdefs = parse_weight_summary_defs(grid)
        assert vdefs, (
            "parse_weight_summary_defs 应接入转置轴路径，返回非空卷定义\n"
            "  当前: '加权得分'门未命中 → 返回 {}\n"
            "  期望: 优先尝试转置轴 → {commercial:20, technical:40, price:40}"
        )

    def test_parse_weight_summary_defs_returns_correct_volumes(self) -> None:
        grid = _load_p9_grid()
        vdefs = parse_weight_summary_defs(grid)
        assert set(vdefs.keys()) == {"commercial", "technical", "price"}, (
            f"期望三卷 {{commercial,technical,price}}，实际 {set(vdefs.keys())}"
        )

    def test_parse_weight_summary_defs_final_score_volume_max(self) -> None:
        """最终分值制: volume_max = weight×100（不是各卷100）。"""
        grid = _load_p9_grid()
        vdefs = parse_weight_summary_defs(grid)
        if not vdefs:
            pytest.skip("parse_weight_summary_defs 尚未接入转置轴（C1 未实现）")
        assert abs(vdefs["commercial"].volume_max - 20.0) < 0.01
        assert abs(vdefs["technical"].volume_max - 40.0) < 0.01
        assert abs(vdefs["price"].volume_max - 40.0) < 0.01

    def test_parse_weight_summary_defs_old_logic_still_works(self) -> None:
        """旧逻辑（加权得分行）在非转置轴表格上必须仍然生效（无退化）。"""
        old_style_grid = [
            ["项目", "报价得分", "加权得分"],
            ["A公司", "0.40", "0.40"],
            ["B公司", "技术得分", "0.40"],
            ["C公司", "综合得分", "0.20"],
        ]
        # 含"加权得分"的旧格式，旧逻辑至少不报错（允许返回{}或找到卷定义）
        result = parse_weight_summary_defs(old_style_grid)
        assert isinstance(result, dict), "旧逻辑应返回 dict，不得抛异常"


# ===========================================================================
# C2: 去除 expected_total≈100 硬编码，支持任意公式总分
# ===========================================================================


class TestNon100TotalC2:
    """C2: 公式总分可以不是100（如80、120）。

    当前 RED: abs(expected_total - 100.0) > 0.5 门禁强制拒绝非100总分。
    GREEN 后: 仅要求 expected_total > 0，权重和=1即可。
    """

    def test_formula_total_80_accepted(self) -> None:
        """公式总分=80，权重25%+75%=100%，应解析为 expected_total=80。"""
        grid = [
            ["附表 权重表和综合得分汇总", "", ""],
            ["权重名称", "技术标权重", "价格标权重"],
            ["权重", "75%", "25%"],
            ["综合得分", "技术标得分+价格标得分=80", ""],
        ]
        result = parse_transposed_final_score_axis(grid)
        assert result is not None, (
            "公式总分=80 应被接受（C2: 去除 ≈100 硬编码），实际 None"
        )
        assert abs(result.expected_total - 80.0) < 0.01

    def test_formula_total_80_expected_by_volume_correct(self) -> None:
        grid = [
            ["附表 权重表和综合得分汇总", "", ""],
            ["权重名称", "技术标权重", "价格标权重"],
            ["权重", "75%", "25%"],
            ["综合得分", "技术标得分+价格标得分=80", ""],
        ]
        result = parse_transposed_final_score_axis(grid)
        if result is None:
            pytest.skip("C2 未实现")
        assert abs(result.expected_by_volume["technical"] - 60.0) < 0.01, (
            "technical: 0.75×80=60，实际 {result.expected_by_volume['technical']}"
        )
        assert abs(result.expected_by_volume["price"] - 20.0) < 0.01

    def test_formula_total_80_volume_defs_volume_max(self) -> None:
        grid = [
            ["附表 权重表和综合得分汇总", "", ""],
            ["权重名称", "技术标权重", "价格标权重"],
            ["权重", "75%", "25%"],
            ["综合得分", "技术标得分+价格标得分=80", ""],
        ]
        result = parse_transposed_final_score_axis(grid)
        if result is None:
            pytest.skip("C2 未实现")
        vdefs = final_score_axis_to_volume_defs(result)
        assert abs(vdefs["technical"].volume_max - 60.0) < 0.01, (
            f"volume_max 应为 60.0（0.75×80），实际 {vdefs['technical'].volume_max}"
        )
        assert abs(vdefs["price"].volume_max - 20.0) < 0.01

    def test_formula_total_120_accepted(self) -> None:
        """公式总分=120，权重1/3+2/3≈100%。"""
        grid = [
            ["权重表和综合得分汇总", "", ""],
            ["权重名称", "商务标权重", "技术标权重"],
            ["权重", "33%", "67%"],
            ["综合得分", "商务标得分+技术标得分=120", ""],
        ]
        # 33%+67%=100% 权重和满足（实际0.33+0.67=1.00）
        result = parse_transposed_final_score_axis(grid)
        assert result is not None, "公式总分=120 应被接受（C2 修复后）"
        assert abs(result.expected_total - 120.0) < 0.01


# ===========================================================================
# I1: 标签行唯一性约束（"X权重"格，无%，无=，fail-closed多候选）
# ===========================================================================


class TestLabelRowCandidatesI1:
    """I1: 标签行候选必须唯一——>=2 不同 X权重列，无%，无=，多候选 fail-closed。"""

    def test_two_label_row_candidates_fail_closed(self) -> None:
        """两行均含 >=2 X权重列 → 歧义，fail-closed 拒绝。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重"],   # 候选1
            ["评审名称", "商务评审权重", "技术评审权重"],  # 候选2（同列映射）
            ["权重", "60%", "40%"],
            ["综合得分", "商务标得分+技术标得分=100", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "两个标签行候选应 fail-closed 拒绝（歧义不得静默取首）"
        )

    def test_formula_row_not_hijack_label(self) -> None:
        """公式行（含'='）不得劫持为标签行，即使它含 X权重 词。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重", "价格标权重"],
            # 如果这里有一行含 "X权重" + "="，不应被当作标签行
            ["商务标权重=0.2", "技术标权重=0.4", "价格标权重=0.4", "说明"],
            ["权重", "20%", "40%", "40%"],
            ["综合得分", "商务标得分+技术标得分+价格标得分=100", "", ""],
        ]
        # Row 1 含 "=", 不得被选为标签行；Row 0 是合法标签行
        result = parse_transposed_final_score_axis(grid)
        # 只有row0是合法标签行(1个候选)→应该成功解析
        assert result is not None, (
            "公式行不应劫持标签行；唯一合法标签行应被正确识别"
        )
        assert set(result.weights.keys()) == {"commercial", "technical", "price"}

    def test_label_row_with_pct_cell_not_used_as_label(self) -> None:
        """若唯一含 X权重 列的行中包含百分比单元格，不得用作标签行 → 无标签 → 拒绝。"""
        grid = [
            ["附表 权重表综合得分汇总", "", "", "", ""],
            ["商务标权重", "技术标权重", "价格标权重", "40%", ""],  # X权重 + 纯%同行
            ["20%", "40%", "40%", "", ""],
            ["综合得分", "商务标得分+技术标得分+价格标得分=100", "", "", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "含百分比单元格的行不得用作标签行（I1 约束），应被拒绝"
        )


# ===========================================================================
# I2: 标签行到公式行之间恰有一个权重行
# ===========================================================================


class TestSingleWeightRowI2:
    """I2: 恰有一个完整有效权重行；多个权重行/冲突行拒绝，不能静默取首。

    当前 RED: _find_weights 静默取第一个匹配行，多权重行时不拒绝。
    GREEN 后: 计数后拒绝 len != 1 的情况。
    """

    def test_two_weight_rows_rejected(self) -> None:
        """两个完整有效权重行（均可被接受）→ 拒绝，不能静默取首。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重", "价格标权重"],
            ["方案A", "20%", "40%", "40%"],   # 完整权重行1
            ["方案B", "25%", "35%", "40%"],   # 完整权重行2
            ["投标人综合得分", "商务标得分+技术标得分+价格标得分=100", "", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "两个完整权重行应被拒绝（I2: 不能静默取首）"
        )

    def test_single_weight_row_still_accepted(self) -> None:
        """仅一个完整权重行时正常接受（I2 回归）。"""
        grid = _load_p9_grid()
        assert parse_transposed_final_score_axis(grid) is not None


# ===========================================================================
# I3: 强制顺序 label < weight < formula，公式在前拒绝
# ===========================================================================


class TestOrderEnforcementI3:
    """I3: 严格行序 label_row < weight_row < formula_row。

    当前 RED: _find_weights / _find_formula 全表扫描，不检查相对顺序。
    GREEN 后: weight 只在 (label, formula) 区间内查找；formula 只在 label 之后查找。
    """

    def test_formula_before_weight_row_rejected(self) -> None:
        """公式行在权重行之前：label < formula < weight → 拒绝。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重", "价格标权重"],  # row0: label
            ["综合得分", "商务标得分+技术标得分+价格标得分=100", "", ""],  # row1: formula BEFORE weight
            ["权重", "20%", "40%", "40%"],  # row2: weight AFTER formula
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "公式行在权重行之前（I3 顺序违反）应被拒绝"
        )

    def test_correct_order_label_weight_formula_accepted(self) -> None:
        """正确顺序 label < weight < formula 应被接受（I3 回归）。"""
        grid = _load_p9_grid()
        assert parse_transposed_final_score_axis(grid) is not None


# ===========================================================================
# I4: 多种格式（列序、卷数、重复同义、公式权重不一致）
# ===========================================================================


class TestVariousFormatsI4:
    """I4: 不固定列序/卷数量；拒绝重复同义卷、公式遗漏卷。"""

    def test_column_order_independent(self) -> None:
        """列序：价格→商务→技术（非默认顺序），应能正确识别权重。"""
        grid = [
            ["附表 权重表和综合得分汇总", "", "", ""],
            ["权重名称", "价格标权重", "商务标权重", "技术标权重"],  # 非标准列序
            ["权重", "40%", "20%", "40%"],
            ["综合得分", "价格标得分+商务标得分+技术标得分=100", "", ""],
        ]
        result = parse_transposed_final_score_axis(grid)
        assert result is not None, "非标准列序应能解析"
        assert abs(result.weights["price"] - 0.40) < 0.001
        assert abs(result.weights["commercial"] - 0.20) < 0.001
        assert abs(result.weights["technical"] - 0.40) < 0.001

    def test_four_volumes_parsed(self) -> None:
        """四卷（商务+技术+价格+服务）应能解析。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重", "价格标权重", "服务标权重"],
            ["权重", "15%", "40%", "35%", "10%"],
            ["综合得分", "商务标得分+技术标得分+价格标得分+服务标得分=100", "", "", ""],
        ]
        result = parse_transposed_final_score_axis(grid)
        assert result is not None, "四卷形态应能解析"
        assert set(result.weights.keys()) == {"commercial", "technical", "price", "service"}
        assert abs(sum(result.weights.values()) - 1.0) < 0.01

    def test_duplicate_synonym_volume_rejected(self) -> None:
        """重复同义卷：两列均含'价格'关键词 → 重复卷 → 拒绝。"""
        grid = [
            ["权重名称", "技术标权重", "价格标权重", "价格评分权重"],  # 价格出现2次
            ["权重", "60%", "20%", "20%"],
            ["综合得分", "技术标得分+价格标得分+价格评分得分=100", "", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "两列均映射到 price（重复同义卷）应被拒绝"
        )

    def test_formula_weight_inconsistency_rejected(self) -> None:
        """公式遗漏声明卷 → Gate 5 拒绝（公式与权重不一致）。"""
        grid = [
            ["权重名称", "商务标权重", "技术标权重", "价格标权重"],
            ["权重", "20%", "40%", "40%"],
            ["综合得分", "商务标得分+技术标得分=100", "", ""],  # 遗漏价格
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "公式遗漏'价格'卷（公式与权重不一致）应被拒绝"
        )

    def test_two_volume_non100_formula(self) -> None:
        """双卷+非100总分 = C2+双卷的组合场景。"""
        grid = [
            ["权重表和综合得分汇总", "", ""],
            ["权重名称", "商务标权重", "价格标权重"],
            ["权重", "40%", "60%"],
            ["综合得分", "商务标得分+价格标得分=60", ""],
        ]
        result = parse_transposed_final_score_axis(grid)
        assert result is not None, "双卷+总分60应能解析（C2+双卷组合）"
        assert abs(result.expected_total - 60.0) < 0.01
        assert abs(result.expected_by_volume["commercial"] - 24.0) < 0.01  # 0.4×60
        assert abs(result.expected_by_volume["price"] - 36.0) < 0.01  # 0.6×60


# ===========================================================================
# I5: 结构信号门禁（不得用 `权重名称` 单词单独开闸）
# ===========================================================================


class TestStructuralSignalI5:
    """I5: 不以`权重名称`单词单独开闸；须有强结构信号。

    当前 RED: `权重名称` 在 _SUMMARY_TRIGGER_TERMS 中 → 含该词即可过 Gate 1，
              即使列头无"X权重"结构，也能进入解析流程。
    GREEN 后: Gate 1 仅接受"权重表+综合得分汇总"标题信号，或>=2 "X权重"列头；
              仅有`权重名称`但列头是"商务分值/技术分值"形式的表格被拒绝。
    """

    def test_score_column_labels_without_X_weight_rejected(self) -> None:
        """列头是分值型（'商务分值'）而非权重型（'商务标权重'），应被拒绝。"""
        grid = [
            ["权重名称", "商务分值", "技术分值", "价格分值"],  # 无 X权重 结构
            ["权重", "20%", "40%", "40%"],
            ["综合得分", "商务得分+技术得分+价格得分=100", "", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "仅有'权重名称'但列头无'X权重'结构，应被拒绝（I5）"
        )

    def test_strong_title_signal_still_accepted(self) -> None:
        """有'权重表+综合得分汇总'强标题信号时，即使无'权重名称'也可识别。"""
        grid = _load_p9_grid()
        result = parse_transposed_final_score_axis(grid)
        assert result is not None, "强标题信号应通过 Gate 1（I5 正例）"

    def test_plain_percentage_table_no_signal_rejected(self) -> None:
        """完全普通百分比统计表（无任何识别信号）→ 拒绝。"""
        grid = [
            ["季度", "A占比", "B占比", "C占比"],
            ["Q1", "25%", "35%", "40%"],
            ["Q2", "30%", "30%", "40%"],
        ]
        assert parse_transposed_final_score_axis(grid) is None


# ===========================================================================
# M1: 混合关键词格必须拒绝（不按首匹配）
# ===========================================================================


class TestMixedKeywordCellM1:
    """M1: 含多个卷关键词的单元格（如'技术商务权重'）必须拒绝，不按首匹配。

    当前 RED: _cell_to_volume 用首匹配，'技术商务权重' → technical（遮蔽商务）。
    GREEN 后: 匹配多个卷关键词 → None（拒绝该格），导致标签行不足2列 → 整体拒绝。
    """

    def test_mixed_keyword_weight_cell_causes_rejection(self) -> None:
        """'技术商务权重'含两个卷词 → 该列不被识别 → 标签行仅剩1卷 → 拒绝。"""
        grid = [
            ["附表 权重表和综合得分汇总", "", ""],
            ["权重名称", "技术商务权重", "价格标权重"],   # '技术商务权重' 含技术+商务
            ["权重", "60%", "40%"],
            ["综合得分", "技术得分+商务得分+价格标得分=100", ""],
        ]
        assert parse_transposed_final_score_axis(grid) is None, (
            "含'技术商务权重'（两卷关键词混合）的标签格应被拒绝（M1）"
        )

    def test_clean_cells_with_same_volumes_still_accepted(self) -> None:
        """每列干净（仅含一个卷关键词+权重），正常接受（M1 回归）。"""
        grid = _load_p9_grid()
        assert parse_transposed_final_score_axis(grid) is not None

    def test_m2_fixture_企业综合实力_volume_is_commercial(self) -> None:
        """M2: fixture expected.items[0].volume 已修正为 commercial（14+6=20=商务卷合计）。"""
        path = _FIXTURE_DIR / "project_2503_grid.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data["expected"]["items"]
        first = next(it for it in items if it["name"] == "企业综合实力")
        assert first["volume"] == "commercial", (
            f"M2: 企业综合实力应归属commercial(14+6=20)，fixture值为 {first['volume']}"
        )
