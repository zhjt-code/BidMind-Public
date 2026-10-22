"""换轴重构 P0-3 探针纯函数防御测试。

职责: 验证 run_scoring_ledger_probe 中构成参照解析(四种写法)、标签归一匹配、
      账本自洽判定(含折算分支)的正确性。
不做: 不运行捕获/提取链、不读 fixture 语料、不读任何 holdout 真值文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from scripts.run_scoring_ledger_probe import (  # noqa: E402
    assess_ledger_consistency,
    match_reference_pairs,
    normalize_group_label,
    parse_composition_references,
)

pytestmark = pytest.mark.scoring

_MINI_TRUTH = """
## structure-01

- declared_total: 100
- volume_totals: unknown = 100（技术 40，价格 60；综合实力 0、后评估 0 不计入有效评分项）

## structure-08

- `declared_total`: 100
- `volume_totals`: 商务技术评审部分=20；价格评审部分=80；另有扣分项=-3（不计入100分构成）

## structure-09

- `declared_total`: 多套评分模板并列，单套模板各为100分，不横向相加。
- `volume_totals`: 技术评分标准/FWJS05=100；商务评分标准/FWSW01=100

## structure-16

- declared_total: 100（按权重折算：资信标10% + 技术标50% + 商务标40%）
- volume_totals:
  - 资信标: 原始100，权重10%，折算10
  - 技术标: 原始100，权重50%，折算50
"""


def test_parse_reference_paren_format_excludes_zero_groups():
    """括注式: 提取正分组对, 0分组(综合实力/后评估)与 unknown=100 不入参照。"""
    refs = parse_composition_references(_MINI_TRUTH)
    entry = refs["01"]
    assert entry["declared_total"] == 100.0
    assert [(p["label"], p["score"]) for p in entry["pairs"]] == [("技术", 40.0), ("价格", 60.0)]


def test_parse_reference_semicolon_format_excludes_negative():
    """分号式: 解析 label=N 组对, 负分扣分项(-3)不入参照。"""
    entry = parse_composition_references(_MINI_TRUTH)["08"]
    assert [(p["label"], p["score"]) for p in entry["pairs"]] == [
        ("商务技术评审部分", 20.0),
        ("价格评审部分", 80.0),
    ]


def test_parse_reference_multi_scheme_falls_back_to_sum():
    """多方案式(09): declared_total 无数字时回退为显式 =N 之和。"""
    entry = parse_composition_references(_MINI_TRUTH)["09"]
    assert entry["declared_total"] == 200.0
    assert entry["pairs"][0]["label"] == "技术评分标准/FWJS05"


def test_parse_reference_bullet_weighted_format():
    """列表式(16): 解析 原始/权重/折算 三元, alt_score 为折算值。"""
    entry = parse_composition_references(_MINI_TRUTH)["16"]
    first = entry["pairs"][0]
    assert (first["label"], first["score"], first["alt_score"], first["weight"]) == ("资信标", 100.0, 10.0, 0.10)


def test_normalize_group_label_strips_suffixes_and_width():
    """标签归一: 全半角/空白归一 + 迭代尾缀剥离。"""
    assert normalize_group_label("商务 标") == "商务"
    assert normalize_group_label("报价评分标准") == "报价"
    assert normalize_group_label("价格分") == "价格"
    assert normalize_group_label("商务评审标准") == "商务"
    assert normalize_group_label("技术服务") == "技术服务"


def test_match_reference_pairs_score_and_folded_value():
    """R1 匹配: 原始分值直配; 折算值(score*weight)也可命中参照。"""
    refs = [
        {"label": "技术", "score": 40.0, "alt_score": None, "weight": None},
        {"label": "价格", "score": 60.0, "alt_score": None, "weight": None},
        {"label": "商务", "score": 13.0, "alt_score": None, "weight": None},
    ]
    captured = [
        {"label": "技术", "score": 100.0, "weight": 0.4},
        {"label": "价格部分", "score": 60.0, "weight": None},
    ]
    details = match_reference_pairs(refs, captured)
    assert [d["hit"] for d in details] == [True, True, False]


def test_consistency_self_consistent_with_candidate():
    """R2: Σ组分与总分候选差<=1.0 判自洽。"""
    pairs = [{"label": "商务", "score": 34.0}, {"label": "技术", "score": 66.0}]
    result = assess_ledger_consistency(pairs, [100.0, 30.0])
    assert result["status"] == "self_consistent"
    assert result["matched_candidate"] == 100.0


def test_consistency_folded_branch():
    """R2 折算分支: Σ原始分不匹配但 Σ(组分x权重) 匹配候选 判折算制自洽。"""
    pairs = [
        {"label": "技术", "score": 100.0, "weight": 0.4},
        {"label": "价格", "score": 100.0, "weight": 0.6},
    ]
    result = assess_ledger_consistency(pairs, [100.0])
    assert result["status"] == "folded_consistent"
    assert result["folded_sum"] == 100.0


def test_consistency_inconsistent_and_insufficient():
    """R2: 无匹配候选判不自洽; 组对<2 判无捕获。"""
    pairs = [{"label": "商务", "score": 30.0}, {"label": "技术", "score": 50.0}]
    assert assess_ledger_consistency(pairs, [100.0])["status"] == "inconsistent"
    assert assess_ledger_consistency(pairs, [])["status"] == "inconsistent"
    assert assess_ledger_consistency([{"label": "商务", "score": 30.0}], [100.0])["status"] == "insufficient"
