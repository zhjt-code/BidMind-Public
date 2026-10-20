"""VisualIntent → Diagram IR 确定性编译器。

职责: 将闸门归一后的结构化图参数编译为 Diagram IR。
不做: 不调用 LLM、不渲染 PNG、不访问数据库、不从自由文本正则挖掘参数。
"""

from __future__ import annotations

from typing import Any

from app.core.writer.visuals.ir_contracts import (
    MAX_FLOWCHART_NODES,
    MAX_GANTT_PHASES,
    MIN_FLOWCHART_NODES,
    MIN_GANTT_PHASES,
    DiagramIR,
    FlowchartEdge,
    FlowchartIR,
    FlowchartNode,
    GanttBar,
    GanttIR,
    compute_ir_sha256,
)


class DiagramBuildError(Exception):
    """IR 编译失败(带稳定失败码, 供 DB failure_code 落库)。"""

    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


def _referenced_measures(
    plan_payload: dict[str, Any],
    intent: dict[str, Any],
) -> list[dict[str, Any]]:
    measures_by_id = {
        measure["measure_id"]: measure
        for measure in plan_payload.get("measures", [])
    }
    referenced: list[dict[str, Any]] = []
    for measure_id in intent.get("measure_ids", []):
        measure = measures_by_id.get(measure_id)
        if measure is None:
            raise DiagramBuildError(
                "unknown_measure_reference",
                f"measure not found in plan: {measure_id}",
            )
        referenced.append(measure)
    if not referenced:
        raise DiagramBuildError(
            "empty_measure_reference",
            "visual intent references no measures",
        )
    return referenced


def _flowchart_labels(
    plan_payload: dict[str, Any],
    intent: dict[str, Any],
) -> list[str]:
    """节点标签优先消费闸门归一的 node_labels; 兼容存量计划回退 steps。"""

    labels = [
        str(label).strip()[:120]
        for label in intent.get("node_labels", [])
        if str(label).strip()
    ]
    if labels:
        return labels
    referenced = _referenced_measures(plan_payload, intent)
    return [
        str(step).strip()[:120]
        for measure in referenced
        for step in measure.get("steps", [])
        if str(step).strip()
    ]


def _build_flowchart_ir(
    plan_payload: dict[str, Any],
    intent: dict[str, Any],
) -> FlowchartIR:
    labels = _flowchart_labels(plan_payload, intent)
    if len(labels) < MIN_FLOWCHART_NODES:
        raise DiagramBuildError(
            "flowchart_too_few_nodes",
            f"flowchart needs >={MIN_FLOWCHART_NODES} nodes, got {len(labels)}",
        )
    if len(labels) > MAX_FLOWCHART_NODES:
        raise DiagramBuildError(
            "flowchart_too_many_nodes",
            f"flowchart supports <= {MAX_FLOWCHART_NODES} nodes, got {len(labels)}",
        )
    nodes = [
        FlowchartNode(node_id=f"n{index + 1}", label=label)
        for index, label in enumerate(labels)
    ]
    edges = [
        FlowchartEdge(source=nodes[index].node_id, target=nodes[index + 1].node_id)
        for index in range(len(nodes) - 1)
    ]
    payload = {
        "diagram_type": "flowchart",
        "title": intent["title"],
        "nodes": [node.model_dump() for node in nodes],
        "edges": [edge.model_dump() for edge in edges],
    }
    if intent.get("style"):
        payload["style"] = intent["style"]
    return FlowchartIR(
        **payload,
        content_sha256=compute_ir_sha256(payload),
    )


def _build_gantt_ir(
    plan_payload: dict[str, Any],
    intent: dict[str, Any],
) -> GanttIR:
    phases = intent.get("phases", [])
    if not phases:
        raise DiagramBuildError(
            "gantt_missing_phases",
            "gantt intent carries no structured phases",
        )
    if not (MIN_GANTT_PHASES <= len(phases) <= MAX_GANTT_PHASES):
        raise DiagramBuildError(
            "gantt_phase_count_out_of_range",
            f"gantt needs {MIN_GANTT_PHASES}-{MAX_GANTT_PHASES} phases, got {len(phases)}",
        )
    bars: list[GanttBar] = []
    cursor = 0
    for phase in phases:
        label = str(phase.get("label", "")).strip()[:120]
        duration_raw = phase.get("duration_days")
        if not label or not isinstance(duration_raw, int) or duration_raw < 1:
            raise DiagramBuildError(
                "gantt_invalid_phase",
                f"phase must carry label and positive duration_days: {phase}",
            )
        bars.append(GanttBar(
            label=label,
            start_day=cursor,
            duration_days=duration_raw,
        ))
        cursor += duration_raw
    payload = {
        "diagram_type": "gantt",
        "title": intent["title"],
        "bars": [bar.model_dump() for bar in bars],
    }
    if intent.get("style"):
        payload["style"] = intent["style"]
    return GanttIR(
        **payload,
        content_sha256=compute_ir_sha256(payload),
    )


def build_diagram_ir(
    plan_payload: dict[str, Any],
    intent: dict[str, Any],
) -> DiagramIR:
    """按 intent 类型编译 Diagram IR; 结构不足抛 DiagramBuildError。"""

    diagram_type = intent.get("diagram_type")
    if diagram_type == "flowchart":
        return _build_flowchart_ir(plan_payload, intent)
    if diagram_type == "gantt":
        return _build_gantt_ir(plan_payload, intent)
    from app.core.writer.visuals.ir_builder_extra import (
        build_layered_ir,
        build_org_chart_ir,
        build_roadmap_ir,
        build_swimlane_ir,
    )

    if diagram_type == "org_chart":
        return build_org_chart_ir(intent)
    if diagram_type == "swimlane":
        return build_swimlane_ir(intent)
    if diagram_type == "roadmap":
        return build_roadmap_ir(intent)
    if diagram_type == "layered":
        return build_layered_ir(intent)
    from app.core.writer.visuals.ir_builder_construction import (
        build_network_ir,
        build_wbs_ir,
        build_zone_layout_ir,
    )

    if diagram_type == "network":
        return build_network_ir(intent)
    if diagram_type == "wbs":
        return build_wbs_ir(intent)
    if diagram_type == "zone_layout":
        return build_zone_layout_ir(intent)
    raise DiagramBuildError(
        "unsupported_diagram_type",
        f"unsupported diagram_type: {diagram_type}",
    )


__all__ = ["DiagramBuildError", "build_diagram_ir"]
