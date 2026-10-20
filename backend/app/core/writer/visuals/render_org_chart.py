"""组织架构图渲染。

职责: 将 OrgChartIR 画成 PNG。
不做: 不编译 IR、不访问数据库。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from app.core.writer.visuals.ir_contracts import OrgChartIR
from app.core.writer.visuals.render_common import DPI, font_kwargs, wrap_label
from app.core.writer.visuals.render_connectors import (
    TEXT_Z,
    add_node_shape,
    add_orthogonal_arrow,
)
from app.core.writer.visuals.render_geometry import Box, org_bus_paths
from app.core.writer.visuals.styles import paint_for_kind, resolve_diagram_style

_LEAD_W, _LEAD_H = 2.6, 1.05
_UNIT_W, _UNIT_H = 1.9, 1.05


def render_org_chart_png(ir: OrgChartIR, out_path: Path) -> None:
    unit_count = len(ir.units)
    fig_w = max(7.2, unit_count * 1.8)
    fig, ax = plt.subplots(figsize=(fig_w, 4.2), dpi=DPI)
    ax.set_xlim(0, unit_count * 2.2 + 0.4)
    ax.set_ylim(0, 5.2)
    ax.axis("off")
    fonts = font_kwargs()
    style = resolve_diagram_style(ir.style)
    lead_x = (unit_count * 2.2 + 0.4) / 2
    lead = Box(cx=lead_x, cy=4.07, width=_LEAD_W, height=_LEAD_H)
    units = [
        Box(cx=1.2 + index * 2.2, cy=1.37, width=_UNIT_W, height=_UNIT_H)
        for index in range(unit_count)
    ]
    bus_paths = org_bus_paths(lead, units)
    for index, path in enumerate(bus_paths):
        add_orthogonal_arrow(
            ax,
            path,
            style.edge_color,
            with_arrow=index >= 2,
        )
    lead_paint = paint_for_kind(style, "end")
    add_node_shape(ax, lead, "end", lead_paint.fill, lead_paint.stroke)
    ax.text(
        lead.cx, lead.cy, wrap_label(ir.lead, 10),
        ha="center", va="center", fontsize=10 * style.font_scale,
        color=style.text_color, zorder=TEXT_Z, **fonts,
    )
    unit_paint = paint_for_kind(style, "step")
    for unit, label in zip(units, ir.units, strict=True):
        add_node_shape(ax, unit, "step", unit_paint.fill, unit_paint.stroke)
        ax.text(
            unit.cx, unit.cy, wrap_label(label, 8),
            ha="center", va="center", fontsize=9 * style.font_scale,
            color=style.text_color, zorder=TEXT_Z, **fonts,
        )
    ax.set_title(ir.title, fontsize=12 * style.font_scale, color=style.text_color, pad=10, **fonts)
    fig.tight_layout()
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)


__all__ = ["render_org_chart_png"]
