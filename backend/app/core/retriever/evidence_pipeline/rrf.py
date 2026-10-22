"""通道排名融合 

职责: 用 Reciprocal Rank Fusion 合并多通道召回, 保留各命中原相似度供后续 cosine rerank
不做: 数据库查询、向量编码、覆盖评估、LLM 调用
"""

from __future__ import annotations

from typing import Sequence

from app.core.retriever.evidence_pipeline.contracts import ChannelHits
from app.core.retriever.semantic_retriever import SearchResult

RRF_K = 60


def fuse_channel_rankings(
    channels: Sequence[ChannelHits],
    *,
    k: int = RRF_K,
    limit: int | None = None,
) -> list[SearchResult]:
    """按 RRF 合并通道. 同分时保留先出现通道的结果对象(角色不被覆盖)。"""
    scores: dict[int, float] = {}
    best: dict[int, SearchResult] = {}
    for hits in channels:
        for rank, result in enumerate(hits.results or [], start=1):
            chunk_id = result.chunk_id
            if chunk_id is None:
                continue
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            if chunk_id not in best:
                best[chunk_id] = result

    ordered = sorted(
        best.values(),
        key=lambda item: (scores.get(item.chunk_id, 0.0), item.similarity or 0.0),
        reverse=True,
    )
    if limit is None:
        return ordered
    return ordered[: max(0, limit)]
