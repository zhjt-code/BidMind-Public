"""WBS 工作分解图渲染。

职责: 将 WbsIR 画成两级树 PNG。
不做: 不编译 IR、不访问数据库、不画组织人员图。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from app.core.writer.visuals.ir_contracts import WbsIR
from app.core.writer.visuals.render_common import DPI, font_kwargs, wrap_label
from app.core.writer.visuals.styles import resolve_diagram_style


def render_wbs_png(ir: WbsIR, out_path: Path) -> None:
    package_count = len(ir.packages)
    fig_w = max(7.4, package_count * 2.3)
    fig_h = 4.8
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=DPI)
    ax.set_xlim(0, package_count * 2.4 + 0.6)
    ax.set_ylim(0, 6.2)
    ax.axis("off")
    fonts = font_kwargs()
    style = resolve_diagram_style(ir.style)
    root_x = (package_count * 2.4 + 0.6) / 2
    ax.add_patch(FancyBboxPatch(
        (root_x - 1.5, 4.85),
        3.0,
        0.9,
        boxstyle="round,pad=0.06",
        facecolor=style.box_color,
        edgecolor=style.edge_color,
        linewidth=1.2,
    ))
    ax.text(
        root_x, 5.3, wrap_label(ir.root, 12),
        ha="center", va="center", fontsize=11 * style.font_scale,
        color=style.text_color, fontweight="bold", **fonts,
    )
    for index, package in enumerate(ir.packages):
        x_center = 1.3 + index * 2.4
        ax.plot([root_x, x_center], [4.85, 3.85], color=style.edge_color, linewidth=1.0)
        ax.add_patch(FancyBboxPatch(
            (x_center - 1.0, 2.95),
            2.0,
            0.9,
            boxstyle="round,pad=0.05",
            facecolor=style.box_color,
            edgecolor=style.edge_color,
            linewidth=1.0,
        ))
        ax.text(
            x_center, 3.4, wrap_label(package.label, 8),
            ha="center", va="center", fontsize=9 * style.font_scale,
            color=style.text_color, **fonts,
        )
        for item_index, item in enumerate(package.items[:4]):
            y_bottom = 2.35 - item_index * 0.55
            ax.add_patch(FancyBboxPatch(
                (x_center - 0.9, y_bottom),
                1.8,
                0.45,
                boxstyle="round,pad=0.03",
                facecolor="#ffffff",
                edgecolor=style.edge_color,
                linewidth=0.7,
            ))
            ax.text(
                x_center, y_bottom + 0.22, wrap_label(item, 8),
                ha="center", va="center", fontsize=7 * style.font_scale,
                color=style.text_color, **fonts,
            )
    ax.set_title(ir.title, fontsize=12 * style.font_scale, color=style.text_color, pad=10, **fonts)
    fig.tight_layout()
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)


__all__ = ["render_wbs_png"]
