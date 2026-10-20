"""Diagram IR 编译器测试（步骤2, 重构后）。

覆盖: flowchart 节点/边构建（node_labels 优先/steps 回退）、节点数上下限、
gantt 结构化 phases 消费、缺 phases/非法阶段失败码、IR 哈希稳定性。
"""

import pytest


def _plan_payload() -> dict:
    return {
        "section_goal": "阐明施工组织方案",
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
                "action": "基础施工阶段",
                "steps": ["场地平整", "基础浇筑"],
                "verification": "查验隐蔽工程记录",
                "argument_ids": [],
                "project_fact_ids": [],
            },
        ],
    }


class TestFlowchartIR:
    def test_builds_from_explicit_node_labels_first(self) -> None:
        from app.core.writer.visuals.ir_builder import build_diagram_ir

        intent = {
            "visual_id": "vis_1",
            "diagram_type": "flowchart",
            "title": "施工管理组织流程图",
            "purpose": "展示组建流程",
            "measure_ids": ["measure_1"],
            "placement_hint": None,
            "node_labels": ["体系建立", "风险辨识", "培训宣贯", "应急演练"],
        }
        ir = build_diagram_ir(_plan_payload(), intent)
        assert [node.label for node in ir.nodes] == [
            "体系建立",
            "风险辨识",
            "培训宣贯",
            "应急演练",
        ]
        assert len(ir.edges) == 3

    def test_falls_back_to_measure_steps_for_legacy_plans(self) -> None:
        """存量计划的 intent 无 node_labels, 必须仍可从 steps 组装。"""
        from app.core.writer.visuals.ir_builder import build_diagram_ir

        intent = {
            "visual_id": "vis_1",
            "diagram_type": "flowchart",
            "title": "施工管理组织流程图",
            "purpose": "展示组建流程",
            "measure_ids": ["measure_1"],
            "placement_hint": None,
        }
        ir = build_diagram_ir(_plan_payload(), intent)
        assert [node.label for node in ir.nodes] == [
            "确定项目经理",
            "配置专职安全员",
            "明确岗位职责",
        ]
        assert ir.edges[0].source == ir.nodes[0].node_id
        assert ir.edges[0].target == ir.nodes[1].node_id

    def test_flowchart_too_many_nodes_fails(self) -> None:
        from app.core.writer.visuals.ir_builder import (
            DiagramBuildError,
            build_diagram_ir,
        )

        payload = _plan_payload()
        payload["measures"] = [
            {
                "measure_id": f"measure_{i}",
                "action": f"措施{i}",
                "steps": [f"步骤{i}_{j}" for j in range(8)],
                "verification": "记录",
                "argument_ids": [],
                "project_fact_ids": [],
            }
            for i in range(1, 3)
        ]
        intent = {
            "visual_id": "vis_1",
            "diagram_type": "flowchart",
            "title": "流程图",
            "purpose": "展示流程",
            "measure_ids": ["measure_1", "measure_2"],
            "placement_hint": None,
        }
        with pytest.raises(DiagramBuildError) as exc_info:
            build_diagram_ir(payload, intent)
        assert exc_info.value.failure_code == "flowchart_too_many_nodes"

    def test_flowchart_too_few_nodes_fails(self) -> None:
        from app.core.writer.visuals.ir_builder import (
            DiagramBuildError,
            build_diagram_ir,
        )

        intent = {
            "visual_id": "vis_1",
            "diagram_type": "flowchart",
            "title": "流程图",
            "purpose": "展示流程",
            "measure_ids": ["measure_2"],
            "placement_hint": None,
        }
        with pytest.raises(DiagramBuildError) as exc_info:
            build_diagram_ir(_plan_payload(), intent)
        assert exc_info.value.failure_code == "flowchart_too_few_nodes"

    def test_unknown_measure_fails(self) -> None:
        from app.core.writer.visuals.ir_builder import (
            DiagramBuildError,
            build_diagram_ir,
        )

        intent = {
            "visual_id": "vis_1",
            "diagram_type": "flowchart",
            "title": "流程图",
            "purpose": "展示流程",
            "measure_ids": ["measure_missing"],
            "placement_hint": None,
        }
        with pytest.raises(DiagramBuildError) as exc_info:
            build_diagram_ir(_plan_payload(), intent)
        assert exc_info.value.failure_code == "unknown_measure_reference"


class TestGanttIR:
    def test_builds_sequential_bars_from_structured_phases(self) -> None:
        from app.core.writer.visuals.ir_builder import build_diagram_ir

        intent = {
            "visual_id": "vis_2",
            "diagram_type": "gantt",
            "title": "实施进度甘特图",
            "purpose": "展示阶段进度",
            "measure_ids": ["measure_2"],
            "placement_hint": None,
            "phases": [
                {"label": "基础施工", "duration_days": 30},
                {"label": "设备安装调试", "duration_days": 14},
            ],
        }
        ir = build_diagram_ir(_plan_payload(), intent)
        assert ir.diagram_type == "gantt"
        assert len(ir.bars) == 2
        assert ir.bars[0].label == "基础施工"
        assert ir.bars[0].start_day == 0
        assert ir.bars[0].duration_days == 30
        assert ir.bars[1].start_day == 30
        assert ir.bars[1].duration_days == 14

    def test_gantt_missing_phases_fails(self) -> None:
        """ 回归: 项目2232节点7352——无结构化 phases 的 gantt 意图。

 旧实现从措施自由文本正则挖掘工期, "每周召开进度例会"无工期即整图失败;
 新实现直接以稳定失败码拒绝, 不再猜测。
 """
        from app.core.writer.visuals.ir_builder import (
            DiagramBuildError,
            build_diagram_ir,
        )

        intent = {
            "visual_id": "vis_1",
            "diagram_type": "gantt",
            "title": "宁东油田防腐性能测试项目进度计划甘特图",
            "purpose": "直观展示90天工期内的任务分解",
            "measure_ids": ["measure_1"],
            "placement_hint": "measure_1",
        }
        with pytest.raises(DiagramBuildError) as exc_info:
            build_diagram_ir(_plan_payload(), intent)
        assert exc_info.value.failure_code == "gantt_missing_phases"

    def test_gantt_single_phase_fails(self) -> None:
        from app.core.writer.visuals.ir_builder import (
            DiagramBuildError,
            build_diagram_ir,
        )

        intent = {
            "visual_id": "vis_2",
            "diagram_type": "gantt",
            "title": "进度甘特图",
            "purpose": "展示进度",
            "measure_ids": ["measure_2"],
            "placement_hint": None,
            "phases": [{"label": "基础施工", "duration_days": 30}],
        }
        with pytest.raises(DiagramBuildError) as exc_info:
            build_diagram_ir(_plan_payload(), intent)
        assert exc_info.value.failure_code == "gantt_phase_count_out_of_range"

    def test_gantt_invalid_phase_duration_fails(self) -> None:
        from app.core.writer.visuals.ir_builder import (
            DiagramBuildError,
            build_diagram_ir,
        )

        intent = {
            "visual_id": "vis_2",
            "diagram_type": "gantt",
            "title": "进度甘特图",
            "purpose": "展示进度",
            "measure_ids": ["measure_2"],
            "placement_hint": None,
            "phases": [
                {"label": "基础施工", "duration_days": 30},
                {"label": "调试", "duration_days": 0},
            ],
        }
        with pytest.raises(DiagramBuildError) as exc_info:
            build_diagram_ir(_plan_payload(), intent)
        assert exc_info.value.failure_code == "gantt_invalid_phase"


class TestIRFingerprint:
    def test_content_sha256_is_stable(self) -> None:
        from app.core.writer.visuals.ir_builder import build_diagram_ir

        intent = {
            "visual_id": "vis_1",
            "diagram_type": "flowchart",
            "title": "施工管理组织流程图",
            "purpose": "展示组建流程",
            "measure_ids": ["measure_1"],
            "placement_hint": None,
        }
        first = build_diagram_ir(_plan_payload(), intent)
        second = build_diagram_ir(_plan_payload(), intent)
        assert first.content_sha256 == second.content_sha256
        assert len(first.content_sha256) == 64
