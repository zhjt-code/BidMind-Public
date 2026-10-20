"""扩展图元 IR 组装。

职责: 将闸门归一后的 org_chart/swimlane/roadmap/layered 参数编译为 IR。
不做: 不调用 LLM、不渲染 PNG、不访问数据库。
"""

from __future__ import annotations

from typing import Any

from app.core.writer.visuals.ir_builder import DiagramBuildError
from app.core.writer.visuals.ir_contracts import (
    MAX_LAYERS,
    MAX_ORG_UNITS,
    MAX_ROADMAP_PHASES,
    MAX_SWIM_LANES,
    MAX_SWIM_NODES,
    MIN_LAYERS,
    MIN_ORG_UNITS,
    MIN_ROADMAP_PHASES,
    MIN_SWIM_LANES,
    MIN_SWIM_NODES,
    FlowchartEdge,
    LayerBand,
    LayeredIR,
    OrgChartIR,
    RoadmapIR,
    RoadmapPhase,
    SwimlaneIR,
    SwimlaneNode,
    compute_ir_sha256,
)


def _optional_style(intent: dict[str, Any], payload: dict[str, Any]) -> None:
    if intent.get("style"):
        payload["style"] = intent["style"]


def build_org_chart_ir(intent: dict[str, Any]) -> OrgChartIR:
    lead = str(intent.get("org_lead") or "").strip()[:120]
    units = [
        str(item).strip()[:120]
        for item in intent.get("org_units") or []
        if str(item).strip()
    ]
    if not lead or not (MIN_ORG_UNITS <= len(units) <= MAX_ORG_UNITS):
        raise DiagramBuildError(
            "org_chart_invalid",
            "org_chart needs lead and 2-8 units",
        )
    payload = {
        "diagram_type": "org_chart",
        "title": intent["title"],
        "lead": lead,
        "units": units,
    }
    _optional_style(intent, payload)
    return OrgChartIR(**payload, content_sha256=compute_ir_sha256(payload))


def build_swimlane_ir(intent: dict[str, Any]) -> SwimlaneIR:
    lanes = [
        str(item).strip()[:60]
        for item in intent.get("lanes") or []
        if str(item).strip()
    ]
    raw_nodes = intent.get("swim_nodes") or []
    nodes: list[SwimlaneNode] = []
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:120]
        lane = str(item.get("lane") or "").strip()[:60]
        if not label or not lane:
            continue
        nodes.append(SwimlaneNode(node_id=f"n{index + 1}", label=label, lane=lane))
    if not (MIN_SWIM_LANES <= len(lanes) <= MAX_SWIM_LANES):
        raise DiagramBuildError("swimlane_invalid_lanes", "swimlane needs 2-4 lanes")
    if not (MIN_SWIM_NODES <= len(nodes) <= MAX_SWIM_NODES):
        raise DiagramBuildError("swimlane_invalid_nodes", "swimlane needs 3-12 nodes")
    if any(node.lane not in lanes for node in nodes):
        raise DiagramBuildError("swimlane_unknown_lane", "node lane is not in lanes")
    edges = [
        FlowchartEdge(source=nodes[index].node_id, target=nodes[index + 1].node_id)
        for index in range(len(nodes) - 1)
    ]
    payload = {
        "diagram_type": "swimlane",
        "title": intent["title"],
        "lanes": lanes,
        "nodes": [node.model_dump() for node in nodes],
        "edges": [edge.model_dump() for edge in edges],
    }
    _optional_style(intent, payload)
    return SwimlaneIR(**payload, content_sha256=compute_ir_sha256(payload))


def build_roadmap_ir(intent: dict[str, Any]) -> RoadmapIR:
    phases: list[RoadmapPhase] = []
    for item in intent.get("phases") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:120]
        duration = item.get("duration_days")
        if not label or not isinstance(duration, int) or duration < 1:
            raise DiagramBuildError("roadmap_invalid_phase", f"invalid phase: {item}")
        phases.append(RoadmapPhase(
            label=label,
            duration_days=duration,
            milestone=bool(item.get("milestone")),
        ))
    if not (MIN_ROADMAP_PHASES <= len(phases) <= MAX_ROADMAP_PHASES):
        raise DiagramBuildError(
            "roadmap_phase_count_out_of_range",
            f"roadmap needs {MIN_ROADMAP_PHASES}-{MAX_ROADMAP_PHASES} phases",
        )
    payload = {
        "diagram_type": "roadmap",
        "title": intent["title"],
        "phases": [phase.model_dump() for phase in phases],
    }
    _optional_style(intent, payload)
    return RoadmapIR(**payload, content_sha256=compute_ir_sha256(payload))


def build_layered_ir(intent: dict[str, Any]) -> LayeredIR:
    layers: list[LayerBand] = []
    for item in intent.get("layers") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:120]
        if not label:
            continue
        entries = [
            str(entry).strip()[:60]
            for entry in item.get("items") or []
            if str(entry).strip()
        ][:6]
        layers.append(LayerBand(label=label, items=entries))
    if not (MIN_LAYERS <= len(layers) <= MAX_LAYERS):
        raise DiagramBuildError(
            "layered_invalid",
            f"layered needs {MIN_LAYERS}-{MAX_LAYERS} layers",
        )
    payload = {
        "diagram_type": "layered",
        "title": intent["title"],
        "layers": [layer.model_dump() for layer in layers],
    }
    _optional_style(intent, payload)
    return LayeredIR(**payload, content_sha256=compute_ir_sha256(payload))


__all__ = [
    "build_layered_ir",
    "build_org_chart_ir",
    "build_roadmap_ir",
    "build_swimlane_ir",
]
