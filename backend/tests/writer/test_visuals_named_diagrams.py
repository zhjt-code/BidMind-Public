""": 6 图元 IR/闸门/渲染回归。"""

from pathlib import Path

from app.core.writer.visuals.ir_builder import build_diagram_ir
from app.core.writer.visuals.page_fit import MAX_DIAGRAM_HEIGHT_MM, fit_diagram_size_mm
from app.core.writer.visuals.png_renderer import render_diagram_png


def _plan_kwargs() -> dict:
    return {
        "section_goal": "阐明本章施工组织方案的目标与实现路径",
        "arguments": [
            {
                "argument_id": "arg_1",
                "claim": "施工组织安排能够满足工期要求",
                "scoring_point_ids": [],
                "project_fact_ids": [],
            }
        ],
        "measures": [
            {
                "measure_id": "measure_1",
                "action": "组建现场施工管理团队",
                "steps": ["确定项目经理", "配置专职安全员", "明确岗位职责"],
                "verification": "查验任命文件",
                "argument_ids": [],
                "project_fact_ids": [],
            },
            {
                "measure_id": "measure_2",
                "action": "编制施工进度计划",
                "steps": ["分解工序", "排布关键线路"],
                "verification": "查验进度计划表",
                "argument_ids": [],
                "project_fact_ids": [],
            },
        ],
        "acceptance_criteria": [
            {
                "criterion_id": "acc_1",
                "criterion": "施工组织方案通过内部评审",
                "verification_method": "评审会议纪要",
                "source_clause_ids": [],
                "scoring_point_ids": [],
                "measure_ids": [],
            }
        ],
    }


def _request_kwargs() -> dict:
    return {
        "project_id": 1,
        "outline_node_id": 2,
        "node_title": "施工组织方案",
        "target_word_count": 2000,
        "constraints": [],
        "evidence_catalog": [],
        "prompt_snapshot": {
            "planner_prompt_sha256": "0" * 64,
            "source": "code",
        },
        "input_fingerprint": "0" * 64,
    }


def _intent_org() -> dict:
    return {
        "title": "项目经理部组织架构图",
        "org_lead": "项目经理",
        "org_units": ["技术组", "质量组", "安全组", "商务组"],
    }


def test_org_chart_ir_and_png_fit_a4(tmp_path: Path) -> None:
    from PIL import Image

    ir = build_diagram_ir({}, {**_intent_org(), "diagram_type": "org_chart"})
    assert ir.diagram_type == "org_chart"
    assert ir.lead == "项目经理"
    assert len(ir.units) == 4
    out_path = tmp_path / "org.png"
    render_diagram_png(ir, out_path)
    with Image.open(out_path) as image:
        fitted_w, fitted_h = fit_diagram_size_mm(140, image.width, image.height)
    assert fitted_h <= MAX_DIAGRAM_HEIGHT_MM
    assert fitted_w <= 140


def test_swimlane_ir_compiles_cross_role_steps(tmp_path: Path) -> None:
    from PIL import Image

    ir = build_diagram_ir({}, {
        "diagram_type": "swimlane",
        "title": "甲乙方协作泳道图",
        "lanes": ["甲方", "乙方"],
        "swim_nodes": [
            {"label": "下达任务", "lane": "甲方"},
            {"label": "编制方案", "lane": "乙方"},
            {"label": "审核确认", "lane": "甲方"},
            {"label": "组织实施", "lane": "乙方"},
        ],
    })
    assert [node.lane for node in ir.nodes] == ["甲方", "乙方", "甲方", "乙方"]
    out_path = tmp_path / "swim.png"
    render_diagram_png(ir, out_path)
    assert Image.open(out_path).size[0] > 100


def test_roadmap_and_layered_render(tmp_path: Path) -> None:
    from PIL import Image

    roadmap = build_diagram_ir({}, {
        "diagram_type": "roadmap",
        "title": "建设路线图",
        "phases": [
            {"label": "建设期", "duration_days": 60, "milestone": False},
            {"label": "试运行", "duration_days": 30, "milestone": True},
            {"label": "验收运维", "duration_days": 15, "milestone": True},
        ],
    })
    assert roadmap.phases[1].milestone is True
    layered = build_diagram_ir({}, {
        "diagram_type": "layered",
        "title": "系统技术架构图",
        "layers": [
            {"label": "应用层", "items": ["门户", "业务系统"]},
            {"label": "服务层", "items": ["API"]},
            {"label": "数据层", "items": ["库"]},
            {"label": "基础设施层", "items": ["云主机"]},
        ],
    })
    assert len(layered.layers) == 4
    render_diagram_png(roadmap, tmp_path / "roadmap.png")
    render_diagram_png(layered, tmp_path / "layered.png")
    assert Image.open(tmp_path / "roadmap.png").size[1] > 80
    assert Image.open(tmp_path / "layered.png").size[1] > 80


def test_org_chart_gate_keeps_structured_intent() -> None:
    from app.core.writer.section_plan.contracts import (
        SectionPlanDraft,
        SectionPlanRequest,
        validate_and_materialize_section_plan,
    )

    plan = SectionPlanDraft(
        **_plan_kwargs(),
        visuals=[
            {
                "visual_id": "vis_org",
                "diagram_type": "org_chart",
                "title": "项目经理部组织架构图",
                "purpose": "展示汇报关系",
                "measure_ids": ["measure_1"],
                "org_lead": "项目经理",
                "org_units": ["技术组", "质量组", "安全组"],
            }
        ],
    )
    payload = validate_and_materialize_section_plan(
        plan, SectionPlanRequest(**_request_kwargs()),
    )
    assert payload["visuals"][0]["diagram_type"] == "org_chart"
    assert payload["visuals"][0]["org_units"] == ["技术组", "质量组", "安全组"]


def test_pdca_template_fills_flowchart_when_labels_missing() -> None:
    from app.core.writer.section_plan.contracts import (
        SectionPlanDraft,
        SectionPlanRequest,
        validate_and_materialize_section_plan,
    )

    plan = SectionPlanDraft(
        **_plan_kwargs(),
        visuals=[
            {
                "visual_id": "vis_pdca",
                "diagram_type": "flowchart",
                "title": "质量PDCA闭环图",
                "purpose": "展示持续改进",
                "measure_ids": ["measure_2"],
                "template_id": "pdca",
            }
        ],
    )
    payload = validate_and_materialize_section_plan(
        plan, SectionPlanRequest(**_request_kwargs()),
    )
    assert payload["visuals"][0]["node_labels"] == ["计划", "执行", "检查", "改进"]


def test_org_chapter_allows_org_chart() -> None:
    from app.core.writer.visuals.eligibility_policy import _is_visual_allowed

    assert _is_visual_allowed(
        {
            "diagram_type": "org_chart",
            "title": "项目组织架构图",
            "purpose": "展示汇报关系",
        },
        node_title="项目组织架构与职责",
        chapter_profile={"semantic_role": "说明项目团队层级"},
    ) is True
