from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_free_writing_prompt_includes_section_plan_text() -> None:
    from app.core.writer.prompts.section_compose import get_free_writing_prompt

    _, user = get_free_writing_prompt(
        node_title="技术方案",
        target_word_count=1200,
        clauses_text="评分点",
        materials_text="素材",
        previous_summaries_text="",
        industry_profile_excerpt="",
        section_plan_text="论证计划",
    )

    assert "## 章节论证计划" in user
    assert "论证计划" in user


@pytest.mark.asyncio
async def test_free_writing_route_legacy_when_section_plan_flag_off(monkeypatch) -> None:
    from app.core.config import settings
    from app.core.writer.section_composer import SectionComposer

    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.0)
    monkeypatch.setattr(settings, "WRITING_DE_AI_REWRITE_ENABLED", False)
    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_ENABLED", False, raising=False)

    llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>自由论述输出</p>"))
    composer = SectionComposer(db=None, llm_client=llm_client, project_id=1)
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]

    node = SimpleNamespace(id=3, title="服务方案", depth=1, target_word_count=2000, chapter_profile=None)
    payload = await composer._compose_free(
        node,
        industry_profile=None,
        previous_summaries=[],
        number_map={3: "三、"},
    )

    assert "自由论述输出" in payload.content
    assert payload.planning_mode == "legacy_flag_off"
    llm_client.chat.assert_awaited_once()
