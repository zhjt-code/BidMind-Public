"""素材索引器

将素材内容分块并向量化,写入 WritingMaterialChunk 表。
独立于投标审查的 VectorIndexer,专用于标书编写场景。

调用链:
  素材内容 → MaterialIndexer.index_material()
          → TextChunker 分块 → EmbeddingModel 向量化
          → 写入 WritingMaterialChunk 表
          → 返回 chunk_id 列表
"""

import asyncio
import json
import time
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy import String, text, select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_array_filters import not_in_array_values
from app.models.writing_material_chunk import WritingMaterialChunk
from app.core.parser import ChunkPlanner
from app.core.parser.chunk_evidence import plan_pages_with_image_evidence
from app.core.parser.pdf_parser import ParsedPage
from app.core.parser.text_chunker import TextChunk
from app.core.config import settings
from app.core.vectorizer.encode_gate import safe_encode_batch


class MaterialIndexer:
    """素材索引器

    职责: 将写标素材切分为可检索证据块并写入素材向量表。
    不做: 不解析原始文件、不执行素材标签判定、不直接生成章节内容。

    将素材文本内容分块、向量化并写入 WritingMaterialChunk 表。

    使用示例:
        indexer = MaterialIndexer(db)
        chunk_ids = await indexer.index_material(
            material_id=1,
            project_id=1,
            content="素材文本内容...",
        )
        print(f"创建了 {len(chunk_ids)} 个分块")
    """

    def __init__(
        self,
        db: AsyncSession,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ):
        """初始化索引器

        Args:
            db: 异步数据库会话
            chunk_size: 分块大小(字符数),默认1500
            chunk_overlap: 分块重叠(字符数),默认200
        """
        self.db = db
        if chunk_size is None and chunk_overlap is None:
            self._planner = ChunkPlanner()
        else:
            effective_chunk_size = chunk_size or int(settings.VECTOR_MAX_ITEM_CHARS)
            effective_overlap = chunk_overlap or int(settings.VECTOR_PARAGRAPH_OVERLAP_CHARS)
            self._planner = ChunkPlanner(
                max_item_chars=effective_chunk_size,
                paragraph_target_chars=effective_chunk_size,
                paragraph_overlap_chars=effective_overlap,
            )

    async def index_material(
        self,
        material_id: int,
        project_id: int,
        content: str,
        page_texts: Optional[list[tuple[int, str]] | list[ParsedPage]] = None,
        batch_size: int = 32,
        phase_callback: Optional[Callable[[str, int, int, dict[str, Any]], None]] = None,
        task_stick_to_cpu: bool = False,
    ) -> list[int]:
        """将素材内容分块向量化并写入数据库

        处理流程:
        1. 使用 TextChunker 对 content 分块
        2. 使用 EmbeddingModel 对分块批量向量化
        3. 批量写入 WritingMaterialChunk 表
        4. 返回创建的 chunk_id 列表

        Args:
            material_id: 素材ID
            project_id: 项目ID
            content: 素材文本内容
            batch_size: 向量化批处理大小
            phase_callback: 可选的进度回调。签名 ``callback(phase, done, total, meta)``，
                phase 取值 ``wait_lock``/``encode``/``flush``/``done``；meta 字段与
                VectorIndexer.phase_callback 保持一致（degraded_to_cpu、fallback_reason、
                retry_chain、device_cooldown_sec、stick_to_cpu 等），便于上层任务心跳汇总。
            task_stick_to_cpu: 上层任务是否要求本素材的所有批次强制走 CPU（任务级粘滞）

        Returns:
            list[int]: 创建的 chunk_id 列表
        """
        if not content or not content.strip():
            logger.warning(f"素材 {material_id} 内容为空,跳过索引")
            return []

        logger.debug(f"开始索引素材 {material_id}: 内容长度 {len(content)} 字符")

        text_chunks = self.plan_material_chunks(content=content, page_texts=page_texts)
        if not text_chunks:
            logger.warning(f"素材 {material_id} 分块结果为空")
            return []

        texts = [chunk.content for chunk in text_chunks]
        total = len(texts)

        logger.debug(f"素材 {material_id} 分块完成: {total} 个分块,开始向量化")

        chunk_ids: list[int] = []
        encode_time = 0.0
        db_time = 0.0

        desired_fingerprints = {
            str(chunk.metadata.get("chunk_fingerprint"))
            for chunk in text_chunks
        }

        # 素材级降级粘滞：本素材一旦触发 CPU fallback，剩余批次也强制走 CPU
        stick_to_cpu_enabled = bool(
            getattr(settings, "VECTOR_TASK_STICK_TO_CPU_AFTER_FALLBACK", True)
        )
        material_stick_to_cpu = bool(task_stick_to_cpu)
        degraded_to_cpu = False
        admission_fail_streak = 0
        last_fallback_reason: Optional[str] = None
        batch_count_total = max((total + batch_size - 1) // batch_size, 1)

        def _invoke_phase_callback(phase: str, done_count: int, meta: dict[str, Any]) -> None:
            if phase_callback is None:
                return
            try:
                phase_callback(phase, done_count, total, meta)
            except Exception:
                pass

        done_count = 0

        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch_texts = texts[batch_start:batch_end]
            batch_chunks = text_chunks[batch_start:batch_end]
            batch_no = (batch_start // batch_size) + 1

            _invoke_phase_callback(
                "wait_lock",
                done_count,
                {
                    "batch_no": batch_no,
                    "batch_count": batch_count_total,
                    "degraded_to_cpu": degraded_to_cpu,
                    "stick_to_cpu": material_stick_to_cpu,
                },
            )

            # safe_encode_batch 内部按设备路由选锁 (GPU 串行 / CPU 并发),
            # 调用方不再需要外层持锁。
            encode_start = time.perf_counter()
            batch_embeddings, encode_meta = await safe_encode_batch(
                texts=batch_texts,
                initial_batch_size=min(batch_size, len(batch_texts)),
                batch_no=batch_no,
                batch_count=batch_count_total,
                stick_to_cpu=material_stick_to_cpu,
                admission_fail_streak=admission_fail_streak,
            )
            encode_elapsed = time.perf_counter() - encode_start
            encode_time += encode_elapsed
            if encode_meta.get("admission_failed"):
                admission_fail_streak = int(
                    encode_meta.get("admission_fail_streak") or admission_fail_streak + 1
                )
            else:
                admission_fail_streak = 0
            lock_wait_ms = (
                int(encode_meta.get("gpu_lock_wait_ms") or 0)
                + int(encode_meta.get("cpu_queue_wait_ms") or 0)
            )
            lock_wait = lock_wait_ms / 1000.0

            if encode_meta.get("degraded_to_cpu"):
                degraded_to_cpu = True
                new_reason = encode_meta.get("fallback_reason")
                if new_reason:
                    last_fallback_reason = new_reason
                fallback_kind = encode_meta.get("cpu_fallback_kind")
                sticky_cpu_eligible = bool(encode_meta.get("sticky_cpu_eligible"))
                if (
                    stick_to_cpu_enabled
                    and not material_stick_to_cpu
                    and fallback_kind == "hard"
                    and sticky_cpu_eligible
                ):
                    material_stick_to_cpu = True
                    logger.info(
                        f"[MaterialIndexer] 素材级 stick_to_cpu 开启: "
                        f"material_id={material_id}, batch_no={batch_no}, "
                        f"reason={last_fallback_reason}, kind={fallback_kind}"
                    )

            done_count += len(batch_texts)
            _invoke_phase_callback(
                "encode",
                done_count,
                {
                    "batch_no": batch_no,
                    "batch_count": batch_count_total,
                    "batch_size": len(batch_texts),
                    "batch_elapsed_ms": int(encode_elapsed * 1000),
                    "lock_wait_ms": int(lock_wait * 1000),
                    "degraded_to_cpu": degraded_to_cpu,
                    "fallback_reason": last_fallback_reason,
                    "recovery_mode": encode_meta.get("recovery_mode", "thread"),
                    "retry_chain": encode_meta.get("retry_chain", []),
                    "cpu_timeout_sec": encode_meta.get("cpu_timeout_sec", 0),
                    "device_cooldown_sec": encode_meta.get("device_cooldown_sec", 0.0),
                    "stick_to_cpu": material_stick_to_cpu,
                    "cpu_fallback_kind": encode_meta.get("cpu_fallback_kind"),
                    "sticky_cpu_eligible": bool(encode_meta.get("sticky_cpu_eligible")),
                    "admission_fail_streak": admission_fail_streak,
                },
            )

            db_start = time.perf_counter()
            rows: list[dict] = []
            for i, (tc, emb) in enumerate(zip(batch_chunks, batch_embeddings)):
                clean_content = tc.content.replace("\x00", "") if tc.content else ""
                rows.append(
                    {
                        "material_id": material_id,
                        "project_id": project_id,
                        "content": clean_content,
                        "embedding": emb.tolist(),
                        "chunk_fingerprint": str(tc.metadata.get("chunk_fingerprint")),
                        "embedding_version": str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
                        "parent_block_id": tc.metadata.get("parent_block_id"),
                        "section_path": tc.metadata.get("section_path"),
                        "chunk_index": batch_start + i,
                        "char_count": len(clean_content),
                        "page_num": tc.metadata.get("page_num"),
                    }
                )
            stmt = pg_insert(WritingMaterialChunk).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_writing_material_chunks_material_fingerprint",
                set_={
                    "content": stmt.excluded.content,
                    "embedding": stmt.excluded.embedding,
                    "embedding_version": stmt.excluded.embedding_version,
                    "parent_block_id": stmt.excluded.parent_block_id,
                    "section_path": stmt.excluded.section_path,
                    "chunk_index": stmt.excluded.chunk_index,
                    "char_count": stmt.excluded.char_count,
                    "page_num": stmt.excluded.page_num,
                },
            )
            await self.db.execute(stmt)
            await self.db.flush()
            db_time += time.perf_counter() - db_start

        if desired_fingerprints:
            # 同类: 逐指纹 NOT IN 展开在超大素材下会突破 asyncpg 参数上限。
            cleanup_stmt = delete(WritingMaterialChunk).where(
                WritingMaterialChunk.material_id == material_id,
                not_in_array_values(
                    WritingMaterialChunk.chunk_fingerprint,
                    desired_fingerprints,
                    element_type=String(),
                ),
            )
            await self.db.execute(cleanup_stmt)
            await self.db.flush()

        result = await self.db.execute(
            select(WritingMaterialChunk.id)
            .where(WritingMaterialChunk.material_id == material_id)
            .order_by(WritingMaterialChunk.chunk_index)
        )
        chunk_ids = list(result.scalars().all())

        logger.debug(
            f"素材 {material_id} 索引完成: {len(chunk_ids)} 个分块, "
            f"encode_ms={int(encode_time * 1000)}, db_ms={int(db_time * 1000)}, "
            f"degraded_to_cpu={degraded_to_cpu}, fallback_reason={last_fallback_reason}"
        )

        _invoke_phase_callback(
            "done",
            total,
            {
                "batch_count": batch_count_total,
                "encode_ms": int(encode_time * 1000),
                "db_ms": int(db_time * 1000),
                "degraded_to_cpu": degraded_to_cpu,
                "fallback_reason": last_fallback_reason,
                "stick_to_cpu": material_stick_to_cpu,
            },
        )

        return chunk_ids

    def plan_material_chunks(
        self,
        *,
        content: str,
        page_texts: Optional[list[tuple[int, str]] | list[ParsedPage]] = None,
    ) -> list[TextChunk]:
        """按素材可用结构规划分块，优先保留 ParsedPage 中的表格和图片证据。"""
        if page_texts:
            first = page_texts[0]
            if isinstance(first, ParsedPage):
                return plan_pages_with_image_evidence(
                    self._planner,
                    list(page_texts),  # type: ignore[arg-type]
                )
            return self._planner.plan_page_tuples(page_texts)  # type: ignore[arg-type]
        return self._planner.plan_page_tuples([(1, content)])

    async def delete_material_chunks(self, material_id: int) -> int:
        """删除素材的所有分块

        Args:
            material_id: 素材ID

        Returns:
            int: 删除的记录数
        """
        result = await self.db.execute(
            text("DELETE FROM writing_material_chunks WHERE material_id = :mid"),
            {"mid": material_id},
        )
        deleted = result.rowcount
        await self.db.flush()

        if deleted > 0:
            logger.info(f"已删除素材 {material_id} 的 {deleted} 个分块")

        return deleted

    async def get_material_chunk_count(self, material_id: int) -> int:
        """获取素材的分块数量

        Args:
            material_id: 素材ID

        Returns:
            int: 分块数量
        """
        result = await self.db.execute(
            text(
                "SELECT COUNT(*) FROM writing_material_chunks "
                "WHERE material_id = :mid"
            ),
            {"mid": material_id},
        )
        return result.scalar() or 0

    async def search_by_embedding(
        self,
        query_embedding: list[float],
        project_id: int,
        top_k: int = 10,
        material_ids: Optional[list[int]] = None,
    ) -> list[tuple[int, int, float]]:
        """使用向量相似度搜索素材分块

        Args:
            query_embedding: 查询向量
            project_id: 项目ID
            top_k: 返回结果数量
            material_ids: 限定素材ID列表(可选)

        Returns:
            list[tuple[int, int, float]]: (chunk_id, material_id, similarity) 列表
        """
        query_vector = str(query_embedding)
        params: dict[str, Any] = {
            "query_vector": query_vector,
            "project_id": project_id,
            "top_k": top_k,
        }

        if material_ids:
            params["material_ids"] = [int(mid) for mid in material_ids]
            sql = text("""
                SELECT id, material_id, 1 - (embedding <=> :query_vector) as similarity
                FROM writing_material_chunks
                WHERE project_id = :project_id
                  AND material_id = ANY(:material_ids)
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> :query_vector
                LIMIT :top_k
            """)
        else:
            sql = text("""
                SELECT id, material_id, 1 - (embedding <=> :query_vector) as similarity
                FROM writing_material_chunks
                WHERE project_id = :project_id
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> :query_vector
                LIMIT :top_k
            """)

        result = await self.db.execute(
            sql,
            params,
        )

        return [(row[0], row[1], row[2]) for row in result.fetchall()]


def chunk_ids_to_json(chunk_ids: list[int]) -> str:
    """将 chunk_id 列表序列化为 JSON 字符串

    Args:
        chunk_ids: chunk_id 列表

    Returns:
        str: JSON 字符串,如 "[1, 2, 3]"
    """
    return json.dumps(chunk_ids)


def json_to_chunk_ids(json_str: Optional[str]) -> list[int]:
    """从 JSON 字符串解析 chunk_id 列表

    Args:
        json_str: JSON 字符串

    Returns:
        list[int]: chunk_id 列表,解析失败返回空列表
    """
    if not json_str:
        return []
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return []
