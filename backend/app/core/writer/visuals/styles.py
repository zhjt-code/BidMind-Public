"""生成图示样式解析。

职责: 将 DiagramStyle 解析为渲染器可直接消费的主题色板、强调色与节点涂装。
不做: 不编译 IR、不读取用户配置、不渲染 PNG。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.writer.visuals.ir_contracts import DiagramStyle


@dataclass(frozen=True)
class NodePaint:
    fill: str
    stroke: str


@dataclass(frozen=True)
class DiagramRenderStyle:
    box_color: str
    edge_color: str
    bar_color: str
    text_color: str
    font_scale: float
    accent: str
    accent_tint: str
    paper: str


_THEMES: dict[str, DiagramRenderStyle] = {
    "classic_blue": DiagramRenderStyle(
        box_color="#eaf1f8",
        edge_color="#33506b",
        bar_color="#4a708b",
        text_color="#1d2b3a",
        font_scale=1.0,
        accent="#b4532a",
        accent_tint="#fde8dc",
        paper="#ffffff",
    ),
    "business_gray": DiagramRenderStyle(
        box_color="#f3f4f6",
        edge_color="#4b5563",
        bar_color="#6b7280",
        text_color="#111827",
        font_scale=1.0,
        accent="#b4532a",
        accent_tint="#fde8dc",
        paper="#ffffff",
    ),
    "tech_teal": DiagramRenderStyle(
        box_color="#e6fffb",
        edge_color="#0f766e",
        bar_color="#14b8a6",
        text_color="#134e4a",
        font_scale=1.0,
        accent="#c2410c",
        accent_tint="#ffedd5",
        paper="#ffffff",
    ),
}
_FONT_SCALES = {
    "sm": 0.9,
    "md": 1.0,
    "lg": 1.12,
}


def resolve_diagram_style(style: DiagramStyle | None) -> DiagramRenderStyle:
    """解析图示样式; 缺省保持历史 classic_blue 输出。"""

    if style is None:
        return _THEMES["classic_blue"]
    base = _THEMES.get(style.theme, _THEMES["classic_blue"])
    primary = style.primary_color or base.bar_color
    accent = style.primary_color or base.accent
    return DiagramRenderStyle(
        box_color=base.box_color,
        edge_color=primary,
        bar_color=primary,
        text_color=style.text_color or base.text_color,
        font_scale=_FONT_SCALES.get(style.font_scale, 1.0),
        accent=accent,
        accent_tint=base.accent_tint,
        paper=base.paper,
    )


def paint_for_kind(style: DiagramRenderStyle, kind: str) -> NodePaint:
    """按节点种类取填色/描边；终点用强调色，起止与判断用纸色。"""

    if kind == "end":
        return NodePaint(style.accent_tint, style.accent)
    if kind in {"start", "decision"}:
        return NodePaint(style.paper, style.edge_color)
    return NodePaint(style.box_color, style.edge_color)


__all__ = [
    "DiagramRenderStyle",
    "NodePaint",
    "paint_for_kind",
    "resolve_diagram_style",
]
