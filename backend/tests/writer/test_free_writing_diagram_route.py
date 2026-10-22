"""free_writing 图示物化路由测试（步骤3）。

覆盖: 图示 flag 关闭不物化、开启后指令进入 Prompt、LLM 漏插时兜底追加、
物化失败不降级 SectionPlan。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _make_composer(llm_output: str = "<p>自由论述输出</p>"):
    from app.core.writer.section_composer import SectionComposer

    llm_client = SimpleNamespace(chat=AsyncMock(return_value=llm_output))
    composer = SectionComposer(
        db=SimpleNamespace(),
        llm_client=llm_client,
        project_id=1,
    )
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return composer, llm_client


def _patch_section_plan_success(monkeypatch, plan_payload: dict) -> None:
    """把 SectionPlan 解析链打桩为固定成功结果。"""
    import app.core.writer.section_plan.context as plan_context
    import app.core.writer.section_plan.service as plan_service
    import app.core.writer.section_plan.source_loader as plan_source_loader
    from app.core.writer.section_plan.contracts import SectionPlanResolution

    monkeypatch.setattr(
        plan_source_loader,
        "load_section_plan_clauses",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        plan_context,
        "build_section_plan_request",
        lambda **kwargs: SimpleNamespace(outline_node_id=3, input_fingerprint="f" * 64),
    )
    monkeypatch.setattr(
        plan_service,
        "resolve_section_plan",
        AsyncMock(return_value=SectionPlanResolution(
            mode="planned_new",
            section_plan_id=42,
            plan=plan_payload,
        )),
    )


def _plan_with_visuals() -> dict:
    return {
        "section_goal": "阐明施工组织方案",
        "arguments": [],
        "measures": [
            {
                "measure_id": "measure_1",
                "action": "组建现场施工管理团队",
                "steps": ["确定项目经理", "配置专职安全员", "明确岗位职责"],
                "verification": "查验任命文件",
                "argument_ids": [],
                "project_fact_ids": [],
            },
        ],
        "acceptance_criteria": [],
        "visuals": [
            {
                "visual_id": "vis_1",
                "diagram_type": "flowchart",
                "title": "施工管理组织流程图",
                "purpose": "展示组建流程",
                "measure_ids": ["measure_1"],
                "placement_hint": None,
            }
        ],
    }


@pytest.fixture
def base_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "WRITING_WORD_COUNT_MIN_RATIO", 0.0)
    monkeypatch.setattr(settings, "WRITING_DE_AI_REWRITE_ENABLED", False)
    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_ENABLED", True)
    monkeypatch.setattr(settings, "WRITING_SECTION_PLAN_RATIO", 1.0)
    return settings


@pytest.mark.asyncio
async def test_diagram_flag_off_skips_materialization(monkeypatch, base_settings) -> None:
    import app.core.writer.visuals.materializer as materializer_module

    monkeypatch.setattr(base_settings, "WRITING_SECTION_DIAGRAM_ENABLED", False)
    _patch_section_plan_success(monkeypatch, _plan_with_visuals())
    materialize_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        materializer_module,
        "materialize_section_diagrams",
        materialize_mock,
    )

    composer, llm_client = _make_composer()
    node = SimpleNamespace(id=3, title="施工组织方案", depth=1, target_word_count=2000, chapter_profile=None)
    payload = await composer._compose_free(node, None, [], {3: "三、"})

    assert payload.planning_mode == "planned_new"
    materialize_mock.assert_not_awaited()
    assert "data-diagram-id" not in payload.content


@pytest.mark.asyncio
async def test_diagram_directives_enter_prompt_and_fallback_appends(
    monkeypatch, base_settings,
) -> None:
    import app.core.writer.visuals.materializer as materializer_module
    from app.core.writer.visuals.materializer import DiagramAsset

    monkeypatch.setattr(base_settings, "WRITING_SECTION_DIAGRAM_ENABLED", True)
    monkeypatch.setattr(base_settings, "WRITING_SECTION_DIAGRAM_RATIO", 1.0)
    _patch_section_plan_success(monkeypatch, _plan_with_visuals())
    asset = DiagramAsset(
        diagram_id=77,
        visual_id="vis_1",
        diagram_type="flowchart",
        title="施工管理组织流程图",
        png_path="storage/uploads/writing_diagrams/1/3/vis_1_abc.png",
    )
    materialize_mock = AsyncMock(return_value=[asset])
    monkeypatch.setattr(
        materializer_module,
        "materialize_section_diagrams",
        materialize_mock,
    )

    # LLM 输出不含 figure 标记 → 期望兜底追加
    composer, llm_client = _make_composer(llm_output="<p>正文未插图</p>")
    node = SimpleNamespace(id=3, title="施工组织方案", depth=1, target_word_count=2000, chapter_profile=None)
    payload = await composer._compose_free(node, None, [], {3: "三、"})

    materialize_mock.assert_awaited_once()
    user_message = llm_client.chat.await_args.kwargs["user_message"]
    assert 'data-diagram-id="77"' in user_message
    assert payload.content.count('data-diagram-id="77"') == 1
    assert payload.planning_mode == "planned_new"


@pytest.mark.asyncio
async def test_diagram_materialize_failure_keeps_planned_mode(
    monkeypatch, base_settings,
) -> None:
    import app.core.writer.visuals.materializer as materializer_module

    monkeypatch.setattr(base_settings, "WRITING_SECTION_DIAGRAM_ENABLED", True)
    monkeypatch.setattr(base_settings, "WRITING_SECTION_DIAGRAM_RATIO", 1.0)
    _patch_section_plan_success(monkeypatch, _plan_with_visuals())
    monkeypatch.setattr(
        materializer_module,
        "materialize_section_diagrams",
        AsyncMock(side_effect=RuntimeError("render env broken")),
    )

    composer, llm_client = _make_composer()
    node = SimpleNamespace(id=3, title="施工组织方案", depth=1, target_word_count=2000, chapter_profile=None)
    payload = await composer._compose_free(node, None, [], {3: "三、"})

    assert payload.planning_mode == "planned_new"
    assert payload.planning_failure_code is None
    assert "内容生成失败" not in payload.content
