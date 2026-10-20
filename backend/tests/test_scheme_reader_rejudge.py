# -*- coding: utf-8 -*-
"""双向对账 + 结构重判环测试（SchemeReader 阶段3/4）。

防御目标(映射真实风险):
-: 校验产物必须携带逐卷双向差异(缺口+盈余), 盈余形态判定为结构失真;
- (红色清单第4次复发): 结构失真触发不得依赖 failed_scopes/expected_by_scope,
 盈余形态必须跳过 L2 add_fact(数学不可修), 路由重判环或人工警告;
- 重判环: 差异反馈驱动、封顶2轮、未闭合恢复归属快照(原子性)并升级人工警告。
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.extractor.scoring.scheme_reader.integration import SchemeReaderContext
from app.core.extractor.scoring.scheme_reader.rejudge_loop import (
    format_delta_feedback,
    rejudge_scheme_map,
    route_structural_distortion,
)
from app.core.extractor.scoring.scoring_facts import build_scoring_fact_set
from app.core.extractor.scoring.scoring_scope_contracts import ScoringValidationKind
from app.core.extractor.scoring.scoring_scope_validator import validate_scoring_fact_set
from app.schemas.requirement import ExtractedScoringItem

from tests.test_scheme_reader import _FakeLLM
from tests.test_scheme_reader_attribution import SCHEME_MAP_2249, SIGNALS_2249

pytestmark = pytest.mark.scoring


def _item(name, score, volume="technical", pages=None):
    return ExtractedScoringItem(
        category="TECH",
        score_item_name=name,
        description=f"{name}的评分标准描述",
        score_weight=score,
        volume=volume,
        source_pages=pages or [20],
    )


def _volume_defs_2249():
    return {
        "technical": SimpleNamespace(volume_max=53.0),
        "commercial": SimpleNamespace(volume_max=32.0),
        "price": SimpleNamespace(volume_max=15.0),
    }


def _items_2249_misattributed():
    """2249形态: 商务两项被错标 technical, 价格正确。守恒(总100)但商务卷零覆盖。"""
    return [
        _item("技术方案", 33, volume="technical"),
        _item("实施计划", 20, volume="technical"),
        _item("综合能力", 12, volume="technical"),
        _item("企业业绩", 20, volume="technical"),
        _item("价格评分", 15, volume="price"),
    ]


def _items_2250_surplus():
    """2250形态: 多方案混算, 单scope总300对隐含基准100。"""
    return [
        _item("商务项甲", 100, volume="technical", pages=[10]),
        _item("技术项乙", 100, volume="technical", pages=[11]),
        _item("其他项丙", 100, volume="technical", pages=[12]),
    ]


# ---------- 双向对账(validator scope_deltas) ----------

def test_validator_deltas_expose_zero_coverage_and_surplus_2249():
    """ 核心: 商务卷零覆盖 + 技术卷盈余必须同时暴露在 scope_deltas。"""
    fact_set = build_scoring_fact_set(_items_2249_misattributed())
    validation = validate_scoring_fact_set(
        fact_set, declared_volume_defs=_volume_defs_2249()
    )

    assert validation.kind == ScoringValidationKind.EVIDENCE_GAP
    assert validation.failed_scopes == (), "零覆盖分支 failed_scopes 为空(死锁形态)"
    deltas = {(d.volume): d for d in validation.scope_deltas}
    assert deltas["commercial"].actual == 0.0
    assert deltas["commercial"].direction == "deficit"
    assert deltas["technical"].actual == 85.0
    assert deltas["technical"].direction == "surplus"
    assert validation.has_structural_distortion() is True


def test_validator_deltas_expose_total_surplus_2250():
    """ 核心: 总集300对基准100的盈余必须判定为结构失真。"""
    fact_set = build_scoring_fact_set(_items_2250_surplus())
    validation = validate_scoring_fact_set(fact_set)

    assert validation.kind == ScoringValidationKind.EVIDENCE_GAP
    total_delta = next(d for d in validation.scope_deltas if d.volume == "")
    assert total_delta.actual == 300.0
    assert total_delta.direction == "surplus"
    assert validation.has_structural_distortion() is True


def test_pure_deficit_is_not_structural():
    """真缺条目形态(总60/100无盈余)不得判结构失真, 保持 L2 通路。"""
    items = [_item("技术方案", 30), _item("实施计划", 30)]
    validation = validate_scoring_fact_set(build_scoring_fact_set(items))

    assert validation.kind == ScoringValidationKind.EVIDENCE_GAP
    assert validation.has_structural_distortion() is False
    directions = {d.direction for d in validation.scope_deltas}
    assert "surplus" not in directions


def test_format_delta_feedback_is_specific():
    """重判反馈必须是具体差异文本, 非笼统重试。"""
    fact_set = build_scoring_fact_set(_items_2249_misattributed())
    validation = validate_scoring_fact_set(
        fact_set, declared_volume_defs=_volume_defs_2249()
    )
    feedback = format_delta_feedback(validation)

    assert "商务卷" in feedback
    assert "零覆盖" in feedback
    assert "技术卷" in feedback
    assert "盈余" in feedback
    assert "85" in feedback and "53" in feedback


# ---------- 重判环 ----------

def _enforce_context():
    return SchemeReaderContext(mode="enforce", signals=SIGNALS_2249)


def _scheme_map_2249_payload():
    return SCHEME_MAP_2249.model_dump()


@pytest.mark.asyncio
async def test_rejudge_resolves_2249_on_first_round():
    """重判环: 判读器给出正确 SchemeMap 后第一轮闭合。"""
    items = _items_2249_misattributed()
    validation = validate_scoring_fact_set(
        build_scoring_fact_set(items), declared_volume_defs=_volume_defs_2249()
    )
    llm = _FakeLLM(response=_scheme_map_2249_payload())

    outcome = await rejudge_scheme_map(
        context=_enforce_context(), items=items, validation=validation,
        volume_defs=_volume_defs_2249(), scope_expectations=None,
        document_id=2249, llm_client=llm,
    )

    assert outcome.resolved is True
    assert outcome.rounds == 1
    assert outcome.warning is None
    by_name = {i.score_item_name: i for i in items}
    assert by_name["综合能力"].volume == "commercial"
    assert by_name["企业业绩"].volume == "commercial"
    # 反馈必须携带具体差异
    assert "零覆盖" in llm.calls[0]["user_message"]


@pytest.mark.asyncio
async def test_rejudge_caps_rounds_and_restores_snapshot():
    """重判环: 判读持续无效时封顶2轮, 恢复归属快照并产出人工警告。"""
    items = _items_2249_misattributed()
    validation = validate_scoring_fact_set(
        build_scoring_fact_set(items), declared_volume_defs=_volume_defs_2249()
    )
    llm = _FakeLLM(response={"schemes": [], "assignments": [], "confidence": 0.1})

    outcome = await rejudge_scheme_map(
        context=_enforce_context(), items=items, validation=validation,
        volume_defs=_volume_defs_2249(), scope_expectations=None,
        document_id=2249, llm_client=llm,
    )

    assert outcome.resolved is False
    assert outcome.warning is not None
    assert outcome.warning["type"] == "scoring_structure_warning"
    assert "零覆盖" in outcome.warning["delta_text"]
    # 快照恢复: 条目保持进入时的错误归属(不半改)
    assert all(i.volume in ("technical", "price") for i in items)


@pytest.mark.asyncio
async def test_rejudge_without_signals_warns_immediately():
    context = SchemeReaderContext(mode="enforce", signals=None)
    items = _items_2249_misattributed()
    validation = validate_scoring_fact_set(
        build_scoring_fact_set(items), declared_volume_defs=_volume_defs_2249()
    )
    outcome = await rejudge_scheme_map(
        context=context, items=items, validation=validation,
        volume_defs=_volume_defs_2249(), scope_expectations=None,
        document_id=2249, llm_client=_FakeLLM(),
    )
    assert outcome.resolved is False
    assert outcome.warning is not None


# ---------- 结构失真路由 ----------

@pytest.mark.asyncio
async def test_route_shadow_mode_warns_without_llm():
    """shadow 模式: 不重判(零LLM调用), 直接产出确定性警告。"""
    items = _items_2250_surplus()
    validation = validate_scoring_fact_set(build_scoring_fact_set(items))
    llm = _FakeLLM()
    context = SchemeReaderContext(mode="shadow", signals=SIGNALS_2249)

    outcome = await route_structural_distortion(
        scheme_context=context, items=items, validation=validation,
        volume_defs={}, scope_expectations=None, document_id=2250, llm_client=llm,
    )

    assert outcome.resolved is False
    assert outcome.warning is not None
    assert llm.calls == []
    assert any("结构性失真" in d for d in outcome.diagnostics)


# 阶段3(2026-08-31): rescue tail L2收窄用例随 legacy 尾部执行器删除, 重判单元用例保留如上。
