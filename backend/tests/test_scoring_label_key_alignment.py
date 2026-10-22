# -*- coding: utf-8 -*-
"""归一键两侧口径对齐防御测试（训练样本 2468/2471/2463 真实形态）。

风险映射:
- 2468(评分表结构-15): 项侧标签带括注"商务部分（16分）" vs 声明侧"商务部分",
 归一键失配 → 归卷全对却报"要求16提取0"假缺口;
- 2471(评分表结构-18): 声明"报价评审" vs 项侧"价格部分", 无同义折叠 → 假缺口60分;
 refine 按项名"业绩"无中生有造标签, 阻断卷定义回填 → 商务卷假缺口9分;
- 2463(评分表结构-10): 行内规则文本"满分为10.0000分"(该行分值=10)被捕为文档级
 总分候选 → L3 高置信错基准"要求10分提取100分"。
"""
from __future__ import annotations

import pytest

from app.core.extractor.scoring.ledger.declaration_capture import (
    capture_scoring_declarations,
)
from app.core.extractor.scoring.ledger.label_normalization import normalize_group_label
from app.core.extractor.scoring.ledger.ledger_reconciliation import (
    reconcile_scoring_ledger,
)
from app.core.extractor.scoring.numeric_score_column_parser import (
    parse_numeric_score_column_table,
)
from app.core.extractor.scoring.volume_labeling import refine_scoring_group_label

pytestmark = pytest.mark.scoring


class TestNormalizeGroupLabelParenScore:
    """括注分值是原文冗余修饰, 归一键必须剥离。"""

    def test_fullwidth_paren_score_stripped(self) -> None:
        assert normalize_group_label("商务部分（16分）") == "商务"

    def test_halfwidth_paren_score_stripped(self) -> None:
        assert normalize_group_label("技术部分(14分)") == "技术"

    def test_decimal_paren_score_stripped(self) -> None:
        assert normalize_group_label("价格部分（10.5分）") == "价格"

    def test_paren_with_inner_space_stripped(self) -> None:
        assert normalize_group_label("报价部分 （70 分）") == "价格"

    def test_plain_label_unchanged(self) -> None:
        assert normalize_group_label("商务部分") == "商务"

    def test_non_score_paren_content_kept(self) -> None:
        # 括注内非分值(如英文缩写)是实义内容必须保留;
        # 括号字符本身是载体修饰, 折叠出键(内容不删)。
        assert normalize_group_label("环境影响评价（ESG）") == "环境影响评价esg"


class TestNormalizeGroupLabelParenFold:
    """: 括号字符折叠——同一实义的带括号/不带括号形态必须同键。

 3477 实测: 声明"详细评审技术" vs 项侧"详细评审(技术)"两键失配,
 组轴 extracted_sum 归零产出 -43/-4 假缺口。折叠只删括号字符不删内容;
 若两个原本不同的标签因此撞键, 命中 同域冲突拒用 fail-visible。
 """

    def test_paren_variant_folds_to_same_key(self) -> None:
        assert normalize_group_label("详细评审(技术)") == normalize_group_label(
            "详细评审技术"
        )

    def test_fullwidth_paren_variant_folds_to_same_key(self) -> None:
        assert normalize_group_label("详细评审（商务）") == normalize_group_label(
            "详细评审商务"
        )

    def test_distinct_groups_stay_distinct_after_fold(self) -> None:
        assert normalize_group_label("详细评审(技术)") != normalize_group_label(
            "详细评审(商务)"
        )

    def test_fold_then_suffix_stripped_to_fixpoint(self) -> None:
        # 折叠后暴露的尾缀仍须剥离: "商务(部分)" → "商务部分" → "商务"。
        assert normalize_group_label("商务(部分)") == "商务"


class TestNormalizeGroupLabelSynonymFold:
    """评标领域稳定同义: 报价/价格指同一评审维度, 归一键折叠。

 折叠只作用于归一键(匹配层), 不改任何原文标签; 若同文档真的并存
 "报价X分"与"价格Y分"两条不同分值声明, 折叠后同键不同值会命中
 冲突拒用, fail-visible 而非静默错配。
 """

    def test_baojia_folds_to_jiage(self) -> None:
        assert normalize_group_label("报价评审") == normalize_group_label("价格部分")

    def test_bare_baojia_folds(self) -> None:
        assert normalize_group_label("报价") == "价格"

    def test_compound_label_not_folded(self) -> None:
        # 折叠仅限剥离尾缀后的精确键, 复合标签(投标报价)不做词面联想。
        assert normalize_group_label("投标报价") == "投标报价"


class TestReconcile2468ParenLabelShape:
    """2468 真实形态: 声明(报价70/商务16/技术14) vs 项侧括注标签 → 组级差额必须全 0。"""

    def _capture(self):
        grids = [
            (1, [["序号", "评审项目", "分值", "评审内容"],
                 ["1", "报价部分 （70分）", "70分", "低价优先法, 价格分=(Pmin/P)xA"]]),
            (2, [["序号", "评审项目", "评审因素", "分值"],
                 ["2", "商务部分 （16分）", "业绩要求", "10分"],
                 ["3", "", "服务团队", "2分"],
                 ["4", "", "提供增值服务项目", "4分"],
                 ["5", "技术部分 （14分）", "蛋糕品牌覆盖", "8分"],
                 ["", "技术部分（14分）", "质量保证措施和售后服务方案", "6分"]]),
        ]
        pages = [(page, "\n".join(" | ".join(row) for row in grid)) for page, grid in grids]
        return capture_scoring_declarations(pages, grids)

    def test_group_rows_close_despite_paren_labels(self) -> None:
        capture = self._capture()
        items = [
            ("报价部分", 70.0, "报价部分"),
            ("业绩要求", 10.0, "商务部分（16分）"),
            ("服务团队", 2.0, "商务部分（16分）"),
            ("提供增值服务项目", 4.0, "商务部分（16分）"),
            ("蛋糕品牌覆盖", 8.0, "技术部分（14分）"),
            ("质量保证措施和售后服务方案", 6.0, "技术部分（14分）"),
        ]
        report = reconcile_scoring_ledger(capture, items)
        assert report.unlabeled_sum == 0.0
        gaps = {row.label: row.gap for row in report.group_rows}
        assert gaps, "L1 组行必须产出"
        assert all(abs(gap) <= 0.01 for gap in gaps.values()), gaps


class TestReconcile2471SynonymShape:
    """2471 真实形态: 声明"报价评审60" vs 项侧"价格部分" → 同义折叠后差额为 0。"""

    def test_baojia_declaration_matches_jiage_items(self) -> None:
        pages = [(1, "分值构成：详细评审商务：20分；详细评审技术：20分；报价评审：60分")]
        capture = capture_scoring_declarations(pages, [])
        items = [
            ("体系认证", 3.0, "详细评审商务"),
            ("类似项目业绩", 9.0, "详细评审商务"),
            ("项目负责人", 3.0, "详细评审商务"),
            ("技术负责人", 3.0, "详细评审商务"),
            ("安全负责人", 2.0, "详细评审商务"),
            ("施工方案与技术措施", 20.0, "详细评审技术"),
            ("报价评分", 60.0, "价格部分"),
        ]
        report = reconcile_scoring_ledger(capture, items)
        gaps = {row.label: row.gap for row in report.group_rows}
        assert abs(gaps.get("报价评审", -1.0)) <= 0.01, gaps
        assert all(abs(gap) <= 0.01 for gap in gaps.values()), gaps


class TestRowLevelFullScoreNotDocTotal:
    """2463 真实形态: 行内"满分为10.0000分"且该行分值=10 → 不得作为文档级总分候选。

 判据为结构+数值(满分值≈同行独立分值格), 非词面白名单; 误抑真实总分的代价是
 诚实降级 L4(fail-visible), 不会产出错误基准(承 方向)。
 """

    _GRID_2463 = [
        ["分组", "评分因素", "评分内容与标准", "分值"],
        ["价格部分", "", "", ""],
        ["", "报价评分", "价格项(F1×A1)满分为10.0000分, 满足招标文件要求且报价最低的为评标基准价", "10分"],
    ]

    def test_row_level_full_score_suppressed(self) -> None:
        pages = [(1, "价格项(F1×A1)满分为10.0000分, 满足招标文件要求且报价最低的为评标基准价")]
        capture = capture_scoring_declarations(pages, [(1, self._GRID_2463)])
        assert 10.0 not in capture.total_candidates, capture.total_candidates

    def test_doc_level_total_kept(self) -> None:
        pages = [(1, "本项目评分总分100分。价格项(F1×A1)满分为10.0000分")]
        capture = capture_scoring_declarations(pages, [(1, self._GRID_2463)])
        assert 100.0 in capture.total_candidates, capture.total_candidates
        assert 10.0 not in capture.total_candidates, capture.total_candidates

    def test_reconcile_degrades_honestly_without_false_basis(self) -> None:
        pages = [(1, "价格项(F1×A1)满分为10.0000分")]
        capture = capture_scoring_declarations(pages, [(1, self._GRID_2463)])
        items = [("报价评分", 10.0, "价格部分"), ("技术参数响应情况", 90.0, "")]
        report = reconcile_scoring_ledger(capture, items)
        assert report.expected_total != 10.0, report


class TestRefineScoringGroupLabelBoundary:
    """refine 边界(vs 2471/2463 互为反例)。"""

    def test_none_label_must_not_fabricate_from_keyword(self) -> None:
        # 2471/2463: 项名含"业绩"但无分组上下文 → 禁止造标签(留给卷定义回填)。
        assert refine_scoring_group_label(None, "类似项目业绩") is None
        assert refine_scoring_group_label(None, "信誉情况") is None

    def test_price_identity_label_kept(self) -> None:
        # 1707: 评标价为公式计分单项, 组身份与项身份天然同一, 且与声明侧一致。
        assert refine_scoring_group_label(None, "评标价") == "评标价"

    def test_generic_other_factor_still_refined(self) -> None:
        # 1707: 分值构成声明为细粒度(业绩10/信誉10), 细分使项侧与声明侧对齐。
        assert refine_scoring_group_label("其他因素", "类似项目业绩") == "业绩"
        assert refine_scoring_group_label("其他因素", "信誉") == "信誉"

    def test_existing_specific_label_untouched(self) -> None:
        assert refine_scoring_group_label("详细评审商务", "类似项目业绩") == "详细评审商务"


class TestParserLabelSourceHygiene:
    """项侧标签源头卫生: 分组格括注剥离 + 禁止项名关键词造标签。"""

    _GRID_2468 = [
        ["序号", "评审项目", "评审因素", "分值", "评审内容"],
        ["2", "商务部分 （16分）", "业绩要求", "10分", "提供类似合同, 每有一个得2分"],
        ["3", "", "服务团队", "2分", "服务团队在3人的基础上每增加1人得1分"],
        ["4", "", "提供增值服务项目", "4分", "提供增值服务承诺, 优得4分"],
        ["5", "技术部分 （14分）", "蛋糕品牌覆盖", "8分", "提供可使用的品牌, 排名第一得6分"],
        ["", "技术部分（14分）", "质量保证措施和售后服务方案", "6分", "根据质量保证措施合理打分"],
    ]

    def test_group_cell_paren_stripped_in_stored_label(self) -> None:
        result = parse_numeric_score_column_table(self._GRID_2468, "technical")
        assert result is not None and result.items, result
        labels = {item.score_item_name: item.volume_label for item in result.items}
        assert labels.get("业绩要求") == "商务部分", labels
        assert labels.get("蛋糕品牌覆盖") == "技术部分", labels

    def test_item_name_keyword_must_not_fabricate_label(self) -> None:
        grid = [
            ["序号", "评审因素", "分值", "评审标准"],
            ["1", "体系认证", "3分", "取得质量管理体系认证得3分"],
            ["2", "类似项目业绩", "9分", "每年有承担过对应竣工业绩, 有1项得3分"],
            ["3", "项目负责人", "3分", "具有相关专业高级职称得2分"],
        ]
        result = parse_numeric_score_column_table(grid, "commercial")
        assert result is not None and result.items, result
        labels = {item.score_item_name: item.volume_label for item in result.items}
        # 无任何分组上下文时标签必须为 None(留给卷定义回填), 禁止按项名造"业绩"。
        assert labels.get("类似项目业绩") is None, labels
