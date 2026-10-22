"""分层/体系图渲染。

职责: 将 LayeredIR 画成自上而下的分层 PNG。
不做: 不编译 IR、不访问数据库。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from app.core.writer.visuals.ir_contracts import LayeredIR
from app.core.writer.visuals.render_common import DPI, font_kwargs, wrap_label
from app.core.writer.visuals.styles import resolve_diagram_style


def render_layered_png(ir: LayeredIR, out_path: Path) -> None:
    layer_count = len(ir.layers)
    fig_h = max(3.4, layer_count * 1.15 + 1.0)
    fig, ax = plt.subplots(figsize=(8.4, fig_h), dpi=DPI)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, layer_count * 1.3 + 0.6)
    ax.axis("off")
    fonts = font_kwargs()
    style = resolve_diagram_style(ir.style)
    for index, layer in enumerate(ir.layers):
        y_center = (layer_count - index) * 1.3 - 0.35
        ax.add_patch(FancyBboxPatch(
            (0.5, y_center - 0.5),
            9.0,
            1.0,
            boxstyle="round,pad=0.06",
            facecolor=style.box_color,
            edgecolor=style.edge_color,
            linewidth=1.1,
        ))
        ax.text(
            1.2, y_center, wrap_label(layer.label, 8),
            ha="left", va="center", fontsize=10 * style.font_scale,
            color=style.text_color, fontweight="bold", **fonts,
        )
        for item_index, item in enumerate(layer.items[:6]):
            x_center = 4.0 + item_index * 1.05
            ax.add_patch(FancyBboxPatch(
                (x_center - 0.45, y_center - 0.32),
                0.9,
                0.64,
                boxstyle="round,pad=0.04",
                facecolor="#ffffff",
                edgecolor=style.edge_color,
                linewidth=0.8,
            ))
            ax.text(
                x_center, y_center, wrap_label(item, 6),
                ha="center", va="center", fontsize=7 * style.font_scale,
                color=style.text_color, **fonts,
            )
    ax.set_title(ir.title, fontsize=12 * style.font_scale, color=style.text_color, pad=10, **fonts)
    fig.tight_layout()
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)


__all__ = ["render_layered_png"]
