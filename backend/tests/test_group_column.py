# -*- coding: utf-8 -*-
"""分组列结构识别防御测试。

防御目标(映射真实风险):
- doc1933(项目2447) 真实网格: 原文 5 个大类中"设计方案(26分)/施工方案(30分)"因不含
 任何词面关键词, 被 `_GROUP_SUFFIX_GATE` 与 `_volume_label_has_signal` 双重漏失,
 账本仅捕 3 组、18 项/70 分落 unlabeled。核心断言是"结构识别不依赖词面"。
- 判据2(父子闭合)承担原后缀门禁的防护职责: 项级行不得被误升为组。闭合不成立必须
 整体拒收(返回 None 回退旧逻辑), 禁止部分采纳产出错位标签。
- 明细项名"投标报价得分(30分)"含"得分", 不得被 `is_total_like_label` 误杀
 (那会使该组无明细可闭合、整表识别被拒)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.group_column import (  # noqa: E402
    detect_group_column,
    parse_labeled_score,
)

pytestmark = pytest.mark.scoring

# doc1933 真实网格（脱敏保留结构与分值，规则列截断）。列序: 条款号|评审因素|评审标准|评分标准
_DOC1933_GRID: list[list[str]] = [
    ["条款号", "评审因素", "", "评审标准"],
    ["2.2.4 （1）", "设计方案 （26分）", "认识和措施及设计大纲 （8分）", "对本工程的认识和措施，合理得2-4分"],
    ["", "", "设计方案 （10分）", "总体思路新颖，能切合项目实际"],
    ["", "", "技术难点的突破及设计总说明 （8分）", "项目技术难点的突破，显著的得2-4分"],
    ["2.2.4 （2）", "施工方案 （30分）", "施工方案与技术措施 （10分）", "施工方案合理可行"],
    ["", "", "质量管理体系与措施 （5分）", "质量管理体系与措施完善的得3-5分"],
    ["", "", "安全管理体系与措施 （5分）", "安全管理体系与措施完善的得3-5分"],
    ["", "", "环境保护管理体系与措施（4分）", "环境保护管理体系与措施完善的得3-4分"],
    ["", "", "工程进度计划与措施 （4分）", "工程进度计划与措施完善的得3-4分"],
    ["", "", "资源配备计划 （2分）", "资源配备计划完善的得2分"],
    ["2.2.4 （3）", "项目管理机构评分标准 （9分）", "施工单位项目管理机构 （2分）", "拟投入本项目施工管理机构人员"],
    ["", "", "设计负责人业绩 （2分）", "以设计负责人近三年承担过类似项目"],
    ["", "", "施工单位项目经理业绩 （1分）", "以项目经理近三年承担过类似项目"],
    ["", "", "施工单位技术负责人业绩 （1分）", "以施工单位技术负责人近三年承担过类似项目"],
    ["", "", "其他主要人员 （3分）", "专职施工员、材料员、资料员等"],
    ["2.2.4 （4）", "投标报价评分标准 （30分）", "投标报价得分 （30分）", "投标报价满分30分。打分方法以评标基准价为基数"],
    ["2.2.4 （5）", "其他因素评分标准 （5分）", "施工单位企业业绩1 （1分）", "施工单位近三年具有类似EPC项目业绩"],
    ["", "", "施工单位企业业绩2 （2分）", "施工单位近三年具有满足合同金额要求的业绩"],
    ["", "", "施工单位财务状况 （1分）", "施工单位2020-2022年的财务报表"],
    ["", "", "施工单位体系认证 （1分）", "施工单位同时具备质量、环境、职业健康三大体系认证"],
]

_EXPECTED_GROUPS = (
    ("设计方案", 26.0),
    ("施工方案", 30.0),
    ("项目管理机构评分标准", 9.0),
    ("投标报价评分标准", 30.0),
    ("其他因素评分标准", 5.0),
)


# ---------------------------------------------------------------------------
# doc1933 真实网格 golden
# ---------------------------------------------------------------------------


def test_doc1933_detects_five_original_groups_without_any_wordface_keyword():
    """核心防回归: 5 个大类全部识别, 含两个不含词面关键词的"设计方案/施工方案"。"""
    detection = detect_group_column(_DOC1933_GRID)

    assert detection is not None, "结构判据必须能识别该表, 词面白名单在此恒失效"
    assert tuple((g.label, g.score) for g in detection.groups) == _EXPECTED_GROUPS
    assert detection.declared_total == 100.0


def test_doc1933_picks_review_factor_column_not_clause_or_detail_column():
    """分组列必须是「评审因素」(列1); 条款号列(0)无分值、明细列(2)右侧无法闭合。"""
    detection = detect_group_column(_DOC1933_GRID)

    assert detection is not None
    assert detection.column_index == 1
    assert detection.detail_column_index == 2


def test_doc1933_every_group_closes_against_detail_rows():
    """判据2 实证: 26=8+10+8、30=10+5+5+4+4+2、9=2+2+1+1+3、30=30、5=1+2+1+1。"""
    detection = detect_group_column(_DOC1933_GRID)

    assert detection is not None
    for group in detection.groups:
        assert abs(sum(group.detail_scores) - group.score) <= 0.01, group.label
    assert [len(g.detail_scores) for g in detection.groups] == [3, 6, 5, 1, 4]


def test_doc1933_row_labels_cover_all_nineteen_detail_rows():
    """逐行组归属须覆盖全部 19 个明细行, 否则提取项侧仍会落 unlabeled。"""
    detection = detect_group_column(_DOC1933_GRID)

    assert detection is not None
    assert len(detection.row_labels) == 19
    assert detection.row_labels[1] == "设计方案"
    assert detection.row_labels[9] == "施工方案"
    assert detection.row_labels[15] == "投标报价评分标准"
    assert detection.row_labels[19] == "其他因素评分标准"
    # 表头行不得被标注
    assert 0 not in detection.row_labels


def test_detail_item_named_with_defen_is_not_dropped_as_total_like():
    """明细项名含「得分」不得被误杀。

    投标报价得分（30分）若套用 is_total_like_label(其 SKIP_TERMS 含裸「得分」),
    会使该组无明细可闭合、整表识别被拒。此处锁定明细列只排除真正汇总行
    (TOTAL_ROW_TERMS)。
    """
    detection = detect_group_column(_DOC1933_GRID)

    assert detection is not None
    price_group = next(g for g in detection.groups if g.label == "投标报价评分标准")
    assert price_group.detail_scores == (30.0,)


# ---------------------------------------------------------------------------
# 拒收路径: 闭合不成立 / 组数不足 / 结构异形
# ---------------------------------------------------------------------------


def test_rejects_when_parent_child_sum_mismatches():
    """明细和与组声明不符即整体拒收(返回 None 回退旧逻辑), 不做部分采纳。"""
    grid = [
        ["评审因素", "评审标准"],
        ["甲组 （20分）", "子项一 （8分）"],
        ["", "子项二 （8分）"],  # 合计 16 != 20
        ["乙组 （10分）", "子项三 （10分）"],
    ]

    assert detect_group_column(grid) is None


def test_rejects_single_group_table():
    """单组表无法与项级行区分, 拒收(需 >=2 组)。"""
    grid = [
        ["评审因素", "评审标准"],
        ["甲组 （10分）", "子项一 （6分）"],
        ["", "子项二 （4分）"],
    ]

    assert detect_group_column(grid) is None


def test_item_level_row_never_promoted_to_group_by_closure_gate():
    """原 _GROUP_SUFFIX_GATE 的防护职责由闭合判据承担: 项级行右侧无可求和的明细。"""
    grid = [
        ["评审项目", "评审标准"],
        ["质量保证措施（15分）", "措施完善得15分，一般得8分"],
        ["安全保证措施（10分）", "措施完善得10分，一般得5分"],
    ]

    # 右列是规则文本而非"名称（N分）", 无明细可闭合 → 拒收
    assert detect_group_column(grid) is None


def test_rejects_column_with_heterogeneous_cells_after_first_anchor():
    """判据1: 首锚点之后混入非「名称（N分）」文本, 该列不是分组列。"""
    grid = [
        ["评审因素", "评审标准"],
        ["甲组 （20分）", "子项一 （20分）"],
        ["随附说明文字", "子项二 （5分）"],
        ["乙组 （5分）", "子项三 （5分）"],
    ]

    assert detect_group_column(grid) is None


def test_header_text_before_first_anchor_is_tolerated():
    """首锚点之前的表头文字(如「评审因素」)不算结构异形。"""
    grid = [
        ["条款号", "评审因素", "评审标准"],
        ["1", "甲组 （20分）", "子项一 （20分）"],
        ["2", "乙组 （5分）", "子项二 （5分）"],
    ]
    detection = detect_group_column(grid)

    assert detection is not None
    assert detection.column_index == 1


def test_summary_row_in_group_column_is_skipped_not_treated_as_anchor():
    """汇总行(如「合计（25分）」)既不作组锚点, 也不判为结构异形。"""
    grid = [
        ["评审因素", "评审标准"],
        ["甲组 （20分）", "子项一 （20分）"],
        ["乙组 （5分）", "子项二 （5分）"],
        ["合计 （25分）", "—"],
    ]
    detection = detect_group_column(grid)

    assert detection is not None
    assert [g.label for g in detection.groups] == ["甲组", "乙组"]


def test_empty_and_degenerate_grids_return_none():
    assert detect_group_column(None) is None
    assert detect_group_column([]) is None
    assert detect_group_column([["只有一行 （10分）"]]) is None


# ---------------------------------------------------------------------------
# 单元格解析
# ---------------------------------------------------------------------------


def test_parse_labeled_score_accepts_long_and_digit_bearing_item_names():
    """明细项名可长达13字或含数字, 不得沿用 _GROUP_PAREN_RE 的纯汉字长度约束。"""
    assert parse_labeled_score("技术难点的突破及设计总说明 （8分）") == (
        "技术难点的突破及设计总说明",
        8.0,
    )
    assert parse_labeled_score("施工单位企业业绩1 （1分）") == ("施工单位企业业绩1", 1.0)
    assert parse_labeled_score("环境保护管理体系与措施（4分）") == (
        "环境保护管理体系与措施",
        4.0,
    )
    assert parse_labeled_score("资源配备计划 （2分）") == ("资源配备计划", 2.0)


def test_parse_labeled_score_rejects_non_score_parens_and_zero():
    assert parse_labeled_score("2.2.4 （1）") is None, "条款号括注不含分字, 不得当分值"
    assert parse_labeled_score("甲组 （0分）") is None, "0 分不构成组声明"
    assert parse_labeled_score("（10分）") is None, "无汉字标签"
    assert parse_labeled_score("") is None
