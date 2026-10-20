"""建筑向图元 IR 组装。

职责: 将闸门归一后的 network/wbs/zone_layout 参数编译为 IR。
不做: 不调用 LLM、不渲染 PNG、不访问数据库。
"""

from __future__ import annotations

from typing import Any

from app.core.writer.visuals.construction_pack import (
    ensure_zone_layout_caption,
    normalize_zone_names,
)
from app.core.writer.visuals.ir_builder import DiagramBuildError
from app.core.writer.visuals.ir_contracts import (
    MAX_NETWORK_EDGES,
    MAX_NETWORK_NODES,
    MAX_WBS_ITEMS,
    MAX_WBS_PACKAGES,
    MAX_ZONES,
    MIN_NETWORK_NODES,
    MIN_WBS_PACKAGES,
    MIN_ZONES,
    NetworkActivity,
    NetworkEdge,
    NetworkIR,
    WbsIR,
    WbsPackage,
    ZoneLayoutIR,
    compute_ir_sha256,
)


def _optional_style(intent: dict[str, Any], payload: dict[str, Any]) -> None:
    if intent.get("style"):
        payload["style"] = intent["style"]


def _network_has_cycle(node_ids: set[str], edges: list[NetworkEdge]) -> bool:
    graph: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.source in graph:
            graph[edge.source].append(edge.target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node_id: str) -> bool:
        visiting.add(node_id)
        for nxt in graph.get(node_id, []):
            if nxt in visiting:
                return True
            if nxt not in visited and nxt in graph and dfs(nxt):
                return True
        visiting.discard(node_id)
        visited.add(node_id)
        return False

    return any(dfs(node_id) for node_id in node_ids if node_id not in visited)


def build_network_ir(intent: dict[str, Any]) -> NetworkIR:
    nodes: list[NetworkActivity] = []
    for index, item in enumerate(intent.get("network_nodes") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:120]
        duration = item.get("duration_days")
        node_id = str(item.get("node_id") or "").strip() or f"n{index + 1}"
        if not label or not isinstance(duration, int) or duration < 1:
            raise DiagramBuildError(
                "network_invalid_node",
                f"network node needs label and duration_days: {item}",
            )
        nodes.append(NetworkActivity(
            node_id=node_id[:32],
            label=label,
            duration_days=duration,
        ))
    if not (MIN_NETWORK_NODES <= len(nodes) <= MAX_NETWORK_NODES):
        raise DiagramBuildError(
            "network_node_count_out_of_range",
            f"network needs {MIN_NETWORK_NODES}-{MAX_NETWORK_NODES} nodes",
        )
    known = {node.node_id for node in nodes}
    edges: list[NetworkEdge] = []
    for item in intent.get("network_edges") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        if source in known and target in known and source != target:
            edges.append(NetworkEdge(source=source, target=target))
    if not edges:
        edges = [
            NetworkEdge(source=nodes[index].node_id, target=nodes[index + 1].node_id)
            for index in range(len(nodes) - 1)
        ]
    if len(edges) > MAX_NETWORK_EDGES:
        raise DiagramBuildError(
            "network_too_many_edges",
            f"network supports <= {MAX_NETWORK_EDGES} edges",
        )
    if _network_has_cycle(known, edges):
        raise DiagramBuildError("network_cycle", "network edges form a cycle")
    payload = {
        "diagram_type": "network",
        "title": intent["title"],
        "nodes": [node.model_dump() for node in nodes],
        "edges": [edge.model_dump() for edge in edges],
    }
    _optional_style(intent, payload)
    return NetworkIR(**payload, content_sha256=compute_ir_sha256(payload))


def build_wbs_ir(intent: dict[str, Any]) -> WbsIR:
    root = str(intent.get("wbs_root") or "").strip()[:120]
    packages: list[WbsPackage] = []
    for item in intent.get("wbs_packages") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:120]
        if not label:
            continue
        entries = [
            str(entry).strip()[:60]
            for entry in item.get("items") or []
            if str(entry).strip()
        ][:MAX_WBS_ITEMS]
        packages.append(WbsPackage(label=label, items=entries))
    if not root or not (MIN_WBS_PACKAGES <= len(packages) <= MAX_WBS_PACKAGES):
        raise DiagramBuildError(
            "wbs_invalid",
            f"wbs needs root and {MIN_WBS_PACKAGES}-{MAX_WBS_PACKAGES} packages",
        )
    payload = {
        "diagram_type": "wbs",
        "title": intent["title"],
        "root": root,
        "packages": [package.model_dump() for package in packages],
    }
    _optional_style(intent, payload)
    return WbsIR(**payload, content_sha256=compute_ir_sha256(payload))


def build_zone_layout_ir(intent: dict[str, Any]) -> ZoneLayoutIR:
    zones = normalize_zone_names([
        str(item) for item in (intent.get("zones") or [])
    ])
    if not (MIN_ZONES <= len(zones) <= MAX_ZONES):
        raise DiagramBuildError(
            "zone_layout_invalid",
            f"zone_layout needs {MIN_ZONES}-{MAX_ZONES} known zones",
        )
    payload = {
        "diagram_type": "zone_layout",
        "title": ensure_zone_layout_caption(str(intent.get("title") or "")),
        "zones": zones,
    }
    _optional_style(intent, payload)
    return ZoneLayoutIR(**payload, content_sha256=compute_ir_sha256(payload))


__all__ = [
    "build_network_ir",
    "build_wbs_ir",
    "build_zone_layout_ir",
]
