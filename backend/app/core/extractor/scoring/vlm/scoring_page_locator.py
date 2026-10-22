"""评分区页独立定位器。

职责: 从 PDF 逐页文本层用双信号(表头词 + 分值信号)定位评分区候选页集。
不做: 不渲染、不调 LLM、不查 DB、不做门禁校验; 不改共享分值信号函数。
"""

from __future__ import annotations

import re

from app.core.extractor.scoring.embedded_score_parser import (
    has_embedded_score_signal_text,
)

_HEADER_TERMS = (
    "评分标准",
    "评分办法",
    "评标办法",
    "评分细则",
    "评分因素",
    "分值",
    "权重",
)
_TOC_LEADER_LINE_RE = re.compile(
    r".{1,60}?(?:[.．·⋅]{3,}|…{1,})\s*\d{1,4}\s*$"
)


def _is_dotted_toc_page(text: str) -> bool:
    """点线目录页: 多行「标题……页码」或短页内单条目录行。"""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    hits = [line for line in lines if _TOC_LEADER_LINE_RE.search(line)]
    if len(hits) >= 2:
        return True
    return len(hits) == 1 and len(lines) <= 4


def locate_scoring_pages(page_texts: dict[int, str]) -> set[int]:
    """双信号命中的页码集合; 点线目录页拒绝; 无命中返回空集。"""
    pages: set[int] = set()
    for page, text in page_texts.items():
        raw = str(text or "")
        if not raw.strip():
            continue
        if not any(term in raw for term in _HEADER_TERMS):
            continue
        if _is_dotted_toc_page(raw):
            continue
        if has_embedded_score_signal_text(raw):
            pages.add(int(page))
    return pages


__all__ = ["locate_scoring_pages"]
