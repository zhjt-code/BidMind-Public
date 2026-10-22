# -*- coding: utf-8 -*-
"""判读基准推导测试（①）。

防御目标(映射真实风险):
- 2304形态: 判读出的卷基准(21/49/30)必须进入对账与 L2 目标, 使缺口精确可修;
- 污染防线: 确定性声明恒优先(只补缺); 同卷跨方案取值冲突跳过; 判读不可用零改动;
- 语义防线: 方案 total_score 是加权折算值, 禁止作为 expected_total 输出。
"""

from types import SimpleNamespace

import pytest

from app.core.extractor.scoring.scheme_reader.basis import (
    derive_scope_expectations_from_map,
    derive_volume_defs_from_map,
    merge_judged_basis,
)
from app.core.extractor.scoring.scheme_reader.contracts import SchemeMap
from app.core.extractor.scoring.scheme_reader.integration import SchemeReaderContext

pytestmark = pytest.mark.scoring


# 2304 形态: 三独立方案, 各方案 total_score=100(加权), 卷基准 21/49/30
MAP_2304 = SchemeMap(
    **{
        "schemes": [
            {
                "scheme_index": 1, "title": "商务评审标准表", "total_score": 100,
                "volumes": [{"volume": "commercial", "label": "商务", "max_score": 21}],
            },
            {
                "scheme_index": 2, "title": "技术评审标准表", "total_score": 100,
                "volumes": [{"volume": "technical", "label": "技术", "max_score": 49}],
            },
            {
                "scheme_index": 3, "title": "价格评审标准表", "total_score": 100,
                "volumes": [{"volume": "price", "label": "价格", "max_score": 30}],
            },
        ],
        "assignments": [],
        "confidence": 0.9,
    }
)


def test_derive_volume_defs_2304_shape():
    derived = derive_volume_defs_from_map(MAP_2304, {})
    assert set(derived) == {"commercial", "technical", "price"}
    assert derived["commercial"].volume_max == 21.0
    assert derived["technical"].volume_max == 49.0
    assert derived["price"].volume_max == 30.0
    # 权重按方案 total_score 折算
    assert derived["commercial"].weight == 0.21


def test_derive_volume_defs_never_overrides_deterministic():
    existing = {"commercial": SimpleNamespace(volume_max=25.0)}
    derived = derive_volume_defs_from_map(MAP_2304, existing)
    assert "commercial" not in derived, "确定性声明必须优先"
    assert set(derived) == {"technical", "price"}


def test_derive_volume_defs_skips_cross_scheme_conflict():
    conflicted = SchemeMap(
        **{
            "schemes": [
                {"scheme_index": 1, "volumes": [{"volume": "technical", "max_score": 53}]},
                {"scheme_index": 2, "volumes": [{"volume": "technical", "max_score": 100}]},
            ],
            "assignments": [],
            "confidence": 0.9,
        }
    )
    assert derive_volume_defs_from_map(conflicted, {}) == {}


def test_derive_scope_expectations_keys_and_no_expected_total():
    expectations = derive_scope_expectations_from_map(MAP_2304)
    assert set(expectations) == {
        ("scheme_1", "__single__"), ("scheme_2", "__single__"), ("scheme_3", "__single__"),
    }
    exp = expectations[("scheme_1", "__single__")]
    assert exp.expected_by_volume == (("commercial", 21.0),)
    assert exp.expected_total is None, "加权 total_score 禁止作为原始分母"


_ALL_2304_KEYS = {(1, "commercial"), (2, "technical"), (3, "price")}


def _reading_2304(confidence=0.9, verified_keys=None):
    from app.core.extractor.scoring.scheme_reader.contracts import SchemeMapReading

    return SchemeMapReading(
        ok=True, verified_map=MAP_2304, effective_confidence=confidence,
        verified_volume_keys=_ALL_2304_KEYS if verified_keys is None else verified_keys,
    )


def test_merge_judged_basis_noop_when_unusable():
    context = SchemeReaderContext(mode="enforce", signals=None)
    volume_defs: dict = {}
    exp, changed, note = merge_judged_basis(
        context=context, volume_defs=volume_defs, scope_expectations=None,
    )
    assert (exp, changed, note) == (None, False, "")
    assert volume_defs == {}


def test_merge_judged_basis_keeps_existing_expectations():
    context = SchemeReaderContext(mode="enforce")
    context.reading = _reading_2304()
    existing_exp = {("scheme_9", "__single__"): object()}
    volume_defs: dict = {}
    exp, changed, note = merge_judged_basis(
        context=context, volume_defs=volume_defs, scope_expectations=existing_exp,
    )
    assert exp is existing_exp, "既有权威期望不得被判读覆盖"
    assert changed is True and "补卷基准" in note
    assert set(volume_defs) == {"commercial", "technical", "price"}


def test_merge_judged_basis_blocked_below_confidence_threshold():
    """①: 低置信判读禁止注入基准(2307形态: 置信0.27仍注入了幻觉基准)。"""
    context = SchemeReaderContext(mode="enforce")
    context.reading = _reading_2304(confidence=0.27)
    volume_defs: dict = {}
    exp, changed, note = merge_judged_basis(
        context=context, volume_defs=volume_defs, scope_expectations=None,
    )
    assert changed is False and note == ""
    assert volume_defs == {}


def test_merge_judged_basis_only_accepts_anchor_verified_volumes():
    """②: 未锚定卷声明(prompt示例幻觉形态)不得进入基准。"""
    context = SchemeReaderContext(mode="enforce")
    context.reading = _reading_2304(verified_keys={(3, "price")})
    volume_defs: dict = {}
    exp, changed, note = merge_judged_basis(
        context=context, volume_defs=volume_defs, scope_expectations=None,
    )
    assert set(volume_defs) == {"price"}, "仅锚点核验通过的卷可注入"
    assert exp == {("scheme_3", "__single__"): exp[("scheme_3", "__single__")]}


def test_merge_judged_basis_zero_injection_when_no_volume_anchored():
    """②: 全部卷声明无锚/失锚时零注入(claim不是fact)。"""
    context = SchemeReaderContext(mode="enforce")
    context.reading = _reading_2304(verified_keys=set())
    volume_defs: dict = {}
    exp, changed, note = merge_judged_basis(
        context=context, volume_defs=volume_defs, scope_expectations=None,
    )
    assert (exp, changed, note) == (None, False, "")
    assert volume_defs == {}


# 阶段3(2026-08-31): tail 接线用例随 legacy 尾部执行器删除, 判读单元用例保留如上。
