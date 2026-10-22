"""评分救援分组关系证据加载器。

职责: 按救援实际页范围加载有序 table_row_with_header 行块供关系裁判使用。
不做: 不解析表格、不修改评分项、不调用LLM、不裁决分组或verdict。
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select

from app.models.chunk import Chunk


async def load_group_relation_rows(
    db: Any,
    document_id: int,
    pages: tuple[int, ...],
) -> list[Any]:
    """加载救援页内普通表格行；查询失败返回空证据，由关系裁判fail-closed。"""
    normalized_pages = sorted({int(page) for page in pages if int(page) > 0})
    if db is None or not normalized_pages:
        return []
    try:
        result = await db.execute(
            select(Chunk)
            .where(
                Chunk.document_id == document_id,
                Chunk.chunk_type == "table_row_with_header",
                Chunk.page_num.in_(normalized_pages),
            )
            .order_by(Chunk.chunk_index)
        )
        return list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001 - 关系证据失败必须降级而非中断救援
        logger.warning(
            "[评分归组关系] 行块加载失败 doc={}: {}",
            document_id,
            exc,
        )
        return []


__all__ = ["load_group_relation_rows"]
