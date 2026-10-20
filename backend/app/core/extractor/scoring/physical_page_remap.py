"""
职责: 把评分候选项的 source_pages 映射到 scoring chunk 的物理页。
不做: 不解析表格、不调用 LLM、不读 toc_map、不落库、不回填缺页。
"""
from __future__ import annotations

from typing import Any, Sequence


def _positive_int(value: Any) -> int | None:
    """把宽松页码值收成 >=1 的 int; 非法返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page >= 1 else None


def _chunk_payload(chunk: Any) -> dict:
    """读取 scoring_payload, 不要求 grid(与 payload_from_chunk 门槛解耦)。"""
    for container in (
        getattr(chunk, "chunk_metadata", None),
        getattr(chunk, "metadata", None),
    ):
        if isinstance(container, dict):
            payload = container.get("scoring_payload")
            if isinstance(payload, dict):
                return payload
    return {}


def collect_chunk_physical_pages(chunks: Sequence[Any] | None) -> tuple[int, ...]:
    """从 chunk.page_num / payload 页范围 / row_pages 收集去重有序物理页。"""
    pages: set[int] = set()
    for chunk in chunks or []:
        payload = _chunk_payload(chunk)
        start = _positive_int(payload.get("page_start"))
        end = _positive_int(payload.get("page_end"))
        if start is not None and end is not None and start <= end:
            pages.update(range(start, end + 1))
        for raw in payload.get("row_pages") or []:
            page = _positive_int(raw)
            if page is not None:
                pages.add(page)
        page_num = _positive_int(getattr(chunk, "page_num", None))
        if page_num is not None:
            pages.add(page_num)
    return tuple(sorted(pages))


def _item_pages(item: Any) -> list[int]:
    pages: list[int] = []
    for raw in getattr(item, "source_pages", None) or []:
        page = _positive_int(raw)
        if page is not None:
            pages.append(page)
    return pages


def _with_pages(item: Any, pages: list[int]) -> Any:
    unique = list(dict.fromkeys(pages))
    try:
        return item.model_copy(update={"source_pages": unique})
    except (AttributeError, TypeError, ValueError):
        try:
            item.source_pages = unique
        except (AttributeError, TypeError, ValueError):
            pass
        return item


def _nearest_physical(page: int, physical: Sequence[int]) -> int:
    return min(physical, key=lambda candidate: (abs(candidate - page), candidate))


def remap_item_source_pages(
    items: list, physical_pages: Sequence[int]
) -> list:
    """把落在物理页集合外的 source_pages 换算进 chunk 物理页。

    优先均匀偏移(印刷页/附件相对页 1..N); 偏移无法全部落入集合时逐项就近。
    已全部落在物理页内或没有任何页码时原样返回(缺页不编造)。
    """
    physical = tuple(sorted({
        page for page in (_positive_int(raw) for raw in physical_pages)
        if page is not None
    }))
    if not items or not physical:
        return items
    physical_set = set(physical)
    reported = [page for item in items for page in _item_pages(item)]
    if not reported or all(page in physical_set for page in reported):
        return items

    offset = min(physical) - min(reported)
    if all((page + offset) in physical_set for page in reported):
        remapped: list = []
        for item in items:
            pages = _item_pages(item)
            remapped.append(
                _with_pages(item, [page + offset for page in pages])
                if pages else item
            )
        return remapped

    snapped: list = []
    for item in items:
        pages = _item_pages(item)
        if not pages:
            snapped.append(item)
            continue
        snapped.append(
            _with_pages(
                item,
                [
                    page if page in physical_set else _nearest_physical(page, physical)
                    for page in pages
                ],
            )
        )
    return snapped
