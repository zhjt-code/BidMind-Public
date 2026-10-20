"""Diagram IR → PNG 确定性渲染器。

职责: 将 9 类图元 IR 分发到对应 matplotlib(Agg) 渲染器。
不做: 不解析 SectionPlan、不调用 LLM、不访问数据库。
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib.pyplot as plt

from app.core.writer.visuals.ir_contracts import (
    DiagramIR,
    FlowchartIR,
    GanttIR,
    LayeredIR,
    NetworkIR,
    OrgChartIR,
    RoadmapIR,
    SwimlaneIR,
    WbsIR,
    ZoneLayoutIR,
)
from app.core.writer.visuals.render_common import DPI, font_kwargs, wrap_label
from app.core.writer.visuals.render_connectors import (
    TEXT_Z,
    add_node_shape,
    add_orthogonal_arrow,
)
from app.core.writer.visuals.render_geometry import Box, infer_flowchart_kind, route_between_boxes
from app.core.writer.visuals.render_layered import render_layered_png
from app.core.writer.visuals.render_network import render_network_png
from app.core.writer.visuals.render_org_chart import render_org_chart_png
from app.core.writer.visuals.render_roadmap import render_roadmap_png
from app.core.writer.visuals.render_swimlane import render_swimlane_png
from app.core.writer.visuals.render_wbs import render_wbs_png
from app.core.writer.visuals.render_zone_layout import render_zone_layout_png
from app.core.writer.visuals.styles import paint_for_kind, resolve_diagram_style

_FLOWCHART_WRAP_AFTER = 6
_FLOWCHART_MAX_COLS = 4


def _layout_flowchart(
    node_count: int,
) -> tuple[list[tuple[float, float]], float, float, tuple[float, float], tuple[float, float], float, float]:
    """返回 centers、figsize、xlim/ylim、盒宽高。节点>6 时按行换行，避免竖条超出 A4。"""

    if node_count <= _FLOWCHART_WRAP_AFTER:
        centers = [
            (5.0, (node_count - index) * 2 - 0.5)
            for index in range(node_count)
        ]
        fig_h = max(2.5, node_count * 1.1 + 1.0)
        return centers, 6.4, fig_h, (0.0, 10.0), (0.0, node_count * 2 + 1.0), 5.2, 1.1

    cols = _FLOWCHART_MAX_COLS
    rows = max(1, math.ceil(node_count / cols))
    box_w, box_h = 2.2, 1.0
    x_step, y_step = 2.55, 2.45
    centers = []
    for index in range(node_count):
        row, col = divmod(index, cols)
        x_center = 1.35 + col * x_step
        y_center = (rows - row) * y_step - 0.5
        centers.append((x_center, y_center))
    fig_w = 10.0
    fig_h = max(2.8, rows * 1.9 + 1.2)
    xlim = (0.0, 1.35 + (cols - 1) * x_step + 1.35)
    ylim = (0.0, rows * y_step + 0.7)
    return centers, fig_w, fig_h, xlim, ylim, box_w, box_h


def _render_flowchart(ir: FlowchartIR, out_path: Path) -> None:
    node_count = len(ir.nodes)
    centers_list, fig_w, fig_h, xlim, ylim, box_w, box_h = _layout_flowchart(node_count)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=DPI)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.axis("off")
    fonts = font_kwargs()
    style = resolve_diagram_style(ir.style)
    wrap_width = 8 if node_count > _FLOWCHART_WRAP_AFTER else 12
    boxes = {
        node.node_id: Box(cx=x_center, cy=y_center, width=box_w, height=box_h)
        for node, (x_center, y_center) in zip(ir.nodes, centers_list, strict=True)
    }
    kinds = {
        node.node_id: infer_flowchart_kind(index, node_count, node.label)
        for index, node in enumerate(ir.nodes)
    }

    for edge in ir.edges:
        source = boxes.get(edge.source)
        target = boxes.get(edge.target)
        if source is None or target is None:
            continue
        add_orthogonal_arrow(ax, route_between_boxes(source, target), style.edge_color)

    for node, (x_center, y_center) in zip(ir.nodes, centers_list, strict=True):
        box = boxes[node.node_id]
        kind = kinds[node.node_id]
        paint = paint_for_kind(style, kind)
        add_node_shape(ax, box, kind, paint.fill, paint.stroke)
        ax.text(
            x_center,
            y_center,
            wrap_label(node.label, width=wrap_width),
            ha="center",
            va="center",
            fontsize=(9 if node_count > _FLOWCHART_WRAP_AFTER else 10) * style.font_scale,
            color=style.text_color,
            zorder=TEXT_Z,
            **fonts,
        )

    ax.set_title(ir.title, fontsize=12 * style.font_scale, color=style.text_color, pad=12, **fonts)
    fig.tight_layout()
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)


def _render_gantt(ir: GanttIR, out_path: Path) -> None:
    bar_count = len(ir.bars)
    fig_height = max(2.5, bar_count * 0.6 + 1.4)
    fig, ax = plt.subplots(figsize=(7.5, fig_height), dpi=DPI)
    fonts = font_kwargs()
    style = resolve_diagram_style(ir.style)

    labels = [wrap_label(bar.label, width=14) for bar in ir.bars]
    positions = list(range(bar_count - 1, -1, -1))
    for position, bar in zip(positions, ir.bars, strict=True):
        ax.barh(
            position,
            bar.duration_days,
            left=bar.start_day,
            height=0.5,
            color=style.bar_color,
            edgecolor=style.edge_color,
        )
        ax.text(
            bar.start_day + bar.duration_days + 0.6,
            position,
            f"{bar.duration_days}天",
            va="center",
            fontsize=9 * style.font_scale,
            color=style.text_color,
            **fonts,
        )
    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=9 * style.font_scale, **fonts)
    ax.set_xlabel("相对工期(天)", fontsize=10 * style.font_scale, **fonts)
    ax.set_title(ir.title, fontsize=12 * style.font_scale, color=style.text_color, pad=12, **fonts)
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)


def render_diagram_png(ir: DiagramIR, out_path: Path) -> Path:
    """将 Diagram IR 渲染为 PNG 文件并返回路径。"""

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(ir, FlowchartIR):
        _render_flowchart(ir, out_path)
    elif isinstance(ir, GanttIR):
        _render_gantt(ir, out_path)
    elif isinstance(ir, OrgChartIR):
        render_org_chart_png(ir, out_path)
    elif isinstance(ir, SwimlaneIR):
        render_swimlane_png(ir, out_path)
    elif isinstance(ir, RoadmapIR):
        render_roadmap_png(ir, out_path)
    elif isinstance(ir, LayeredIR):
        render_layered_png(ir, out_path)
    elif isinstance(ir, NetworkIR):
        render_network_png(ir, out_path)
    elif isinstance(ir, WbsIR):
        render_wbs_png(ir, out_path)
    elif isinstance(ir, ZoneLayoutIR):
        render_zone_layout_png(ir, out_path)
    else:  # pragma: no cover - IR 契约已约束类型
        raise ValueError(f"unsupported IR type: {type(ir)!r}")
    return out_path


__all__ = ["render_diagram_png"]
