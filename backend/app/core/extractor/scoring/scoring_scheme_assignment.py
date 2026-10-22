"""评分方案接线与写回（Task 5 E9）。

职责: 封装"从 payload 读取指纹/边界 → 推进 SchemeSegmenter → 盖章 items → 写回
 chunk payload"的完整流程，供 scoring_extractor 薄调用，保持 extractor <=800。
不做: 不解析表格、不调用 LLM、不访问 DB、不决定方案是否关闭。
"""
from __future__ import annotations

from typing import Any

from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter, stamp_items_scheme
from app.core.extractor.scoring.scoring_grid_payload import write_chunk_scope_metadata


def assign_and_stamp_scheme(
    segmenter: SchemeSegmenter,
    items: list[Any],
    chunk: Any,
    payload: dict[str, Any] | None,
    survivors: list[Any],
    volume_defs: dict[str, Any],
) -> str:
    """推进 segmenter，给 items 盖章 scheme，写回 chunk payload，返回 scheme 标识。

    职责: 整合"边界/指纹消费 → scheme 赋值 → items 盖章 → payload 写回"四步，
          供 scoring_extractor 的置信路径和 LLM 兜底路径共用。
    参数:
        segmenter: 有状态的方案切分机；由 extractor 持有并跨 chunk 传递。
        items: 本 chunk 解析出的评分项列表（就地盖章）。
        chunk: 原始 chunk 对象（TextChunk 或 ORM Chunk）；用于写回 payload。
        payload: chunk 已解析的 scoring_payload dict（可为 None）。
        survivors: 迄今所有存活评分项（用于方案闭合判断）。
        volume_defs: 已收集的卷定义（用于方案闭合判断）。
    返回: 分配到的 scheme 标识字符串（如 "scheme_1"）。
    """
    scheme = segmenter.assign_scheme_for_chunk(
        chunk_fingerprint=(payload or {}).get("table_fingerprint"),
        incoming_item_count=len(items),
        current_survivors=survivors,
        volume_defs=volume_defs,
        scheme_boundary_id=(payload or {}).get("scheme_boundary_id"),
    )
    stamp_items_scheme(items, scheme)
    write_chunk_scope_metadata(chunk, scheme=scheme)
    return scheme


__all__ = ["assign_and_stamp_scheme"]
