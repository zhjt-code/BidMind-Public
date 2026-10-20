"""写标素材扫描页处理策略。

职责: 判定扫描主导页并将该页整页渲染为单张合成图。
不做: OCR/VL调用、数据库写入、普通插图页的逐图提取。
"""

from __future__ import annotations

from pathlib import Path

import fitz
from loguru import logger

from app.core.parser.image_extractor import ExtractedImage
from app.core.parser.page_renderer import is_scan_heavy_page


def is_scan_dominated_page(
    page_text: str,
    images: list[ExtractedImage],
) -> bool:
    """判定素材PDF页是否应以整页渲染图替代逐XObject提取图。"""
    return is_scan_heavy_page(page_text, images)


def render_scan_page_image(
    pdf_path: str | Path,
    page_num: int,
    save_dir: Path,
    *,
    image_index: int = 0,
    dpi: int = 150,
    clip: tuple[float, float, float, float] | None = None,
) -> ExtractedImage:
    """把指定PDF页渲染为单张PNG图片并返回 ExtractedImage。"""
    save_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = Path(pdf_path)
    output_path = save_dir / f"p{page_num}_full.png"

    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_num - 1]
        clip_rect = fitz.Rect(clip) if clip else None
        pixmap = page.get_pixmap(dpi=dpi, alpha=False, clip=clip_rect)
        png_bytes = pixmap.tobytes("png")
        output_path.write_bytes(png_bytes)
        source_rect = clip_rect or page.rect
        bbox = (
            float(source_rect.x0),
            float(source_rect.y0),
            float(source_rect.x1),
            float(source_rect.y1),
        )

    logger.debug(
        "素材扫描页整页渲染: file={}, page={}, size={}x{}, bytes={}",
        pdf_path.name,
        page_num,
        pixmap.width,
        pixmap.height,
        len(png_bytes),
    )
    return ExtractedImage(
        image_index=image_index,
        page_num=page_num,
        width=pixmap.width,
        height=pixmap.height,
        image_format="png",
        xref=0,
        bbox=bbox,
        file_path=str(output_path),
        file_size=len(png_bytes),
        spatial_context="整页扫描件",
    )


__all__ = ["is_scan_dominated_page", "render_scan_page_image"]
