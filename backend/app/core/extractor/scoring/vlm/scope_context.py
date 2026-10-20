"""评分 chunk 的 scope(lot/scheme)-页码上下文（/ 审查V1）。

职责: 从 scoring chunk 提取 lot 与 scheme 的页范围映射, 产出读图提示注入文本,
 并对缺失归属的 VLM 候选按 source_page 做确定性回填(margin 页就近)。
不做: 不调 LLM、不查 DB、不判定闭合(裁判职责)、不修改单一 scope 文档候选。

lot 解析与账本 bridge 同源(resolve_chunk_lot); scheme 取 payload.scheme
(Task5 写入)。不走 payload_from_chunk——其"必须带 grid"门槛会漏掉文本形态评分表。
"""

from __future__ import annotations

from typing import Any, Callable, Optional


def _chunk_payload(chunk: Any) -> dict:
    meta = getattr(chunk, "chunk_metadata", None) or getattr(chunk, "metadata", None)
    payload = meta.get("scoring_payload") if isinstance(meta, dict) else None
    return payload if isinstance(payload, dict) else {}


def _page_map_from_chunks(
    scoring_chunks: list[Any], resolve_value: Callable[[Any, dict], Optional[str]]
) -> dict[int, str]:
    """通用 (页 -> scope 值) 映射装配; 无值 chunk 跳过。"""
    page_values: dict[int, str] = {}
    for chunk in scoring_chunks or []:
        payload = _chunk_payload(chunk)
        value = str(resolve_value(chunk, payload) or "").strip()
        if not value:
            continue
        start = payload.get("page_start")
        end = payload.get("page_end")
        try:
            pages = (
                range(int(start), int(end) + 1)
                if start and end
                else [int(getattr(chunk, "page_num", 0) or 0)]
            )
        except (TypeError, ValueError):
            continue
        for page in pages:
            if page > 0:
                page_values[page] = value
    return page_values


def lot_page_map_from_chunks(scoring_chunks: list[Any]) -> dict[int, str]:
    """(页 -> lot) 映射; 与账本 bridge 同源解析。"""
    from app.core.extractor.scoring.ledger.declaration_scope import resolve_chunk_lot

    return _page_map_from_chunks(
        scoring_chunks,
        lambda chunk, payload: resolve_chunk_lot(
            str(getattr(chunk, "content", "") or ""), payload.get("lot") or None
        ),
    )


def scheme_page_map_from_chunks(scoring_chunks: list[Any]) -> dict[int, str]:
    """(页 -> scheme) 映射; scheme 取 payload.scheme（审查V1: 多方案文档归属上下文）。"""
    return _page_map_from_chunks(
        scoring_chunks, lambda chunk, payload: payload.get("scheme") or None
    )


def _hint_segments(page_values: dict[int, str]) -> str:
    by_value: dict[str, list[int]] = {}
    for page, value in sorted(page_values.items()):
        by_value.setdefault(value, []).append(page)
    parts = [
        (f"第{pages[0]}页" if len(pages) == 1 else f"第{pages[0]}-{pages[-1]}页")
        + f"属「{value}」"
        for value, pages in by_value.items()
    ]
    return "、".join(parts)


def build_scope_page_hint(scoring_chunks: list[Any]) -> str:
    """多 scope 文档的读图提示片段; 单一 scope/无信息返回空串(零注入)。"""
    hints: list[str] = []
    lot_map = lot_page_map_from_chunks(scoring_chunks)
    if len(set(lot_map.values())) >= 2:
        hints.append(
            f"本文档为多标段评分: {_hint_segments(lot_map)}; "
            f"每个评分项的 group_path 第一个元素必须是其所属标段名。"
        )
    scheme_map = scheme_page_map_from_chunks(scoring_chunks)
    if len(set(scheme_map.values())) >= 2:
        hints.append(
            f"本文档含多套评分方案: {_hint_segments(scheme_map)}; "
            f"不同方案的评分项禁止混在同一组, 请按页归属分别输出。"
        )
    return (" " + " ".join(hints)) if hints else ""


def _backfill_field_from_pages(
    items: list[Any], page_values: dict[int, str], field: str
) -> list[Any]:
    """按 source_page 就近(±1)回填缺失字段; 已有值不覆盖。"""
    if len(set(page_values.values())) < 2:
        return items
    filled: list[Any] = []
    for item in items:
        if getattr(item, field, None):
            filled.append(item)
            continue
        pages = list(getattr(item, "source_pages", None) or [])
        value = None
        if pages:
            try:
                page = int(pages[0])
            except (TypeError, ValueError):
                page = 0
            value = (
                page_values.get(page)
                or page_values.get(page - 1)
                or page_values.get(page + 1)
            )
        if value is None:
            filled.append(item)
            continue
        try:
            filled.append(item.model_copy(update={field: value}))
        except (AttributeError, ValueError, TypeError):
            filled.append(item)
    return filled


def backfill_item_scopes_from_chunks(
    items: list[Any], scoring_chunks: list[Any]
) -> list[Any]:
    """候选缺 lot/scheme 时按 source_page 映射确定性回填(/ 审查V1)。

 仅多 scope 时生效; margin 扩页按 ±1 就近归属, 错配会被 per-scope 闭合裁判
 fail-visible, 不会静默凑分。已带值的候选不覆盖。
 """
    items = _backfill_field_from_pages(
        items, lot_page_map_from_chunks(scoring_chunks), "lot"
    )
    return _backfill_field_from_pages(
        items, scheme_page_map_from_chunks(scoring_chunks), "scheme"
    )


__all__ = [
    "backfill_item_scopes_from_chunks",
    "build_scope_page_hint",
    "lot_page_map_from_chunks",
    "scheme_page_map_from_chunks",
]
