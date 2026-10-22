"""页面渲染器

将PDF页面渲染为图片,供VL模型做整页识别。
用于扫描件密集页面: 这类页面pdfplumber提取不到文字,
逐图fitz提取可能遭遇tiles碎片化,整页渲染绕过这两个问题。
"""

import base64

import fitz
from loguru import logger

from app.core.parser.image_extractor import ExtractedImage


def is_scan_heavy_page(
    page_text: str,
    images: list[ExtractedImage],
) -> bool:
    """判定当前页是否为扫描件密集页

    触发条件(满足任一):
    1. 文字极少(<50字符) 且 有大幅扫描件
    2. 文字较少(<100字符) 且 有2张以上图片

    Args:
        page_text: pdfplumber提取的页面文字
        images: fitz提取的图片列表

    Returns:
        bool: 是否为扫描密集页
    """
    text_len = len(page_text.strip()) if page_text else 0
    has_large = any(img.size_category == "大幅扫描件" for img in images)

    if text_len < 50 and has_large:
        return True
    if text_len < 100 and len(images) >= 2:
        return True
    return False


def render_page_to_base64(
    fitz_page: fitz.Page,
    dpi: int = 200,
    clip: tuple[float, float, float, float] | None = None,
) -> str:
    """将fitz页面渲染为base64编码的PNG图片

    Args:
        fitz_page: fitz页面对象
        dpi: 渲染分辨率(默认200, 平衡清晰度与大小)
        clip: 可选正文区域裁剪框, 用于排除页眉页脚

    Returns:
        str: base64编码的PNG图片(不含 data: 前缀)
    """
    clip_rect = fitz.Rect(clip) if clip else None
    pixmap = fitz_page.get_pixmap(dpi=dpi, clip=clip_rect)
    png_bytes = pixmap.tobytes("png")
    b64 = base64.b64encode(png_bytes).decode("ascii")
    logger.debug(
        f"页面渲染: page={fitz_page.number + 1}, "
        f"size={pixmap.width}x{pixmap.height}, "
        f"bytes={len(png_bytes)}"
    )
    return b64
