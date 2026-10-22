"""路线图渲染。

职责: 将 RoadmapIR 画成横向阶段 PNG。
不做: 不编译 IR、不访问数据库。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon

from app.core.writer.visuals.ir_contracts import RoadmapIR
from app.core.writer.visuals.render_common import DPI, font_kwargs, wrap_label
from app.core.writer.visuals.styles import resolve_diagram_style


def render_roadmap_png(ir: RoadmapIR, out_path: Path) -> None:
    phase_count = len(ir.phases)
    fig_w = max(8.0, phase_count * 2.15 + 1.2)
    fig, ax = plt.subplots(figsize=(fig_w, 3.6), dpi=DPI)
    ax.set_xlim(0, phase_count * 2.2 + 0.4)
    ax.set_ylim(0, 4.0)
    ax.axis("off")
    fonts = font_kwargs()
    style = resolve_diagram_style(ir.style)
    for index, phase in enumerate(ir.phases):
        x0 = 0.3 + index * 2.2
        chevron = Polygon(
            [
                (x0, 1.55),
                (x0 + 1.55, 1.55),
                (x0 + 1.85, 2.15),
                (x0 + 1.55, 2.75),
                (x0, 2.75),
                (x0 + 0.3, 2.15),
            ],
            closed=True,
            facecolor=style.box_color,
            edgecolor=style.edge_color,
            linewidth=1.1,
        )
        ax.add_patch(chevron)
        ax.text(
            x0 + 0.95, 2.15, wrap_label(phase.label, 8),
            ha="center", va="center", fontsize=9 * style.font_scale,
            color=style.text_color, **fonts,
        )
        ax.text(
            x0 + 0.95, 1.15, f"{phase.duration_days}天",
            ha="center", va="center", fontsize=8 * style.font_scale,
            color=style.text_color, **fonts,
        )
        if phase.milestone:
            ax.add_patch(Circle(
                (x0 + 0.95, 0.7),
                0.12,
                facecolor=style.bar_color,
                edgecolor=style.edge_color,
            ))
            ax.text(
                x0 + 0.95, 0.38, "里程碑",
                ha="center", va="center", fontsize=7 * style.font_scale,
                color=style.text_color, **fonts,
            )
    ax.set_title(ir.title, fontsize=12 * style.font_scale, color=style.text_color, pad=10, **fonts)
    fig.tight_layout()
    fig.savefig(out_path, format="png", bbox_inches="tight")
    plt.close(fig)


__all__ = ["render_roadmap_png"]
