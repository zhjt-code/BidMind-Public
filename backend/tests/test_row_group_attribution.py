# -*- coding: utf-8 -*-
"""逐行原文组归属恢复防御测试。

防御目标(映射真实风险 项目2475 / 项目2476):
- 2475: 组身份写在合并单元格里且措辞为"承包人建议书和承包人实施方案", 不含任何
 词表关键词; 项名另带"承包人建议书——X"前缀, 真实基准应细分为 17/13 两组。
- 2476: 组身份写在长文本单元格"技术标(汇总规则:当专家数量小于等于4位…)"里,
 既有 `clean_volume_label` 对 >30 字单元格直接放弃, 19 项技术标全部无标签。
- 红色清单要求: 断言"不含任何词表关键词的真实大类仍被识别", 并覆盖负例(条款号
 列不得被当组列、无法全覆盖时整体拒收)。
不做: 不测枚举卷映射、不触发 LLM/DB。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.row_group_attribution import (  # noqa: E402
    recover_row_group_labels,
)

import pytest

pytestmark = pytest.mark.scoring

# 项目2475 真实形态: col0=条款号(稀疏), col1=分组合并单元格, col2=项名, col3=分值
_DOC2475_ROWS = [
    ["2.2.4（1） 和（3）", "承包人建议书和承包人实施方案", "承包人建议书 ——项目理解及设计思路", "1"],
    ["", "", "承包人建议书 ——实施效果表现", "1"],
    ["", "", "承包人建议书 ——重难点分析", "2"],
    ["", "", "承包人实施方案 ——施工方案、方法和技术措施", "6"],
    ["", "", "承包人实施方案 ——质保服务承诺", "4"],
    ["2.2.4（2）", "资信业绩评分标准", "类似项目业绩", "4"],
    ["", "", "工程获奖", "8"],
    ["2.2.4（4）", "投标报价评分标准", "投标报价", "50"],
]

# 项目2476 真实形态: col0=空分组列, col1=长文本组名(带汇总规则括注与兄弟序号)
_DOC2476_ROWS = [
    ["", "技术标(汇总规则:当专家数量小于等于4位，取去掉0个最高分、0个最低分后的算术平均值;)", "设计任务书优化", "20"],
    ["", "技术标(汇总规则:当专家数量小于等于4位，取去掉0个最高分、0个最低分后的算术平均值;)(2)", "进度管理方案", "4"],
    ["", "资信标", "拟派项目部应配备专门的项目经理", "13"],
    ["", "资信标(2)", "信誉评价结果", "67"],
    ["", "商务标", "投标总报价", "83"],
    ["", "商务标(2)", "材料设备报价合理性", "10"],
]


def test_doc2475_merged_group_column_refined_by_name_prefix() -> None:
    """2475: 合并单元格给出粗分组, 项名前缀把"建议书+实施方案"细分为两组。"""
    labels = recover_row_group_labels(_DOC2475_ROWS, name_col=2, score_col=3)

    assert labels == {
        0: "承包人建议书",
        1: "承包人建议书",
        2: "承包人建议书",
        3: "承包人实施方案",
        4: "承包人实施方案",
        5: "资信业绩评分标准",
        6: "资信业绩评分标准",
        7: "投标报价评分标准",
    }


def test_doc2475_group_totals_match_original_declaration() -> None:
    """细分后各组合计须等于原文声明(17/13/20/50 的同构缩样)。"""
    labels = recover_row_group_labels(_DOC2475_ROWS, name_col=2, score_col=3)

    totals: dict[str, float] = {}
    for idx, label in labels.items():
        totals[label] = totals.get(label, 0.0) + float(_DOC2475_ROWS[idx][3])

    assert totals == {
        "承包人建议书": 4.0,
        "承包人实施方案": 10.0,
        "资信业绩评分标准": 12.0,
        "投标报价评分标准": 50.0,
    }


def test_clause_number_column_is_not_treated_as_group_column() -> None:
    """负例: 条款号列("2.2.4（1）和（3）")归一后无实义中文, 不得充当组列。"""
    labels = recover_row_group_labels(_DOC2475_ROWS, name_col=2, score_col=3)

    assert "2.2.4" not in "".join(labels.values())


def test_doc2476_long_annotated_group_cell_recovered() -> None:
    """2476: 长文本组名剥掉括注与兄弟序号后仍是有效组标签(旧实现直接放弃)。

 name_col 按生产列角色指向项名列(col2, "设计任务书优化"等), 分组列(col1)
 必须与名称列不同—— 起名称列不再参与分组列候选(名字族伪分区),
 本用例同时锁定"分组列在名称列左侧仍可命中"。端到端护栏见
 test_scoring_group_attribution_golden.test_holdout24。
 """
    labels = recover_row_group_labels(_DOC2476_ROWS, name_col=2, score_col=3)

    assert labels == {
        0: "技术标",
        1: "技术标",
        2: "资信标",
        3: "资信标",
        4: "商务标",
        5: "商务标",
    }


def test_no_keyword_table_dependency() -> None:
    """红色清单要求: 完全不含词表关键词的大类同样必须被识别。"""
    rows = [
        ["", "设计方案", "总体构思", "26"],
        ["", "", "深化设计", "4"],
        ["", "施工组织", "进度计划", "30"],
        ["", "", "现场管理", "40"],
    ]

    labels = recover_row_group_labels(rows, name_col=2, score_col=3)

    assert labels == {0: "设计方案", 1: "设计方案", 2: "施工组织", 3: "施工组织"}


def test_partial_coverage_is_rejected_wholesale() -> None:
    """fail-closed: 首行之前的计分行拿不到标签时整体拒收, 不做部分标注。"""
    rows = [
        ["", "", "先于任何分组出现的计分行", "5"],
        ["", "技术部分", "方案", "50"],
        ["", "商务部分", "报价", "45"],
    ]

    assert recover_row_group_labels(rows, name_col=2, score_col=3) == {}


def test_single_group_is_not_a_partition() -> None:
    """只有一个组等于没有分组信息, 不产出标签(避免给全表贴同一标签)。"""
    rows = [
        ["", "技术部分", "方案A", "50"],
        ["", "", "方案B", "50"],
    ]

    assert recover_row_group_labels(rows, name_col=2, score_col=3) == {}


def test_declared_scores_closure_rejects_mismatched_partition() -> None:
    """声明闭合门禁: 恢复出的组分值与声明对不上时整体拒收。"""
    declared_ok = {"技术": 50.0, "商务": 45.0}
    declared_bad = {"技术": 60.0, "商务": 45.0}
    rows = [
        ["", "技术部分", "方案A", "30"],
        ["", "", "方案B", "20"],
        ["", "商务部分", "报价", "45"],
    ]

    assert recover_row_group_labels(
        rows, name_col=2, score_col=3, declared_scores=declared_ok
    )
    assert (
        recover_row_group_labels(
            rows, name_col=2, score_col=3, declared_scores=declared_bad
        )
        == {}
    )


def test_total_row_label_never_becomes_a_group() -> None:
    """汇总类标签("合计")不是评分组, 出现即判该列不可用。"""
    rows = [
        ["", "技术部分", "方案", "50"],
        ["", "合计", "小计行", "50"],
    ]

    assert recover_row_group_labels(rows, name_col=2, score_col=3) == {}


# ---------------------------------------------------------------------------
# 全宽组标题行作用域(项目2478 形态)
# ---------------------------------------------------------------------------

_DOC2478_ROWS = [
    ["序号", "评分点名称", "评审标准"],
    ["1", "报价得分 （15分）", "满足询比文件要求且投标价格最低…"],
    ["技术评分（总分：53分）", "", ""],
    ["1", "符合性条款", "实质性响应…(不计分)"],
    ["2", "实施方案要求（12分）", "规则A"],
    ["3", "项目技术方案要求 （41分）", "规则B"],
    ["商务评分（总分：32分）", "", ""],
    ["1", "综合能力 （8分）", "规则C"],
    ["2", "业绩 （24分）", "规则D"],
]
_DOC2478_ROW_SCORES = {1: 15.0, 4: 12.0, 5: 41.0, 7: 8.0, 8: 24.0}
_DOC2478_ROW_NAMES = {1: "报价得分", 4: "实施方案要求", 5: "项目技术方案要求", 7: "综合能力", 8: "业绩"}


def test_doc2478_group_header_rows_partition_scopes() -> None:
    """全宽"X（总分：N分）"行切分作用域, 首标题被表头消费时经 leading_rows 补回。"""
    labels = recover_row_group_labels(
        _DOC2478_ROWS,
        row_scores=_DOC2478_ROW_SCORES,
        row_names=_DOC2478_ROW_NAMES,
        leading_rows=[["价格评分（总分：15分）", "", ""]],
    )

    assert labels == {
        1: "价格评分",
        4: "技术评分",
        5: "技术评分",
        7: "商务评分",
        8: "商务评分",
    }


def test_doc2478_header_scope_rejected_when_any_scope_not_closed() -> None:
    """任一作用域与标题声明不闭合 -> 整表拒绝(fail-closed), 不做部分标注。"""
    bad_scores = {**_DOC2478_ROW_SCORES, 4: 20.0}  # 技术作用域 61 != 53

    labels = recover_row_group_labels(
        _DOC2478_ROWS,
        row_scores=bad_scores,
        row_names=_DOC2478_ROW_NAMES,
        leading_rows=[["价格评分（总分：15分）", "", ""]],
    )

    assert labels == {}


def test_embedded_name_cells_do_not_self_anchor_into_groups() -> None:
    """ 影子守卫: 名称格"XX（N分）"逐行自闭合不是分组——含"得分"字样
 的行落出标签集时守卫仍须生效(2478 实测逃逸形态)。"""
    labels = recover_row_group_labels(
        _DOC2478_ROWS,
        row_scores=_DOC2478_ROW_SCORES,
        row_names=_DOC2478_ROW_NAMES,
    )

    # 无 leading_rows 时判据1不成立(首作用域缺失), 判据2/3 不得用名称列造组
    assert labels == {}


# ---------------------------------------------------------------------------
# 名称列名字族不是分组列(项目2480 形态)
# ---------------------------------------------------------------------------


def test_name_family_column_is_not_a_group_column() -> None:
    """消歧名字族"企业规模(2)/(3)"伪装的"组数<行数"分区必须被拒绝。"""
    rows = [
        ["1", "企业规模", "规则1", "3"],
        ["2", "企业规模(2)", "规则2", "3"],
        ["3", "企业规模(3)", "规则3", "3"],
        ["4", "财务状况", "规则4", "3"],
        ["5", "财务状况(2)", "规则5", "3"],
    ]

    assert recover_row_group_labels(rows, name_col=1, score_col=3) == {}


# ---------------------------------------------------------------------------
# 锚点声明闭合有界的部分采纳(项目2481 形态)
# ---------------------------------------------------------------------------

_DOC2481_ROWS = [
    ["2.2.4 （1）", "（明标） 资信业绩评分标准 （3分）", "类似项目业绩（3 分）", "规则", "0-3.00"],
    ["", "", "项目管理大纲编制 （5分）", "编制全面…", "4.01-5.00"],
    ["", "", "对本工程的认识和理解 （3分）", "理解清晰…", "2.41-3.00"],
    ["", "", "其他工作和合理化建议 （22分）", "全面详尽…", "2.41-3.00"],
    ["2.2.4 （3）", "投标报价评分标准 （60分）", "偏差率", "投标报价与评标基准价…", "0-60.00"],
    ["2.2.4 （4）", "（明标） 其他因素评分标准 （7分）", "项目负责人（3分）", "两项及以上…", ""],
    ["", "", "其他人员 （4分）", "5名以上…", ""],
]
_DOC2481_ROW_SCORES = {0: 3.0, 1: 5.0, 2: 3.0, 3: 22.0, 4: 60.0, 5: 3.0, 6: 4.0}
_DOC2481_ROW_NAMES = {
    0: "类似项目业绩", 1: "项目管理大纲编制", 2: "对本工程的认识和理解",
    3: "其他工作和合理化建议", 4: "偏差率", 5: "项目负责人", 6: "其他人员",
}


def test_doc2481_closure_bounded_partial_adoption() -> None:
    """锚点组按声明值精确封组(3/60/7), 合并单元格丢失的大纲行诚实留空。"""
    labels = recover_row_group_labels(
        _DOC2481_ROWS,
        name_col=2,
        row_scores=_DOC2481_ROW_SCORES,
        row_names=_DOC2481_ROW_NAMES,
    )

    assert labels == {
        0: "资信业绩评分标准",
        4: "投标报价评分标准",
        5: "其他因素评分标准",
        6: "其他因素评分标准",
    }
    assert 1 not in labels and 2 not in labels and 3 not in labels


def test_doc2481_unbounded_inheritance_never_adopted() -> None:
    """反断言: 大纲行绝不能被上方"资信业绩（3分）"锚点无界继承成 33 分假组。"""
    labels = recover_row_group_labels(
        _DOC2481_ROWS,
        name_col=2,
        row_scores=_DOC2481_ROW_SCORES,
        row_names=_DOC2481_ROW_NAMES,
    )

    dangling = [idx for idx, label in labels.items() if label == "资信业绩评分标准"]
    assert dangling == [0]


def test_closure_bounded_adoption_rejected_when_anchor_overshoots() -> None:
    """锚点组无法恰好闭合(累计越过声明值)时整体放弃, 不产出部分错误组。"""
    bad_scores = {**_DOC2481_ROW_SCORES, 0: 4.0}  # 资信业绩首行4 > 声明3

    labels = recover_row_group_labels(
        _DOC2481_ROWS,
        name_col=2,
        row_scores=bad_scores,
        row_names=_DOC2481_ROW_NAMES,
    )

    assert labels == {}
