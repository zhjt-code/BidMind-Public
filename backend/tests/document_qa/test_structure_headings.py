"""版面块到标题树、表格区隔离。夹具含标题与表格两种版面标签。"""

from __future__ import annotations

import sys
from pathlib import Path

from app.core.document_qa.contracts import QaBlock
from app.core.document_qa.structure_headings import (
    exclude_table_blocks,
    headings_from_pages,
    table_regions,
)

_OCR = Path(__file__).resolve().parents[2].parent / "ocr"
if str(_OCR) not in sys.path:
    sys.path.insert(0, str(_OCR))

from structure_blocks import blocks_from_page_result  # noqa: E402


def test_unnumbered_layout_title_is_not_outline() -> None:
    """无编号版面标题不进大纲。封面名与摘要名是同一形态的两个实体。"""
    from app.core.document_qa.heading_detector import is_outline_heading

    pages = {
        1: [
            {"label": "doc_title", "text": "投标文件", "bbox": [0.2, 0.4, 0.8, 0.48], "level": 1},
            {"label": "title", "text": "摘要", "bbox": [0.2, 0.2, 0.5, 0.26], "level": 1},
            {"label": "paragraph_title", "text": "1.2 研究目标", "bbox": [0.1, 0.3, 0.5, 0.34], "level": 2},
        ],
    }
    headings = headings_from_pages(pages)
    furniture = [item for item in headings if not item.number]
    numbered = [item for item in headings if item.number]
    assert [item.kind for item in furniture] == ["doc_title", "title"]
    assert all(not is_outline_heading(item, {}) for item in furniture)
    assert numbered and is_outline_heading(numbered[0], {})


def test_title_becomes_heading_and_table_does_not() -> None:
    pages = {
        3: [
            {"label": "doc_title", "text": "第一章 总则", "bbox": [0.1, 0.1, 0.4, 0.14], "level": 1},
            {"label": "table", "text": "1.2 报价 82万元", "bbox": [0.1, 0.5, 0.8, 0.8], "level": None},
            {"label": "paragraph_title", "text": "8.3 实验方案", "bbox": [0.1, 0.2, 0.5, 0.24], "level": 3},
        ],
    }
    headings = headings_from_pages(pages)
    titles = [item.title for item in headings]
    assert "总则" in titles[0] or titles[0] == "第一章 总则"
    assert any(item.number == "8.3" for item in headings)
    assert all("万元" not in item.title for item in headings)
    assert 3 in table_regions(pages)


def test_table_region_drops_centered_block_only() -> None:
    regions = table_regions({
        2: [{"label": "table", "bbox": [0.2, 0.2, 0.8, 0.8]}],
    })
    inside = QaBlock(page=2, text="单元格", bbox=(0.3, 0.3, 0.5, 0.4))
    outside = QaBlock(page=2, text="正文标题", bbox=(0.05, 0.05, 0.15, 0.1))
    kept = exclude_table_blocks([inside, outside], regions)
    assert [item.text for item in kept] == ["正文标题"]


def test_structure_result_normalizes_bbox_and_order() -> None:
    page = {
        "res": {
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_content": "后读",
                    "block_bbox": [0, 200, 100, 240],
                    "block_order": 2,
                },
                {
                    "block_label": "paragraph_title",
                    "block_content": "先读",
                    "block_bbox": [0, 40, 100, 80],
                    "block_order": 1,
                },
                {"block_label": "table", "block_content": "", "block_bbox": [0, 300, 100, 400]},
            ],
        },
    }
    blocks = blocks_from_page_result(page, width=100, height=400)
    assert [item["text"] for item in blocks[:2]] == ["先读", "后读"]
    assert blocks[0]["bbox"] == [0.0, 0.1, 1.0, 0.2]
    assert blocks[0]["level"] == 3
    assert blocks[2]["label"] == "table"
