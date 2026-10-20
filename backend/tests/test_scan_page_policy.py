""": 写标素材扫描页应以整页合成图作为图片真源。"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from inspect import signature

import fitz

from app.core.parser.image_extractor import ImageExtractor
from app.core.parser.scan_page_policy import (
    is_scan_dominated_page,
    render_scan_page_image,
)
from app.core.writer.material_content_extractor import MaterialContentExtractor


def _png_bytes(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """生成测试用RGB PNG，避免引入Pillow依赖。"""
    raw_rows = []
    row = bytes(rgb) * width
    for _ in range(height):
        raw_rows.append(b"\x00" + row)
    raw = b"".join(raw_rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _make_layered_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    rect = fitz.Rect(0, 0, 600, 800)
    page.insert_image(rect, stream=_png_bytes(600, 800, (255, 255, 255)))
    page.insert_image(rect, stream=_png_bytes(600, 800, (30, 30, 30)))
    doc.save(path)
    doc.close()


def test_scan_dominated_page_renders_one_full_page_image(tmp_path: Path) -> None:
    pdf_path = tmp_path / "layered.pdf"
    image_dir = tmp_path / "images"
    _make_layered_pdf(pdf_path)

    extracted = ImageExtractor().extract_from_pdf(pdf_path, save_dir=image_dir)[1]
    assert len(extracted) >= 2
    assert is_scan_dominated_page("", extracted) is True

    rendered = render_scan_page_image(pdf_path, 1, image_dir, image_index=0, dpi=120)

    assert rendered.page_num == 1
    assert rendered.image_index == 0
    assert rendered.image_format == "png"
    assert rendered.file_path is not None
    assert Path(rendered.file_path).name == "p1_full.png"
    assert Path(rendered.file_path).exists()
    assert rendered.width > 0
    assert rendered.height > 0


def test_scan_page_render_accepts_body_clip(tmp_path: Path) -> None:
    """整页扫描渲染应支持正文裁剪, 避免页眉页脚进入 OCR/VL。"""
    assert "clip" in signature(render_scan_page_image).parameters

    pdf_path = tmp_path / "layered.pdf"
    image_dir = tmp_path / "images"
    _make_layered_pdf(pdf_path)

    rendered = render_scan_page_image(
        pdf_path,
        1,
        image_dir,
        image_index=0,
        dpi=72,
        clip=(0, 120, 600, 700),
    )

    assert rendered.bbox == (0.0, 120.0, 600.0, 700.0)
    assert rendered.height < 800


def test_text_heavy_page_is_not_scan_dominated(tmp_path: Path) -> None:
    pdf_path = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 72), "这是一页包含足够文字的普通PDF页面。" * 10)
    page.insert_image(fitz.Rect(100, 120, 250, 270), stream=_png_bytes(150, 150, (255, 0, 0)))
    doc.save(pdf_path)
    doc.close()

    extracted = ImageExtractor().extract_from_pdf(pdf_path, save_dir=tmp_path / "images")[1]
    assert is_scan_dominated_page("这是一页包含足够文字的普通PDF页面。" * 10, extracted) is False


def test_material_pdf_parse_replaces_scan_layers_with_full_page_image(tmp_path: Path) -> None:
    pdf_path = tmp_path / "layered.pdf"
    image_dir = tmp_path / "material_images"
    _make_layered_pdf(pdf_path)

    result = MaterialContentExtractor(enable_vl=False).parse_only(
        str(pdf_path),
        ".pdf",
        image_save_dir=image_dir,
    )

    assert len(result.images) == 1
    assert result.images[0].file_path is not None
    assert Path(result.images[0].file_path).name == "p1_full.png"
    assert result.parsed_pages[0].images == result.images
