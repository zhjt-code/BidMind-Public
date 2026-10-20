"""Task5：扁平顶层评分轴检测器测试套件。

覆盖：
- FlatScoringAxisRow 数据模型
- detect_flat_scoring_axis 正例（holdout31 / validation01 / sample33 / 非100总分）
- detect_flat_scoring_axis 拒绝条件（无表头/稀疏group/重复group/重复标签/符合性表等）
- resolve_axis_label 精确/去前缀/歧义/分值不匹配/未找到场景
- 序号前缀格式（（一）/(1)/1./一、）与 1.0 业务数误剥防护（I3）
- 区间分值格式 0-30 支持（I2）
- 回填「只填空不覆盖」（U5 门禁重设 2026-08-03 推翻归档的覆盖语义，见对应测试组注释）

注: 本文件测的是**单表结构检测**。"这张表是不是本文档的顶层轴"属文档级判定，
由 `test_flat_axis_document_gate.py` 覆盖——归档缺的正是那一层，故过触发。
"""

from dataclasses import dataclass

import pytest

from app.core.extractor.scoring.flat_scoring_axis import (
    FlatScoringAxisRow,
    backfill_flat_axis_labels,
    detect_flat_scoring_axis,
    resolve_axis_label,
)

pytestmark = pytest.mark.scoring

# ── 测试夹具：正例网格 ──────────────────────────────────────────────────────

# holdout31: 序号列|评分内容|评分标准/子项|子项规则|分值
# 父项: 企业综合实力(9分) / 实施方案(31分) / 谈判报价(60分)
GRID_HOLDOUT31: list[list[str]] = [
    ["评审项目", "评分内容", "评分标准", "", "分值"],
    ["一", "企业综合实力", "具体评分规则：具有1个不低于10万元的供货业绩得3分，每增加1个加2分，最多得9分", "", "9分"],
    ["二", "实施方案", "供货方案", "从供货进度安排的合理性及及时供货的具体措施等进行综合评审得分为0-12分", "31分"],
    ["", "", "质量保证方案", "从质量保证措施的全面性关键性等进行综合评审得分为0-10分", ""],
    ["", "", "售后方案", "从售后服务技术服务措施的合理性及可行性综合评审得分为0-9分", ""],
    ["五", "谈判报价", "通过初步评审的有效报价平均价作为评标基准价等于得满分60分扣完为止", "", "60分"],
]

# validation01: 评分大类|分值|评分细则  (label 在首列)
GRID_VALIDATION01: list[list[str]] = [
    ["评分大类", "分值", "评分细则"],
    ["一、投标报价", "45分", "等于评标基准价得满分每偏差1%扣1分扣完为止"],
    ["二、施工组织设计", "25分", "方案合理可行性强综合评分得满分一般酌情扣分"],
    ["三、项目管理机构", "15分", "人员资质完备业绩突出得满分一般酌情扣分"],
    ["四、企业业绩与实力", "10分", "业绩丰富资质齐全得满分一般酌情扣分"],
    ["五、信誉、承诺、体系", "5分", "获得质量管理体系认证得满分缺项按比例扣分"],
    ["总分合计", "100分", ""],
]

# sample33: 序号|评分因素|分值|评分标准|说明
GRID_SAMPLE33: list[list[str]] = [
    ["序号", "评分因素", "分值", "评分标准", "说明"],
    ["1", "投标报价", "10", "等于评标基准价得满分偏差扣分", ""],
    ["2", "施工组织设计", "20", "方案合理针对性强综合评审得0-20分", ""],
    ["", "", "", "方案内容详见附件", ""],
    ["3", "项目管理机构", "15", "人员资质完备业绩突出综合评审得0-15分", ""],
    ["4", "企业业绩", "20", "业绩丰富类似项目多综合评审得0-20分", ""],
    ["", "", "", "业绩材料详见附表", ""],
    ["5", "履约能力", "15", "财务健康信用良好综合评审得0-15分", ""],
    ["6", "服务方案", "10", "服务响应及时完备综合评审得0-10分", ""],
    ["7", "安全措施", "10", "措施齐全针对性强综合评审得0-10分", ""],
    ["合计", "100", "", "", ""],
]

# I4: 总分不等于100的评分表（3项合计60）
GRID_NON_100_TOTAL: list[list[str]] = [
    ["评分因素", "分值", "评分标准"],
    ["技术方案", "20", "技术方案合理性针对性综合评审得0-20分"],
    ["商务能力", "25", "商务能力资质业绩综合评审得0-25分"],
    ["投标报价", "15", "投标报价等于基准价得满分偏离扣分共15分"],
]


# ── 测试夹具：拒绝网格 ──────────────────────────────────────────────────────

# 无有效表头（headerless续表）
GRID_HEADERLESS: list[list[str]] = [
    ["20分", "15分", "30分"],
    ["技术方案", "商务实力", "投标报价"],
    ["方案描述A", "资质描述B", "报价说明C"],
]

# 重复group列（每组多行，C1同类：旧测试保留作对照）
GRID_HAS_GROUP: list[list[str]] = [
    ["大类", "评分项目", "分值"],
    ["技术", "技术方案", "20"],
    ["技术", "实施能力", "15"],
    ["商务", "资信实力", "10"],
    ["商务", "合同响应", "5"],
    ["价格", "投标报价", "50"],
]

# 稀疏group列（每组仅一行，全唯一，绕过重复值检测）—— C1 核心修复验证
GRID_HAS_SPARSE_GROUP: list[list[str]] = [
    ["大类", "评分项目", "分值"],
    ["技术", "技术方案", "20"],
    ["商务", "商务实力", "30"],
    ["价格", "投标报价", "50"],
]

# 稀疏group + 序号 + 规则列（5列层级表）
GRID_HAS_SPARSE_GROUP_5COL: list[list[str]] = [
    ["序号", "大类", "评分项目", "分值", "评分标准"],
    ["1", "技术", "技术方案", "20", "技术方案合理性综合评审得0-20分"],
    ["2", "商务", "商务实力", "30", "商务实力资质业绩综合评审得0-30分"],
    ["3", "价格", "投标报价", "50", "投标报价等于基准价得满分偏离扣分"],
]

# 重复标签（label列本身含重复值）
GRID_DUP_LABELS: list[list[str]] = [
    ["评分因素", "分值", "详细评分标准"],
    ["技术方案", "20", "技术方案的合理性及针对性进行综合评分得0-20分"],
    ["商务实力", "30", "商务实力包括企业业绩资质证书等综合评分得0-30分"],
    ["技术方案", "50", "相同技术方案重复出现的评分标准文本足够长"],
]

# 单数据行（不足2个父项）
GRID_ONE_DATA_ROW: list[list[str]] = [
    ["评分因素", "分值", "评分标准"],
    ["技术方案", "100", "技术方案合理性针对性等综合评分得0-100分"],
]

# 符合性表
GRID_CONFORMITY: list[list[str]] = [
    ["序号", "审查内容", "是否符合", "备注"],
    ["1", "营业执照", "符合", ""],
    ["2", "安全生产许可证", "符合", ""],
    ["3", "资质证书", "符合", ""],
]

# 空网格
GRID_EMPTY: list[list[str]] = []

# 仅1列（宽度不足）
GRID_ONE_COL: list[list[str]] = [
    ["评分项目"],
    ["技术方案"],
    ["投标报价"],
]

# 无合格分值列（分值全为0或无正数）
GRID_NO_SCORE_COL: list[list[str]] = [
    ["评分因素", "分值", "评分标准"],
    ["技术方案", "0", "技术方案评分标准文本用于测试目的"],
    ["商务实力", "0", "商务实力评分标准文本用于测试目的"],
    ["投标报价", "0", "投标报价评分标准文本用于测试目的"],
]

# 分值不足2行（仅1行有正分）
GRID_ONE_POSITIVE_SCORE_ROW: list[list[str]] = [
    ["评分因素", "分值", "评分标准"],
    ["技术方案", "60", "技术方案评分标准文本用于测试目的"],
    ["商务实力", "0", "商务实力评分标准文本用于测试目的"],
    ["投标报价", "0", "投标报价评分标准文本用于测试目的"],
]

# 区间分值格式（I2验证）
GRID_RANGE_SCORE: list[list[str]] = [
    ["评分因素", "分值", "评分标准"],
    ["技术方案", "0-30", "技术方案合理性综合评审取上限30分为满分"],
    ["商务实力", "0-40", "商务实力综合评审取上限40分为满分"],
    ["投标报价", "0-30", "投标报价评分取上限30分为满分"],
]

# 分值无"分"后缀
GRID_SCORE_WITHOUT_FEN: list[list[str]] = [
    ["评分因素", "分值", "评分标准"],
    ["技术方案", "30", "技术方案评分标准：方案合理针对性强得满分，一般酌情扣分"],
    ["商务实力", "20", "商务实力评分标准：资质齐全业绩丰富得满分，缺项酌情扣分"],
    ["投标报价", "50", "投标报价评分标准：等于基准价得满分，偏离扣分"],
]


# ═══════════════════════════════════════════════════════════════════════════
class TestFlatScoringAxisRowModel:
    """FlatScoringAxisRow 数据模型基础验证。"""

    def test_creates_with_valid_fields(self) -> None:
        row = FlatScoringAxisRow(row_index=1, label="投标报价", score=45.0)
        assert row.row_index == 1
        assert row.label == "投标报价"
        assert row.score == 45.0

    def test_frozen_immutable(self) -> None:
        row = FlatScoringAxisRow(row_index=1, label="投标报价", score=45.0)
        with pytest.raises((AttributeError, TypeError)):
            row.score = 60.0  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        a = FlatScoringAxisRow(row_index=1, label="投标报价", score=45.0)
        b = FlatScoringAxisRow(row_index=1, label="投标报价", score=45.0)
        assert a == b

    def test_hashable_in_set(self) -> None:
        rows = {
            FlatScoringAxisRow(row_index=1, label="A", score=10.0),
            FlatScoringAxisRow(row_index=1, label="A", score=10.0),
        }
        assert len(rows) == 1


# ═══════════════════════════════════════════════════════════════════════════
class TestDetectFlatScoringAxisPositive:
    """正例：三种典型结构均能正确提取轴行。"""

    # holdout31 ──────────────────────────────────────────────────────────
    def test_holdout31_count_3(self) -> None:
        rows = detect_flat_scoring_axis(GRID_HOLDOUT31)
        assert len(rows) == 3

    def test_holdout31_labels(self) -> None:
        rows = detect_flat_scoring_axis(GRID_HOLDOUT31)
        labels = {r.label for r in rows}
        assert "企业综合实力" in labels
        assert "实施方案" in labels
        assert "谈判报价" in labels

    def test_holdout31_scores(self) -> None:
        rows = detect_flat_scoring_axis(GRID_HOLDOUT31)
        scores = sorted(r.score for r in rows)
        assert scores == [9.0, 31.0, 60.0]

    # validation01 ────────────────────────────────────────────────────────
    def test_validation01_count_5(self) -> None:
        rows = detect_flat_scoring_axis(GRID_VALIDATION01)
        assert len(rows) == 5

    def test_validation01_labels_unique(self) -> None:
        rows = detect_flat_scoring_axis(GRID_VALIDATION01)
        labels = [r.label for r in rows]
        assert len(labels) == len(set(labels))

    def test_validation01_labels_contain_business_text(self) -> None:
        rows = detect_flat_scoring_axis(GRID_VALIDATION01)
        labels = [r.label for r in rows]
        assert any("投标报价" in lbl for lbl in labels)
        assert any("施工" in lbl for lbl in labels)

    # sample33 ────────────────────────────────────────────────────────────
    def test_sample33_count_7(self) -> None:
        rows = detect_flat_scoring_axis(GRID_SAMPLE33)
        assert len(rows) == 7

    def test_sample33_scores(self) -> None:
        rows = detect_flat_scoring_axis(GRID_SAMPLE33)
        scores = sorted(r.score for r in rows)
        assert scores == [10.0, 10.0, 10.0, 15.0, 15.0, 20.0, 20.0]

    def test_sample33_labels_unique(self) -> None:
        rows = detect_flat_scoring_axis(GRID_SAMPLE33)
        labels = [r.label for r in rows]
        assert len(labels) == len(set(labels))

    # I4: 总分非100 ────────────────────────────────────────────────────────
    def test_non_100_total_is_valid(self) -> None:
        """总分不等于100的评分表仍应被识别（不要求总分闭合）。"""
        rows = detect_flat_scoring_axis(GRID_NON_100_TOTAL)
        assert len(rows) == 3
        total = sum(r.score for r in rows)
        assert total == 60.0
        assert total != 100.0


# ═══════════════════════════════════════════════════════════════════════════
class TestDetectFlatScoringAxisReject:
    """拒绝条件：各类不合格网格均返回空列表。"""

    def test_reject_headerless(self) -> None:
        assert detect_flat_scoring_axis(GRID_HEADERLESS) == []

    def test_reject_has_group_column(self) -> None:
        """重复group列（旧场景）应被拒绝。"""
        assert detect_flat_scoring_axis(GRID_HAS_GROUP) == []

    def test_reject_sparse_group_column(self) -> None:
        """稀疏独立group列（每组唯一非空值，无重复）应被拒绝。C1修复验证。"""
        assert detect_flat_scoring_axis(GRID_HAS_SPARSE_GROUP) == []

    def test_reject_sparse_group_column_5col(self) -> None:
        """含序号+稀疏group+item+分值+规则的5列层级表应被拒绝。"""
        assert detect_flat_scoring_axis(GRID_HAS_SPARSE_GROUP_5COL) == []

    def test_reject_duplicate_labels(self) -> None:
        assert detect_flat_scoring_axis(GRID_DUP_LABELS) == []

    def test_reject_single_data_row(self) -> None:
        assert detect_flat_scoring_axis(GRID_ONE_DATA_ROW) == []

    def test_reject_conformity_table(self) -> None:
        assert detect_flat_scoring_axis(GRID_CONFORMITY) == []

    def test_reject_empty_grid(self) -> None:
        assert detect_flat_scoring_axis(GRID_EMPTY) == []

    def test_reject_one_column(self) -> None:
        assert detect_flat_scoring_axis(GRID_ONE_COL) == []

    def test_reject_no_score_column(self) -> None:
        assert detect_flat_scoring_axis(GRID_NO_SCORE_COL) == []

    def test_reject_grid_with_one_positive_score_row(self) -> None:
        assert detect_flat_scoring_axis(GRID_ONE_POSITIVE_SCORE_ROW) == []


# ═══════════════════════════════════════════════════════════════════════════
class TestDetectFlatScoringAxisEdgeCases:
    """边缘场景与健壮性验证。"""

    def test_score_without_fen_suffix(self) -> None:
        """分值无"分"后缀仍能识别。"""
        rows = detect_flat_scoring_axis(GRID_SCORE_WITHOUT_FEN)
        assert len(rows) == 3
        scores = sorted(r.score for r in rows)
        assert scores == [20.0, 30.0, 50.0]

    def test_range_score_format(self) -> None:
        """分值列包含区间格式 0-30 时应解析为上限值（I2验证）。"""
        rows = detect_flat_scoring_axis(GRID_RANGE_SCORE)
        assert len(rows) == 3
        scores = sorted(r.score for r in rows)
        assert scores == [30.0, 30.0, 40.0]

    def test_minimum_valid_flat_table(self) -> None:
        """最小合法扁平表（2个父项）应被识别。"""
        grid = [
            ["评分因素", "分值", "评分标准内容详细文本"],
            ["技术方案", "60", "技术方案合理性针对性等综合评分得0-60分"],
            ["投标报价", "40", "投标报价评分标准等于基准价得满分偏离扣分"],
        ]
        rows = detect_flat_scoring_axis(grid)
        assert len(rows) == 2

    def test_total_row_excluded(self) -> None:
        """合计行不计入轴行数。"""
        rows = detect_flat_scoring_axis(GRID_VALIDATION01)
        labels = [r.label for r in rows]
        assert "总分合计" not in labels
        assert not any("合计" in lbl for lbl in labels)

    def test_row_index_points_to_correct_grid_row(self) -> None:
        """row_index 指向原始网格中包含该标签的行（不硬编码列号）。"""
        rows = detect_flat_scoring_axis(GRID_SAMPLE33)
        for r in rows:
            grid_row = GRID_SAMPLE33[r.row_index]
            assert r.label in grid_row, (r, grid_row)

    def test_labels_are_nonempty_strings(self) -> None:
        """所有提取行的标签均为非空字符串。"""
        rows = detect_flat_scoring_axis(GRID_HOLDOUT31)
        for r in rows:
            assert isinstance(r.label, str)
            assert len(r.label) > 0

    def test_scores_are_positive_floats(self) -> None:
        """所有提取行的分值均为严格正浮点数。"""
        rows = detect_flat_scoring_axis(GRID_SAMPLE33)
        for r in rows:
            assert isinstance(r.score, float)
            assert r.score > 0


# ═══════════════════════════════════════════════════════════════════════════
class TestResolveAxisLabel:
    """resolve_axis_label：精确/去前缀匹配、歧义返回 None。"""

    _AXIS_ROWS = [
        FlatScoringAxisRow(row_index=1, label="一、投标报价", score=45.0),
        FlatScoringAxisRow(row_index=2, label="二、施工组织设计", score=25.0),
        FlatScoringAxisRow(row_index=3, label="三、项目管理机构", score=15.0),
        FlatScoringAxisRow(row_index=4, label="四、企业业绩与实力", score=10.0),
        FlatScoringAxisRow(row_index=5, label="五、信誉、承诺、体系", score=5.0),
    ]

    def test_exact_name_match(self) -> None:
        result = resolve_axis_label("一、投标报价", 45.0, self._AXIS_ROWS)
        assert result == "一、投标报价"

    def test_stripped_prefix_match(self) -> None:
        """去序号前缀后名称匹配。"""
        result = resolve_axis_label("投标报价", 45.0, self._AXIS_ROWS)
        assert result == "一、投标报价"

    def test_complex_label_stripped_match(self) -> None:
        result = resolve_axis_label("信誉、承诺、体系", 5.0, self._AXIS_ROWS)
        assert result == "五、信誉、承诺、体系"

    def test_wrong_score_returns_none(self) -> None:
        """分值不匹配 → None。"""
        result = resolve_axis_label("投标报价", 99.0, self._AXIS_ROWS)
        assert result is None

    def test_unknown_name_returns_none(self) -> None:
        """名称不存在 → None。"""
        result = resolve_axis_label("完全不存在的条目", 45.0, self._AXIS_ROWS)
        assert result is None

    def test_empty_axis_rows_returns_none(self) -> None:
        result = resolve_axis_label("投标报价", 45.0, [])
        assert result is None

    def test_ambiguous_same_name_same_score_returns_none(self) -> None:
        """同分同名（stripped后）多候选 → None（禁止静默取首个）。"""
        rows = [
            FlatScoringAxisRow(row_index=1, label="一、技术方案", score=20.0),
            FlatScoringAxisRow(row_index=2, label="二、技术方案", score=20.0),
        ]
        assert resolve_axis_label("技术方案", 20.0, rows) is None

    def test_same_score_different_name_exact_match(self) -> None:
        """同分不同名 → 精确 name 匹配找到唯一候选。"""
        rows = [
            FlatScoringAxisRow(row_index=1, label="技术方案", score=20.0),
            FlatScoringAxisRow(row_index=2, label="商务实力", score=20.0),
        ]
        assert resolve_axis_label("技术方案", 20.0, rows) == "技术方案"
        assert resolve_axis_label("商务实力", 20.0, rows) == "商务实力"


# ═══════════════════════════════════════════════════════════════════════════
class TestOrdinalPrefixStripping:
    """序号前缀支持与业务数字保护（I3验证，通过 resolve_axis_label 间接测试）。"""

    def _make_row(self, label: str, score: float = 30.0) -> list[FlatScoringAxisRow]:
        return [FlatScoringAxisRow(row_index=1, label=label, score=score)]

    def test_chinese_ordinal_一(self) -> None:
        """一、前缀可剥离。"""
        rows = self._make_row("一、投标报价", 45.0)
        assert resolve_axis_label("投标报价", 45.0, rows) == "一、投标报价"

    def test_fullwidth_parenthesis_ordinal(self) -> None:
        """（一）前缀可剥离（I3）。"""
        rows = self._make_row("（一）投标报价", 45.0)
        assert resolve_axis_label("投标报价", 45.0, rows) == "（一）投标报价"

    def test_halfwidth_parenthesis_ordinal(self) -> None:
        """(1) 前缀可剥离（I3）。"""
        rows = self._make_row("(1)技术方案")
        assert resolve_axis_label("技术方案", 30.0, rows) == "(1)技术方案"

    def test_dot_ordinal_prefix(self) -> None:
        """1.前缀可剥离（非小数）（I3）。"""
        rows = self._make_row("1.技术方案")
        assert resolve_axis_label("技术方案", 30.0, rows) == "1.技术方案"

    def test_decimal_number_not_stripped(self) -> None:
        """1.0等小数不被当作序号前缀误剥（I3核心防护）。"""
        rows = self._make_row("1.0技术方案")
        # "技术方案" 不应因误剥 "1.0" 而匹配成功
        assert resolve_axis_label("技术方案", 30.0, rows) is None
        # 完整名称 "1.0技术方案" 精确匹配仍有效
        assert resolve_axis_label("1.0技术方案", 30.0, rows) == "1.0技术方案"

    def test_decimal_15_not_stripped(self) -> None:
        """1.5等小数同样不被误剥。"""
        rows = self._make_row("1.5项目服务方案")
        assert resolve_axis_label("项目服务方案", 30.0, rows) is None


# ── validation04-like 分段层级表测试夹具 ───────────────────────────────────

# validation04：首列含4个父级组标题行，label 在第2列，应被拒绝
GRID_VALIDATION04: list[list[str]] = [
    ["序号", "评分因素", "分值", "评分标准"],
    ["施工组织设计", "", "", ""],                         # 父级组标题行1
    ["1", "施工方案", "20", "方案合理性综合评审得0-20分"],
    ["2", "施工工艺", "15", "工艺先进性综合评审得0-15分"],
    ["主要人员", "", "", ""],                             # 父级组标题行2
    ["3", "项目经理资质", "10", "资质证书综合评审得0-10分"],
    ["4", "技术人员配置", "10", "人员配置综合评审得0-10分"],
    ["其他因素-技术能力", "", "", ""],                   # 父级组标题行3
    ["5", "施工机械配置", "10", "机械配置综合评审得0-10分"],
    ["价格因素", "", "", ""],                             # 父级组标题行4
    ["6", "投标报价", "35", "报价综合评审得0-35分"],
]

# 单个全宽组标题行（不触发≥2门禁，仍可识别其余评分项）
GRID_ONE_SECTION_HEADER: list[list[str]] = [
    ["评分因素", "分值", "评分标准"],
    ["技术能力", "", ""],                                 # 唯一全宽组标题行
    ["施工方案", "30", "方案合理性综合评审得0-30分"],
    ["商务实力", "30", "商务实力资质业绩综合评审得0-30分"],
    ["投标报价", "40", "报价综合评审得0-40分"],
]

# 仅规则列非空的续写行（位于 label 右侧，即使是短文本也不计组标题）
GRID_RULE_CONTINUATION: list[list[str]] = [
    ["评分因素", "分值", "评分标准"],
    ["技术方案", "30", "技术方案合理性综合评审"],
    ["", "", "方案内容详见附件"],
    ["商务实力", "30", "商务实力资质业绩综合评审"],
    ["", "", "业绩材料详见附表"],
    ["投标报价", "40", "报价综合评审得0-40分"],
]


# ═══════════════════════════════════════════════════════════════════════════
class TestSectionHeaderGate:
    """全宽组标题行门禁：validation04-like 分段层级表检测。"""

    def test_reject_validation04_section_headers(self) -> None:
        """含4个全宽组标题行（≥2）的分段层级表应被拒绝。"""
        assert detect_flat_scoring_axis(GRID_VALIDATION04) == []

    def test_reject_exactly_two_section_headers(self) -> None:
        """恰好2个全宽组标题行即触发门禁。"""
        grid = [
            ["序号", "评分因素", "分值", "评分标准"],
            ["施工组织设计", "", "", ""],                 # 父级标题行1
            ["1", "施工方案", "25", "方案综合评审得0-25分"],
            ["2", "施工工艺", "15", "工艺综合评审得0-15分"],
            ["价格因素", "", "", ""],                     # 父级标题行2
            ["3", "报价评分", "60", "报价综合评审得0-60分"],
        ]
        assert detect_flat_scoring_axis(grid) == []

    def test_single_section_header_does_not_trigger(self) -> None:
        """单个全宽组标题行不触发≥2门禁，3个评分项正常检出。"""
        rows = detect_flat_scoring_axis(GRID_ONE_SECTION_HEADER)
        assert len(rows) == 3
        labels = {r.label for r in rows}
        assert "施工方案" in labels
        assert "商务实力" in labels
        assert "投标报价" in labels

    def test_rule_continuation_rows_not_counted_as_title(self) -> None:
        """label 右侧规则列的唯一短文本续写行不被误判为组标题行。"""
        rows = detect_flat_scoring_axis(GRID_RULE_CONTINUATION)
        assert len(rows) == 3
        labels = {r.label for r in rows}
        assert "技术方案" in labels
        assert "商务实力" in labels
        assert "投标报价" in labels

    def test_holdout31_not_affected_by_gate(self) -> None:
        """holdout31 无全宽组标题行，不受新门禁影响，仍识别3父项。"""
        rows = detect_flat_scoring_axis(GRID_HOLDOUT31)
        assert len(rows) == 3

    def test_validation01_not_affected_by_gate(self) -> None:
        """validation01 无全宽组标题行，不受新门禁影响，仍识别5项。"""
        rows = detect_flat_scoring_axis(GRID_VALIDATION01)
        assert len(rows) == 5

    def test_projected_sample33_not_affected_by_gate(self) -> None:
        """projected sample33 含规则列续写行，仍识别7项。"""
        rows = detect_flat_scoring_axis(GRID_SAMPLE33)
        assert len(rows) == 7

    def test_non_100_total_not_affected_by_gate(self) -> None:
        """非100总分正例无全宽组标题行，仍识别3项。"""
        rows = detect_flat_scoring_axis(GRID_NON_100_TOTAL)
        assert len(rows) == 3

    def test_sparse_group_still_rejected(self) -> None:
        """稀疏group拒绝逻辑不受新门禁干扰，仍返回[]。"""
        assert detect_flat_scoring_axis(GRID_HAS_SPARSE_GROUP) == []


# ── H2修复：产出标签去序号前缀 ─────────────────────────────────────────────

# validation01式嵌入序号网格（label列含一、/二、前缀，产出后应已清除）
GRID_VALIDATION01_ORDINAL = GRID_VALIDATION01  # 已含"一、投标报价"等前缀

# 去前缀后碰撞：一、投标报价 + 二、投标报价 → 两者去前缀后均为"投标报价" → 拒绝
GRID_DUP_AFTER_STRIP: list[list[str]] = [
    ["评分因素", "分值", "评分标准"],
    ["一、投标报价", "20", "投标报价评分标准合理性综合评审得0-20分"],
    ["二、投标报价", "30", "投标报价评分标准先进性综合评审得0-30分"],
    ["三、商务实力", "50", "商务实力评分标准资质业绩综合评审得0-50分"],
]


# ═══════════════════════════════════════════════════════════════════════════
class TestStrippedLabelOutput:
    """H2修复验证：detect产出的FlatScoringAxisRow.label已统一去序号前缀。"""

    def test_validation01_labels_are_stripped(self) -> None:
        """validation01式序号前缀（一、/二、等）在产出label中已被剥离。"""
        rows = detect_flat_scoring_axis(GRID_VALIDATION01)
        labels = [r.label for r in rows]
        assert "投标报价" in labels
        assert "施工组织设计" in labels
        assert "项目管理机构" in labels
        # 原始带序号版本不应出现在产出中
        assert "一、投标报价" not in labels
        assert "二、施工组织设计" not in labels

    def test_resolve_matches_stripped_label_directly(self) -> None:
        """清洁标签产出后，resolve_axis_label精确匹配无需再剥前缀。"""
        rows = detect_flat_scoring_axis(GRID_VALIDATION01)
        # 下游直接用parser item名匹配，不再需要剥前缀
        assert resolve_axis_label("投标报价", 45.0, rows) == "投标报价"
        assert resolve_axis_label("施工组织设计", 25.0, rows) == "施工组织设计"
        assert resolve_axis_label("信誉、承诺、体系", 5.0, rows) == "信誉、承诺、体系"

    def test_reject_collision_after_strip(self) -> None:
        """去前缀后标签碰撞 → 拒绝整个轴（不静默保留首个）。"""
        assert detect_flat_scoring_axis(GRID_DUP_AFTER_STRIP) == []

    def test_no_prefix_labels_unchanged(self) -> None:
        """无序号前缀的标签不受影响，sample33/holdout31正例仍正常识别。"""
        rows_s33 = detect_flat_scoring_axis(GRID_SAMPLE33)
        labels_s33 = [r.label for r in rows_s33]
        assert "投标报价" in labels_s33  # sample33 无前缀，原样保留
        assert len(rows_s33) == 7

        rows_h31 = detect_flat_scoring_axis(GRID_HOLDOUT31)
        labels_h31 = {r.label for r in rows_h31}
        assert "企业综合实力" in labels_h31
        assert "实施方案" in labels_h31
        assert "谈判报价" in labels_h31

    def test_bracketed_ordinal_stripped_in_detect(self) -> None:
        """（一）/(1)格式前缀在detect产出中同样被剥离。"""
        grid = [
            ["评分因素", "分值", "评分标准"],
            ["（一）技术方案", "40", "技术方案合理性综合评审得0-40分"],
            ["（二）商务实力", "30", "商务实力资质业绩综合评审得0-30分"],
            ["（三）投标报价", "30", "报价综合评审得0-30分"],
        ]
        rows = detect_flat_scoring_axis(grid)
        labels = [r.label for r in rows]
        assert "技术方案" in labels
        assert "商务实力" in labels
        assert "投标报价" in labels
        assert "（一）技术方案" not in labels

    def test_dot_ordinal_stripped_in_detect(self) -> None:
        """1.格式前缀在detect产出中被剥离；1.0格式不被误剥。"""
        grid = [
            ["评分因素", "分值", "评分标准"],
            ["1.技术方案", "40", "技术方案合理性综合评审得0-40分"],
            ["2.商务实力", "30", "商务实力资质业绩综合评审得0-30分"],
            ["3.投标报价", "30", "报价综合评审得0-30分"],
        ]
        rows = detect_flat_scoring_axis(grid)
        labels = [r.label for r in rows]
        assert "技术方案" in labels
        assert "1.技术方案" not in labels


@dataclass
class _AxisLabelItem:
    """测试用最小评分项协议实现。"""

    score_item_name: str
    score_weight: float
    volume_label: str | None


class TestBackfillFlatAxisLabels:
    """严格扁平轴标签回填：唯一匹配才覆盖，门禁未命中时保持原值。"""

    def test_holdout32_like_existing_two_group_labels_are_preserved(self) -> None:
        items = [
            _AxisLabelItem("投标报价", 10.0, "投标报价"),
            _AxisLabelItem("施工组织设计", 20.0, "施工组织设计"),
            _AxisLabelItem("项目管理机构", 15.0, "施工组织设计"),
            _AxisLabelItem("企业业绩", 20.0, "施工组织设计"),
            _AxisLabelItem("履约能力", 15.0, "施工组织设计"),
            _AxisLabelItem("服务方案", 10.0, "施工组织设计"),
            _AxisLabelItem("安全措施", 10.0, "施工组织设计"),
        ]
        original_labels = [item.volume_label for item in items]

        updated = backfill_flat_axis_labels(items, GRID_SAMPLE33)

        assert updated == 0
        assert [item.volume_label for item in items] == original_labels

    def test_validation04_like_five_existing_groups_and_empty_axis_are_preserved(
        self,
    ) -> None:
        items = [
            _AxisLabelItem("施工方案", 20.0, "组甲"),
            _AxisLabelItem("施工工艺", 15.0, "组甲"),
            _AxisLabelItem("项目经理资质", 10.0, "组乙"),
            _AxisLabelItem("技术人员配置", 10.0, "组丙"),
            _AxisLabelItem("施工机械配置", 10.0, "组丁"),
            _AxisLabelItem("投标报价", 35.0, "组戊"),
        ]
        original_labels = [item.volume_label for item in items]

        updated = backfill_flat_axis_labels(items, GRID_VALIDATION04)

        assert detect_flat_scoring_axis(GRID_VALIDATION04) == []
        assert updated == 0
        assert [item.volume_label for item in items] == original_labels

    # ── 只填空不覆盖（U5 门禁重设 2026-08-03，推翻归档的「覆盖垃圾标签」意图）──
    #
    # 归档设计为用轴标签覆盖已有标签，理由是清理 `×`/`但缺` 这类解析残片。
    # 58 份语料实测该覆盖能力零收益、两处真实回归:
    #   sample_corpus/12 持有 `技术商务部分`(70)/`报价部分`(30) 两组来自声明的
    #   权威卷标签，被覆盖后账本组行 extracted_sum 由 70/30 归零、缺口变 -70/-30；
    #   holdout/30 的 `商务部分` 被覆盖成 `咨询服务（10分）(2)`。
    # 归档守卫的第三个条件 `existing_label_set.issubset(axis_label_set)` 写反了——
    # 它只在已有标签本就是轴标签时保护（即已填过的平凡情形），
    # 真正需要保护的「来自外部更粗层级的权威分组」反而不受保护。
    # 「已有标签必定来自更有上下文的来源」是可判定的单一原则，故改为只填空。

    def test_unique_garbage_labels_are_preserved_not_overwritten(self) -> None:
        """已有标签一律保留——即便看起来是噪声。

        判定「噪声 vs 权威」需要本函数不具备的上下文（声明对/group 列来源），
        猜错的代价是抹掉权威分组（sample_corpus/12 实测），故一律不覆盖。
        """
        items = [
            _AxisLabelItem("投标报价", 10.0, "噪声甲"),
            _AxisLabelItem("施工组织设计", 20.0, "噪声乙"),
            _AxisLabelItem("项目管理机构", 15.0, "噪声丙"),
        ]

        updated = backfill_flat_axis_labels((item for item in items), GRID_SAMPLE33)

        assert updated == 0
        assert [item.volume_label for item in items] == ["噪声甲", "噪声乙", "噪声丙"]

    def test_coarse_external_grouping_is_never_overwritten(self) -> None:
        """粗粒度外部分组必须原样保留（sample_corpus/12 回归的最小复现）。

        标签数少于项数说明存在真实分组层级，它比"每项自成一卷"信息量更高。
        """
        items = [
            _AxisLabelItem("投标报价", 10.0, "报价部分"),
            _AxisLabelItem("施工组织设计", 20.0, "技术商务部分"),
            _AxisLabelItem("项目管理机构", 15.0, "技术商务部分"),
        ]

        updated = backfill_flat_axis_labels(items, GRID_SAMPLE33)

        assert updated == 0
        assert [item.volume_label for item in items] == [
            "报价部分", "技术商务部分", "技术商务部分",
        ]

    def test_partial_empty_labels_fill_only_the_empty_ones(self) -> None:
        """混合形态: 空标签被填，已有标签一项不动。"""
        items = [
            _AxisLabelItem("投标报价", 45.0, "旧值甲"),
            _AxisLabelItem("施工组织设计", 25.0, None),
            _AxisLabelItem("项目管理机构", 15.0, ""),
            _AxisLabelItem("企业业绩与实力", 10.0, "旧值丙"),
            _AxisLabelItem("信誉、承诺、体系", 5.0, "   "),
        ]

        updated = backfill_flat_axis_labels(items, GRID_VALIDATION01)

        assert updated == 3
        assert [item.volume_label for item in items] == [
            "旧值甲",
            "施工组织设计",
            "项目管理机构",
            "旧值丙",
            "信誉、承诺、体系",
        ]

    def test_uniqueness_is_judged_over_all_items_not_only_empty_ones(self) -> None:
        """唯一性判定须覆盖全部 items，不得因只填空而放行本应竞争的匹配。

        两项同名同分时轴标签归属歧义，即使其中一项标签为空也不得填入——
        只看空标签子集会让歧义匹配变成"唯一"匹配。
        """
        items = [
            _AxisLabelItem("施工组织设计", 20.0, None),
            _AxisLabelItem("施工组织设计", 20.0, "已有标签"),
        ]

        updated = backfill_flat_axis_labels(items, GRID_SAMPLE33)

        assert updated == 0
        assert items[0].volume_label is None

    def test_holdout31_all_empty_labels_allow_axis_overwrite(self) -> None:
        items = [
            _AxisLabelItem("企业综合实力", 9.0, None),
            _AxisLabelItem("实施方案", 31.0, ""),
            _AxisLabelItem("谈判报价", 60.0, None),
        ]

        updated = backfill_flat_axis_labels(items, GRID_HOLDOUT31)

        assert updated == 3
        assert [item.volume_label for item in items] == [
            "企业综合实力",
            "实施方案",
            "谈判报价",
        ]

    def test_same_existing_label_for_all_items_is_preserved(self) -> None:
        items = [
            _AxisLabelItem("投标报价", 10.0, "施工组织设计"),
            _AxisLabelItem("施工组织设计", 20.0, "施工组织设计"),
            _AxisLabelItem("项目管理机构", 15.0, "施工组织设计"),
            _AxisLabelItem("企业业绩", 20.0, "施工组织设计"),
            _AxisLabelItem("履约能力", 15.0, "施工组织设计"),
            _AxisLabelItem("服务方案", 10.0, "施工组织设计"),
            _AxisLabelItem("安全措施", 10.0, "施工组织设计"),
        ]

        updated = backfill_flat_axis_labels(items, GRID_SAMPLE33)

        assert updated == 0
        assert {item.volume_label for item in items} == {"施工组织设计"}

    def test_long_rule_fragment_label_is_still_preserved(self) -> None:
        """连规则正文碎片这种明显垃圾的标签也保留——不猜"什么算垃圾"。

        代价是脏标签留存（原状，非回归）；收益是权威分组永不被误抹。
        清理脏标签应在其产生处治理，不在本函数事后猜测。
        """
        items = [_AxisLabelItem(
            "项目管理机构",
            15.0,
            "项目团队人员配置以及专业程度相关评分规则正文碎片",
        )]

        updated = backfill_flat_axis_labels(items, GRID_SAMPLE33)

        assert updated == 0
        assert items[0].volume_label == "项目团队人员配置以及专业程度相关评分规则正文碎片"

    def test_fills_empty_label_but_keeps_short_heuristic_label(self) -> None:
        """空标签填入轴标签；短启发式标签（如 `价格`）属已有来源，保留。"""
        items = [
            _AxisLabelItem("施工组织设计", 20.0, None),
            _AxisLabelItem("投标报价", 10.0, "价格"),
        ]

        updated = backfill_flat_axis_labels(items, GRID_SAMPLE33)

        assert updated == 1
        assert [item.volume_label for item in items] == ["施工组织设计", "价格"]

    def test_validation04_empty_axis_preserves_authoritative_labels(self) -> None:
        items = [
            _AxisLabelItem("施工方案", 20.0, "施工组织设计"),
            _AxisLabelItem("投标报价", 35.0, "价格因素"),
        ]

        updated = backfill_flat_axis_labels(items, GRID_VALIDATION04)

        assert updated == 0
        assert [item.volume_label for item in items] == ["施工组织设计", "价格因素"]

    def test_group_table_preserves_existing_group_labels(self) -> None:
        items = [
            _AxisLabelItem("技术方案", 20.0, "技术"),
            _AxisLabelItem("投标报价", 50.0, "价格"),
        ]

        updated = backfill_flat_axis_labels(items, GRID_HAS_GROUP)

        assert updated == 0
        assert [item.volume_label for item in items] == ["技术", "价格"]

    def test_unmatched_item_is_unchanged(self) -> None:
        item = _AxisLabelItem("不存在的评分项", 99.0, "原标签")

        updated = backfill_flat_axis_labels([item], GRID_SAMPLE33)

        assert updated == 0
        assert item.volume_label == "原标签"

    def test_ambiguous_duplicate_items_are_unchanged(self) -> None:
        items = [
            _AxisLabelItem("投标报价", 10.0, "旧标签甲"),
            _AxisLabelItem("投标报价", 10.0, "旧标签乙"),
        ]

        updated = backfill_flat_axis_labels(items, GRID_SAMPLE33)

        assert updated == 0
        assert [item.volume_label for item in items] == ["旧标签甲", "旧标签乙"]

    def test_backfill_keeps_embedded_ordinal_cleaning(self) -> None:
        item = _AxisLabelItem("投标报价", 45.0, "")

        updated = backfill_flat_axis_labels([item], GRID_VALIDATION01)

        assert updated == 1
        assert item.volume_label == "投标报价"
