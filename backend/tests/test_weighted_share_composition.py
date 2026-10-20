# -*- coding: utf-8 -*-
"""并列评分卷权重声明解析防御测试。

防御目标(映射真实风险 项目2476 / holdout-24):
- 原文"评分细则按技术标、资信标、商务标分别编写,满分均为100,按…分别占30%、15%、
 55%的权重计算最终评标得分"是**单一评审办法下的并行评分卷**声明。既有解析器
 只认"分值构成: X20分; Y80分"这类"分值即最终得分"形态, 对本形态零命中, 导致
 三卷权重全部未落地(DB 实测 volume_weight=0), 账本亦无基准可用。
- 序位对应是本形态的唯一配对依据, 必须全序列等长且权重和≈100% 才采纳; 任一条件
 不成立即整体拒收(fail-closed), 禁止部分采纳制造错配。
不做: 不测枚举卷映射(volume_parser 职责)、不触发 LLM/DB。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.volume_parser import (  # noqa: E402
    extract_volume_composition_sentence,
    parse_flat_volume_composition,
)
from app.core.extractor.scoring.weighted_share_composition import (  # noqa: E402
    parse_weighted_share_composition,
)

import pytest

pytestmark = pytest.mark.scoring

# 项目2476 / holdout-24 第6页原文(逐字, 含原文中的半角逗号)
_DOC2476_TEXT = (
    "(2)评分细则按技术标、资信标、商务标分别编写,满分均为100,"
    "按技术标、资信标、商务标分别占30%、15%、55%的权重计算最终评标得分,"
    "最终评标得分公式如下:"
)


def test_doc2476_three_parallel_volumes_with_declared_weights() -> None:
    """核心正例: 三卷各原始满分100, 权重按序位对应 30%/15%/55%。"""
    shares = parse_weighted_share_composition(_DOC2476_TEXT)

    assert [s.label for s in shares] == ["技术标", "资信标", "商务标"]
    assert [s.max_score for s in shares] == [100.0, 100.0, 100.0]
    assert [s.weight for s in shares] == [0.3, 0.15, 0.55]


def test_declaration_split_across_lines_still_parsed() -> None:
    """原文常在百分数中间断行, 拼窗后仍须解析(2476 实测断在"分别占30%、"后)。"""
    joined = (
        "(2)评分细则按技术标、资信标、商务标分别编写,满分均为100,"
        "按技术标、资信标、商务标分别占30%、 15%、55%的权重计算最终评标得分"
    )
    shares = parse_weighted_share_composition(joined)

    assert len(shares) == 3
    assert [s.weight for s in shares] == [0.3, 0.15, 0.55]


def test_length_mismatch_is_rejected_wholesale() -> None:
    """标签数与权重数不等 = 序位对应不成立 -> 整体拒收, 禁止部分采纳。"""
    text = "按技术标、资信标、商务标分别编写,满分均为100,按技术标、资信标分别占40%、60%的权重"

    assert parse_weighted_share_composition(text) == []


def test_weight_sum_far_from_100_is_rejected() -> None:
    """权重和显著偏离 100% -> 声明不自洽, 拒收(不得凑分)。"""
    text = "按技术标、商务标分别编写,满分均为100,按技术标、商务标分别占30%、15%的权重"

    assert parse_weighted_share_composition(text) == []


def test_single_volume_is_not_a_parallel_declaration() -> None:
    """单卷不构成"并列卷"声明, 交既有折算卷定义路径处理。"""
    text = "技术标满分均为100,按技术标分别占100%的权重"

    assert parse_weighted_share_composition(text) == []


def test_plain_rule_text_without_declaration_is_ignored() -> None:
    """普通评分规则正文不得被误当权重声明(防误捕)。"""
    text = "评委根据投标文件情况分为一般、良、优,分别酌情得10-14分、14-17分、17-20分"

    assert parse_weighted_share_composition(text) == []


def test_missing_shared_max_score_is_rejected() -> None:
    """缺"满分均为N"锚点时无法确定各卷原始满分 -> 拒收, 不按 100 假设兜底。"""
    text = "按技术标、资信标、商务标分别占30%、15%、55%的权重计算最终评标得分"

    assert parse_weighted_share_composition(text) == []


# ---------------------------------------------------------------------------
# 卷定义落地: 权重必须真正进入 volume_defs(2476 实测 DB 内三卷权重全为 0)
# ---------------------------------------------------------------------------


def test_volume_defs_keep_raw_max_and_declared_weight_on_separate_axes() -> None:
    """volume_max 取卷内原始满分, weight 取声明比例——两者分属不同轴。

    既有"分值构成"路径按 weight=分值/总分 反推, 用在本形态上会得出 100/300 这类
    与原文声明无关的权重, 并把三卷原始满分横向相加成 300 分制。
    """
    defs = parse_flat_volume_composition(_DOC2476_TEXT)

    assert set(defs) == {"technical", "qualification", "commercial"}
    assert defs["technical"].volume_max == 100.0
    assert defs["technical"].weight == 0.3
    assert defs["qualification"].weight == 0.15
    assert defs["commercial"].weight == 0.55


def test_sentence_locator_recognizes_weighted_share_form() -> None:
    """声明句定位器须识别本形态, 否则 prelude 不注入、账本永远拿不到基准。"""
    page_text = "2.2.1 分值构成:见评标办法前附表。\n" + _DOC2476_TEXT

    sentence = extract_volume_composition_sentence(page_text)

    assert sentence is not None
    assert len(parse_flat_volume_composition(sentence)) == 3
