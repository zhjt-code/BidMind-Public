"""章节图示编辑 API Schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.writer.visuals.ir_contracts import (
    DIAGRAM_TYPES,
    DiagramStyle,
    MAX_FLOWCHART_NODES,
    MAX_GANTT_PHASES,
    MAX_GANTT_PHASE_DURATION_DAYS,
    MAX_LAYERS,
    MAX_LAYER_ITEMS,
    MAX_NETWORK_EDGES,
    MAX_NETWORK_NODES,
    MAX_ORG_UNITS,
    MAX_SWIM_LANES,
    MAX_SWIM_NODES,
    MAX_WBS_ITEMS,
    MAX_WBS_PACKAGES,
    MAX_ZONES,
    MIN_FLOWCHART_NODES,
    MIN_GANTT_PHASES,
)

DiagramType = Literal[
    "flowchart", "gantt", "swimlane", "roadmap", "org_chart", "layered",
    "network", "wbs", "zone_layout",
]


class GanttPhaseUpdate(BaseModel):
    """甘特/路线图阶段编辑项。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=120)
    duration_days: int = Field(ge=1, le=MAX_GANTT_PHASE_DURATION_DAYS)
    milestone: bool = False


class SwimNodeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=120)
    lane: str = Field(min_length=1, max_length=60)


class LayerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=120)
    items: list[str] = Field(default_factory=list, max_length=MAX_LAYER_ITEMS)


class NetworkNodeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    node_id: str = Field(default="", max_length=32)
    label: str = Field(min_length=1, max_length=120)
    duration_days: int = Field(ge=1, le=MAX_GANTT_PHASE_DURATION_DAYS)


class NetworkEdgeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=32)
    target: str = Field(min_length=1, max_length=32)


class WbsPackageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=120)
    items: list[str] = Field(default_factory=list, max_length=MAX_WBS_ITEMS)


class WritingDiagramUpdateRequest(BaseModel):
    """章节图示人工编辑请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    phases: list[GanttPhaseUpdate] | None = None
    node_labels: list[str] | None = None
    org_lead: str | None = None
    org_units: list[str] | None = None
    lanes: list[str] | None = None
    swim_nodes: list[SwimNodeUpdate] | None = None
    layers: list[LayerUpdate] | None = None
    network_nodes: list[NetworkNodeUpdate] | None = Field(
        default=None, max_length=MAX_NETWORK_NODES,
    )
    network_edges: list[NetworkEdgeUpdate] | None = Field(
        default=None, max_length=MAX_NETWORK_EDGES,
    )
    wbs_root: str | None = None
    wbs_packages: list[WbsPackageUpdate] | None = Field(
        default=None, max_length=MAX_WBS_PACKAGES,
    )
    zones: list[str] | None = Field(default=None, max_length=MAX_ZONES)
    style: DiagramStyle | None = None

    @field_validator("node_labels", "org_units", "lanes", "zones")
    @classmethod
    def normalize_label_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [item.strip() for item in value if item and item.strip()]

    @model_validator(mode="after")
    def ensure_definition_present(self) -> "WritingDiagramUpdateRequest":
        """必须提供一种图示定义，避免空编辑请求进入服务层。"""

        if not any([
            self.phases is not None,
            self.node_labels is not None,
            self.org_units is not None,
            self.swim_nodes is not None,
            self.layers is not None,
            self.network_nodes is not None,
            self.wbs_packages is not None,
            self.zones is not None,
        ]):
            raise ValueError("必须提供图示定义")
        return self


class WritingDiagramResponse(BaseModel):
    """章节图示编辑态响应。"""

    id: int
    diagram_type: DiagramType
    title: str
    status: str
    definition: dict
    content_sha256: str = ""


__all__ = [
    "DIAGRAM_TYPES",
    "DiagramStyle",
    "DiagramType",
    "GanttPhaseUpdate",
    "LayerUpdate",
    "MAX_FLOWCHART_NODES",
    "MAX_GANTT_PHASES",
    "MAX_LAYERS",
    "MAX_NETWORK_NODES",
    "MAX_ORG_UNITS",
    "MAX_SWIM_LANES",
    "MAX_SWIM_NODES",
    "MAX_WBS_PACKAGES",
    "MAX_ZONES",
    "MIN_FLOWCHART_NODES",
    "MIN_GANTT_PHASES",
    "NetworkEdgeUpdate",
    "NetworkNodeUpdate",
    "SwimNodeUpdate",
    "WbsPackageUpdate",
    "WritingDiagramResponse",
    "WritingDiagramUpdateRequest",
]
