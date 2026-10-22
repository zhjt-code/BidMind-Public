"""PdfParser Page Chrome 接入测试。"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import fitz
import pytest

from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table
from app.core.parser import ChunkPlanner
from app.core.parser.pdf_parser import PdfParser
from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks
from app.core.parser.scoring_section_locator import locate_scoring_sections

pytestmark = pytest.mark.scoring


def _png_bytes(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

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


def _make_pdf_with_repeated_chrome(path: Path) -> None:
    doc = fitz.open()
    logo = _png_bytes(600, 120, (220, 0, 0))
    for page_num in range(1, 6):
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 35), "EQUATOR 朱道", fontsize=14)
        page.insert_image(fitz.Rect(0, 0, 600, 120), stream=logo)
        page.insert_text((80, 230), f"BODY-CONTENT-{page_num}: business clause", fontsize=12)
        page.insert_text((260, 780), f"PAGE {page_num}", fontsize=10)
    doc.save(path)
    doc.close()


def test_pdf_parser_filters_repeated_page_chrome_before_downstream(tmp_path: Path) -> None:
    pdf_path = tmp_path / "chrome.pdf"
    image_dir = tmp_path / "images"
    _make_pdf_with_repeated_chrome(pdf_path)

    parsed = PdfParser().parse(pdf_path, image_save_dir=image_dir)

    assert "EQUATOR" not in parsed.pages[0].text
    assert "PAGE 1" not in parsed.pages[0].text
    assert "BODY-CONTENT-1" in parsed.pages[0].text
    assert parsed.all_images == []
    assert parsed.metadata["page_chrome"]["cropped_pages"] == 5


def test_pdf_parser_preserves_scoring_tail_when_learning_page_chrome() -> None:
    """: 无真实页眉页脚时, 顶部评分尾行不得被Page Chrome裁剪。"""
    pdf_path = Path("storage/uploads/1646/tender_评分测试15.pdf")
    if not pdf_path.exists():
        return

    parsed = PdfParser(extract_images=False).parse(pdf_path)
    page13_text = parsed.pages[12].text

    assert "质量保证措施和售后服务方案" in page13_text
    assert "6分" in page13_text or "0～6分" in page13_text

    chunks = build_scoring_table_chunks(
        parsed,
        ChunkPlanner(),
        0,
        sections=locate_scoring_sections(parsed),
    )
    scoring_text = "\n".join(chunk.content for chunk in chunks)
    assert "质量保证措施和售后服务方案" in scoring_text

    items = []
    for chunk in chunks:
        parsed_table = parse_scoring_table(
            chunk.content,
            section_path=chunk.metadata.get("section_path", "") or "",
        )
        items.extend(parsed_table.items)
    assert sum(float(item.score_weight or 0) for item in items) == 100.0
