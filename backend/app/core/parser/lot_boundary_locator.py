"""采购包/标段评分边界定位器。

职责: 在文档页文本中定位"采购包/标段"评标边界锚点, 输出各lot的中文标签与页范围。
不做: 不判定评分表、不解析评分项、不调用 LLM/DB。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.parser.pdf_parser import ParsedDocument


_LOT_PATTERN = re.compile(
    r"(采购包\s*[一二三四五六七八九十0-9]+|第?\s*[一二三四五六七八九十0-9]+\s*包|标段\s*[一二三四五六七八九十0-9]+|分标\s*[一二三四五六七八九十0-9]+)"
)
_SCORING_CONTEXT_TERMS = ("综合评分法", "评标办法", "评标标准", "评分标准", "评审因素", "评标方法")
_PAIR_WINDOW = 220


@dataclass(frozen=True)
class LotBoundary:
    """采购包/标段评分边界。"""

    label: str
    page_start: int
    page_end: int


def locate_lot_boundaries(parsed_doc: "ParsedDocument") -> list[LotBoundary]:
    """定位多采购包评分边界; 少于两个有效锚点时返回空, 保持单包文档兼容。"""
    anchors: dict[str, int] = {}
    for page in parsed_doc.pages:
        text = _compact_text(getattr(page, "text", "") or "")
        if not text or not any(term in text for term in _SCORING_CONTEXT_TERMS):
            continue
        for match in _LOT_PATTERN.finditer(text):
            label = _normalize_label(match.group(1))
            if not label:
                continue
            tail = text[match.start() : match.start() + _PAIR_WINDOW]
            if not any(term in tail for term in _SCORING_CONTEXT_TERMS):
                continue
            # 同一 lot 在总述页与自身评分标准页可能重复出现; 取后出现的评分语境页作为边界。
            anchors[label] = int(page.page_num)
    if len(anchors) < 2:
        return []

    ordered = sorted(anchors.items(), key=lambda item: item[1])
    page_count = parsed_doc.page_count or max((p.page_num for p in parsed_doc.pages), default=0)
    out: list[LotBoundary] = []
    for idx, (label, start) in enumerate(ordered):
        next_start = ordered[idx + 1][1] if idx + 1 < len(ordered) else None
        end = (next_start - 1) if next_start else page_count
        if start <= end:
            out.append(LotBoundary(label=label, page_start=start, page_end=end))
    return out


def lot_for_page(lots: list[LotBoundary], page_num: int) -> str | None:
    """按页码返回所属 lot 标签。"""
    for lot in lots:
        if lot.page_start <= page_num <= lot.page_end:
            return lot.label
    return None


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _normalize_label(raw: str) -> str:
    label = re.sub(r"\s+", "", raw or "")
    if label.startswith("第") and label.endswith("包"):
        label = "采购包" + label.strip("第包")
    if label.startswith("包"):
        label = "采购包" + label[1:]
    return label


__all__ = ["LotBoundary", "locate_lot_boundaries", "lot_for_page"]
