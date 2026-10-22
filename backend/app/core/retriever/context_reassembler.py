"""检索命中后的上下文回拼。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.retriever.semantic_retriever import SearchResult
from app.core.retriever.table_context_expander import (
    expand_table_parent_context,
    is_table_row_seed,
)
from app.models.chunk import Chunk


@dataclass
class ReassembledContext:
    """回拼后的上下文。"""

    primary_results: list[SearchResult]
    image_results: list[SearchResult]
    added_neighbors: list[SearchResult]


class ContextReassembler:
    """根据命中 chunk 的邻接关系补充审核上下文。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.neighbor_radius = max(0, int(settings.VECTOR_CONTEXT_NEIGHBOR_RADIUS))
        self.max_expansions = max(0, int(settings.VECTOR_CONTEXT_MAX_EXPANSIONS))

    async def expand(
        self,
        document_id: int,
        search_results: list[SearchResult],
        image_chunks: list[SearchResult] | None = None,
        *,
        is_mandatory: bool = False,
    ) -> ReassembledContext:
        """为正文检索结果补充相邻 chunk 与同页证据。: 表格行种子委托 table_context_expander 补齐同父块全部
 兄弟行(不受 chunk_index 半径限制, 独立预算 max_expansions);
 普通种子保持页内半径邻居查询, 预算改为多种子轮转公平分配,
 禁止首种子耗尽预算饿死后序种子。
 """
        if not search_results:
            return ReassembledContext([], image_chunks or [], [])

        radius = self.neighbor_radius + (1 if is_mandatory else 0)
        max_expansions = self.max_expansions + (2 if is_mandatory else 0)

        seed_ids = {item.chunk_id for item in search_results}
        seed_similarity = {
            item.chunk_id: item.similarity or 0.0 for item in search_results
        }

        stmt = (
            select(Chunk)
            .where(Chunk.document_id == document_id, Chunk.id.in_(list(seed_ids)))
            .order_by(Chunk.chunk_index)
        )
        result = await self.db.execute(stmt)
        chunks = list(result.scalars().all())

        table_seeds = [
            (chunk, seed_similarity.get(chunk.id, 0.0))
            for chunk in chunks
            if is_table_row_seed(chunk)
        ]
        text_seeds = [chunk for chunk in chunks if not is_table_row_seed(chunk)]

        added: list[SearchResult] = []
        candidate_ids: set[int] = set(seed_ids)
        if table_seeds:
            table_rows = await expand_table_parent_context(
                self.db, document_id, table_seeds, budget=max_expansions
            )
            for row in table_rows:
                if row.chunk_id in candidate_ids:
                    continue
                candidate_ids.add(row.chunk_id)
                added.append(row)

        neighbor_lists: list[list[Chunk]] = []
        for chunk in text_seeds:
            low = max(0, int(chunk.chunk_index) - radius)
            high = int(chunk.chunk_index) + radius
            clauses = [Chunk.page_num == chunk.page_num]
            if chunk.parent_block_id:
                clauses.append(Chunk.parent_block_id == chunk.parent_block_id)
            neighbor_stmt = (
                select(Chunk)
                .where(
                    Chunk.document_id == document_id,
                    or_(*clauses),
                    Chunk.chunk_index >= low,
                    Chunk.chunk_index <= high,
                )
                .order_by(Chunk.chunk_index)
                .limit(max_expansions)
            )
            neighbor_result = await self.db.execute(neighbor_stmt)
            neighbor_lists.append(list(neighbor_result.scalars().all()))

        added.extend(
            self._allocate_page_neighbors(neighbor_lists, candidate_ids, max_expansions)
        )

        merged_image = list(image_chunks or [])
        existing_image_ids = {item.chunk_id for item in merged_image}
        for neighbor in added:
            if neighbor.chunk_type not in {"image_annotation", "image_evidence"}:
                continue
            if neighbor.chunk_id in existing_image_ids:
                continue
            merged_image.append(neighbor)
            existing_image_ids.add(neighbor.chunk_id)

        text_neighbors = [
            item for item in added
            if item.chunk_type not in {"image_annotation", "image_evidence"}
        ]
        merged_primary = list(search_results) + text_neighbors
        merged_primary.sort(key=lambda item: (item.similarity, -(item.page_num or 0)), reverse=True)
        return ReassembledContext(
            primary_results=merged_primary,
            image_results=merged_image,
            added_neighbors=added,
        )

    @staticmethod
    def _allocate_page_neighbors(
        neighbor_lists: list[list[Chunk]],
        candidate_ids: set[int],
        budget: int,
    ) -> list[SearchResult]:
        """页内邻居预算按种子轮转公平分配(每轮每种子各取一个候选)。"""
        added: list[SearchResult] = []
        cursor = [0] * len(neighbor_lists)
        while len(added) < budget:
            progressed = False
            for i, neighbors in enumerate(neighbor_lists):
                # 跳过种子自身与已入选的候选
                while cursor[i] < len(neighbors) and neighbors[cursor[i]].id in candidate_ids:
                    cursor[i] += 1
                if cursor[i] >= len(neighbors):
                    continue
                neighbor = neighbors[cursor[i]]
                cursor[i] += 1
                candidate_ids.add(neighbor.id)
                added.append(SearchResult.from_chunk(neighbor, similarity=0.0))
                progressed = True
                if len(added) >= budget:
                    break
            if not progressed:
                break
        return added
