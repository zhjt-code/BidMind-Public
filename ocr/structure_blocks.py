"""把版面推理的页结果收成稳定块列表。

职责: 归一化版面块的标签、文本、层级和 0-1 坐标(PP-StructureV3 产线结果或版面检测结果)
不做: 不加载模型、不发 HTTP、不识别文字
"""

from __future__ import annotations

from typing import Any

_TITLE_LEVELS = {
    "doc_title": 1,
    "title": 1,
    "content_title": 2,
    "abstract_title": 2,
    "paragraph_title": 3,
}


def _as_mapping(page: Any) -> dict[str, Any]:
    if isinstance(page, dict):
        inner = page.get("res", page)
        return inner if isinstance(inner, dict) else {}
    data = getattr(page, "json", None)
    if callable(data):
        data = data()
    if isinstance(data, dict):
        inner = data.get("res", data)
        return inner if isinstance(inner, dict) else {}
    return {}


def _normalize_bbox(
    raw: Any,
    width: float,
    height: float,
) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    if width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    return [
        max(0.0, min(1.0, x0 / width)),
        max(0.0, min(1.0, y0 / height)),
        max(0.0, min(1.0, x1 / width)),
        max(0.0, min(1.0, y1 / height)),
    ]


def _level(block: dict[str, Any], label: str) -> int | None:
    for key in ("title_level", "level"):
        value = block.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return _TITLE_LEVELS.get(label)


def blocks_from_page_result(
    page: Any,
    *,
    width: float,
    height: float,
) -> list[dict[str, Any]]:
    """一页预测结果 -> 按阅读顺序排列的块。"""
    data = _as_mapping(page)
    raw_blocks = data.get("parsing_res_list") or []
    if not isinstance(raw_blocks, list):
        return []
    normalized: list[dict[str, Any]] = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue
        label = str(block.get("block_label") or block.get("label") or "")
        bbox = _normalize_bbox(block.get("block_bbox") or block.get("bbox"), width, height)
        if not label or bbox is None:
            continue
        order = block.get("block_order")
        normalized.append({
            "label": label,
            "text": str(block.get("block_content") or block.get("text") or "").strip(),
            "bbox": bbox,
            "level": _level(block, label),
            "order": order if isinstance(order, int) else 10**9,
        })
    normalized.sort(key=lambda item: (item["order"], item["bbox"][1], item["bbox"][0]))
    return normalized


def blocks_from_layout_result(
    page: Any,
    *,
    width: float,
    height: float,
) -> list[dict[str, Any]]:
    """版面检测结果 -> 自上而下、自左而右排列的块。只有标签与坐标, 文字由调用方另取。"""
    data = _as_mapping(page)
    raw_boxes = data.get("boxes") or []
    if not isinstance(raw_boxes, list):
        return []
    normalized: list[dict[str, Any]] = []
    for box in raw_boxes:
        if not isinstance(box, dict):
            continue
        label = str(box.get("label") or "")
        bbox = _normalize_bbox(box.get("coordinate"), width, height)
        if not label or bbox is None:
            continue
        normalized.append({"label": label, "text": "", "bbox": bbox, "level": _TITLE_LEVELS.get(label)})
    normalized.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    for order, item in enumerate(normalized):
        item["order"] = order
    return normalized
