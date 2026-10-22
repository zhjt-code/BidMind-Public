"""证据检索结果的相关性重排与净化

职责: 对证据chunk检索结果做查询向量相关性重排、低信息密度惩罚与
 重复内容去重
不做: 类别先验维护、业务判定、LLM调用

设计背景:
图片证据按页码序截断与预算"先到先得"系统性偏向文档前部,无关扫描件
(身份证/学位证)挤占业绩合同的上下文预算。本模块以库内已有的chunk
向量做相似度重排(一次查询编码+一次IN查询),并对目录点线页等低信息
密度内容降权,使截断与预算分配和"与要求的相关性"对齐。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional, Sequence

from loguru import logger
from sqlalchemy import bindparam, text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.retriever.semantic_retriever import SearchResult


# 目录引导点线(全/半角句点、间隔号、省略号连续段)
_DOT_LEADER = re.compile(r"[\.·．…]{6,}")

# 低信息内容降权幅度(相似度减分)
LOW_INFO_PENALTY = 0.15

# 触发向量重排的最小结果数(低于该值仅去重,不做查询编码,控成本)
DEFAULT_MIN_RERANK_SIZE = 6


def is_low_information_content(content: str) -> bool:
    """判断内容是否为低信息密度(目录点线页/空壳页)。

    此类chunk因罗列全部章节标题而对任何查询都有虚高相似度,
    需要降权防止挤占真实证据的位置。
    """
    if not content or not content.strip():
        return True
    sample = content[:400]
    stripped = sample.lstrip()
    if stripped.startswith("目录") or stripped.startswith("目 录"):
        return True
    dot_chars = sum(len(m) for m in _DOT_LEADER.findall(sample))
    if dot_chars >= 12 and dot_chars / max(len(sample), 1) > 0.15:
        return True
    return False


def dedupe_by_content_prefix(
    results: Sequence["SearchResult"],
    prefix_len: int = 60,
) -> list["SearchResult"]:
    """按内容前缀去重,保留首个出现的结果。

    解析层会为同页多张图片生成近乎相同的标注段
    (如同页三条"注：页面左上角标注…"),去重避免浪费上下文预算。
    """
    deduped: list["SearchResult"] = []
    seen: set[str] = set()
    for result in results:
        key = re.sub(r"\s+", "", (result.content or ""))[:prefix_len]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def apply_low_info_penalty(
    results: list["SearchResult"],
    penalty: float = LOW_INFO_PENALTY,
) -> list["SearchResult"]:
    """对低信息密度结果降权后按相似度重排(纯函数,不查库)。

    用于主检索合并结果: 目录页/点线页降权而非剔除,保召回防误杀。
    """
    for result in results:
        if is_low_information_content(result.content):
            result.similarity = max(0.0, (result.similarity or 0.0) - penalty)
    return sorted(results, key=lambda r: r.similarity, reverse=True)


def _apply_penalty_and_sort(
    results: list["SearchResult"],
    similarities: dict[int, float],
    penalty: float = LOW_INFO_PENALTY,
) -> list["SearchResult"]:
    """纯排序: 以真实相似度回填结果,叠加低信息惩罚后降序排列。"""
    for result in results:
        sim = similarities.get(result.chunk_id)
        if sim is not None:
            result.similarity = sim

    def _score(result: "SearchResult") -> float:
        score = result.similarity or 0.0
        if is_low_information_content(result.content):
            score -= penalty
        return score

    return sorted(results, key=_score, reverse=True)


async def rerank_results_by_query(
    db: "AsyncSession",
    results: Sequence["SearchResult"],
    query_text: str,
    limit: Optional[int] = None,
    min_rerank_size: int = DEFAULT_MIN_RERANK_SIZE,
) -> list["SearchResult"]:
    """以查询向量对检索结果做相关性重排(去重 + 相似度回查 + 惩罚排序)。

    chunk向量已全量在库,仅需一次查询编码与一次 IN 相似度查询。
    任一环节失败时保持原序返回,绝不让重排失败阻断提取主流程。

    Args:
        db: 数据库会话
        results: 待重排的检索结果
        query_text: 查询文本(通常为招标要求原文)
        limit: 重排后截断数(None不截断)
        min_rerank_size: 触发向量重排的最小结果数,低于则仅去重

    Returns:
        list[SearchResult]: 重排后的结果列表
    """
    deduped = dedupe_by_content_prefix(results)
    if len(deduped) < max(2, min_rerank_size) or not query_text:
        return deduped[:limit] if limit else deduped

    chunk_ids = [r.chunk_id for r in deduped if r.chunk_id is not None]
    if not chunk_ids:
        return deduped[:limit] if limit else deduped

    try:
        from app.core.vectorizer.encode_gate import safe_encode

        query_embedding = (await safe_encode([query_text], initial_batch_size=1))[0]
        sql = text(
            "SELECT id, 1 - (embedding <=> :query_vector) AS similarity "
            "FROM chunks "
            "WHERE id IN :chunk_ids AND embedding IS NOT NULL"
        ).bindparams(bindparam("chunk_ids", expanding=True))
        rows = await db.execute(
            sql,
            {
                "query_vector": str(query_embedding.tolist()),
                "chunk_ids": chunk_ids,
            },
        )
        similarities = {int(cid): float(sim) for cid, sim in rows.all()}
    except Exception as err:
        logger.warning(f"[evidence_ranker] 相关性重排降级为原序: {err}")
        return deduped[:limit] if limit else deduped

    ranked = _apply_penalty_and_sort(deduped, similarities)
    return ranked[:limit] if limit else ranked
