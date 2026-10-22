"""
职责: 用确定性候选图压缩招标要求合并去重的 LLM 输入规模。
不做: 不做合并决策、不修改条目内容、不替代终检去重。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Callable

import numpy as np
from loguru import logger

from app.core.config import settings
from app.core.extractor.tender_extractor_parts.dedup_normalize import normalize_dedup_key
from app.schemas.requirement import ExtractedRequirement


@dataclass(frozen=True)
class CandidateGateResult:
    """候选图构建结果。"""

    components: list[list[int]]
    singletons: list[int]
    semantic_edges: int = 0
    lexical_edges: int = 0
    canonical_edges: int = 0
    fallback_used: bool = False


@dataclass(frozen=True)
class CandidatePartition:
    """按候选图切分后的要求集合。"""

    candidate_groups: list[list[ExtractedRequirement]]
    bypass_requirements: list[ExtractedRequirement]
    gate_result: CandidateGateResult


class CandidateEmbeddingBuffer:
    """后台批量向量化缓冲。

    职责: 在分块提取阶段提前启动候选门控所需的 embedding 编码。
    不做: 不决定哪些条目合并、不持久化向量、不管理 LLM 调用。
    """

    def __init__(self) -> None:
        self._texts: list[str] = []
        self._task_texts: list[str] = []
        self._task: asyncio.Task[np.ndarray | None] | None = None
        self.wait_ms: int = 0

    def submit(self, requirements: list[ExtractedRequirement]) -> None:
        if not settings.TENDER_INCREMENTAL_EMBED_ENABLED or not requirements:
            return
        self._texts.extend(req.description or "" for req in requirements)
        if self._task is None or self._task.done():
            texts = list(self._texts)
            self._task_texts = texts
            self._task = asyncio.create_task(_encode_texts_async(texts))

    async def embeddings_or_none(self, requirements: list[ExtractedRequirement]) -> np.ndarray | None:
        if not requirements:
            return None
        requested_texts = [req.description or "" for req in requirements]
        start = perf_counter()
        if self._task is not None:
            try:
                result = await self._task
                if (
                    result is not None
                    and len(result) == len(requirements)
                    and self._task_texts == requested_texts
                ):
                    self.wait_ms += int((perf_counter() - start) * 1000)
                    return result
            except Exception as exc:
                logger.warning("增量向量化结果读取失败，改为按需编码: {}", exc)
        result = await _encode_texts_async(requested_texts)
        self.wait_ms += int((perf_counter() - start) * 1000)
        return result


def build_candidate_groups(
    requirements: list[ExtractedRequirement],
    *,
    embeddings: np.ndarray | None = None,
    similarity_threshold: float | None = None,
    keyword_jaccard_threshold: float | None = None,
) -> CandidateGateResult:
    """基于语义/关键词/canonical_key 三通道构建候选重复分量。"""
    count = len(requirements)
    if count <= 1:
        return CandidateGateResult(components=[], singletons=list(range(count)))

    parent = list(range(count))

    def find(idx: int) -> int:
        while parent[idx] != idx:
            parent[idx] = parent[parent[idx]]
            idx = parent[idx]
        return idx

    def union(left: int, right: int) -> bool:
        root_left, root_right = find(left), find(right)
        if root_left == root_right:
            return False
        parent[root_right] = root_left
        return True

    semantic_edges = 0
    lexical_edges = 0
    canonical_edges = 0
    sim_threshold = (
        settings.TENDER_CANDIDATE_SIM_THRESHOLD
        if similarity_threshold is None else similarity_threshold
    )
    kw_threshold = (
        settings.TENDER_CANDIDATE_KEYWORD_JACCARD
        if keyword_jaccard_threshold is None else keyword_jaccard_threshold
    )

    if embeddings is not None and len(embeddings) == count:
        matrix = np.asarray(embeddings, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = np.divide(matrix, np.maximum(norms, 1e-12))
        sims = matrix @ matrix.T
    else:
        sims = None

    keyword_sets = [_keyword_set(req) for req in requirements]
    canonical_keys = [
        (req.canonical_key or normalize_dedup_key(req.description)) for req in requirements
    ]

    for left in range(count):
        for right in range(left + 1, count):
            linked = False
            if sims is not None and float(sims[left, right]) >= sim_threshold:
                linked = True
                semantic_edges += 1
            elif (
                canonical_keys[left]
                and canonical_keys[left] == canonical_keys[right]
            ):
                linked = True
                canonical_edges += 1
            elif _jaccard(keyword_sets[left], keyword_sets[right]) >= kw_threshold:
                linked = True
                lexical_edges += 1
            if linked:
                union(left, right)

    grouped: dict[int, list[int]] = {}
    for idx in range(count):
        grouped.setdefault(find(idx), []).append(idx)

    components = [items for items in grouped.values() if len(items) > 1]
    singletons = [items[0] for items in grouped.values() if len(items) == 1]
    components.sort(key=lambda items: items[0])
    singletons.sort()
    return CandidateGateResult(
        components=components,
        singletons=singletons,
        semantic_edges=semantic_edges,
        lexical_edges=lexical_edges,
        canonical_edges=canonical_edges,
    )


def partition_requirements_by_candidates(
    requirements: list[ExtractedRequirement],
    *,
    embeddings: np.ndarray | None = None,
) -> CandidatePartition:
    """把要求切成候选组和可直接绕过 LLM 的孤立条目。"""
    if not settings.TENDER_CANDIDATE_GATE_ENABLED or len(requirements) <= 1:
        result = CandidateGateResult(components=[list(range(len(requirements)))], singletons=[])
        return CandidatePartition([requirements], [], result)
    result = build_candidate_groups(requirements, embeddings=embeddings)
    return CandidatePartition(
        candidate_groups=[[requirements[i] for i in group] for group in result.components],
        bypass_requirements=[requirements[i] for i in result.singletons],
        gate_result=result,
    )


async def partition_requirements_with_embeddings(
    requirements: list[ExtractedRequirement],
    *,
    embedding_buffer: CandidateEmbeddingBuffer | None = None,
    fallback_recorder: Callable[..., None] | None = None,
) -> CandidatePartition:
    """带 embedding 预取/降级的候选分区入口。"""
    embeddings = None
    fallback_used = False
    if settings.TENDER_CANDIDATE_GATE_ENABLED and requirements:
        try:
            if embedding_buffer is not None:
                embeddings = await embedding_buffer.embeddings_or_none(requirements)
            else:
                embeddings = await _encode_texts_async([req.description or "" for req in requirements])
        except Exception as exc:
            fallback_used = True
            logger.warning("候选门控 embedding 编码失败，降级词法通道: {}", exc)
            if fallback_recorder is not None:
                fallback_recorder("candidate_gate", "embedding_unavailable", error=str(exc)[:240])
    partition = partition_requirements_by_candidates(requirements, embeddings=embeddings)
    if fallback_used:
        partition = CandidatePartition(
            candidate_groups=partition.candidate_groups,
            bypass_requirements=partition.bypass_requirements,
            gate_result=CandidateGateResult(
                components=partition.gate_result.components,
                singletons=partition.gate_result.singletons,
                semantic_edges=partition.gate_result.semantic_edges,
                lexical_edges=partition.gate_result.lexical_edges,
                canonical_edges=partition.gate_result.canonical_edges,
                fallback_used=True,
            ),
        )
    return partition


# 同会话文本指纹缓存：复用粒度是单条文本，不是整表列表全等。
# ponytail: 进程内 FIFO 上界 4096；升级路径为 LRU + 会话作用域（提取任务结束驱逐）。
_CANDIDATE_EMBED_CACHE_MAX = 4096
_candidate_embed_cache: dict[str, np.ndarray] = {}


def clear_candidate_embed_cache() -> None:
    """清空候选门控 embedding 指纹缓存（测试隔离用）。"""
    _candidate_embed_cache.clear()


def candidate_embed_cache_size() -> int:
    """当前缓存条目数。"""
    return len(_candidate_embed_cache)


def _cache_put(text: str, vector: np.ndarray) -> None:
    if text in _candidate_embed_cache:
        _candidate_embed_cache[text] = vector
        return
    while len(_candidate_embed_cache) >= _CANDIDATE_EMBED_CACHE_MAX:
        _candidate_embed_cache.pop(next(iter(_candidate_embed_cache)))
    _candidate_embed_cache[text] = vector


async def prefetch_candidate_embeddings(texts: list[str]) -> np.ndarray | None:
    """预热候选门控所需向量（走文本指纹缓存）。

    职责: 在反思 LLM 期间提前编码，供后续候选门命中缓存。
    不做: 不做合并决策、不替代 encode_gate 安全入口。
    """
    return await _encode_texts_async(texts)


async def _encode_texts_async(texts: list[str]) -> np.ndarray | None:
    """经全局 encode_gate 安全入口编码；不再直接调用模型。

 候选门控曾是全代码库唯一绕过 ``encode_gate.safe_encode`` 全局
 ``encode_lock`` 串行闸门的 embedding 调用点：并发的招标要求提取
 任务会同时对同一 GPU 模型发起编码，触发 CUDA 设备端未定义行为
 (``unspecified launch failure``)。改为复用 ``safe_encode`` 后，
 与文档向量化/素材向量化/标书匹配共享同一把全局互斥锁，并自动获得
 OOM/stall 检测与 CPU 回退能力。: 按文本指纹复用向量。分类合并子集与二次合并全表共享同一缓存，
 只对 miss 文本调用 ``safe_encode``；全命中时跳过编码闸门。
 """
    if not texts:
        return None

    hits: list[np.ndarray | None] = [
        _candidate_embed_cache.get(text) for text in texts
    ]
    miss_indices = [idx for idx, vector in enumerate(hits) if vector is None]
    if not miss_indices:
        return np.stack(hits)

    miss_texts: list[str] = []
    seen_miss: set[str] = set()
    for idx in miss_indices:
        text = texts[idx]
        if text in seen_miss:
            continue
        seen_miss.add(text)
        miss_texts.append(text)

    from app.core.vectorizer.encode_gate import safe_encode

    encoded = await safe_encode(miss_texts, initial_batch_size=32)
    if encoded is None or len(encoded) != len(miss_texts):
        return None

    for offset, text in enumerate(miss_texts):
        _cache_put(text, np.asarray(encoded[offset], dtype=np.float32).copy())

    assembled: list[np.ndarray] = []
    for idx, text in enumerate(texts):
        vector = hits[idx] if hits[idx] is not None else _candidate_embed_cache.get(text)
        if vector is None:
            return None
        assembled.append(vector)
    return np.stack(assembled)


def _keyword_set(req: ExtractedRequirement) -> set[str]:
    return {
        normalize_dedup_key(value)
        for value in (req.keywords or [])
        if normalize_dedup_key(value)
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
