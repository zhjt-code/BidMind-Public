"""Diagram IR 数据契约。

职责: 定义 9 个图元中间表示的严格结构与内容指纹。
不做: 不解析 SectionPlan、不渲染图片。
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# 图结构限值（单一事实源）: SectionPlan 闸门与 IR 编译器共享，
# 保证"闸门放行的意图, 编译器必然可消费"。
MIN_FLOWCHART_NODES = 3
MAX_FLOWCHART_NODES = 12
MIN_GANTT_PHASES = 2
MAX_GANTT_PHASES = 15
MAX_GANTT_PHASE_DURATION_DAYS = 3660
MIN_ORG_UNITS = 2
MAX_ORG_UNITS = 8
MIN_SWIM_LANES = 2
MAX_SWIM_LANES = 4
MIN_SWIM_NODES = 3
MAX_SWIM_NODES = 12
MIN_ROADMAP_PHASES = 2
MAX_ROADMAP_PHASES = 8
MIN_LAYERS = 2
MAX_LAYERS = 6
MAX_LAYER_ITEMS = 6
MIN_NETWORK_NODES = 3
MAX_NETWORK_NODES = 12
MAX_NETWORK_EDGES = 16
MIN_WBS_PACKAGES = 2
MAX_WBS_PACKAGES = 6
MAX_WBS_ITEMS = 4
MIN_ZONES = 3
MAX_ZONES = 8
DIAGRAM_TYPES = (
    "flowchart",
    "gantt",
    "swimlane",
    "roadmap",
    "org_chart",
    "layered",
    "network",
    "wbs",
    "zone_layout",
)


class StrictIRModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FlowchartNode(StrictIRModel):
    node_id: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=120)


class FlowchartEdge(StrictIRModel):
    source: str = Field(min_length=1, max_length=32)
    target: str = Field(min_length=1, max_length=32)


class DiagramStyle(StrictIRModel):
    theme: Literal["classic_blue", "business_gray", "tech_teal"] = "classic_blue"
    primary_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    text_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    font_scale: Literal["sm", "md", "lg"] = "md"


class FlowchartIR(StrictIRModel):
    diagram_type: Literal["flowchart"]
    title: str = Field(min_length=1, max_length=200)
    nodes: list[FlowchartNode] = Field(min_length=2, max_length=12)
    edges: list[FlowchartEdge] = Field(min_length=1, max_length=16)
    style: DiagramStyle | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GanttBar(StrictIRModel):
    label: str = Field(min_length=1, max_length=120)
    start_day: int = Field(ge=0)
    duration_days: int = Field(ge=1, le=3660)


class GanttIR(StrictIRModel):
    diagram_type: Literal["gantt"]
    title: str = Field(min_length=1, max_length=200)
    bars: list[GanttBar] = Field(min_length=2, max_length=15)
    style: DiagramStyle | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OrgChartIR(StrictIRModel):
    diagram_type: Literal["org_chart"]
    title: str = Field(min_length=1, max_length=200)
    lead: str = Field(min_length=1, max_length=120)
    units: list[str] = Field(min_length=MIN_ORG_UNITS, max_length=MAX_ORG_UNITS)
    style: DiagramStyle | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SwimlaneNode(StrictIRModel):
    node_id: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=120)
    lane: str = Field(min_length=1, max_length=60)


class SwimlaneIR(StrictIRModel):
    diagram_type: Literal["swimlane"]
    title: str = Field(min_length=1, max_length=200)
    lanes: list[str] = Field(min_length=MIN_SWIM_LANES, max_length=MAX_SWIM_LANES)
    nodes: list[SwimlaneNode] = Field(min_length=MIN_SWIM_NODES, max_length=MAX_SWIM_NODES)
    edges: list[FlowchartEdge] = Field(min_length=1, max_length=16)
    style: DiagramStyle | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RoadmapPhase(StrictIRModel):
    label: str = Field(min_length=1, max_length=120)
    duration_days: int = Field(ge=1, le=3660)
    milestone: bool = False


class RoadmapIR(StrictIRModel):
    diagram_type: Literal["roadmap"]
    title: str = Field(min_length=1, max_length=200)
    phases: list[RoadmapPhase] = Field(
        min_length=MIN_ROADMAP_PHASES,
        max_length=MAX_ROADMAP_PHASES,
    )
    style: DiagramStyle | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LayerBand(StrictIRModel):
    label: str = Field(min_length=1, max_length=120)
    items: list[str] = Field(default_factory=list, max_length=MAX_LAYER_ITEMS)


class LayeredIR(StrictIRModel):
    diagram_type: Literal["layered"]
    title: str = Field(min_length=1, max_length=200)
    layers: list[LayerBand] = Field(min_length=MIN_LAYERS, max_length=MAX_LAYERS)
    style: DiagramStyle | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NetworkActivity(StrictIRModel):
    node_id: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=120)
    duration_days: int = Field(ge=1, le=3660)


class NetworkEdge(StrictIRModel):
    source: str = Field(min_length=1, max_length=32)
    target: str = Field(min_length=1, max_length=32)


class NetworkIR(StrictIRModel):
    diagram_type: Literal["network"]
    title: str = Field(min_length=1, max_length=200)
    nodes: list[NetworkActivity] = Field(
        min_length=MIN_NETWORK_NODES, max_length=MAX_NETWORK_NODES,
    )
    edges: list[NetworkEdge] = Field(min_length=1, max_length=MAX_NETWORK_EDGES)
    style: DiagramStyle | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WbsPackage(StrictIRModel):
    label: str = Field(min_length=1, max_length=120)
    items: list[str] = Field(default_factory=list, max_length=MAX_WBS_ITEMS)


class WbsIR(StrictIRModel):
    diagram_type: Literal["wbs"]
    title: str = Field(min_length=1, max_length=200)
    root: str = Field(min_length=1, max_length=120)
    packages: list[WbsPackage] = Field(
        min_length=MIN_WBS_PACKAGES, max_length=MAX_WBS_PACKAGES,
    )
    style: DiagramStyle | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ZoneLayoutIR(StrictIRModel):
    diagram_type: Literal["zone_layout"]
    title: str = Field(min_length=1, max_length=200)
    zones: list[str] = Field(min_length=MIN_ZONES, max_length=MAX_ZONES)
    style: DiagramStyle | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


DiagramIR = (
    FlowchartIR | GanttIR | OrgChartIR | SwimlaneIR | RoadmapIR | LayeredIR
    | NetworkIR | WbsIR | ZoneLayoutIR
)


def compute_ir_sha256(payload: dict) -> str:
    """对不含指纹字段的 IR 内容计算稳定哈希。"""

    material = {
        key: value
        for key, value in payload.items()
        if key != "content_sha256"
    }
    canonical = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "DIAGRAM_TYPES",
    "DiagramIR",
    "DiagramStyle",
    "FlowchartEdge",
    "FlowchartIR",
    "FlowchartNode",
    "GanttBar",
    "GanttIR",
    "LayerBand",
    "LayeredIR",
    "MAX_FLOWCHART_NODES",
    "MAX_GANTT_PHASES",
    "MAX_GANTT_PHASE_DURATION_DAYS",
    "MAX_LAYERS",
    "MAX_LAYER_ITEMS",
    "MAX_NETWORK_EDGES",
    "MAX_NETWORK_NODES",
    "MAX_ORG_UNITS",
    "MAX_ROADMAP_PHASES",
    "MAX_SWIM_LANES",
    "MAX_SWIM_NODES",
    "MAX_WBS_ITEMS",
    "MAX_WBS_PACKAGES",
    "MAX_ZONES",
    "MIN_FLOWCHART_NODES",
    "MIN_GANTT_PHASES",
    "MIN_LAYERS",
    "MIN_NETWORK_NODES",
    "MIN_ORG_UNITS",
    "MIN_ROADMAP_PHASES",
    "MIN_SWIM_LANES",
    "MIN_SWIM_NODES",
    "MIN_WBS_PACKAGES",
    "MIN_ZONES",
    "NetworkActivity",
    "NetworkEdge",
    "NetworkIR",
    "OrgChartIR",
    "RoadmapIR",
    "RoadmapPhase",
    "SwimlaneIR",
    "SwimlaneNode",
    "WbsIR",
    "WbsPackage",
    "ZoneLayoutIR",
    "compute_ir_sha256",
]
