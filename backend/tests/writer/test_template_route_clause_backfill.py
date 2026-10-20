""" 防御测试: fixed_template 成功路径回填全部直接绑定约束。

风险映射: fixed_template 章节此前只回填命中的单条模板约束, 导致其余已绑定并
被该章节实际消费的约束(material_required/disqualification 等)被覆盖统计误判
"未进入正文来源"。本测试锁定:
- 成功路径: source_clause_ids == 模板约束 + 全部直接绑定约束, 且以
 include_inherited=False 加载(不虚报继承/全局约束消费);
- 缺失降级路径: 不加载直接绑定、不虚报覆盖(占位内容不代表约束被消费)。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _clause(clause_id: int, sub_type: str = "content_required"):
    return SimpleNamespace(
        id=clause_id, sub_type=sub_type, content=f"约束{clause_id}", raw_text=None,
    )


@pytest.mark.asyncio
async def test_fill_template_success_backfills_all_direct_bound_clauses(monkeypatch) -> None:
    import app.core.writer.composer.template_route as template_route
    from app.core.writer.section_composer import ComposePayload, SectionComposer

    # 绕过模板可用性判定(slots>=2/标题匹配等), 聚焦"成功路径回填"契约本身
    monkeypatch.setattr(
        template_route, "_is_template_source_usable", lambda *a, **k: True,
    )

    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    composer._find_bound_clause = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            id=42, raw_text="投标函正文, 投标人: {投标人}, 日期: {日期}",
            sub_type="fixed_template",
        )
    )
    composer._find_fallback_template_clause = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    # 该节点直接绑定 3 条约束: 模板约束 42 自身 + 另外两条
    load_mock = AsyncMock(
        return_value=[
            _clause(42, "fixed_template"), _clause(101), _clause(102),
        ]
    )
    composer._load_bound_clauses = load_mock  # type: ignore[method-assign]

    node = SimpleNamespace(id=1, title="第1章 投标函", project_id=10, node_type=None)
    payload = await composer._fill_template(node)

    assert isinstance(payload, ComposePayload)
    # 模板约束 + 全部直接绑定约束都进入 source_clause_ids
    assert set(payload.source_clause_ids) == {42, 101, 102}
    # 必须以 include_inherited=False 调用(降级/占位不虚报继承约束消费)
    load_mock.assert_awaited_once_with(node.id, include_inherited=False)


@pytest.mark.asyncio
async def test_fill_template_missing_template_does_not_backfill() -> None:
    """降级路径(模板缺失)不加载直接绑定, 占位内容不虚报约束覆盖。"""
    from app.core.writer.section_composer import ComposePayload, SectionComposer

    composer = SectionComposer(db=None, llm_client=SimpleNamespace())
    # 找不到模板约束 → template 为空 → 走"模板缺失"降级出口
    composer._find_bound_clause = AsyncMock(return_value=None)  # type: ignore[method-assign]
    composer._find_fallback_template_clause = AsyncMock(return_value=None)  # type: ignore[method-assign]
    load_mock = AsyncMock(return_value=[_clause(101)])
    composer._load_bound_clauses = load_mock  # type: ignore[method-assign]

    node = SimpleNamespace(id=1, title="第1章 投标函", project_id=10, node_type=None)
    payload = await composer._fill_template(node)

    assert isinstance(payload, ComposePayload)
    # clause 缺失, source_clause_ids 为空; 绝不回填直接绑定
    assert payload.source_clause_ids == []
    load_mock.assert_not_awaited()
