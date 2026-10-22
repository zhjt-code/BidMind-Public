"""扩展图元闸门。

职责: 校验具名图元结构化参数并写回 payload。
不做: 不编译 IR、不渲染 PNG、不访问数据库。
"""

from __future__ import annotations

from typing import Any

from app.core.writer.visuals.construction_pack import normalize_zone_names
from app.core.writer.visuals.intent_templates import layered_template_layers
from app.core.writer.visuals.ir_contracts import (
    MAX_LAYER_ITEMS,
    MAX_LAYERS,
    MAX_NETWORK_EDGES,
    MAX_NETWORK_NODES,
    MAX_ORG_UNITS,
    MAX_ROADMAP_PHASES,
    MAX_SWIM_LANES,
    MAX_SWIM_NODES,
    MAX_WBS_ITEMS,
    MAX_WBS_PACKAGES,
    MAX_ZONES,
    MIN_LAYERS,
    MIN_NETWORK_NODES,
    MIN_ORG_UNITS,
    MIN_ROADMAP_PHASES,
    MIN_SWIM_LANES,
    MIN_SWIM_NODES,
    MIN_WBS_PACKAGES,
    MIN_ZONES,
)


def _clean_text(value: object, limit: int = 60) -> str:
    return str(value or "").strip()[:limit]


def _gate_org_chart(payload: dict[str, Any]) -> bool:
    lead = _clean_text(payload.get("org_lead"))
    units = [
        _clean_text(item)
        for item in payload.get("org_units") or []
        if _clean_text(item)
    ]
    if not lead or not (MIN_ORG_UNITS <= len(units) <= MAX_ORG_UNITS):
        return False
    payload["org_lead"] = lead
    payload["org_units"] = units
    return True


def _gate_swimlane(payload: dict[str, Any]) -> bool:
    lanes = [
        _clean_text(item)
        for item in payload.get("lanes") or []
        if _clean_text(item)
    ]
    nodes: list[dict[str, str]] = []
    for item in payload.get("swim_nodes") or []:
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"))
        lane = _clean_text(item.get("lane"))
        if label and lane:
            nodes.append({"label": label, "lane": lane})
    if not (MIN_SWIM_LANES <= len(lanes) <= MAX_SWIM_LANES):
        return False
    if not (MIN_SWIM_NODES <= len(nodes) <= MAX_SWIM_NODES):
        return False
    if any(node["lane"] not in lanes for node in nodes):
        return False
    payload["lanes"] = lanes
    payload["swim_nodes"] = nodes
    return True


def _gate_roadmap(payload: dict[str, Any]) -> bool:
    phases: list[dict[str, Any]] = []
    for item in payload.get("phases") or []:
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"))
        duration = item.get("duration_days")
        if not label or not isinstance(duration, int) or duration < 1:
            return False
        phases.append({
            "label": label,
            "duration_days": duration,
            "milestone": bool(item.get("milestone")),
        })
    if not (MIN_ROADMAP_PHASES <= len(phases) <= MAX_ROADMAP_PHASES):
        return False
    payload["phases"] = phases
    return True


def _gate_layered(payload: dict[str, Any]) -> bool:
    layers: list[dict[str, Any]] = []
    for item in payload.get("layers") or []:
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"))
        if not label:
            continue
        items = [
            _clean_text(entry)
            for entry in item.get("items") or []
            if _clean_text(entry)
        ][:MAX_LAYER_ITEMS]
        layers.append({"label": label, "items": items})
    if not layers:
        layers = layered_template_layers(str(payload.get("template_id") or ""))
    if not (MIN_LAYERS <= len(layers) <= MAX_LAYERS):
        return False
    payload["layers"] = layers
    return True


def _gate_network(payload: dict[str, Any]) -> bool:
    nodes: list[dict[str, Any]] = []
    for index, item in enumerate(payload.get("network_nodes") or []):
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"), 120)
        duration = item.get("duration_days")
        if not label or not isinstance(duration, int) or duration < 1:
            return False
        node_id = _clean_text(item.get("node_id"), 32) or f"n{index + 1}"
        nodes.append({
            "node_id": node_id,
            "label": label,
            "duration_days": duration,
        })
    if not (MIN_NETWORK_NODES <= len(nodes) <= MAX_NETWORK_NODES):
        return False
    known = {node["node_id"] for node in nodes}
    edges: list[dict[str, str]] = []
    for item in payload.get("network_edges") or []:
        if not isinstance(item, dict):
            continue
        source = _clean_text(item.get("source"), 32)
        target = _clean_text(item.get("target"), 32)
        if source in known and target in known and source != target:
            edges.append({"source": source, "target": target})
    if not edges:
        edges = [
            {"source": nodes[index]["node_id"], "target": nodes[index + 1]["node_id"]}
            for index in range(len(nodes) - 1)
        ]
    if not (1 <= len(edges) <= MAX_NETWORK_EDGES):
        return False
    payload["network_nodes"] = nodes
    payload["network_edges"] = edges
    return True


def _gate_wbs(payload: dict[str, Any]) -> bool:
    root = _clean_text(payload.get("wbs_root"), 120)
    packages: list[dict[str, Any]] = []
    for item in payload.get("wbs_packages") or []:
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"), 120)
        if not label:
            continue
        entries = [
            _clean_text(entry, 60)
            for entry in item.get("items") or []
            if _clean_text(entry, 60)
        ][:MAX_WBS_ITEMS]
        packages.append({"label": label, "items": entries})
    if not root or not (MIN_WBS_PACKAGES <= len(packages) <= MAX_WBS_PACKAGES):
        return False
    payload["wbs_root"] = root
    payload["wbs_packages"] = packages
    return True


def _gate_zone_layout(payload: dict[str, Any]) -> bool:
    zones = normalize_zone_names([
        str(item) for item in (payload.get("zones") or [])
    ])
    if not (MIN_ZONES <= len(zones) <= MAX_ZONES):
        return False
    payload["zones"] = zones
    return True


def gate_named_diagram_intent(diagram_type: str, intent_payload: dict[str, Any]) -> bool:
    """按图元校验并归一结构化字段; 失败则丢弃该 intent。"""

    if diagram_type == "org_chart":
        return _gate_org_chart(intent_payload)
    if diagram_type == "swimlane":
        return _gate_swimlane(intent_payload)
    if diagram_type == "roadmap":
        return _gate_roadmap(intent_payload)
    if diagram_type == "layered":
        return _gate_layered(intent_payload)
    if diagram_type == "network":
        return _gate_network(intent_payload)
    if diagram_type == "wbs":
        return _gate_wbs(intent_payload)
    if diagram_type == "zone_layout":
        return _gate_zone_layout(intent_payload)
    return False


__all__ = ["gate_named_diagram_intent"]
