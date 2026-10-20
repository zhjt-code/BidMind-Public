"""
职责: 从 DocumentModel 已采集的块/span 几何回填证据 bbox
不做: 不判定检查结论、不解析 PDF、不改 Finding 语义

定位: 折叠空白与标点后, 优先「摘录被某区完整包含」的最小框;
      否则合并页内足够长的摘录真子集。禁止用单字/短 span 抢最小面积。
页级发现由调用方显式使用 PAGE_BBOX, 禁止定位失败时默认铺满整页。
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Optional

from app.core.document_qa.contracts import BBox, DocumentModel, Evidence, EvidenceItem, Finding

PAGE_BBOX: BBox = (0.0, 0.0, 1.0, 1.0)
_TRAILING_PAGE_RE = re.compile(r"[\s.·…]+[\d]{1,4}\s*$")


def coerce_bbox(value: Any) -> Optional[BBox]:
    """JSON list / tuple -> BBox; 非法值丢弃。"""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        nums = (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError):
        return None
    return nums


def locate_bbox(
    model: DocumentModel,
    page: Optional[int],
    excerpt: str,
) -> Optional[BBox]:
    """在指定页找回摘录对应的已采集 bbox。页缺失或无匹配时返回 None。"""
    if page is None:
        return None
    regions = _page_regions(model, page)
    if not regions:
        return None
    for needle in _needles(excerpt):
        full = [bbox for text, bbox in regions if needle in text]
        if full:
            full.sort(key=_area)
            return full[0]
        min_len = max(8, len(needle) * 3 // 10)
        pieces = [text for text, _bbox in regions if text in needle and len(text) >= min_len]
        if not pieces:
            continue
        rest = needle
        for piece in sorted(set(pieces), key=len, reverse=True):
            rest = rest.replace(piece, "", 1)
        # 折叠后仍剩超过两个字, 说明摘录并不在本页。禁止用前缀拼出一个框。
        if len(rest) > 2:
            continue
        return _union([bbox for text, bbox in regions if text in needle and len(text) >= min_len])
    return None


def with_geometry(item: EvidenceItem, model: DocumentModel) -> EvidenceItem:
    """已有 bbox 保持不动; 否则按页+摘录回填。"""
    if item.bbox is not None or item.page is None:
        return item
    found = locate_bbox(model, item.page, item.excerpt)
    if found is None:
        return item
    return replace(item, bbox=found)


def enrich_findings(model: DocumentModel, findings: list[Finding]) -> None:
    """就地补齐发现项证据几何(替换列表元素; Finding 为 frozen)。不改变规则命中集合。"""
    for index, finding in enumerate(findings):
        original = finding.evidence
        items = tuple(with_geometry(item, model) for item in original.items)
        if items == original.items:
            continue
        findings[index] = replace(
            finding,
            evidence=Evidence(
                kind=original.kind,
                items=items,
                expected=original.expected,
                actual=original.actual,
            ),
        )


def _fold(text: str) -> str:
    """只留字母数字与 CJK, 去掉空白/标点/省略号, 使段落摘录能对上 fitz 块。"""
    return "".join(
        ch for ch in (text or "")
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )


def _needles(excerpt: str) -> list[str]:
    """整段折叠优先; 目录类「标题……页码」再试去掉尾页码的标题。"""
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in (excerpt or "", _TRAILING_PAGE_RE.sub("", excerpt or "")):
        folded = _fold(raw)
        if folded and folded not in seen:
            seen.add(folded)
            ordered.append(folded)
    return ordered


def _page_regions(model: DocumentModel, page: int) -> list[tuple[str, BBox]]:
    """页内可定位的文字片: 旧块与 span; 有版面的页再加各区域的行与 span(表格行不在旧块里)。"""
    regions: list[tuple[str, BBox]] = []
    for block in model.blocks:
        if block.page != page:
            continue
        for span in block.spans:
            folded = _fold(span.text)
            if folded:
                regions.append((folded, span.bbox))
        folded = _fold(block.text)
        if folded:
            regions.append((folded, block.bbox))
    for region in model.regions.get(page, []):
        for line in region.lines:
            for span in line.spans:
                folded = _fold(span.text)
                if folded:
                    regions.append((folded, span.bbox))
            folded = _fold(line.text)
            if folded:
                regions.append((folded, line.bbox))
        folded = _fold(region.text)
        if folded:
            regions.append((folded, region.bbox))
    return regions


def _union(boxes: list[BBox]) -> BBox:
    left = min(min(b[0], b[2]) for b in boxes)
    top = min(min(b[1], b[3]) for b in boxes)
    right = max(max(b[0], b[2]) for b in boxes)
    bottom = max(max(b[1], b[3]) for b in boxes)
    return (left, top, right, bottom)


def _area(bbox: BBox) -> float:
    return abs(bbox[2] - bbox[0]) * abs(bbox[3] - bbox[1])
