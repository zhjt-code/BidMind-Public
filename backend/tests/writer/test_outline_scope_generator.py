from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.writer.outline_generator import OutlineGenerator
from app.schemas.writing import BidDirectorySpec, BidStructureAnalysis


@pytest.mark.asyncio
async def test_generate_from_spec_still_saves_when_scope_attach_raises(monkeypatch) -> None:
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "technical",
                "volume_title": "技术标",
                "items": [{"order": 1, "title": "技术方案"}],
            }
        ],
    )
    saved_nodes: list = []

    async def _capture_save(_project_id, nodes):
        saved_nodes.extend(nodes)
        return nodes

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("scope attach boom")

    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_enrich_skeleton_node", AsyncMock())
    monkeypatch.setattr(
        "app.core.writer.outline_generator.attach_scope_titles_to_outline",
        _boom,
    )
    monkeypatch.setattr(generator, "_save_tree", _capture_save)

    await generator._generate_from_spec(7, spec, 30000)

    assert saved_nodes, "挂载失败时仍应落库骨架"
    assert saved_nodes[0].children[0].title == "技术方案"


@pytest.mark.asyncio
async def test_generate_self_composed_calls_scope_attach_after_fill(monkeypatch) -> None:
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    call_order: list[str] = []

    async def _fill_volume(*_args, **_kwargs):
        call_order.append("fill")

    async def _attach(*_args, **_kwargs):
        call_order.append("attach")
        return 0

    async def _save_tree(_project_id, nodes):
        call_order.append("save")
        return nodes

    monkeypatch.setattr(generator, "_build_context", AsyncMock(return_value={"industry_profile": ""}))
    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_fill_volume", _fill_volume)
    monkeypatch.setattr(
        "app.core.writer.outline_generator.attach_scope_titles_to_outline",
        _attach,
    )
    monkeypatch.setattr(generator, "_save_tree", _save_tree)

    await generator._generate_self_composed(
        7,
        BidStructureAnalysis(
            structure_mode="tech_only",
            has_technical=True,
            has_commercial=False,
            has_qualification=False,
        ),
        30000,
    )

    assert call_order == ["fill", "attach", "save"]
