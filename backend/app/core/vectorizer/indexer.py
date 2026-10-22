"""向量索引器

提供向量存储和语义检索功能，集成PGVector。
"""

import hashlib
import time
from typing import Any, Callable, Optional

import numpy as np
from loguru import logger
from sqlalchemy import Integer, String, delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.observability.metrics import (
    vector_chunk_char_histogram,
    vector_chunk_plan_total,
    vector_lock_wait_seconds,
)
from app.core.parser.chunk_context import build_embedding_text
from app.core.parser.chunk_quality import filter_chunks as _filter_chunks_for_quality
from app.core.parser.image_extractor import (
    is_annotation_chunk_content,
    is_evidence_chunk_content,
)
from app.core.parser.text_chunker import TextChunk
from app.core.sql_array_filters import in_array_values, not_in_array_values
from app.core.vectorizer.embedding import EmbeddingModel
from app.core.vectorizer.encode_gate import safe_encode, safe_encode_batch
from app.models.chunk import Chunk


class VectorIndexer:
    """向量索引器
    
    负责将文本分块向量化并存入PGVector，
    支持批量索引和语义检索。
    
    示例:
        >>> indexer = VectorIndexer(db_session)
        >>> await indexer.index_chunks(document_id, chunks)
        >>> results = await indexer.search(query, document_id, top_k=5)
    """
    
    def __init__(
        self,
        db: AsyncSession,
        embedding_model: Optional[EmbeddingModel] = None
    ):
        """初始化向量索引器
        
        Args:
            db: 异步数据库会话
            embedding_model: 可选外部向量化模型；默认不预加载，避免绕过 GPU admission。
        """
        self.db = db
        self.embedding_model = embedding_model
    
    async def index_chunks(
        self,
        document_id: int,
        chunks: list[TextChunk],
        batch_size: int = 32,
        progress_callback: Optional[callable] = None,
        phase_callback: Optional[Callable[[str, int, int, dict[str, Any]], None]] = None,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> list[Chunk]:
        """将文本分块向量化并存入数据库

        Args:
            document_id: 文档ID
            chunks: 文本分块列表（来自TextChunker）
            batch_size: 向量化批处理大小
            progress_callback: 向量化进度回调,签名 callback(done, total)

        Returns:
            list[Chunk]: 创建的数据库Chunk记录列表
        """
        if not chunks:
            logger.warning(f"文档 {document_id} 没有需要索引的分块")
            return []
        if cancel_check:
            cancel_check()

        if bool(getattr(settings, "CHUNK_QUALITY_GATE_ENABLED", True)):
            chunks, quality_stats = _filter_chunks_for_quality(chunks, flow="vectorize")
            if quality_stats.rejected and not chunks:
                logger.warning(
                    f"文档 {document_id} 所有分块被质量门控判定为 rejected, "
                    f"stats={quality_stats.to_dict()}"
                )
                return []
        if cancel_check:
            cancel_check()

        logger.info(f"开始索引文档 {document_id}: {len(chunks)} 个分块")

        context_header_enabled = bool(
            getattr(settings, "VECTOR_CONTEXT_HEADER_ENABLED", True)
        )
        if context_header_enabled:
            texts = [build_embedding_text(chunk) for chunk in chunks]
        else:
            texts = [chunk.content for chunk in chunks]
        total = len(texts)
        dynamic_batching = bool(getattr(settings, "VECTOR_DYNAMIC_BATCHING", True))
        bucketing_enabled = bool(getattr(settings, "VECTOR_BUCKETING_ENABLED", True))
        token_budget = int(getattr(settings, "VECTOR_TOKEN_BUDGET", 12000))
        db_insert_mode = str(getattr(settings, "VECTOR_DB_INSERT_MODE", "orm")).lower()
        if db_insert_mode not in {"orm", "core"}:
            db_insert_mode = "orm"
        max_batch_size = max(int(batch_size), 1)
        planned_batches = self._build_index_batches(
            texts=texts,
            max_batch_size=max_batch_size,
            token_budget=max(token_budget, 1),
            dynamic_batching=dynamic_batching,
            bucketing_enabled=bucketing_enabled,
        )

        logger.info(
            f"向量化批次计划: batches={len(planned_batches)}, "
            f"dynamic_batching={dynamic_batching}, "
            f"bucketing={bucketing_enabled}, "
            f"token_budget={token_budget}, "
            f"db_insert_mode={db_insert_mode}"
        )

        db_chunks: list[Chunk] = []
        inserted_ids: list[int] = []
        done = 0
        encode_time = 0.0
        prepare_db_objects_time = 0.0
        db_flush_time = 0.0
        degraded_to_cpu = False
        fallback_reason: str | None = None
        # 任务级降级粘滞：本文档一旦触发 CPU fallback，后续批次也强制走 CPU
        stick_to_cpu_enabled = bool(
            getattr(settings, "VECTOR_TASK_STICK_TO_CPU_AFTER_FALLBACK", True)
        )
        task_stick_to_cpu = False
        admission_fail_streak = 0
        desired_fingerprints = {
            self._resolve_chunk_fingerprint(document_id, chunk)
            for chunk in chunks
        }

        if phase_callback:
            try:
                phase_callback("wait_lock", 0, total, {
                    "batch_count": len(planned_batches),
                })
            except Exception:
                pass

        for batch_no, batch_indices in enumerate(planned_batches, start=1):
            if cancel_check:
                cancel_check()
            batch_texts = [texts[idx] for idx in batch_indices]
            batch_char_lens = [len(t) for t in batch_texts]
            batch_tokens = sum(self._estimate_tokens(t) for t in batch_texts)

            for idx in batch_indices:
                chunk = chunks[idx]
                chunk_type = self._resolve_chunk_type(chunk)
                vector_chunk_plan_total.labels(flow="vectorize", chunk_type=chunk_type, outcome="planned").inc()
                vector_chunk_char_histogram.labels(flow="vectorize", chunk_type=chunk_type).observe(len(chunk.content or ""))

            if phase_callback:
                try:
                    phase_callback("encode", done, total, {
                        "batch_no": batch_no,
                        "batch_count": len(planned_batches),
                        "batch_size": len(batch_indices),
                        "batch_token_estimate": batch_tokens,
                        "degraded_to_cpu": degraded_to_cpu,
                    })
                except Exception:
                    pass

            # 锁与 semaphore 的获取已下沉到 safe_encode_batch 内部:
            # GPU 段持 encode_lock (串行), CPU 段持 cpu_encode_semaphore (并发)。
            # lock_wait 观测改为读 encode_meta 中按设备路径分别返回的等待时长。
            encode_start = time.perf_counter()
            if cancel_check:
                cancel_check()
            batch_embeddings, encode_meta = await safe_encode_batch(
                texts=batch_texts,
                initial_batch_size=min(max_batch_size, len(batch_texts)),
                batch_no=batch_no,
                batch_count=len(planned_batches),
                stick_to_cpu=task_stick_to_cpu,
                admission_fail_streak=admission_fail_streak,
                # 文档向量化大批量 CPU encode 走子进程, 避免持 GIL 阻塞
                # 事件循环饿死 vectorize keepalive 协程, 触发 reconciler 误恢复。
                prefer_subprocess=True,
            )
            if cancel_check:
                cancel_check()
            encode_elapsed = time.perf_counter() - encode_start
            encode_time += encode_elapsed
            done += len(batch_indices)
            if encode_meta.get("admission_failed"):
                admission_fail_streak = int(
                    encode_meta.get("admission_fail_streak") or admission_fail_streak + 1
                )
            else:
                admission_fail_streak = 0
            # 粘滞自愈: encode_gate 在 GPU 成功时已清 ContextVar 并置
            # sticky_cpu_cleared=True; 同步复位本 indexer 的 boolean + 失败累计,
            # 否则同 task 后续 batch 仍会强制 CPU。
            if encode_meta.get("sticky_cpu_cleared") and task_stick_to_cpu:
                logger.info(
                    f"[VectorIndexer] 任务级 stick_to_cpu 复位 ( 自愈): "
                    f"document_id={document_id}, batch_no={batch_no}"
                )
                task_stick_to_cpu = False
                admission_fail_streak = 0
            lock_wait_ms = (
                int(encode_meta.get("gpu_lock_wait_ms") or 0)
                + int(encode_meta.get("cpu_queue_wait_ms") or 0)
            )
            lock_wait = lock_wait_ms / 1000.0
            vector_lock_wait_seconds.labels(flow="vectorize").observe(lock_wait)

            if encode_meta.get("degraded_to_cpu"):
                degraded_to_cpu = True
                # 保留首个有意义的 fallback_reason，避免被后续 batch 覆盖为 None
                new_reason = encode_meta.get("fallback_reason")
                if new_reason:
                    fallback_reason = new_reason
                elif fallback_reason is None:
                    fallback_reason = None
                # 任务级粘滞仅用于 hard fallback；单次 admission 预检失败只影响当前批。
                fallback_kind = encode_meta.get("cpu_fallback_kind")
                sticky_cpu_eligible = bool(encode_meta.get("sticky_cpu_eligible"))
                if (
                    stick_to_cpu_enabled
                    and not task_stick_to_cpu
                    and fallback_kind == "hard"
                    and sticky_cpu_eligible
                ):
                    task_stick_to_cpu = True
                    logger.info(
                        f"[VectorIndexer] 任务级 stick_to_cpu 开启: "
                        f"document_id={document_id}, batch_no={batch_no}, "
                        f"reason={fallback_reason}, kind={fallback_kind}"
                    )
            recovery_mode = encode_meta.get("recovery_mode", "thread")

            if progress_callback:
                try:
                    progress_callback(done, total)
                except Exception:
                    pass
            if phase_callback:
                try:
                    phase_callback(
                        "encode",
                        done,
                        total,
                        {
                            "batch_no": batch_no,
                            "batch_count": len(planned_batches),
                            "batch_size": len(batch_indices),
                            "batch_elapsed_ms": int(encode_elapsed * 1000),
                            "batch_char_min": min(batch_char_lens) if batch_char_lens else 0,
                            "batch_char_max": max(batch_char_lens) if batch_char_lens else 0,
                            "batch_token_estimate": batch_tokens,
                            "degraded_to_cpu": degraded_to_cpu,
                            "fallback_reason": fallback_reason,
                            "recovery_mode": recovery_mode,
                            "retry_chain": encode_meta.get("retry_chain", []),
                            "lock_wait_ms": int(lock_wait * 1000),
                            "cpu_timeout_sec": encode_meta.get("cpu_timeout_sec", 0),
                            "device_cooldown_sec": encode_meta.get("device_cooldown_sec", 0.0),
                            "stick_to_cpu": bool(task_stick_to_cpu),
                            "cpu_fallback_kind": encode_meta.get("cpu_fallback_kind"),
                            "sticky_cpu_eligible": bool(encode_meta.get("sticky_cpu_eligible")),
                            "admission_fail_streak": admission_fail_streak,
                        },
                    )
                except Exception:
                    pass

            prepare_start = time.perf_counter()
            if cancel_check:
                cancel_check()
            batch_rows: list[dict[str, Any]] = []
            for local_pos, source_idx in enumerate(batch_indices):
                source_chunk = chunks[source_idx]
                embedding = batch_embeddings[local_pos]
                content = source_chunk.content.replace("\x00", "") if source_chunk.content else ""
                chunk_type = self._resolve_chunk_type(source_chunk)
                row = {
                    "document_id": document_id,
                    "content": content,
                    "embedding": embedding.tolist(),
                    "chunk_type": chunk_type,
                    "parent_block_id": source_chunk.metadata.get("parent_block_id"),
                    "section_path": source_chunk.metadata.get("section_path"),
                    "chunk_fingerprint": self._resolve_chunk_fingerprint(document_id, source_chunk),
                    "embedding_version": str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
                    "page_num": source_chunk.metadata.get("page_num"),
                    "chunk_index": source_chunk.chunk_index,
                    "char_count": len(content),
                    "chunk_metadata": self._resolve_chunk_metadata(source_chunk),
                }
                batch_rows.append(row)
            prepare_elapsed = time.perf_counter() - prepare_start
            prepare_db_objects_time += prepare_elapsed

            if phase_callback:
                try:
                    phase_callback("flush", done, total, {
                        "batch_no": batch_no,
                        "batch_count": len(planned_batches),
                        "degraded_to_cpu": degraded_to_cpu,
                    })
                except Exception:
                    pass

            flush_start = time.perf_counter()
            if cancel_check:
                cancel_check()
            stmt = pg_insert(Chunk).values(batch_rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_chunks_document_fingerprint",
                set_={
                    "content": stmt.excluded.content,
                    "embedding": stmt.excluded.embedding,
                    "chunk_type": stmt.excluded.chunk_type,
                    "parent_block_id": stmt.excluded.parent_block_id,
                    "section_path": stmt.excluded.section_path,
                    "embedding_version": stmt.excluded.embedding_version,
                    "page_num": stmt.excluded.page_num,
                    "chunk_index": stmt.excluded.chunk_index,
                    "char_count": stmt.excluded.char_count,
                    "chunk_metadata": stmt.excluded.chunk_metadata,
                },
            ).returning(Chunk.id)
            result = await self.db.execute(stmt)
            inserted_ids.extend(result.scalars().all())
            await self.db.flush()
            if cancel_check:
                cancel_check()

            flush_elapsed = time.perf_counter() - flush_start
            db_flush_time += flush_elapsed
            if phase_callback:
                try:
                    phase_callback(
                        "flush",
                        done,
                        total,
                        {
                            "batch_no": batch_no,
                            "batch_count": len(planned_batches),
                            "flush_elapsed_ms": int(flush_elapsed * 1000),
                            "batch_write_count": len(batch_indices),
                            "db_insert_mode": "upsert",
                            "degraded_to_cpu": degraded_to_cpu,
                        },
                    )
                except Exception:
                    pass

        if desired_fingerprints:
            if cancel_check:
                cancel_check()
            # 指纹集合随文档规模线性增长, 逐元素 NOT IN 展开会突破
            # asyncpg 32767 参数上限, 必须折叠为单个数组参数。
            cleanup_stmt = delete(Chunk).where(
                Chunk.document_id == document_id,
                not_in_array_values(
                    Chunk.chunk_fingerprint,
                    desired_fingerprints,
                    element_type=String(),
                ),
            )
            await self.db.execute(cleanup_stmt)
            await self.db.flush()
            if cancel_check:
                cancel_check()

        if inserted_ids:
            if cancel_check:
                cancel_check()
            # 同类: inserted_ids 与分块同规模, IN 展开同样会超参数上限。
            stmt = select(Chunk).where(
                in_array_values(Chunk.id, inserted_ids, element_type=Integer())
            )
            query_result = await self.db.execute(stmt)
            inserted_chunks = query_result.scalars().all()
            db_chunks = list(inserted_chunks)
        
        logger.info(
            f"文档 {document_id} 索引完成: "
            f"存入 {len(db_chunks)} 个分块向量, "
            f"encode_ms={int(encode_time * 1000)}, "
            f"prepare_ms={int(prepare_db_objects_time * 1000)}, "
            f"flush_ms={int(db_flush_time * 1000)}, "
            f"batches={len(planned_batches)}"
        )

        db_chunks.sort(key=lambda item: item.chunk_index)
        return db_chunks

    @staticmethod
    def _resolve_chunk_type(source_chunk: TextChunk) -> str:
        content = source_chunk.content.replace("\x00", "") if source_chunk.content else ""
        chunk_type = source_chunk.metadata.get("chunk_type")
        if chunk_type:
            return chunk_type
        if is_evidence_chunk_content(content):
            return "image_evidence"
        if is_annotation_chunk_content(content):
            return "image_annotation"
        return "text"

    @staticmethod
    def _resolve_chunk_fingerprint(document_id: int, source_chunk: TextChunk) -> str:
        metadata_fp = source_chunk.metadata.get("chunk_fingerprint")
        if metadata_fp:
            return str(metadata_fp)
        raw = "|".join(
            [
                str(document_id),
                str(source_chunk.chunk_index),
                str(source_chunk.metadata.get("page_num") or ""),
                str(source_chunk.metadata.get("chunk_type") or ""),
                source_chunk.content or "",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _resolve_chunk_metadata(source_chunk: TextChunk) -> dict | None:
        """提取需随 chunk 持久化的结构化载荷(阶段1.5: 仅白名单 scoring_payload)。

        内部装配字段(chunk_fingerprint/estimated_tokens 等)不入库, 防止把
        瞬态元数据固化为跨阶段契约。
        """
        from app.core.extractor.scoring.scoring_grid_payload import PAYLOAD_KEY

        payload = source_chunk.metadata.get(PAYLOAD_KEY)
        if isinstance(payload, dict) and payload.get("grid"):
            return {PAYLOAD_KEY: payload}
        return None

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """基于字符数估算token数量（中文场景近似1token≈2字符）"""
        return max(1, len(text) // 2)

    @classmethod
    def _build_index_batches(
        cls,
        texts: list[str],
        max_batch_size: int,
        token_budget: int,
        dynamic_batching: bool,
        bucketing_enabled: bool,
    ) -> list[list[int]]:
        if not texts:
            return []

        if not dynamic_batching:
            return [
                list(range(i, min(i + max_batch_size, len(texts))))
                for i in range(0, len(texts), max_batch_size)
            ]

        indices = list(range(len(texts)))
        if bucketing_enabled:
            indices.sort(key=lambda idx: len(texts[idx]))

        batches: list[list[int]] = []
        current: list[int] = []
        current_tokens = 0

        for idx in indices:
            item_tokens = cls._estimate_tokens(texts[idx])
            if current and (
                len(current) >= max_batch_size
                or current_tokens + item_tokens > token_budget
            ):
                batches.append(current)
                current = []
                current_tokens = 0

            current.append(idx)
            current_tokens += item_tokens

        if current:
            batches.append(current)

        return batches
    
    async def index_text(
        self,
        document_id: int,
        text: str,
        page_num: Optional[int] = None,
        chunk_index: int = 0
    ) -> Chunk:
        """索引单个文本（不分块）
        
        Args:
            document_id: 文档ID
            text: 文本内容
            page_num: 页码（可选）
            chunk_index: 分块索引
            
        Returns:
            Chunk: 创建的数据库记录
        """
        clean_text = text.replace("\x00", "") if text else ""
        embedding = (await safe_encode([clean_text], initial_batch_size=1))[0]
        
        db_chunk = Chunk(
            document_id=document_id,
            content=clean_text,
            embedding=embedding.tolist(),
            chunk_type="text",
            parent_block_id=f"single:{document_id}:{chunk_index}",
            section_path=None,
            chunk_fingerprint=hashlib.sha256(
                f"{document_id}|{chunk_index}|{page_num or ''}|{clean_text}".encode("utf-8", errors="ignore")
            ).hexdigest(),
            embedding_version=str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
            page_num=page_num,
            chunk_index=chunk_index,
            char_count=len(clean_text)
        )
        
        self.db.add(db_chunk)
        await self.db.flush()
        
        return db_chunk
    
    async def search(
        self,
        query: str,
        document_id: Optional[int] = None,
        top_k: int = 5,
        min_similarity: float = 0.0
    ) -> list[tuple[Chunk, float]]:
        """语义检索
        
        Args:
            query: 查询文本
            document_id: 限定文档ID（可选）
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            
        Returns:
            list[tuple[Chunk, float]]: (分块, 相似度)列表，按相似度降序
        """
        # 向量化查询
        query_embedding = (await safe_encode([query], initial_batch_size=1))[0]
        
        # 构建查询（使用PGVector的<=>余弦距离运算符）
        # 注意：余弦距离 = 1 - 余弦相似度
        query_vector = query_embedding.tolist()
        
        # 使用原生SQL进行向量检索
        # PGVector的<=>运算符计算余弦距离
        if document_id is not None:
            sql = text("""
                SELECT id, 1 - (embedding <=> :query_vector) as similarity
                FROM chunks
                WHERE document_id = :document_id
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> :query_vector
                LIMIT :top_k
            """)
            result = await self.db.execute(
                sql,
                {
                    "query_vector": str(query_vector),
                    "document_id": document_id,
                    "top_k": top_k
                }
            )
        else:
            sql = text("""
                SELECT id, 1 - (embedding <=> :query_vector) as similarity
                FROM chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> :query_vector
                LIMIT :top_k
            """)
            result = await self.db.execute(
                sql,
                {
                    "query_vector": str(query_vector),
                    "top_k": top_k
                }
            )
        
        rows = result.fetchall()
        
        # 获取完整的Chunk对象
        chunk_ids = [row[0] for row in rows]
        similarities = {row[0]: row[1] for row in rows}
        
        if not chunk_ids:
            return []
        
        # 查询完整记录
        stmt = select(Chunk).where(Chunk.id.in_(chunk_ids))
        chunks_result = await self.db.execute(stmt)
        chunks = {c.id: c for c in chunks_result.scalars().all()}
        
        # 按相似度排序并过滤
        results: list[tuple[Chunk, float]] = []
        for chunk_id in chunk_ids:
            if chunk_id in chunks:
                similarity = similarities[chunk_id]
                if similarity >= min_similarity:
                    results.append((chunks[chunk_id], similarity))
        
        logger.debug(
            f"语义检索完成: query='{query[:50]}...', "
            f"找到 {len(results)} 个结果"
        )
        
        return results
    
    async def search_by_embedding(
        self,
        query_embedding: np.ndarray,
        document_id: Optional[int] = None,
        top_k: int = 5
    ) -> list[tuple[Chunk, float]]:
        """使用预计算的向量进行检索
        
        Args:
            query_embedding: 查询向量
            document_id: 限定文档ID（可选）
            top_k: 返回结果数量
            
        Returns:
            list[tuple[Chunk, float]]: (分块, 相似度)列表
        """
        query_vector = query_embedding.tolist()
        
        if document_id is not None:
            sql = text("""
                SELECT id, 1 - (embedding <=> :query_vector) as similarity
                FROM chunks
                WHERE document_id = :document_id
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> :query_vector
                LIMIT :top_k
            """)
            result = await self.db.execute(
                sql,
                {
                    "query_vector": str(query_vector),
                    "document_id": document_id,
                    "top_k": top_k
                }
            )
        else:
            sql = text("""
                SELECT id, 1 - (embedding <=> :query_vector) as similarity
                FROM chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> :query_vector
                LIMIT :top_k
            """)
            result = await self.db.execute(
                sql,
                {
                    "query_vector": str(query_vector),
                    "top_k": top_k
                }
            )
        
        rows = result.fetchall()
        
        if not rows:
            return []
        
        chunk_ids = [row[0] for row in rows]
        similarities = {row[0]: row[1] for row in rows}
        
        stmt = select(Chunk).where(Chunk.id.in_(chunk_ids))
        chunks_result = await self.db.execute(stmt)
        chunks = {c.id: c for c in chunks_result.scalars().all()}
        
        results: list[tuple[Chunk, float]] = []
        for chunk_id in chunk_ids:
            if chunk_id in chunks:
                results.append((chunks[chunk_id], similarities[chunk_id]))
        
        return results
    
    async def delete_document_chunks(self, document_id: int) -> int:
        """删除文档的所有分块向量
        
        Args:
            document_id: 文档ID
            
        Returns:
            int: 删除的记录数
        """
        stmt = select(Chunk).where(Chunk.document_id == document_id)
        result = await self.db.execute(stmt)
        chunks = result.scalars().all()
        
        count = len(chunks)
        
        for chunk in chunks:
            await self.db.delete(chunk)
        
        await self.db.flush()
        
        logger.info(f"已删除文档 {document_id} 的 {count} 个分块")
        
        return count
    
    async def get_document_chunk_count(self, document_id: int) -> int:
        """获取文档的分块数量
        
        Args:
            document_id: 文档ID
            
        Returns:
            int: 分块数量
        """
        sql = text("""
            SELECT COUNT(*) FROM chunks WHERE document_id = :document_id
        """)
        result = await self.db.execute(sql, {"document_id": document_id})
        return result.scalar() or 0
    
    async def has_embeddings(self, document_id: int) -> bool:
        """检查文档是否已完成向量化
        
        Args:
            document_id: 文档ID
            
        Returns:
            bool: 是否所有分块都有向量
        """
        sql = text("""
            SELECT COUNT(*) FROM chunks 
            WHERE document_id = :document_id AND embedding IS NULL
        """)
        result = await self.db.execute(sql, {"document_id": document_id})
        null_count = result.scalar() or 0
        
        return null_count == 0 and await self.get_document_chunk_count(document_id) > 0
