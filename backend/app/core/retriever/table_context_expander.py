"""表格父块上下文回拼

职责: 为表格行种子补齐同 parent_block_id 的全部兄弟行, 多父块轮转分配预算
不做: 跨页续表拼接(不同 parent_block_id 禁止合并)、页内普通邻居扩展、rerank

设计背景:
旧实现对同 parent_block_id 的表格行仍施加 chunk_index 半径, 同表远行取不到;
且预算按种子顺序先到先得, 前序种子可耗尽预算饿死后序种子。本模块:
兄弟行查询不受半径限制; 多个父块按种子相似度降序轮转取行, 每轮各取一行,
直到预算耗尽 —— 禁止首父块吃满全部预算。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from sqlalchemy import select

from app.core.retriever.semantic_retriever import SearchResult
from app.models.chunk import Chunk

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 参与父块回拼的表格行类型; 其余 chunk 类型即使带 parent_block_id
# (如段落语义块/图片证据)仍走页内邻居半径逻辑
TABLE_ROW_CHUNK_TYPES = ("table_row_with_header", "table")


def is_table_row_seed(chunk: Chunk) -> bool:
    """判断种子是否为可回拼的表格行(表格类型且有父块ID)。"""
    return (
        getattr(chunk, "chunk_type", "") in TABLE_ROW_CHUNK_TYPES
        and bool(getattr(chunk, "parent_block_id", None))
    )


async def expand_table_parent_context(
    db: "AsyncSession",
    document_id: int,
    seeds: Sequence[tuple[Chunk, float]],
    budget: int,
) -> list[SearchResult]:
    """补齐表格行种子的同父块兄弟行(排除种子自身), 轮转分配预算。

    刻意边界: 不同 parent_block_id 的表格(含跨页续表)禁止合并,
    每个父块单独等值查询、单独取行, 本轮不做跨页续表自动拼接。

    Args:
        db: 数据库会话
        document_id: 文档ID
        seeds: (表格行chunk, 种子相似度) 序列; 非表格行种子被忽略
        budget: 新增行总预算

    Returns:
        list[SearchResult]: 新增兄弟行(similarity=0.0), 不含种子自身
    """
    if budget <= 0 or not seeds:
        return []

    # 按父块分组, 记录各父块的最高种子相似度用于排序
    parent_best: dict[str, float] = {}
    seed_ids: set[int] = set()
    for chunk, similarity in seeds:
        if not is_table_row_seed(chunk):
            continue
        seed_ids.add(chunk.id)
        parent_id = chunk.parent_block_id
        if similarity > parent_best.get(parent_id, float("-inf")):
            parent_best[parent_id] = similarity
    if not parent_best:
        return []

    # 相似度降序确定父块优先级; 同分按父块ID字典序保证确定性
    ordered_parents = sorted(parent_best, key=lambda pid: (-parent_best[pid], pid))

    # 每个父块单独查询全部兄弟行, 不施加 chunk_index 半径
    rows_by_parent: dict[str, list[Chunk]] = {}
    for parent_id in ordered_parents:
        stmt = (
            select(Chunk)
            .where(
                Chunk.document_id == document_id,
                Chunk.parent_block_id == parent_id,
            )
            .order_by(Chunk.chunk_index)
        )
        result = await db.execute(stmt)
        rows_by_parent[parent_id] = [
            row for row in result.scalars().all() if row.id not in seed_ids
        ]

    # 轮转分配: 每轮每父块各取一行, 直到预算耗尽或全部取完
    added: list[SearchResult] = []
    cursor = {pid: 0 for pid in ordered_parents}
    while len(added) < budget:
        progressed = False
        for parent_id in ordered_parents:
            rows = rows_by_parent[parent_id]
            idx = cursor[parent_id]
            if idx >= len(rows):
                continue
            cursor[parent_id] = idx + 1
            added.append(SearchResult.from_chunk(rows[idx], similarity=0.0))
            progressed = True
            if len(added) >= budget:
                break
        if not progressed:
            break
    return added
