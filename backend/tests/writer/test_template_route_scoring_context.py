"""D3 防御测试: fixed_template 路径绑定评分项时保留评分提示。

风险映射: fixed_template 章节不调用 build_clause_directives, 当该章节绑定了
scoring_criterion 约束时, 评分项的 score_rules/score_item_name 等提示信息完全丢失,
无法被下游检查器或报告消费。

锁定契约:
- fill_template 成功路径: 若直接绑定约束中含 scoring_criterion, consumption_trace
 中须包含 scoring_hints 字段, 且其中含对应评分项名称。
- 无评分约束时: consumption_trace 不添加 scoring_hints (或为空列表), 不影响主流程。
- 降级路径(模板缺失/不匹配): 不加载直接绑定, scoring_hints 不出现 (与 契约一致)。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.scoring


def _clause(clause_id: int, sub_type: str = "content_required", *, metadata=None):
    return SimpleNamespace(
        id=clause_id,
        sub_type=sub_type,
        content=f"约束{clause_id}",
        raw_text=None,
        metadata_json=metadata or {},
    )


def _scoring_clause(clause_id: int, *, name: str, score_rules: list[str]):
    return SimpleNamespace(
        id=clause_id,
        sub_type="scoring_criterion",
        content=name,
        raw_text=None,
        metadata_json={
            "score_item_name": name,
            "max_score": 10,
            "score_rules": score_rules,
        },
    )


@pytest.mark.asyncio
async def test_fill_template_with_scoring_clause_records_scoring_hints(monkeypatch) -> None:
    """成功路径: 绑定 scoring_criterion 时, consumption_trace 包含 scoring_hints。"""
    import app.core.writer.composer.template_route as template_route
    from app.core.writer.section_composer import ComposePayload, SectionComposer

    monkeypatch.setattr(
        template_route, "_is_template_source_usable", lambda *a, **k: True,
    )

    template_clause = _clause(42, "fixed_template")
    template_clause.raw_text = "投标函正文, 投标人: {投标人}, 日期: {日期}"
    sc = _scoring_clause(50, name="技术方案完整性", score_rules=["覆盖全部技术指标得满分"])

    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    composer._find_bound_clause = AsyncMock(return_value=template_clause)  # type: ignore[method-assign]
    composer._find_fallback_template_clause = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(  # type: ignore[method-assign]
        return_value=[template_clause, sc]
    )

    node = SimpleNamespace(id=1, title="第1章 投标函", project_id=10, node_type=None)
    payload = await composer._fill_template(node)

    assert isinstance(payload, ComposePayload)
    trace = payload.consumption_trace
    assert trace is not None, "consumption_trace 不应为 None"
    assert "scoring_hints" in trace, (
        "绑定 scoring_criterion 时 consumption_trace 应包含 scoring_hints"
    )
    hints = trace["scoring_hints"]
    assert isinstance(hints, list)
    assert len(hints) >= 1
    assert any("技术方案完整性" in h for h in hints), (
        "scoring_hints 中应包含评分项名称"
    )


@pytest.mark.asyncio
async def test_fill_template_without_scoring_clause_no_scoring_hints(monkeypatch) -> None:
    """无评分约束时: consumption_trace 不含 scoring_hints 或列表为空, 主流程不受影响。"""
    import app.core.writer.composer.template_route as template_route
    from app.core.writer.section_composer import ComposePayload, SectionComposer

    monkeypatch.setattr(
        template_route, "_is_template_source_usable", lambda *a, **k: True,
    )

    template_clause = _clause(42, "fixed_template")
    template_clause.raw_text = "投标函正文, 投标人: {投标人}, 日期: {日期}"

    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    composer._find_bound_clause = AsyncMock(return_value=template_clause)  # type: ignore[method-assign]
    composer._find_fallback_template_clause = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(  # type: ignore[method-assign]
        return_value=[template_clause, _clause(101), _clause(102)]
    )

    node = SimpleNamespace(id=1, title="第1章 投标函", project_id=10, node_type=None)
    payload = await composer._fill_template(node)

    assert isinstance(payload, ComposePayload)
    trace = payload.consumption_trace or {}
    hints = trace.get("scoring_hints", [])
    assert hints == [], "无 scoring_criterion 绑定时 scoring_hints 应为空"


@pytest.mark.asyncio
async def test_fill_template_scoring_hints_preserves_existing_backfill_contract(
    monkeypatch,
) -> None:
    """回归: 加入 D3 后, 的 source_clause_ids 回填契约不变。"""
    import app.core.writer.composer.template_route as template_route
    from app.core.writer.section_composer import ComposePayload, SectionComposer

    monkeypatch.setattr(
        template_route, "_is_template_source_usable", lambda *a, **k: True,
    )

    template_clause = _clause(42, "fixed_template")
    template_clause.raw_text = "投标函正文, 投标人: {投标人}, 日期: {日期}"
    sc = _scoring_clause(50, name="技术方案完整性", score_rules=["覆盖全部技术指标得满分"])

    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    composer._find_bound_clause = AsyncMock(return_value=template_clause)  # type: ignore[method-assign]
    composer._find_fallback_template_clause = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    load_mock = AsyncMock(return_value=[template_clause, _clause(101), sc])
    composer._load_bound_clauses = load_mock  # type: ignore[method-assign]

    node = SimpleNamespace(id=1, title="第1章 投标函", project_id=10, node_type=None)
    payload = await composer._fill_template(node)

    assert isinstance(payload, ComposePayload)
    assert set(payload.source_clause_ids) == {42, 101, 50}, (
        " 契约: source_clause_ids 必须包含所有直接绑定约束"
    )
    load_mock.assert_awaited_once_with(node.id, include_inherited=False)
