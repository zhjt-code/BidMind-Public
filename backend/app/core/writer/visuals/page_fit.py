"""A4 页芯图示尺寸适配。

职责: 按标书页边距计算插图最大高度，并在定宽后按像素比缩小以免超出一页。
不做: 不渲染 PNG、不写 Word、不改 IR。
"""

from __future__ import annotations

from app.core.writer.bid_format_defaults import (
    MARGIN_BOTTOM_MM,
    MARGIN_TOP_MM,
    PAGE_HEIGHT_MM,
)

# 预留图题与段间距，避免图+题顶满页芯。
_CAPTION_RESERVE_MM = 20.0

PAGE_CORE_HEIGHT_MM = PAGE_HEIGHT_MM - MARGIN_TOP_MM - MARGIN_BOTTOM_MM
MAX_DIAGRAM_HEIGHT_MM = PAGE_CORE_HEIGHT_MM - _CAPTION_RESERVE_MM


def fit_diagram_size_mm(
    width_mm: float,
    px_w: int,
    px_h: int,
) -> tuple[float, float]:
    """按像素宽高比把插图缩进 A4 页芯高度内，返回 (宽mm, 高mm)。"""

    if width_mm <= 0 or px_w <= 0 or px_h <= 0:
        return max(width_mm, 0.0), 0.0
    height_mm = width_mm * (px_h / px_w)
    if height_mm <= MAX_DIAGRAM_HEIGHT_MM:
        return width_mm, height_mm
    scale = MAX_DIAGRAM_HEIGHT_MM / height_mm
    return width_mm * scale, MAX_DIAGRAM_HEIGHT_MM


__all__ = [
    "MAX_DIAGRAM_HEIGHT_MM",
    "PAGE_CORE_HEIGHT_MM",
    "fit_diagram_size_mm",
]
