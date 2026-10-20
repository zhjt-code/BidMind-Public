"""
职责: 把版面分析块收成标题树, 并标出表格区
不做: 不调用版面服务、不改检查器、不做文字行归属(见 region_text)
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.document_qa.contracts import BBox, QaBlock, QaHeading
from app.core.document_qa.heading_detector import _match_heading

_TITLE_LABELS = frozenset({
    "doc_title",
    "title",
    "content_title",
    "abstract_title",
    "paragraph_title",
})
_TABLE_LABELS = frozenset({"table"})
_LABEL_LEVEL = {
    "doc_title": 1,
    "title": 1,
    "content_title": 2,
    "abstract_title": 2,
    "paragraph_title": 3,
}


def _bbox(raw: Any) -> Optional[BBox]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))


def _center_in(bbox: BBox, region: BBox) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def table_regions(pages: dict[int, list[dict[str, Any]]]) -> dict[int, list[BBox]]:
    """每页表格块的归一化框。"""
    regions: dict[int, list[BBox]] = {}
    for page, blocks in pages.items():
        boxes = [
            box
            for block in blocks
            if str(block.get("label")) in _TABLE_LABELS
            for box in [_bbox(block.get("bbox"))]
            if box is not None
        ]
        if boxes:
            regions[int(page)] = boxes
    return regions


def exclude_table_blocks(
    blocks: list[QaBlock],
    regions: dict[int, list[BBox]],
) -> list[QaBlock]:
    """中心落在表格区内的文本块退出正文通道。"""
    if not regions:
        return blocks
    kept: list[QaBlock] = []
    for block in blocks:
        boxes = regions.get(block.page) or []
        if any(_center_in(block.bbox, box) for box in boxes):
            continue
        kept.append(block)
    return kept


def headings_from_pages(pages: dict[int, list[dict[str, Any]]]) -> list[QaHeading]:
    """标题类版面块转成质检标题。能套上既有编号句式时沿用其编号和层级。"""
    headings: list[QaHeading] = []
    for page in sorted(pages):
        for block in pages[page]:
            label = str(block.get("label") or "")
            if label not in _TITLE_LABELS:
                continue
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            matched = _match_heading(text.replace("\n", " "))
            if matched is not None:
                number, title, level, kind = matched
            else:
                # 套不上编号句式的版面标题是封面/扉页名, 不是章节节点。
                # kind 留空会被当成大纲, 所以显式标成版面标签。
                number, title = None, text
                kind = label
                level = int(block.get("level") or _LABEL_LEVEL.get(label, 2))
            headings.append(QaHeading(
                page=int(page),
                level=level,
                number=number,
                title=title,
                bbox=_bbox(block.get("bbox")),
                kind=kind or None,
            ))
    return headings
