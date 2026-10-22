"""正交折线与节点形状绘制。

职责: 把几何模块给出的折线/包围盒画到 matplotlib 轴上。
不做: 不计算路由、不编译 IR、不访问数据库。
"""

from __future__ import annotations

from matplotlib.axes import Axes
from matplotlib.patches import Ellipse, FancyArrowPatch, FancyBboxPatch, Polygon

from app.core.writer.visuals.render_geometry import Box

ARROW_Z = 1
NODE_Z = 3
TEXT_Z = 4


def add_orthogonal_arrow(
    ax: Axes,
    path: list[tuple[float, float]],
    color: str,
    *,
    with_arrow: bool = True,
    linewidth: float = 1.1,
) -> None:
    """先画轴对齐折线；需要箭头时只在末段加箭头，尖端落在终点。"""

    if len(path) < 2:
        return
    if with_arrow:
        if len(path) > 2:
            ax.plot(
                [point[0] for point in path[:-1]],
                [point[1] for point in path[:-1]],
                color=color,
                linewidth=linewidth,
                solid_capstyle="butt",
                zorder=ARROW_Z,
            )
        ax.add_patch(FancyArrowPatch(
            path[-2],
            path[-1],
            arrowstyle="-|>",
            mutation_scale=9,
            color=color,
            linewidth=linewidth,
            shrinkA=0,
            shrinkB=0,
            zorder=ARROW_Z,
        ))
        return
    ax.plot(
        [point[0] for point in path],
        [point[1] for point in path],
        color=color,
        linewidth=linewidth,
        solid_capstyle="butt",
        zorder=ARROW_Z,
    )


def add_node_shape(
    ax: Axes,
    box: Box,
    kind: str,
    fill: str,
    stroke: str,
    *,
    linewidth: float = 1.1,
) -> None:
    """按种类画椭圆（起止）、菱形（判断）或圆角矩形（步骤）。"""

    if kind == "decision":
        ax.add_patch(Polygon(
            [
                (box.cx, box.top),
                (box.right, box.cy),
                (box.cx, box.bottom),
                (box.left, box.cy),
            ],
            closed=True,
            facecolor=fill,
            edgecolor=stroke,
            linewidth=linewidth,
            zorder=NODE_Z,
        ))
        return
    if kind in {"start", "end"}:
        ax.add_patch(Ellipse(
            (box.cx, box.cy),
            box.width,
            box.height,
            facecolor=fill,
            edgecolor=stroke,
            linewidth=linewidth,
            zorder=NODE_Z,
        ))
        return
    ax.add_patch(FancyBboxPatch(
        (box.left, box.bottom),
        box.width,
        box.height,
        boxstyle="round,pad=0.0,rounding_size=0.06",
        facecolor=fill,
        edgecolor=stroke,
        linewidth=linewidth,
        zorder=NODE_Z,
    ))


__all__ = [
    "ARROW_Z",
    "NODE_Z",
    "TEXT_Z",
    "add_node_shape",
    "add_orthogonal_arrow",
]
