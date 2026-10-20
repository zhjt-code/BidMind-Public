"""图示 PNG 渲染公共件。

职责: 提供中文字体探测、标签换行与 DPI。
不做: 不绘制具体图种、不访问数据库。
"""

from __future__ import annotations

import textwrap
from functools import lru_cache

import matplotlib

matplotlib.use("Agg")

from loguru import logger
from matplotlib import font_manager

# 注意: 云端 backend/Dockerfile 安装的是 fonts-wqy-microhei,
# 字体名为 "WenQuanYi Micro Hei", 必须保留在候选列表中。
_PREFERRED_CJK_FONTS = (
    "Microsoft YaHei",
    "SimHei",
    "WenQuanYi Micro Hei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Zen Hei",
)
DPI = 150


@lru_cache(maxsize=1)
def resolve_cjk_font() -> str | None:
    """探测可用中文字体; 缺失时返回 None 并告警(图中中文将缺字)。"""

    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in _PREFERRED_CJK_FONTS:
        if candidate in available:
            return candidate
    logger.warning(
        "未找到中文字体, 图示中文可能显示为缺字方块: candidates={}",
        _PREFERRED_CJK_FONTS,
    )
    return None


def font_kwargs() -> dict:
    font_name = resolve_cjk_font()
    return {"fontfamily": font_name} if font_name else {}


def wrap_label(label: str, width: int = 12) -> str:
    return "\n".join(textwrap.wrap(label, width=width) or [label])


__all__ = ["DPI", "font_kwargs", "resolve_cjk_font", "wrap_label"]
