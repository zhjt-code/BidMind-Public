"""
职责: 按位置把版面区域与文件文字行对齐成带身份的文字, 并给检查器提供按区域取数的入口
不做: 不调用版面服务、不做识别、不判定问题、不改解析层; 没有版面结果的页由取数入口退回旧来源
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app.core.document_qa.contracts import BBox, DocumentModel, QaBlock, QaLine, QaRegion

KIND_TITLE = "title"
KIND_BODY = "body"
KIND_TABLE = "table"
KIND_CAPTION = "caption"
KIND_FURNITURE = "furniture"
KIND_TOC = "toc"
KIND_FORMULA = "formula"
KIND_VISUAL = "visual"

# 页文本(乱码、整页重复)只拼这些身份; 页眉页脚、公式、图片不算页面文字
_TEXT_KINDS = frozenset({KIND_TITLE, KIND_BODY, KIND_TABLE, KIND_CAPTION, KIND_TOC})
# 版面框贴着字, 文字层行框略宽: 框向外放宽这么多(归一化坐标)再算覆盖
_EDGE_TOLERANCE = 0.004
# 行至少这么大比例落在某框内才归该框; 行内公式、印章等小框只盖住行的一部分, 抢不走整行
_MIN_LINE_COVER = 0.5


@dataclass(frozen=True)
class SectionUnit:
    """章节区间里的一个版面单元(结构检查判断空章节用)。"""

    page: int
    bbox: BBox
    text: str
    kind: str


# ── 对齐 ─────────────────────────────────────────────────────────────────────


def region_kind_map(rules: dict) -> dict[str, str]:
    """版面标签 -> 区域身份(rules/regions.yaml)。"""
    mapping: dict[str, str] = {}
    for kind, labels in ((rules.get("regions") or {}).get("kinds") or {}).items():
        for label in labels or []:
            mapping[str(label)] = str(kind)
    return mapping


def _bbox(raw: Any) -> Optional[BBox]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))


def _area(bbox: BBox) -> float:
    return abs(bbox[2] - bbox[0]) * abs(bbox[3] - bbox[1])


def _union(boxes: Iterable[BBox]) -> BBox:
    items = list(boxes)
    return (
        min(box[0] for box in items), min(box[1] for box in items),
        max(box[2] for box in items), max(box[3] for box in items),
    )


def _cover(line_box: BBox, box: BBox) -> float:
    """行框落在(放宽后的)区域框内的面积占比。"""
    tol = _EDGE_TOLERANCE
    width = min(line_box[2], box[2] + tol) - max(line_box[0], box[0] - tol)
    height = min(line_box[3], box[3] + tol) - max(line_box[1], box[1] - tol)
    if width <= 0 or height <= 0:
        return 0.0
    return (width * height) / max(_area(line_box), 1e-9)


def _owner(line: QaLine, boxes: list[tuple[int, str, str, BBox]]) -> Optional[int]:
    """覆盖这一行比例最高的区域; 同样覆盖(如表题压在表格框内)取面积小的; 覆盖不到一半算未入框。"""
    best: Optional[int] = None
    best_key: Optional[tuple[float, float]] = None
    for index, (_, _, _, box) in enumerate(boxes):
        cover = _cover(line.bbox, box)
        if cover < _MIN_LINE_COVER:
            continue
        key = (round(cover, 2), -_area(box))
        if best_key is None or key > best_key:
            best, best_key = index, key
    return best


def _reading_order(lines: Iterable[QaLine]) -> tuple[QaLine, ...]:
    return tuple(sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0])))


def _page_boxes(
    raw_blocks: list[dict[str, Any]],
    label_kind: dict[str, str],
    unknown: Counter,
) -> list[tuple[int, str, str, BBox]]:
    boxes: list[tuple[int, str, str, BBox]] = []
    for index, raw in enumerate(raw_blocks or []):
        bbox = _bbox(raw.get("bbox"))
        if bbox is None:
            continue
        label = str(raw.get("label") or "")
        kind = label_kind.get(label)
        if kind is None:
            unknown[label] += 1
            kind = KIND_BODY
        boxes.append((index, label, kind, bbox))
    return boxes


def build_page_regions(
    blocks: list[QaBlock],
    structure_pages: dict[int, list[dict[str, Any]]],
    rules: dict,
) -> tuple[dict[int, list[QaRegion]], dict[str, int]]:
    """有版面结果的页: 文件文字行按中心点归入版面区域。

    落在所有区域外的行按原文字块归入正文区, 一行都不丢。返回 (页 -> 区域, 未知标签计数)。
    """
    label_kind = region_kind_map(rules)
    blocks_by_page: dict[int, list[QaBlock]] = {}
    for block in blocks:
        blocks_by_page.setdefault(block.page, []).append(block)

    unknown: Counter = Counter()
    regions: dict[int, list[QaRegion]] = {}
    for raw_page, raw_blocks in structure_pages.items():
        page = int(raw_page)
        boxes = _page_boxes(raw_blocks, label_kind, unknown)
        members: list[list[QaLine]] = [[] for _ in boxes]
        leftovers: list[tuple[QaLine, ...]] = []
        for block in blocks_by_page.get(page, []):
            orphan: list[QaLine] = []
            for line in block.lines:
                if not line.text.strip():
                    continue
                owner = _owner(line, boxes)
                if owner is None:
                    orphan.append(line)
                else:
                    members[owner].append(line)
            if orphan:
                leftovers.append(tuple(orphan))
        page_regions = [
            QaRegion(
                page=page, kind=kind, label=label, bbox=bbox, order=index,
                lines=_reading_order(members[position]),
            )
            for position, (index, label, kind, bbox) in enumerate(boxes)
        ]
        for offset, lines in enumerate(leftovers):
            page_regions.append(QaRegion(
                page=page, kind=KIND_BODY, label="",
                bbox=_union(line.bbox for line in lines),
                order=len(raw_blocks or []) + offset,
                lines=_reading_order(lines),
            ))
        page_regions.sort(key=lambda region: (region.bbox[1], region.bbox[0]))
        regions[page] = page_regions
    return regions, dict(unknown)


def layout_content_pages(regions: dict[int, list[QaRegion]]) -> set[int]:
    """版面在页上框出页眉、页脚、页码以外区域的页(只剩印刷页码的页仍是空白页)。

    只认版面框出的区域; 未入框的文字行归成的正文区(label 为空)不代表版面看到了内容。
    """
    return {
        page for page, items in regions.items()
        if any(region.kind != KIND_FURNITURE and region.label for region in items)
    }


# ── 取数入口(检查器只经这里读文字) ───────────────────────────────────────────


def layout_pages(model: DocumentModel) -> set[int]:
    """有版面结果的页。"""
    return set(model.regions)


def body_paragraphs(model: DocumentModel, *, visual: bool = False) -> dict[int, list[str]]:
    """正文段落: 有版面的页按正文区逐区重建, 不跨区拼接; 其余页退回旧段落。

    visual=True 时旧段落取 fitz 原字形通道(字形检查专用)。
    """
    from app.core.document_qa.model_builder import build_paragraphs

    fallback = model.visual_paragraphs if visual else model.paragraphs
    result: dict[int, list[str]] = {}
    for page in sorted(set(fallback) | set(model.regions)):
        if page not in model.regions:
            result[page] = list(fallback.get(page) or [])
            continue
        result[page] = [
            paragraph
            for region in model.regions[page] if region.kind == KIND_BODY
            for paragraph in build_paragraphs(region.text)
        ]
    return result


def _table_rows(lines: Iterable[QaLine]) -> list[str]:
    """同一视觉行(中心落在当前行纵向范围内)的片段按横坐标用空格拼成一行; 不跨行拼接。"""
    rows: list[list[QaLine]] = []
    for line in sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0])):
        center = (line.bbox[1] + line.bbox[3]) / 2
        if rows:
            top = min(item.bbox[1] for item in rows[-1])
            bottom = max(item.bbox[3] for item in rows[-1])
            if top <= center <= bottom:
                rows[-1].append(line)
                continue
        rows.append([line])
    texts: list[str] = []
    for row in rows:
        parts = [item.text.strip() for item in sorted(row, key=lambda item: item.bbox[0])]
        text = " ".join(part for part in parts if part)
        if text:
            texts.append(text)
    return texts


def table_rows(model: DocumentModel) -> dict[int, list[str]]:
    """表格区逐行文字(仅有版面的页; 其余页的表格文字仍在旧段落里)。"""
    result: dict[int, list[str]] = {}
    for page in sorted(model.regions):
        rows = [
            row
            for region in model.regions[page] if region.kind == KIND_TABLE
            for row in _table_rows(region.lines)
        ]
        if rows:
            result[page] = rows
    return result


def body_and_table_texts(model: DocumentModel) -> dict[int, list[str]]:
    """正文段落 + 表格行, 按页汇总且同页正文在前; 表格逐行, 取值不会吞进下一行。"""
    paragraphs = body_paragraphs(model)
    rows = table_rows(model)
    return {
        page: paragraphs.get(page, []) + rows.get(page, [])
        for page in sorted(set(paragraphs) | set(rows))
    }


def page_texts(model: DocumentModel) -> dict[int, str]:
    """页面文字: 有版面的页只拼标题/正文/表格/图表题/目录区; 其余页退回旧页文本。"""
    result = dict(model.body_texts)
    for page, regions in model.regions.items():
        result[page] = "\n".join(
            region.text for region in regions if region.kind in _TEXT_KINDS and region.lines
        )
    return result


def region_lines(model: DocumentModel, *kinds: str) -> list[QaLine]:
    """有版面的页里指定身份区域的行, 按页与阅读顺序排列。"""
    wanted = set(kinds)
    return [
        line
        for page in sorted(model.regions)
        for region in model.regions[page] if region.kind in wanted
        for line in region.lines
    ]


def body_blocks(model: DocumentModel) -> list[QaBlock]:
    """正文块: 有版面的页每个正文区一块(表格/公式/页眉页脚天然不在内); 其余页退回旧块。"""
    blocks = [block for block in model.blocks if block.page not in model.regions]
    for page, regions in model.regions.items():
        for region in regions:
            if region.kind != KIND_BODY or not region.lines:
                continue
            blocks.append(QaBlock(
                page=page,
                text=region.text,
                bbox=region.bbox,
                spans=tuple(span for line in region.lines for span in line.spans),
                lines=region.lines,
            ))
    return sorted(blocks, key=lambda block: (block.page, block.bbox[1], block.bbox[0]))


def section_units(model: DocumentModel) -> list[SectionUnit]:
    """章节区间单元: 有版面的页取页眉页脚以外的全部区域(表格、图片也算内容); 其余页退回旧块。"""
    units = [
        SectionUnit(page=block.page, bbox=block.bbox, text=block.text, kind=KIND_BODY)
        for block in model.blocks if block.page not in model.regions
    ]
    for page, regions in model.regions.items():
        units.extend(
            SectionUnit(page=page, bbox=region.bbox, text=region.text, kind=region.kind)
            for region in regions if region.kind != KIND_FURNITURE
        )
    return sorted(units, key=lambda unit: (unit.page, unit.bbox[1], unit.bbox[0]))
