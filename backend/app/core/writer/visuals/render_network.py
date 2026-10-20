"""进度网络图渲染。

职责: 将 NetworkIR 画成带紧前关系与关键线路加粗的 PNG。
不做: 不编译 IR、不访问数据库、不把关键线路写回计划。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from app.core.writer.visuals.ir_contracts import NetworkIR
from app.core.writer.visuals.render_common import DPI, font_kwargs, wrap_label
from app.core.writer.visuals.render_connectors import TEXT_Z, add_node_shape, add_orthogonal_arrow
from app.core.writer.visuals.render_geometry import Box, route_between_boxes
from app.core.writer.visuals.styles import resolve_diagram_style


def _topo_layers(ir: NetworkIR) -> list[list[str]]:
    incoming = {node.node_id: 0 for node in ir.nodes}
    graph: dict[str, list[str]] = {node.node_id: [] for node in ir.nodes}
    for edge in ir.edges:
        if edge.source in graph and edge.target in incoming:
            graph[edge.source].append(edge.target)
            incoming[edge.target] += 1
    remaining = {node.node_id for node in ir.nodes}
    layers: list[list[str]] = []
    while remaining:
        ready = [node_id for node_id in remaining if incoming[node_id] == 0]
        if not ready:
            ready = sorted(remaining)
        layers.append(ready)
        remaining.difference_update(ready)
        for node_id in ready:
            for nxt in graph[node_id]:
                if nxt in remaining:
                    incoming[nxt] -= 1
    return layers


def _critical_ids(ir: NetworkIR) -> set[str]:
    duration = {node.node_id: node.duration_days for node in ir.nodes}
    graph: dict[str, list[str]] = {node.node_id: [] for node in ir.nodes}
    for edge in ir.edges:
        if edge.source in graph:
            graph[edge.source].append(edge.target)
    memo: dict[str, tuple[int, list[str]]] = {}

    def walk(node_id: str) -> tuple[int, list[str]]:
        if node_id in memo:
            return memo[node_id]
        best_len = duration[node_id]
        best_path = [node_id]
        for nxt in graph.get(node_id, []):
            if nxt not in duration:
                continue
            length, path = walk(nxt)
            candidate = duration[node_id] + length
            if candidate > best_len:
                best_len = candidate
                best_path = [node_id, *path]
        memo[node_id] = (best_len, best_path)
        return memo[node_id]

    best: list[str] = []
    best_score = -1
    for node in ir.nodes:
        score, path = walk(node.node_id)
        if score > best_score:
            best_score = score
            best = path
    return set(best)


def render_network_png(ir: NetworkIR, out_path: Path) -> None:
    layers = _topo_layers(ir)
    critical = _critical_ids(ir)
    col_count = max(len(layers), 1)
    row_count = max((len(layer) for layer in layers), default=1)
    fig_w = max(7.2, col_count * 2.4)
    fig_h = max(3.2, row_count * 1.5 + 1.2)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=DPI)
    ax.set_xlim(0, col_count * 2.6 + 0.4)
    ax.set_ylim(0, row_count * 1.8 + 0.8)
    ax.axis("off")
    fonts = font_kwargs()
    style = resolve_diagram_style(ir.style)
    box_w, box_h = 2.0, 1.05
    boxes: dict[str, Box] = {}
    for col, layer in enumerate(layers):
        for row, node_id in enumerate(layer):
            boxes[node_id] = Box(
                cx=1.4 + col * 2.6,
                cy=(row_count - row) * 1.8 - 0.4,
                width=box_w,
                height=box_h,
            )
    for edge in ir.edges:
        source = boxes.get(edge.source)
        target = boxes.get(edge.target)
        if source is None or target is None:
            continue
        on_critical = edge.source in critical and edge.target in critical
        add_orthogonal_arrow(
            ax,
            route_between_boxes(source, target),
            style.accent if on_critical else style.edge_color,
            linewidth=2.0 if on_critical else 1.1,
        )
    by_id = {node.node_id: node for node in ir.nodes}
    for node_id, box in boxes.items():
        node = by_id[node_id]
        fill = style.accent_tint if node_id in critical else style.box_color
        stroke = style.accent if node_id in critical else style.edge_color
        add_node_shape(ax, box, "process", fill, stroke)
        ax.text(
            box.cx,
            box.cy,
            wrap_label(f"{node.label}\n{node.duration_days}天", 8),
            ha="center",
            va="center",
            fontsize=8 * style.font_scale,
            color=style.text_color,
            zorder=TEXT_Z,
            **fonts,
        )
    ax.set_title(ir.title, fontsize=12 * style.font_scale, color=style.text_color, pad=10, **fonts)
    fig.tight_layout()
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)


__all__ = ["render_network_png"]
