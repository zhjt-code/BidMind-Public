"""VLM 救援输入装配（懒渲染）。

职责: 非绿救援触发后按需装配 VLM 输入——经 db 加载原 PDF 路径, 按评分 chunk
      页码与独立定位器页集的并集(±margin 扩页)整页渲染为 base64,
      锚定门禁用渲染页 PDF 文本层(独立于缺损 chunk)。
不做: 不调用 LLM、不做门禁校验、不判定是否触发救援; 绿色项目不会进入本模块。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from loguru import logger


@dataclass(frozen=True)
class VlmRescueInput:
    """VLM 救援输入快照。"""

    images: tuple[str, ...]  # base64 PNG, 与 pages 一一对应
    pages: tuple[int, ...]  # 渲染的 PDF 页码(1-based)
    anchor_text: str  # 渲染页 PDF 文本层(空白归一), 锚定门禁唯一依据
    truncated: bool  # 页数超上限被截断(记入诊断)


def _positive_pages(pages: set[int]) -> set[int]:
    """过滤非正页码。"""
    return {int(page) for page in pages if int(page) > 0}


def _expand_page_set(pages: set[int], margin: int, page_count: int) -> set[int]:
    """逐页 ±margin 扩成邻域, 夹在 1..page_count。"""
    expanded: set[int] = set()
    for page in pages:
        expanded.update(
            candidate
            for candidate in range(page - margin, page + margin + 1)
            if 1 <= candidate <= page_count
        )
    return expanded


def _contiguous_span(pages: set[int], margin: int, page_count: int) -> list[int]:
    """min-max ±margin 连续窗。"""
    if not pages or page_count < 1:
        return []
    lo, hi = min(pages), max(pages)
    return [
        page
        for page in range(lo - margin, hi + margin + 1)
        if 1 <= page <= page_count
    ]


def _largest_page_cluster(pages: set[int], gap_threshold: int) -> set[int]:
    """按页隙分簇, 取页数最多的一簇; 同规模取靠后簇。"""
    ordered = sorted(pages)
    if not ordered:
        return set()
    clusters: list[list[int]] = [[ordered[0]]]
    for page in ordered[1:]:
        if page - clusters[-1][-1] > gap_threshold:
            clusters.append([page])
        else:
            clusters[-1].append(page)
    return set(max(clusters, key=lambda cluster: (len(cluster), cluster[0])))


def _centered_window(
    must_cover: set[int],
    *,
    max_pages: int,
    page_count: int,
    center: int,
) -> list[int]:
    """连续 max_pages 窗; 能盖住 must_cover 时优先覆盖, 否则以 center 为心。"""
    if not must_cover or max_pages < 1 or page_count < 1:
        return []
    width = min(max_pages, page_count)
    start = center - width // 2
    start = max(1, min(start, page_count - width + 1))
    cover_lo, cover_hi = min(must_cover), max(must_cover)
    if cover_hi - cover_lo + 1 <= width:
        if start > cover_lo:
            start = cover_lo
        if start + width - 1 < cover_hi:
            start = cover_hi - width + 1
        start = max(1, min(start, page_count - width + 1))
    return list(range(start, start + width))


def collect_rescue_pages(
    chunk_pages: set[int],
    located_pages: set[int],
    *,
    page_margin: int,
    max_pages: int,
    page_count: int,
) -> tuple[list[int], bool]:
    """chunk 页 ∪ 定位器页; 超限时锚定表页簇, 禁止从头截断。"""
    chunk = _positive_pages(chunk_pages)
    located = _positive_pages(located_pages)
    if page_count < 1:
        return [], False
    if chunk:
        clip_lo = min(chunk) - page_margin
        clip_hi = max(chunk) + page_margin
        located = {page for page in located if clip_lo <= page <= clip_hi}
        base = chunk | located
        expanded = _contiguous_span(base, page_margin, page_count)
        if len(expanded) <= max_pages:
            return expanded, False
        center = sorted(chunk)[len(chunk) // 2]
        return _centered_window(
            chunk, max_pages=max_pages, page_count=page_count, center=center
        ), True
    if not located:
        return [], False
    neighborhood = _expand_page_set(located, page_margin, page_count)
    cluster = _largest_page_cluster(neighborhood, max(page_margin, 2))
    expanded = _contiguous_span(cluster, page_margin, page_count)
    if len(expanded) <= max_pages:
        return expanded, False
    center = sorted(cluster)[len(cluster) // 2]
    return _centered_window(
        cluster, max_pages=max_pages, page_count=page_count, center=center
    ), True


def _normalize_page_texts(page_texts: list[str]) -> str:
    """空白归一 + NFKC 全半角 + 小写（审查V4: 与 candidate_gates._compact 同口径）。"""
    import unicodedata

    return "\n".join(
        re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).lower()
        for text in page_texts
    )


def _collect_scoring_pages(chunks: list[Any]) -> set[int]:
    """从评分 chunk 收集页码: 优先 scoring_payload 的 page_start/page_end。"""
    from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk

    pages: set[int] = set()
    for chunk in chunks:
        payload = None
        try:
            payload = payload_from_chunk(chunk)
        except Exception:  # noqa: BLE001 - 载荷缺失/畸形回退 page_num
            payload = None
        start = (payload or {}).get("page_start")
        end = (payload or {}).get("page_end")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            pages.update(range(start, end + 1))
            continue
        page_num = getattr(chunk, "page_num", None)
        if isinstance(page_num, int) and page_num > 0:
            pages.add(page_num)
    return pages


def _assemble_pages_sync(
    file_path: str,
    chunk_pages: set[int],
    dpi: int,
    page_margin: int,
    max_pages: int,
) -> tuple[list[int], list[str], list[str], bool] | None:
    import fitz

    from app.core.extractor.scoring.vlm.scoring_page_locator import locate_scoring_pages
    from app.core.parser.page_renderer import render_page_to_base64

    with fitz.open(file_path) as doc:
        page_texts = {
            index + 1: doc[index].get_text() or ""
            for index in range(doc.page_count)
        }
        located = locate_scoring_pages(page_texts)
        target_pages, truncated = collect_rescue_pages(
            chunk_pages,
            located,
            page_margin=page_margin,
            max_pages=max_pages,
            page_count=doc.page_count,
        )
        if not target_pages:
            return None
        images: list[str] = []
        texts: list[str] = []
        for page_no in target_pages:
            images.append(render_page_to_base64(doc[page_no - 1], dpi=dpi))
            texts.append(page_texts.get(page_no, ""))
        return target_pages, images, texts, truncated


async def build_vlm_rescue_input(
    *,
    db: Any,
    document_id: int,
    scoring_chunks: list[Any],
    dpi: int,
    page_margin: int,
    max_pages: int,
) -> Optional[VlmRescueInput]:
    """装配 VLM 救援输入; 任一前提缺失返回 None(fail-open, 保持现状)。"""
    if db is None:
        return None

    from sqlalchemy import select

    from app.models.document import Document

    try:
        doc = (
            await db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 - 查询失败不阻断主流程
        logger.warning("[评分VLM救援] Document 查询失败 doc={}: {}", document_id, exc)
        return None
    file_path = getattr(doc, "file_path", None) if doc else None
    if not file_path or not Path(file_path).exists():
        logger.warning(
            "[评分VLM救援] 原PDF不可用, 跳过 doc={} path={}", document_id, file_path
        )
        return None

    chunk_pages = _collect_scoring_pages(list(scoring_chunks or []))
    try:
        assembled = await asyncio.to_thread(
            _assemble_pages_sync,
            str(file_path),
            chunk_pages,
            dpi,
            page_margin,
            max_pages,
        )
    except Exception as exc:  # noqa: BLE001 - 渲染失败不阻断主流程
        logger.warning("[评分VLM救援] 页面渲染失败 doc={}: {}", document_id, exc)
        return None
    if assembled is None:
        return None
    target_pages, images, texts, truncated = assembled
    kept = [
        (page, image, text)
        for page, image, text in zip(target_pages, images, texts)
        if image
    ]
    if not kept:
        return None
    return VlmRescueInput(
        images=tuple(image for _, image, _ in kept),
        pages=tuple(page for page, _, _ in kept),
        anchor_text=_normalize_page_texts([text for _, _, text in kept]),
        truncated=truncated,
    )


__all__ = [
    "VlmRescueInput",
    "build_vlm_rescue_input",
    "collect_rescue_pages",
]
