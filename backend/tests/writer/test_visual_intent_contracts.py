"""VisualIntent 契约与闸门测试（步骤1）。

覆盖: visuals 字段默认/上限、measure 引用校验、
flowchart/gantt 结构闸门不满足时丢弃 intent 并记录 safety_patches。
"""

import pytest
from pydantic import ValidationError


def _base_plan_kwargs() -> dict:
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


class TestVisualIntentDraftSchema:
    def test_visuals_default_empty_list(self) -> None:
        from app.core.writer.section_plan.contracts import SectionPlanDraft

        plan = SectionPlanDraft(**_base_plan_kwargs())
        assert plan.visuals == []

    def test_valid_flowchart_intent_accepted(self) -> None:
        from app.core.writer.section_plan.contracts import SectionPlanDraft

        plan = SectionPlanDraft(
            **_base_plan_kwargs(),
            visuals=[
                {
                    "visual_id": "vis_1",
                    "diagram_type": "flowchart",
                    "title": "施工管理组织流程图",
                    "purpose": "展示施工管理团队组建流程",
                    "measure_ids": ["measure_1"],
                    "placement_hint": "measure_1",
                }
            ],
        )
        assert plan.visuals[0].diagram_type == "flowchart"

    def test_unknown_diagram_type_rejected(self) -> None:
        from app.core.writer.section_plan.contracts import SectionPlanDraft

        with pytest.raises(ValidationError):
            SectionPlanDraft(
                **_base_plan_kwargs(),
                visuals=[
                    {
                        "visual_id": "vis_1",
                        "diagram_type": "pie_chart",
                        "title": "占比图",
                        "purpose": "展示占比",
                        "measure_ids": ["measure_1"],
                    }
                ],
            )

    def test_more_than_two_visuals_rejected(self) -> None:
        from app.core.writer.section_plan.contracts import SectionPlanDraft

        visuals = [
            {
                "visual_id": f"vis_{i}",
                "diagram_type": "flowchart",
                "title": f"流程图{i}",
                "purpose": "展示流程",
                "measure_ids": ["measure_1"],
            }
            for i in range(1, 4)
        ]
        with pytest.raises(ValidationError):
            SectionPlanDraft(**_base_plan_kwargs(), visuals=visuals)

    def test_empty_measure_ids_rejected(self) -> None:
        from app.core.writer.section_plan.contracts import SectionPlanDraft

        with pytest.raises(ValidationError):
            SectionPlanDraft(
                **_base_plan_kwargs(),
                visuals=[
                    {
                        "visual_id": "vis_1",
                        "diagram_type": "flowchart",
                        "title": "流程图",
                        "purpose": "展示流程",
                        "measure_ids": [],
                    }
                ],
            )


class TestVisualIntentMaterializeGate:
    def test_unknown_measure_id_drops_intent_with_patch(self) -> None:
        from app.core.writer.section_plan.contracts import (
            SectionPlanDraft,
            SectionPlanRequest,
            validate_and_materialize_section_plan,
        )

        plan = SectionPlanDraft(
            **_base_plan_kwargs(),
            visuals=[
                {
                    "visual_id": "vis_1",
                    "diagram_type": "flowchart",
                    "title": "流程图",
                    "purpose": "展示流程",
                    "measure_ids": ["measure_missing"],
                }
            ],
        )
        request = SectionPlanRequest(**_request_kwargs())
        payload = validate_and_materialize_section_plan(plan, request)
        assert payload["visuals"] == []
        kinds = {patch["kind"] for patch in payload["safety_patches"]}
        assert "invalid_visual_intent" in kinds

    def test_flowchart_with_too_few_steps_dropped(self) -> None:
        from app.core.writer.section_plan.contracts import (
            SectionPlanDraft,
            SectionPlanRequest,
            validate_and_materialize_section_plan,
        )

        # measure_2 只有2步, flowchart 闸门要求引用步骤总数 >= 3
        plan = SectionPlanDraft(
            **_base_plan_kwargs(),
            visuals=[
                {
                    "visual_id": "vis_1",
                    "diagram_type": "flowchart",
                    "title": "进度编制流程图",
                    "purpose": "展示进度编制流程",
                    "measure_ids": ["measure_2"],
                }
            ],
        )
        request = SectionPlanRequest(**_request_kwargs())
        payload = validate_and_materialize_section_plan(plan, request)
        assert payload["visuals"] == []
        kinds = {patch["kind"] for patch in payload["safety_patches"]}
        assert "visual_gate_failed" in kinds

    def test_valid_flowchart_intent_kept_with_normalized_node_labels(self) -> None:
        """闸门通过的 flowchart 意图必须写回归一后的 node_labels, 编译器100%可消费。"""
        from app.core.writer.section_plan.contracts import (
            SectionPlanDraft,
            SectionPlanRequest,
            validate_and_materialize_section_plan,
        )

        plan = SectionPlanDraft(
            **_base_plan_kwargs(),
            visuals=[
                {
                    "visual_id": "vis_1",
                    "diagram_type": "flowchart",
                    "title": "施工管理组织流程图",
                    "purpose": "展示施工管理团队组建流程",
                    "measure_ids": ["measure_1"],
                }
            ],
        )
        request = SectionPlanRequest(**_request_kwargs())
        payload = validate_and_materialize_section_plan(plan, request)
        assert len(payload["visuals"]) == 1
        assert payload["visuals"][0]["visual_id"] == "vis_1"
        assert payload["visuals"][0]["node_labels"] == [
            "确定项目经理",
            "配置专职安全员",
            "明确岗位职责",
        ]
        assert "safety_patches" not in payload

    def test_flowchart_over_limit_degrades_to_measure_actions(self) -> None:
        """ 回归: 项目2232节点7354——引用多措施步骤总数超12。

 旧实现闸门放行、编译期 flowchart_too_many_nodes 失败;
 新实现闸门降级为按 measure.action 聚合的粗粒度流程图。
 """
        from app.core.writer.section_plan.contracts import (
            SectionPlanDraft,
            SectionPlanRequest,
            validate_and_materialize_section_plan,
        )

        base = _base_plan_kwargs()
        base["measures"] = [
            {
                "measure_id": f"measure_{i}",
                "action": f"专项管理措施{i}",
                "steps": [f"步骤{i}_{j}" for j in range(4)],
                "verification": "记录",
                "argument_ids": [],
                "project_fact_ids": [],
            }
            for i in range(1, 5)
        ]
        plan = SectionPlanDraft(
            **base,
            visuals=[
                {
                    "visual_id": "vis_1",
                    "diagram_type": "flowchart",
                    "title": "HSE管理措施实施流程图",
                    "purpose": "展示完整HSE管理流程",
                    "measure_ids": ["measure_1", "measure_2", "measure_3", "measure_4"],
                }
            ],
        )
        request = SectionPlanRequest(**_request_kwargs())
        payload = validate_and_materialize_section_plan(plan, request)
        assert len(payload["visuals"]) == 1
        assert payload["visuals"][0]["node_labels"] == [
            "专项管理措施1",
            "专项管理措施2",
            "专项管理措施3",
            "专项管理措施4",
        ]
        kinds = {patch["kind"] for patch in payload["safety_patches"]}
        assert "visual_degraded_aggregated" in kinds

    def test_flowchart_over_limit_without_aggregation_room_dropped(self) -> None:
        """2个措施共16步: 聚合后仅2节点 < 下限3, 意图应丢弃而非产出无意义流程图。"""
        from app.core.writer.section_plan.contracts import (
            SectionPlanDraft,
            SectionPlanRequest,
            validate_and_materialize_section_plan,
        )

        base = _base_plan_kwargs()
        base["measures"] = [
            {
                "measure_id": f"measure_{i}",
                "action": f"专项管理措施{i}",
                "steps": [f"步骤{i}_{j}" for j in range(8)],
                "verification": "记录",
                "argument_ids": [],
                "project_fact_ids": [],
            }
            for i in range(1, 3)
        ]
        plan = SectionPlanDraft(
            **base,
            visuals=[
                {
                    "visual_id": "vis_1",
                    "diagram_type": "flowchart",
                    "title": "管理流程图",
                    "purpose": "展示管理流程",
                    "measure_ids": ["measure_1", "measure_2"],
                }
            ],
        )
        request = SectionPlanRequest(**_request_kwargs())
        payload = validate_and_materialize_section_plan(plan, request)
        assert payload["visuals"] == []
        kinds = {patch["kind"] for patch in payload["safety_patches"]}
        assert "visual_gate_failed" in kinds

    def test_gantt_without_phases_dropped(self) -> None:
        """ 回归: 项目2232节点7352——gantt 意图未携带结构化 phases。

 旧实现放行后在编译期以 gantt_missing_duration 失败;
 新实现在闸门层直接丢弃并打补丁, 失败不再后置。
 """
        from app.core.writer.section_plan.contracts import (
            SectionPlanDraft,
            SectionPlanRequest,
            validate_and_materialize_section_plan,
        )

        plan = SectionPlanDraft(
            **_base_plan_kwargs(),
            visuals=[
                {
                    "visual_id": "vis_1",
                    "diagram_type": "gantt",
                    "title": "项目进度计划甘特图",
                    "purpose": "直观展示90天工期内的任务分解",
                    "measure_ids": ["measure_1"],
                    "placement_hint": "measure_1",
                }
            ],
        )
        request = SectionPlanRequest(**_request_kwargs())
        payload = validate_and_materialize_section_plan(plan, request)
        assert payload["visuals"] == []
        kinds = {patch["kind"] for patch in payload["safety_patches"]}
        assert "visual_gate_failed" in kinds

    def test_visual_policy_rejects_company_intro_diagram(self) -> None:
        from app.core.writer.visuals.eligibility_policy import filter_visual_intents

        payload = {
            "measures": [
                {
                    "measure_id": "measure_1",
                    "action": "介绍企业基本情况",
                    "steps": ["说明公司概况", "列明资质证书", "展示服务承诺"],
                }
            ],
            "visuals": [
                {
                    "visual_id": "vis_1",
                    "diagram_type": "flowchart",
                    "title": "公司简介流程图",
                    "purpose": "展示企业概况",
                    "measure_ids": ["measure_1"],
                    "placement_hint": "measure_1",
                    "node_labels": ["公司概况", "资质证书", "服务承诺"],
                }
            ],
        }

        result = filter_visual_intents(
            payload,
            node_title="公司简介",
            chapter_profile={"semantic_role": "介绍投标人基本情况的文字章节"},
        )

        assert result.payload["visuals"] == []
        assert result.patches[0]["kind"] == "visual_policy_rejected"

    def test_gantt_single_phase_dropped(self) -> None:
        from app.core.writer.section_plan.contracts import (
            SectionPlanDraft,
            SectionPlanRequest,
            validate_and_materialize_section_plan,
        )

        plan = SectionPlanDraft(
            **_base_plan_kwargs(),
            visuals=[
                {
                    "visual_id": "vis_1",
                    "diagram_type": "gantt",
                    "title": "调试进度甘特图",
                    "purpose": "展示调试进度安排",
                    "measure_ids": ["measure_2"],
                    "phases": [{"label": "整机调试", "duration_days": 7}],
                }
            ],
        )
        request = SectionPlanRequest(**_request_kwargs())
        payload = validate_and_materialize_section_plan(plan, request)
        assert payload["visuals"] == []
        kinds = {patch["kind"] for patch in payload["safety_patches"]}
        assert "visual_gate_failed" in kinds

    def test_gantt_with_structured_phases_kept(self) -> None:
        from app.core.writer.section_plan.contracts import (
            SectionPlanDraft,
            SectionPlanRequest,
            validate_and_materialize_section_plan,
        )

        plan = SectionPlanDraft(
            **_base_plan_kwargs(),
            visuals=[
                {
                    "visual_id": "vis_1",
                    "diagram_type": "gantt",
                    "title": "实施进度甘特图",
                    "purpose": "展示阶段进度安排",
                    "measure_ids": ["measure_1", "measure_2"],
                    "phases": [
                        {"label": "样品准备与设备校准", "duration_days": 14},
                        {"label": "静态抗蚀测试", "duration_days": 42},
                        {"label": "报告编制与审核", "duration_days": 14},
                    ],
                }
            ],
        )
        request = SectionPlanRequest(**_request_kwargs())
        payload = validate_and_materialize_section_plan(plan, request)
        assert len(payload["visuals"]) == 1
        assert payload["visuals"][0]["phases"] == [
            {"label": "样品准备与设备校准", "duration_days": 14},
            {"label": "静态抗蚀测试", "duration_days": 42},
            {"label": "报告编制与审核", "duration_days": 14},
        ]
        assert "safety_patches" not in payload
