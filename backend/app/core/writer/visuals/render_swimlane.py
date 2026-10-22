"""泳道图渲染。

职责: 将 SwimlaneIR 画成 PNG。
不做: 不编译 IR、不访问数据库。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from app.core.writer.visuals.ir_contracts import SwimlaneIR
from app.core.writer.visuals.render_common import DPI, font_kwargs, wrap_label
from app.core.writer.visuals.render_connectors import (
    TEXT_Z,
    add_node_shape,
    add_orthogonal_arrow,
)
from app.core.writer.visuals.render_geometry import Box, route_between_boxes
from app.core.writer.visuals.styles import paint_for_kind, resolve_diagram_style

_BOX_W = 1.3
_BOX_H = 0.76
_X_STEP = 1.85


def render_swimlane_png(ir: SwimlaneIR, out_path: Path) -> None:
    lane_count = len(ir.lanes)
    node_count = len(ir.nodes)
    fig_w = max(8.5, 2.4 + node_count * _X_STEP)
    fig_h = max(3.6, lane_count * 1.5 + 1.2)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=DPI)
    ax.set_xlim(0, 2.2 + node_count * _X_STEP)
    ax.set_ylim(0, lane_count * 1.65 + 0.5)
    ax.axis("off")
    fonts = font_kwargs()
    style = resolve_diagram_style(ir.style)
    lane_y = {
        lane: (lane_count - index) * 1.65 - 0.8
        for index, lane in enumerate(ir.lanes)
    }
    for lane, y_center in lane_y.items():
        ax.add_patch(Rectangle(
            (0.15, y_center - 0.65),
            2.0 + node_count * _X_STEP - 0.15,
            1.3,
            facecolor=style.box_color,
            edgecolor=style.edge_color,
            alpha=0.25,
            linewidth=0.8,
            zorder=0,
        ))
        ax.text(
            0.25, y_center, wrap_label(lane, 6),
            ha="left", va="center", fontsize=9 * style.font_scale,
            color=style.text_color, zorder=TEXT_Z, **fonts,
        )
    boxes: dict[str, Box] = {}
    for index, node in enumerate(ir.nodes):
        x_center = 2.5 + index * _X_STEP
        y_center = lane_y.get(node.lane, 1.0)
        boxes[node.node_id] = Box(cx=x_center, cy=y_center, width=_BOX_W, height=_BOX_H)

    for edge in ir.edges:
        source = boxes.get(edge.source)
        target = boxes.get(edge.target)
        if source is None or target is None:
            continue
        add_orthogonal_arrow(ax, route_between_boxes(source, target), style.edge_color)

    last_index = node_count - 1
    for index, node in enumerate(ir.nodes):
        box = boxes[node.node_id]
        if index == 0:
            kind = "start"
        elif index == last_index:
            kind = "end"
        else:
            kind = "step"
        paint = paint_for_kind(style, kind)
        add_node_shape(ax, box, kind, paint.fill, paint.stroke)
        ax.text(
            box.cx, box.cy, wrap_label(node.label, 8),
            ha="center", va="center", fontsize=8 * style.font_scale,
            color=style.text_color, zorder=TEXT_Z, **fonts,
        )
    ax.set_title(ir.title, fontsize=12 * style.font_scale, color=style.text_color, pad=10, **fonts)
    fig.tight_layout()
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)


__all__ = ["render_swimlane_png"]
