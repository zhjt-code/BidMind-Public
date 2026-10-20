"""向量相似度批量计算模块

核心优化：一次 DB 查询加载所有 chunk embedding → numpy 矩阵乘法批量计算。
相比 PGVector 逐条查询：5 个文件各 100 chunks → <2 秒（vs 100+ 秒）。

公开接口：
  batch_compute_similarity(db, bid_doc_ids, tender_doc_id) -> SimilarityResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.core.config import settings
from app.core.collusion.knowledge_base import is_known_normal_similarity


@dataclass
class SimilarityResult:
    """向量预筛选结果

    Attributes:
        pair_scores: {(doc_a_id, doc_b_id): max_similarity} — 文件对层级得分
        high_pairs:   相似度 >= COLLUSION_SIMILARITY_HIGH 的文件对列表
        medium_pairs: 相似度在 [COLLUSION_SIMILARITY_MEDIUM, HIGH) 的文件对列表
        low_pairs:    相似度 < COLLUSION_SIMILARITY_MEDIUM 的文件对列表
        similar_chunks: {(doc_a_id, doc_b_id): [{score, chunk_a_id, chunk_b_id, text_a, text_b, page_a, page_b}]}
        tender_sourced_pairs: 因招标白名单过滤而排除的 chunk 对集合 {(chunk_a_id, chunk_b_id)}
    """

    pair_scores: dict[tuple[int, int], float] = field(default_factory=dict)
    candidate_scores: dict[tuple[int, int], float] = field(default_factory=dict)
    high_pairs: list[tuple[int, int, float]] = field(default_factory=list)
    medium_pairs: list[tuple[int, int, float]] = field(default_factory=list)
    low_pairs: list[tuple[int, int, float]] = field(default_factory=list)
    similar_chunks: dict[tuple[int, int], list[dict]] = field(default_factory=dict)
    tender_sourced_pairs: set[tuple[int, int]] = field(default_factory=set)
    coverage_ratios: dict[tuple[int, int], float] = field(default_factory=dict)
    max_continuous_spans: dict[tuple[int, int], int] = field(default_factory=dict)
    avg_literal_scores: dict[tuple[int, int], float] = field(default_factory=dict)
    tender_sourced_ratios: dict[tuple[int, int], float] = field(default_factory=dict)


async def batch_compute_similarity(
    db: AsyncSession,
    bid_doc_ids: list[int],
    tender_doc_id: Optional[int] = None,
    top_k_chunks: int = 20,
) -> SimilarityResult:
    """批量计算投标文件间的向量相似度

    流程：
    1. 一次 DB 查询加载所有相关 chunk 的 embedding（bid 文件 + 可选 tender）
    2. 若有招标文件，先计算每个 bid chunk 与 tender chunks 的相似度，
       标记 tender_sourced chunk（两个 bid chunk 都来源于 tender 时排除）
    3. 对所有 bid 文件对，用 numpy 矩阵乘法计算 chunk 级相似度矩阵
    4. 按文件对汇总：max 相似度 + Top-K 相似 chunk 对

    Args:
        db: 异步数据库会话
        bid_doc_ids: 投标文件文档ID列表（2-5 个）
        tender_doc_id: 招标文件文档ID（可选，用于白名单过滤）
        top_k_chunks: 每对文件保留前 K 个最相似 chunk 对

    Returns:
        SimilarityResult
    """
    if len(bid_doc_ids) < 2:
        raise ValueError("至少需要 2 个投标文件才能计算相似度")

    result = SimilarityResult()

    # ── 1. 加载所有 chunk embeddings ──────────────────────────────────────────
    all_doc_ids = list(bid_doc_ids)
    if tender_doc_id is not None:
        all_doc_ids.append(tender_doc_id)

    logger.info(f"[Similarity] 加载 {len(all_doc_ids)} 个文件的 chunk embeddings...")

    rows = await db.execute(
        select(
            Chunk.id,
            Chunk.document_id,
            Chunk.embedding,
            Chunk.content,
            Chunk.page_num,
        )
        .where(Chunk.document_id.in_(all_doc_ids))
        .where(Chunk.embedding.is_not(None))
        .order_by(Chunk.document_id, Chunk.chunk_index)
    )
    chunks_raw = rows.fetchall()

    if not chunks_raw:
        logger.warning("[Similarity] 没有找到任何 chunk embedding，跳过相似度计算")
        _fill_empty_results(result, bid_doc_ids)
        return result

    # 按文档 ID 分组
    doc_chunks: dict[int, list[dict]] = {}
    for row in chunks_raw:
        doc_id = row.document_id
        if doc_id not in doc_chunks:
            doc_chunks[doc_id] = []
        doc_chunks[doc_id].append({
            "id": row.id,
            "doc_id": doc_id,
            "embedding": row.embedding,  # pgvector 返回 list[float]
            "content": row.content or "",
            "page_num": row.page_num,
        })

    total_chunks = sum(len(v) for v in doc_chunks.values())
    logger.info(f"[Similarity] 共加载 {total_chunks} 个 chunk embeddings")

    # ── 2. 招标白名单过滤 ──────────────────────────────────────────────────────
    tender_sourced_bid_chunk_ids: set[int] = set()  # 与招标高度相似的 bid chunk ID

    if tender_doc_id is not None and tender_doc_id in doc_chunks:
        tender_chunks = doc_chunks[tender_doc_id]
        tender_embs = _to_numpy([c["embedding"] for c in tender_chunks])
        tender_embs_normed = _normalize(tender_embs)

        threshold = settings.COLLUSION_TENDER_WHITELIST_THRESHOLD

        for bid_doc_id in bid_doc_ids:
            if bid_doc_id not in doc_chunks:
                continue
            bid_chunks = doc_chunks[bid_doc_id]
            bid_embs = _to_numpy([c["embedding"] for c in bid_chunks])
            bid_embs_normed = _normalize(bid_embs)

            # shape: (n_bid_chunks, n_tender_chunks)
            sim_matrix = bid_embs_normed @ tender_embs_normed.T
            max_tender_sim = np.max(sim_matrix, axis=1)  # 每个 bid chunk 与 tender 的最大相似度

            for i, chunk in enumerate(bid_chunks):
                if max_tender_sim[i] >= threshold:
                    tender_sourced_bid_chunk_ids.add(chunk["id"])

        result.tender_sourced_pairs = set()
        logger.info(
            f"[Similarity] 招标白名单过滤: {len(tender_sourced_bid_chunk_ids)} 个 bid chunk "
            f"被标记为 tender_sourced（相似度>={threshold}）"
        )
    else:
        logger.info("[Similarity] 无招标文件参照，跳过白名单过滤")

    # ── 3. 计算投标文件对的相似度 ─────────────────────────────────────────────
    from itertools import combinations

    bid_pairs = list(combinations(bid_doc_ids, 2))
    logger.info(f"[Similarity] 计算 {len(bid_pairs)} 对文件的相似度...")

    for doc_a_id, doc_b_id in bid_pairs:
        chunks_a = doc_chunks.get(doc_a_id, [])
        chunks_b = doc_chunks.get(doc_b_id, [])

        if not chunks_a or not chunks_b:
            logger.warning(f"[Similarity] 文件对 ({doc_a_id}, {doc_b_id}) 缺少 embedding，跳过")
            result.pair_scores[(doc_a_id, doc_b_id)] = 0.0
            result.low_pairs.append((doc_a_id, doc_b_id, 0.0))
            continue

        embs_a = _to_numpy([c["embedding"] for c in chunks_a])
        embs_b = _to_numpy([c["embedding"] for c in chunks_b])
        embs_a_normed = _normalize(embs_a)
        embs_b_normed = _normalize(embs_b)

        # (n_a, n_b) 相似度矩阵
        sim_matrix = embs_a_normed @ embs_b_normed.T

        tender_filtered_pairs = 0
        # 排除双方都是 tender_sourced 的 chunk 对
        for i, ca in enumerate(chunks_a):
            if ca["id"] in tender_sourced_bid_chunk_ids:
                for j, cb in enumerate(chunks_b):
                    if cb["id"] in tender_sourced_bid_chunk_ids:
                        sim_matrix[i, j] = 0.0  # 屏蔽白名单对
                        tender_filtered_pairs += 1

        pair_peak_sim = float(np.max(sim_matrix)) if sim_matrix.size > 0 else 0.0

        # 分级
        high_thresh = settings.COLLUSION_SIMILARITY_HIGH
        med_thresh = settings.COLLUSION_SIMILARITY_MEDIUM

        row_max = np.max(sim_matrix, axis=1) if sim_matrix.shape[1] > 0 else np.array([])
        pair_p90 = float(np.percentile(row_max, 90)) if row_max.size > 0 else 0.0
        pair_topk = float(np.mean(np.sort(row_max)[-min(5, len(row_max)):])) if row_max.size > 0 else 0.0
        pair_score = max(pair_peak_sim * 0.4 + pair_topk * 0.6, pair_p90)
        pair_score = round(max(0.0, min(pair_score, 1.0)), 3)
        result.pair_scores[(doc_a_id, doc_b_id)] = pair_score

        if pair_score >= high_thresh:
            result.high_pairs.append((doc_a_id, doc_b_id, pair_score))
        elif pair_score >= med_thresh:
            result.medium_pairs.append((doc_a_id, doc_b_id, pair_score))
        else:
            result.low_pairs.append((doc_a_id, doc_b_id, pair_score))

        # 覆盖率: 文件A中有多少chunk与文件B存在高相似匹配
        n_similar_rows = int(np.sum(row_max >= high_thresh)) if row_max.size > 0 else 0
        coverage = n_similar_rows / len(chunks_a) if chunks_a else 0.0
        result.coverage_ratios[(doc_a_id, doc_b_id)] = round(coverage, 3)

        # 连续性: 最长连续相似段（沿近对角线方向搜索）
        max_span = _find_max_continuous_span(
            sim_matrix, threshold=high_thresh,
        )
        result.max_continuous_spans[(doc_a_id, doc_b_id)] = max_span

        # Top-K 相似 chunk 对
        # 片段证据下限改用独立阈值 COLLUSION_CHUNK_PAIR_MIN_SCORE(默认 0.6)，
        # 不再复用 med_thresh(0.4)。对级分流(high/medium/low_pairs)仍由 med_thresh
        # 决定，故此处收紧只影响"喂给 LLM / 落库展示的片段对"，不缩窄 LLM 覆盖面。
        top_k_pairs = _extract_top_k_pairs(
            sim_matrix, chunks_a, chunks_b, top_k=top_k_chunks,
            min_score=settings.COLLUSION_CHUNK_PAIR_MIN_SCORE,
        )
        if top_k_pairs:
            result.similar_chunks[(doc_a_id, doc_b_id)] = top_k_pairs
        avg_literal = (
            sum(p.get("literal_score", 0.0) for p in top_k_pairs) / len(top_k_pairs)
            if top_k_pairs else 0.0
        )
        boilerplate_ratio = (
            sum(1 for p in top_k_pairs if p.get("boilerplate_flag")) / len(top_k_pairs)
            if top_k_pairs else 0.0
        )
        tender_ratio = tender_filtered_pairs / float(max(len(chunks_a) * len(chunks_b), 1))
        result.avg_literal_scores[(doc_a_id, doc_b_id)] = round(avg_literal, 3)
        result.tender_sourced_ratios[(doc_a_id, doc_b_id)] = round(tender_ratio, 3)
        span_score = min(max_span / 6.0, 1.0)
        candidate_score = (
            pair_score * 0.45
            + coverage * 0.25
            + span_score * 0.15
            + avg_literal * 0.15
            - boilerplate_ratio * 0.20
            - tender_ratio * 0.15
        )
        result.candidate_scores[(doc_a_id, doc_b_id)] = round(
            max(0.0, min(candidate_score, 1.0)),
            3,
        )

    logger.info(
        f"[Similarity] 完成: 高相似={len(result.high_pairs)} 对, "
        f"中相似={len(result.medium_pairs)} 对, "
        f"低相似={len(result.low_pairs)} 对"
    )

    return result


# ── 私有辅助函数 ──────────────────────────────────────────────────────────────

def _to_numpy(embeddings: list) -> np.ndarray:
    """将 pgvector 返回的嵌入列表转换为 numpy 数组"""
    return np.array(embeddings, dtype=np.float32)


def _normalize(matrix: np.ndarray) -> np.ndarray:
    """L2 归一化，使余弦相似度等价于点积"""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-8)  # 防止除零
    return matrix / norms


def _compute_literal_score(text_a: str, text_b: str) -> float:
    """计算两段文本的字面重合度（SequenceMatcher ratio）

    高向量相似度 + 低字面重合度 → 同义表述/行业通用内容（非抄袭）
    高向量相似度 + 高字面重合度 → 复制粘贴（强异常信号）
    """
    from difflib import SequenceMatcher

    if not text_a or not text_b:
        return 0.0
    a = text_a[:500]
    b = text_b[:500]
    return SequenceMatcher(None, a, b).ratio()


def _extract_top_k_pairs(
    sim_matrix: np.ndarray,
    chunks_a: list[dict],
    chunks_b: list[dict],
    top_k: int,
    min_score: float = 0.4,
) -> list[dict]:
    """从相似度矩阵中提取 Top-K 相似 chunk 对（含字面重合度双轨判据）"""
    if sim_matrix.size == 0:
        return []

    rows, cols = np.where(sim_matrix >= min_score)
    if len(rows) == 0:
        return []

    scores = sim_matrix[rows, cols]
    sort_idx = np.argsort(-scores)
    top_idx = sort_idx[:top_k]

    results = []
    seen_pairs = set()

    for idx in top_idx:
        i, j = int(rows[idx]), int(cols[idx])
        score = float(sim_matrix[i, j])

        key_a = chunks_a[i]["id"]
        key_b = chunks_b[j]["id"]
        if key_a in seen_pairs or key_b in seen_pairs:
            continue
        seen_pairs.add(key_a)
        seen_pairs.add(key_b)

        text_a = chunks_a[i]["content"]
        text_b = chunks_b[j]["content"]
        literal = _compute_literal_score(text_a, text_b)
        kb_rule_match = is_known_normal_similarity(text_a) or is_known_normal_similarity(text_b)
        boilerplate = kb_rule_match or (score >= 0.8 and literal < 0.4)

        results.append({
            "score": score,
            "literal_score": round(literal, 3),
            "boilerplate_flag": boilerplate,
            "kb_rule_match": kb_rule_match,
            "chunk_a_id": chunks_a[i]["id"],
            "chunk_b_id": chunks_b[j]["id"],
            "text_a": text_a,
            "text_b": text_b,
            "page_a": chunks_a[i]["page_num"],
            "page_b": chunks_b[j]["page_num"],
        })

    return results


def _find_max_continuous_span(
    sim_matrix: np.ndarray,
    threshold: float = 0.7,
) -> int:
    """沿矩阵近对角线方向搜索最长连续高相似段

    如果 chunk A[i]~B[j] 且 A[i+1]~B[j+1] 都超过阈值，
    认为是连续的抄袭段落。返回最长链的长度。
    """
    if sim_matrix.size == 0:
        return 0
    n_a, n_b = sim_matrix.shape
    if n_a == 0 or n_b == 0:
        return 0

    max_span = 0
    hit = sim_matrix >= threshold

    for start_j in range(n_b):
        span = 0
        i, j = 0, start_j
        while i < n_a and j < n_b:
            if hit[i, j]:
                span += 1
                max_span = max(max_span, span)
            else:
                span = 0
            i += 1
            j += 1

    for start_i in range(1, n_a):
        span = 0
        i, j = start_i, 0
        while i < n_a and j < n_b:
            if hit[i, j]:
                span += 1
                max_span = max(max_span, span)
            else:
                span = 0
            i += 1
            j += 1

    return max_span


def _fill_empty_results(result: SimilarityResult, bid_doc_ids: list[int]) -> None:
    """当无 embedding 时填充空结果（防止下游崩溃）"""
    from itertools import combinations
    for a, b in combinations(bid_doc_ids, 2):
        result.pair_scores[(a, b)] = 0.0
        result.candidate_scores[(a, b)] = 0.0
        result.low_pairs.append((a, b, 0.0))
