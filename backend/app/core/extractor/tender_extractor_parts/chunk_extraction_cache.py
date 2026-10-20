"""
职责: 为招标要求单 chunk LLM 抽取提供可失效的结果缓存。
不做: 不决定 chunk 是否应被抽取、不做候选预筛、不做要求合并去重。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models.tender_chunk_extraction_cache import TenderChunkExtractionCache
from app.schemas.requirement import ExtractedRequirement


@dataclass(frozen=True)
class ChunkExtractionCacheKey:
    document_id: int
    chunk_id: int
    chunk_fingerprint: str
    prompt_signature: str
    model_id: str
    extractor_schema_version: str
    section_context_enabled: bool


def resolve_chunk_cache_model_id(llm_client: Any) -> str:
    """从客户端对象提取稳定模型标识，缺失时使用类名兜底。"""
    for attr in ("model_name", "model", "deployment_name"):
        value = getattr(llm_client, attr, None)
        if value:
            return str(value)
    return llm_client.__class__.__name__ if llm_client is not None else "unknown"


def build_chunk_extraction_cache_key(
    *,
    chunk: Any,
    system_msg: str,
    user_msg: str,
    model_id: str,
    schema_version: str,
    section_context_enabled: bool,
) -> ChunkExtractionCacheKey:
    prompt_signature = hashlib.sha256(
        f"{system_msg}\n---USER---\n{user_msg}".encode("utf-8")
    ).hexdigest()
    return ChunkExtractionCacheKey(
        document_id=int(chunk.document_id),
        chunk_id=int(chunk.id),
        chunk_fingerprint=str(chunk.chunk_fingerprint),
        prompt_signature=prompt_signature,
        model_id=str(model_id or "unknown"),
        extractor_schema_version=str(schema_version),
        section_context_enabled=bool(section_context_enabled),
    )


async def load_cached_chunk_requirements(
    db: AsyncSession | None,
    key: ChunkExtractionCacheKey,
) -> list[ExtractedRequirement] | None:
    if db is None:
        return None

    result = await db.execute(
        select(TenderChunkExtractionCache).where(
            TenderChunkExtractionCache.document_id == key.document_id,
            TenderChunkExtractionCache.chunk_fingerprint == key.chunk_fingerprint,
            TenderChunkExtractionCache.prompt_signature == key.prompt_signature,
            TenderChunkExtractionCache.model_id == key.model_id,
            TenderChunkExtractionCache.extractor_schema_version
            == key.extractor_schema_version,
            TenderChunkExtractionCache.section_context_enabled
            == key.section_context_enabled,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    items = row.result_items or []
    if not isinstance(items, list):
        logger.warning(
            "chunk 抽取缓存载荷不是列表: cache_id={}",
            row.id,
        )
        return None
    try:
        return [ExtractedRequirement.model_validate(item) for item in items]
    except Exception as exc:
        logger.warning(
            "chunk 抽取缓存载荷校验失败: cache_id={}, err={}",
            row.id,
            exc,
        )
        return None


async def save_chunk_requirements_cache(
    db: AsyncSession | None,
    key: ChunkExtractionCacheKey,
    items: list[ExtractedRequirement],
    *,
    empty_result: bool,
) -> None:
    if db is None:
        return

    payload = [item.model_dump(mode="json") for item in items]
    insert_stmt = pg_insert(TenderChunkExtractionCache).values(
        document_id=key.document_id,
        chunk_id=key.chunk_id,
        chunk_fingerprint=key.chunk_fingerprint,
        prompt_signature=key.prompt_signature,
        model_id=key.model_id,
        extractor_schema_version=key.extractor_schema_version,
        section_context_enabled=key.section_context_enabled,
        result_items=payload,
        empty_result=bool(empty_result),
        failure_fingerprint=None,
    )
    stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_tender_chunk_extract_cache_key",
        set_={
            "chunk_id": key.chunk_id,
            "result_items": payload,
            "empty_result": bool(empty_result),
            "failure_fingerprint": None,
            "updated_at": func.now(),
        },
    )
    await db.execute(stmt)
