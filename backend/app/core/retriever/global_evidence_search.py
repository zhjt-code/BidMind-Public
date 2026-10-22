"""全局证据候选排序

职责: 对满足过滤条件的全文档集合先按查询向量相似度排序、后限额截取候选池
不做: 过滤条件构造、rerank/去重收尾、上下文回拼、业务判定

设计背景:
旧实现先 ORDER BY page_num LIMIT 60 再 rerank —— 排序发生在截断之后,
长文档后置页的真实证据根本进不了候选池。本模块把排序前移到 SQL 层:
全过滤集合按 PGVector 余弦距离升序(相似度降序)取 limit*4, 再交由调用方
用现有 rerank/dedupe 收尾截到 limit。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from loguru import logger
from sqlalchemy import select

from app.models.chunk import Chunk

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 候选池相对最终限额的放大倍数(与 引入的 rerank 池倍数一致)
CANDIDATE_POOL_MULTIPLIER = 4


async def rank_global_candidates(
    db: "AsyncSession",
    base_conditions: Sequence,
    query_text: str | None,
    limit: int,
) -> list[Chunk]:
    """对过滤集合先排序后限额, 返回候选 chunk 列表(降序保序)。

    Args:
        db: 数据库会话
        base_conditions: SQLAlchemy 过滤条件(document_id/chunk_type/关键词等)
        query_text: 查询文本; 为空时保持旧行为(页码序取 limit)
        limit: 最终限额; query 提供时候选池为 limit*4

    Returns:
        list[Chunk]: 候选 chunk, query 路径按向量相似度降序
    """
    if not query_text:
        stmt = (
            select(Chunk)
            .where(*base_conditions)
            .order_by(Chunk.page_num, Chunk.chunk_index)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    pool_size = max(limit, 1) * CANDIDATE_POOL_MULTIPLIER
    try:
        from app.core.vectorizer.encode_gate import safe_encode

        query_embedding = (await safe_encode([query_text], initial_batch_size=1))[0]
    except Exception as err:
        logger.warning(
            f"[global_evidence_search] 查询编码失败, 降级为首尾轮转采样: {err}"
        )
        return await _sample_head_tail(db, base_conditions, pool_size)

    # 余弦距离升序 = 相似度降序, 排序在 LIMIT 之前由 DB 完成
    distance = Chunk.embedding.cosine_distance(query_embedding.tolist())
    stmt = (
        select(Chunk)
        .where(*base_conditions, Chunk.embedding.isnot(None))
        .order_by(distance)
        .limit(pool_size)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _sample_head_tail(
    db: "AsyncSession",
    base_conditions: Sequence,
    pool_size: int,
) -> list[Chunk]:
    """编码失败时的确定性降级采样: 首尾各取一批后轮转合并。

    禁止退回"全文页码前N"(那会系统性丢弃文档后部证据)。确定性规则:
    1. 头部查询: (page_num, chunk_index, id) 升序取 pool_size 条
    2. 尾部查询: 同键降序取 pool_size 条
    3. 轮转合并 head[0], tail[0], head[1], tail[1], ... 按 chunk id 去重,
       截到 pool_size —— 保证前部与后部页面都有代表
    """
    head_stmt = (
        select(Chunk)
        .where(*base_conditions)
        .order_by(Chunk.page_num, Chunk.chunk_index, Chunk.id)
        .limit(pool_size)
    )
    tail_stmt = (
        select(Chunk)
        .where(*base_conditions)
        .order_by(Chunk.page_num.desc(), Chunk.chunk_index.desc(), Chunk.id.desc())
        .limit(pool_size)
    )
    head = list((await db.execute(head_stmt)).scalars().all())
    tail = list((await db.execute(tail_stmt)).scalars().all())

    merged: list[Chunk] = []
    seen: set[int] = set()
    for i in range(max(len(head), len(tail))):
        for source in (head, tail):
            if i < len(source) and source[i].id not in seen:
                seen.add(source[i].id)
                merged.append(source[i])
        if len(merged) >= pool_size:
            break
    return merged[:pool_size]
