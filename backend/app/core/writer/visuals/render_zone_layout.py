"""场地分区示意图渲染。

职责: 将 ZoneLayoutIR 画成封闭词表分区网格，并标明非施工平面图。
不做: 不编译 IR、不访问数据库、不绘制 CAD/实景平面。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from app.core.writer.visuals.construction_pack import ZONE_LAYOUT_CAPTION_SUFFIX
from app.core.writer.visuals.ir_contracts import ZoneLayoutIR
from app.core.writer.visuals.render_common import DPI, font_kwargs, wrap_label
from app.core.writer.visuals.styles import resolve_diagram_style


def render_zone_layout_png(ir: ZoneLayoutIR, out_path: Path) -> None:
    zone_count = len(ir.zones)
    cols = 2 if zone_count <= 4 else 3
    rows = max(1, (zone_count + cols - 1) // cols)
    fig_w = max(7.0, cols * 2.8)
    fig_h = max(3.6, rows * 1.6 + 1.4)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=DPI)
    ax.set_xlim(0, cols * 3.1 + 0.3)
    ax.set_ylim(0, rows * 1.7 + 1.1)
    ax.axis("off")
    fonts = font_kwargs()
    style = resolve_diagram_style(ir.style)
    for index, zone in enumerate(ir.zones):
        row, col = divmod(index, cols)
        x0 = 0.25 + col * 3.1
        y0 = (rows - row - 1) * 1.7 + 0.85
        ax.add_patch(FancyBboxPatch(
            (x0, y0),
            2.8,
            1.35,
            boxstyle="round,pad=0.06",
            facecolor=style.box_color,
            edgecolor=style.edge_color,
            linewidth=1.1,
        ))
        ax.text(
            x0 + 1.4, y0 + 0.68, wrap_label(zone, 8),
            ha="center", va="center", fontsize=11 * style.font_scale,
            color=style.text_color, **fonts,
        )
    ax.text(
        (cols * 3.1 + 0.3) / 2,
        0.35,
        ZONE_LAYOUT_CAPTION_SUFFIX.strip(""),
        ha="center",
        va="center",
        fontsize=8 * style.font_scale,
        color=style.edge_color,
        **fonts,
    )
    ax.set_title(ir.title, fontsize=12 * style.font_scale, color=style.text_color, pad=10, **fonts)
    fig.tight_layout()
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)


__all__ = ["render_zone_layout_png"]
