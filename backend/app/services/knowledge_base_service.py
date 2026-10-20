"""知识库服务。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ServiceError
from app.core.filename import sanitize_filename as _shared_sanitize_filename
from app.core.parser import ChunkPlanner
from app.core.parser.chunk_evidence import plan_pages_with_image_evidence
from app.core.permissions import check_project_permission
from app.core.vectorizer.encode_gate import safe_encode, safe_encode_batch
from app.models.kb_document import (
    KbAtom,
    KbAtomChunk,
    KbChunk,
    KbDocument,
    KnowledgeOwnerScope,
    KnowledgeReviewStatus,
)
from app.models.project import Project, ProjectType
from app.models.user import User
from app.models.writing_material import (
    MaterialProcessingStatus,
    MaterialRole,
    MaterialType,
    SplitReviewStatus,
    WritingMaterial,
)
from app.models.writing_material_chunk import WritingMaterialChunk
from app.schemas.knowledge_base import KnowledgeChunkReference


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
_ALLOWED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".txt", ".md", *_IMAGE_SUFFIXES}
_AUTHORITY_RANK = {
    "national_standard": 4,
    "industry_standard": 3,
    "enterprise_standard": 2,
    "user_experience": 1,
}


def _sanitize_filename(name: str) -> str:
    """清理上传文件名，确保 Windows 兼容(委托共享实现)。"""
    return _shared_sanitize_filename(name, fallback="knowledge_document")


class KnowledgeService:
    """知识库资料管理、索引与写标投影服务。

    职责: 管理知识资料生命周期、索引分块和写标素材投影。
    不做: 不直接执行审查结论生成、不绕过权限边界读取跨用户资料。
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.upload_dir = Path(settings.UPLOAD_DIR) / "knowledge_base"
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    async def create_document(
        self,
        *,
        current_user: User,
        filename: str,
        content: bytes,
        mime_type: Optional[str],
        metadata: dict[str, Any],
    ) -> KbDocument:
        """保存知识资料并创建待处理记录。"""
        suffix = Path(filename).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise ServiceError("知识库仅支持 PDF/DOCX/XLSX/TXT/MD/PNG/JPG/WEBP 等文件")
        if not content:
            raise ServiceError("上传文件为空")

        safe_name = _sanitize_filename(filename)
        digest = hashlib.sha256(content).hexdigest()
        target_dir = self.upload_dir / str(current_user.id)
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{digest[:12]}_{safe_name}"
        file_path.write_bytes(content)

        title = str(metadata.get("title") or Path(filename).stem).strip()
        doc = KbDocument(
            owner_id=current_user.id,
            title=title,
            filename=filename,
            file_path=str(file_path),
            file_size=len(content),
            mime_type=mime_type,
            applicable_flows=[str(item.value if hasattr(item, "value") else item) for item in metadata.get("applicable_flows", [])],
            business_type=str(metadata.get("business_type") or "other"),
            business_label=metadata.get("business_label"),
            valid_from=metadata.get("valid_from"),
            valid_to=metadata.get("valid_to"),
            source=metadata.get("source"),
            authority=str(getattr(metadata.get("authority"), "value", metadata.get("authority") or "user_experience")),
            enabled=bool(metadata.get("enabled", True)),
            version=str(metadata.get("version") or "1"),
            owner_scope=str(getattr(metadata.get("owner_scope"), "value", metadata.get("owner_scope") or KnowledgeOwnerScope.personal.value)),
            industry_tags=list(metadata.get("industry_tags") or []),
            region_tags=list(metadata.get("region_tags") or []),
            review_status=str(getattr(metadata.get("review_status"), "value", metadata.get("review_status") or KnowledgeReviewStatus.approved.value)),
            content_fingerprint=digest,
            processing_status="saved",
        )
        self.db.add(doc)
        await self.db.flush()
        await self.db.refresh(doc)
        return doc

    async def process_document(self, document_id: int, current_user: User) -> KbDocument:
        """投递知识资料后台处理任务。"""
        doc = await self.get_document(document_id, current_user)
        doc.processing_status = "processing"
        doc.error_message = None
        await self.db.commit()
        await self.db.refresh(doc)

        from app.core.tasks import dispatch_background_job, is_task_active
        from app.core.tasks.knowledge_base_task import (
            get_knowledge_base_process_task_name,
            run_knowledge_base_process_task,
        )

        task_name = get_knowledge_base_process_task_name(document_id)
        if not is_task_active(task_name):
            await dispatch_background_job(
                task_name=task_name,
                arq_function="knowledge_base_process_job",
                fallback_factory=lambda: run_knowledge_base_process_task(document_id),
                task_type="knowledge_base_process",
                arq_args=(document_id,),
            )
        return doc

    async def execute_process_document(self, document_id: int) -> KbDocument:
        """执行知识资料解析、图片识别、分块与向量化。

 防御:
 - 解析/向量化耗时较长, 期间父文档可能被并发删除或外部清理,
 因此在写 ``kb_chunks/kb_atoms`` 前必须重新拉取一次最新提交快照, 父行
 缺失则立即抛 ``ServiceError`` 终止, 不再撞数据库外键。
 - 异常分支不再在已失败事务里继续 ``flush`` 写失败态, 改由任务层
 (``run_knowledge_base_process_task``) 先 ``rollback`` 再回写
 ``processing_status='failed'`` / ``error_message``。
 """
        doc = await self.db.get(KbDocument, document_id)
        if not doc:
            raise NotFoundError("知识资料不存在")
        doc.processing_status = "processing"
        doc.error_message = None
        await self.db.flush()
        try:
            from app.core.knowledge.atomizer import KnowledgeAtomizer

            atomized = await KnowledgeAtomizer(enable_vl=True).atomize(
                file_path=Path(doc.file_path),
                document_id=int(doc.id),
                image_save_dir=self.upload_dir / "images" / str(doc.id),
                title=doc.title,
                business_type=doc.business_type,
                business_label=doc.business_label,
            )
            resolved_path = getattr(atomized, "resolved_file_path", None)
            if resolved_path and resolved_path != doc.file_path:
                doc.file_path = resolved_path
            page_texts, page_count, full_text = (
                atomized.page_texts,
                atomized.page_count,
                atomized.full_text,
            )
            if not full_text.strip():
                raise ServiceError("知识资料解析结果为空")

            fresh_doc = await self.db.get(
                KbDocument, document_id, populate_existing=True
            )
            if fresh_doc is None:
                raise ServiceError("知识资料已被删除，停止处理")
            doc = fresh_doc

            await self.db.execute(delete(KbChunk).where(KbChunk.document_id == doc.id))
            await self.db.execute(delete(KbAtom).where(KbAtom.document_id == doc.id))
            planner = ChunkPlanner()
            if atomized.parsed_pages:
                chunks = plan_pages_with_image_evidence(planner, atomized.parsed_pages)
            else:
                chunks = planner.plan_page_tuples(page_texts)
            if not chunks:
                raise ServiceError("知识资料分块结果为空")

            texts = [chunk.content for chunk in chunks]
            embeddings = await safe_encode_batch(texts=texts, initial_batch_size=min(32, len(texts)))
            if isinstance(embeddings, tuple):
                embeddings = embeddings[0]

            page_chunk_ids: dict[int, list[int]] = {}
            for idx, chunk in enumerate(chunks):
                page_num = chunk.metadata.get("page_num") or chunk.metadata.get("page")
                section_path = chunk.metadata.get("section_path") or chunk.metadata.get("section")
                fingerprint = str(chunk.metadata.get("chunk_fingerprint") or hashlib.sha256(
                    f"{doc.id}|{idx}|{page_num or ''}|{chunk.content}".encode("utf-8", errors="ignore")
                ).hexdigest())
                kb_chunk = KbChunk(
                    document_id=doc.id,
                    content=chunk.content,
                    embedding=embeddings[idx].tolist(),
                    chunk_type=str(chunk.metadata.get("chunk_type") or "text"),
                    parent_block_id=chunk.metadata.get("parent_block_id"),
                    section_path=section_path,
                    chunk_fingerprint=fingerprint,
                    embedding_version=str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
                    page_num=int(page_num) if page_num else None,
                    chunk_index=idx,
                    char_count=len(chunk.content),
                )
                self.db.add(kb_chunk)
                await self.db.flush()
                if kb_chunk.page_num is not None:
                    page_chunk_ids.setdefault(int(kb_chunk.page_num), []).append(int(kb_chunk.id))

            for atom_index, atom in enumerate(atomized.atoms):
                atom_fingerprint = hashlib.sha256(
                    f"{doc.id}|{atom_index}|{atom.page_start or ''}|{atom.title}|{atom.content}".encode(
                        "utf-8",
                        errors="ignore",
                    )
                ).hexdigest()
                source_chunk_ids: list[int] = []
                if atom.page_start is not None and atom.page_end is not None:
                    for page in range(atom.page_start, atom.page_end + 1):
                        source_chunk_ids.extend(page_chunk_ids.get(page, []))
                kb_atom = KbAtom(
                    document_id=doc.id,
                    title=atom.title,
                    business_type=atom.business_type or doc.business_type,
                    business_label=atom.business_label or doc.business_label,
                    summary=atom.summary,
                    content=atom.content,
                    page_start=atom.page_start,
                    page_end=atom.page_end,
                    atom_type=atom.atom_type,
                    confidence=atom.confidence,
                    source_chunk_ids=source_chunk_ids,
                    source_image_ids=[],
                    fingerprint=atom_fingerprint,
                    processing_status="completed",
                )
                self.db.add(kb_atom)
                await self.db.flush()

                atom_chunks = planner.plan_page_tuples(
                    [(atom.page_start or 1, atom.content)]
                )
                atom_texts = [item.content for item in atom_chunks]
                atom_embeddings = await safe_encode_batch(
                    texts=atom_texts,
                    initial_batch_size=min(32, len(atom_texts)),
                )
                if isinstance(atom_embeddings, tuple):
                    atom_embeddings = atom_embeddings[0]
                for chunk_index, atom_chunk in enumerate(atom_chunks):
                    page_num = atom_chunk.metadata.get("page_num") or atom_chunk.metadata.get("page")
                    fingerprint = hashlib.sha256(
                        f"{kb_atom.id}|{chunk_index}|{page_num or ''}|{atom_chunk.content}".encode(
                            "utf-8",
                            errors="ignore",
                        )
                    ).hexdigest()
                    self.db.add(KbAtomChunk(
                        atom_id=kb_atom.id,
                        document_id=doc.id,
                        content=atom_chunk.content,
                        embedding=atom_embeddings[chunk_index].tolist(),
                        chunk_fingerprint=fingerprint,
                        embedding_version=str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
                        page_num=int(page_num) if page_num else atom.page_start,
                        chunk_index=chunk_index,
                        char_count=len(atom_chunk.content),
                    ))

            doc.content_text = full_text
            doc.page_count = page_count
            doc.processed_at = datetime.now(timezone.utc)
            doc.processing_status = "completed"
            await self.db.flush()
            await self.db.refresh(doc)
            return doc
        except Exception as exc:
            logger.error(f"知识资料处理失败: doc={document_id}, err={exc}", exc_info=True)
            if isinstance(exc, ServiceError):
                raise
            raise ServiceError(f"知识资料处理失败: {exc}")

    async def list_documents(
        self,
        current_user: User,
        *,
        applicable_flow: Optional[str] = None,
        business_type: Optional[str] = None,
        include_disabled: bool = False,
    ) -> list[KbDocument]:
        """列出当前用户可见的知识资料。"""
        stmt = select(KbDocument).order_by(KbDocument.updated_at.desc(), KbDocument.id.desc())
        stmt = stmt.where(KbDocument.owner_id == current_user.id)
        if not include_disabled:
            stmt = stmt.where(KbDocument.enabled.is_(True))
        if business_type:
            stmt = stmt.where(KbDocument.business_type == business_type)
        result = await self.db.execute(stmt)
        docs = list(result.scalars().all())
        if applicable_flow:
            docs = [doc for doc in docs if applicable_flow in (doc.applicable_flows or [])]
        return docs

    async def get_document(self, document_id: int, current_user: User) -> KbDocument:
        """获取并校验知识资料权限。"""
        doc = await self.db.get(KbDocument, document_id)
        if not doc:
            raise NotFoundError("知识资料不存在")
        if not self._can_access(doc, current_user):
            raise ForbiddenError("无权限访问该知识资料")
        return doc

    async def update_document(
        self,
        document_id: int,
        current_user: User,
        payload: dict[str, Any],
    ) -> KbDocument:
        """更新知识资料元数据。"""
        doc = await self.get_document(document_id, current_user)
        nullable_fields = {
            "business_label",
            "valid_from",
            "valid_to",
            "source",
            "superseded_by_id",
        }
        for key, value in payload.items():
            if value is None and key not in nullable_fields:
                continue
            if key in {"applicable_flows"}:
                value = [str(getattr(item, "value", item)) for item in value]
            if key in {"authority", "owner_scope", "review_status"}:
                value = str(getattr(value, "value", value))
            setattr(doc, key, value)
        await self.db.flush()
        await self.db.refresh(doc)
        return doc

    async def delete_document(self, document_id: int, current_user: User) -> None:
        """删除知识资料。

 防御: 处理中或后台任务仍活跃时禁止删除, 否则后台任务会撞外键
 (kb_chunks.document_id 父行不存在) 并在已失败事务里二次 flush 触发
 ``Session's transaction has been rolled back`` 二次错误。
 """
        doc = await self.get_document(document_id, current_user)

        from app.core.tasks import is_task_active
        from app.core.tasks.knowledge_base_task import (
            get_knowledge_base_process_task_name,
        )

        task_name = get_knowledge_base_process_task_name(document_id)
        if doc.processing_status == "processing" or is_task_active(task_name):
            raise ServiceError(
                "该知识资料正在后台处理中，请等待处理完成或失败后再删除"
            )

        if doc.file_path:
            try:
                Path(doc.file_path).unlink(missing_ok=True)
            except OSError:
                pass
        await self.db.delete(doc)
        await self.db.flush()

    async def list_atoms(
        self,
        document_id: int,
        current_user: User,
    ) -> list[KbAtom]:
        """列出当前用户知识资料下的原子素材。"""
        await self.get_document(document_id, current_user)
        result = await self.db.execute(
            select(KbAtom)
            .where(KbAtom.document_id == document_id)
            .order_by(KbAtom.page_start, KbAtom.id)
        )
        return list(result.scalars().all())

    async def search(
        self,
        *,
        query: str,
        current_user: Optional[User] = None,
        owner_id: Optional[int] = None,
        applicable_flow: Optional[str] = None,
        business_types: Optional[list[str]] = None,
        allowed_document_ids: Optional[list[int]] = None,
        top_k: int = 5,
        min_similarity: float = 0.5,
    ) -> list[KnowledgeChunkReference]:
        """检索可自动使用的知识库上下文。

 Args:
 allowed_document_ids: 项目级显式授权的知识资料 ID 列表; 为 None 表示
 调用方未提供白名单 (如知识库后台搜索接口); 为空列表则视为"未授权",
 直接返回空结果, 防止业务流程未配置时把任意资料注入 Prompt。: 方法内部严格分两段:
 1) 入参校验 + query 向量编码 (受 GPU 准入排队, 可能数十秒, 与 DB 解耦)
 2) `_vector_search_kb_chunks` 纯 DB 段 (仅 SQL + ORM 映射, 无慢 await)
 让 DB 段不再被 encode 钉住, 配合 db_session_scope 形成连接池保护。
 """
        effective_owner_id = owner_id or getattr(current_user, "id", None)
        if effective_owner_id is None:
            logger.warning("知识库检索缺少用户上下文，已按空结果降级")
            return []
        if allowed_document_ids is not None and not allowed_document_ids:
            logger.debug(
                "知识库检索未提供授权资料 IDs (空列表)，已按空结果降级"
            )
            return []
        if allowed_document_ids:
            sanitized_doc_ids: list[int] = []
            for doc_id in allowed_document_ids:
                try:
                    sanitized_doc_ids.append(int(doc_id))
                except (TypeError, ValueError):
                    continue
            if not sanitized_doc_ids:
                logger.debug(
                    "知识库检索的授权资料 IDs 全部非法，已按空结果降级"
                )
                return []
        else:
            sanitized_doc_ids = []

        query_embedding = (await safe_encode([query], initial_batch_size=1))[0].tolist()

        return await self._vector_search_kb_chunks(
            query_embedding=query_embedding,
            effective_owner_id=int(effective_owner_id),
            applicable_flow=applicable_flow,
            business_types=business_types,
            allowed_document_ids=sanitized_doc_ids,
            top_k=int(top_k),
            min_similarity=float(min_similarity),
        )

    async def _vector_search_kb_chunks(
        self,
        *,
        query_embedding: list[float],
        effective_owner_id: int,
        applicable_flow: Optional[str],
        business_types: Optional[list[str]],
        allowed_document_ids: list[int],
        top_k: int,
        min_similarity: float,
    ) -> list[KnowledgeChunkReference]:
        """职责: 仅以预编码 query 向量在 kb_chunks 上做 pgvector 相似度检索 + 权威度排序。
 不做: 不做 encode/embedding、不做权限解析、不做项目级配置加载。: 抽离原因 -- 让本段成为"纯 DB 段", 在并发循环里被 `db_session_scope`
 许可的连接持有期内只跑 SQL, 不再混入 GPU 编码等慢 await, 保证连接很快归还。
 """
        params: dict[str, Any] = {
            "query_vector": str(query_embedding),
            "top_k": top_k,
            "min_similarity": min_similarity,
            "today": datetime.now(timezone.utc).date(),
            "flow": applicable_flow,
            "user_id": effective_owner_id,
        }
        where = [
            "c.embedding IS NOT NULL",
            "d.owner_id = :user_id",
            "d.enabled = TRUE",
            "d.processing_status = 'completed'",
            "d.review_status = 'approved'",
            "d.superseded_by_id IS NULL",
            "(d.valid_from IS NULL OR d.valid_from <= :today)",
            "(d.valid_to IS NULL OR d.valid_to >= :today)",
        ]
        if applicable_flow:
            where.append("d.applicable_flows::jsonb ? :flow")
        if allowed_document_ids:
            allowed_placeholders: list[str] = []
            for idx, int_id in enumerate(allowed_document_ids):
                key = f"allowed_doc_{idx}"
                params[key] = int_id
                allowed_placeholders.append(f":{key}")
            where.append(f"d.id IN ({', '.join(allowed_placeholders)})")
        if business_types:
            placeholders = []
            for idx, item in enumerate(business_types):
                key = f"business_type_{idx}"
                params[key] = item
                placeholders.append(f":{key}")
            where.append(f"d.business_type IN ({', '.join(placeholders)})")

        sql = text(f"""
            SELECT
                c.id AS chunk_id,
                c.document_id AS document_id,
                c.content AS content,
                c.page_num AS page_num,
                d.title AS title,
                d.authority AS authority,
                d.version AS version,
                1 - (c.embedding <=> :query_vector) AS similarity
            FROM kb_chunks c
            JOIN kb_documents d ON d.id = c.document_id
            WHERE {' AND '.join(where)}
              AND 1 - (c.embedding <=> :query_vector) >= :min_similarity
            ORDER BY c.embedding <=> :query_vector
            LIMIT :top_k
        """)
        rows = (await self.db.execute(sql, params)).mappings().all()
        results = [
            KnowledgeChunkReference(
                kb_doc_id=int(row["document_id"]),
                kb_chunk_id=int(row["chunk_id"]),
                page_num=row["page_num"],
                title=str(row["title"]),
                authority=str(row["authority"]),
                version=str(row["version"]),
                similarity=float(row["similarity"] or 0),
                content=str(row["content"]),
            )
            for row in rows
        ]
        results.sort(
            key=lambda item: (
                _AUTHORITY_RANK.get(item.authority, 0),
                item.similarity,
            ),
            reverse=True,
        )
        return results[:top_k]

    async def project_to_writing_materials(
        self,
        *,
        project_id: int,
        current_user: User,
        kb_document_ids: list[int],
    ) -> tuple[list[int], list[int], list[str]]:
        """将知识资料影子复制为当前写标项目素材。"""
        project = await self.db.get(Project, project_id)
        if not project:
            raise NotFoundError("项目不存在")
        check_project_permission(project, current_user)
        if project.project_type != ProjectType.BID_WRITING:
            raise ServiceError("仅标书编写项目支持引用知识库素材")

        created_ids: list[int] = []
        skipped_ids: list[int] = []
        warnings: list[str] = []

        for kb_id in kb_document_ids:
            kb_doc = await self.get_document(kb_id, current_user)
            if "bid_writing" not in (kb_doc.applicable_flows or []):
                skipped_ids.append(kb_id)
                warnings.append(f"知识资料 {kb_doc.title} 未标记适用于标书编写，已跳过")
                continue
            if not self._is_auto_usable(kb_doc):
                skipped_ids.append(kb_id)
                warnings.append(f"知识资料 {kb_doc.title} 已过期、停用或未审核，已跳过")
                continue

            existing = await self.db.execute(
                select(WritingMaterial).where(
                    WritingMaterial.project_id == project_id,
                    WritingMaterial.kb_source_id == kb_id,
                )
            )
            if existing.scalar_one_or_none():
                skipped_ids.append(kb_id)
                warnings.append(f"知识资料 {kb_doc.title} 已引用到当前项目，已跳过")
                continue

            chunk_rows = await self.db.execute(
                select(KbChunk)
                .where(KbChunk.document_id == kb_id)
                .order_by(KbChunk.chunk_index)
            )
            kb_chunks = list(chunk_rows.scalars().all())
            if not kb_chunks:
                skipped_ids.append(kb_id)
                warnings.append(f"知识资料 {kb_doc.title} 尚未完成索引，已跳过")
                continue

            material = WritingMaterial(
                project_id=project_id,
                document_id=None,
                material_type=MaterialType.text,
                title=kb_doc.title,
                display_title=kb_doc.title,
                content=kb_doc.content_text or "\n\n".join(chunk.content for chunk in kb_chunks),
                file_path=kb_doc.file_path,
                metadata_tags=json.dumps({
                    "source": "knowledge_base",
                    "kb_doc_id": kb_doc.id,
                    "authority": kb_doc.authority,
                    "version": kb_doc.version,
                    "business_type": kb_doc.business_type,
                    "business_label": kb_doc.business_label,
                }, ensure_ascii=False),
                processing_status=MaterialProcessingStatus.completed.value,
                resolved_label=kb_doc.business_label or kb_doc.business_type,
                label_conflict="matched",
                label_source="kb",
                material_role=MaterialRole.normal.value,
                split_review_status=SplitReviewStatus.none.value,
                kb_source_id=kb_doc.id,
                kb_source_version=kb_doc.version,
                kb_snapshot_at=datetime.now(timezone.utc),
                kb_source_chunk_ids=json.dumps([chunk.id for chunk in kb_chunks], ensure_ascii=False),
            )
            self.db.add(material)
            await self.db.flush()

            chunk_ids = await self._copy_kb_chunks_to_writing_material(
                material_id=int(material.id),
                project_id=project_id,
                chunks=kb_chunks,
            )
            material.chunk_ids = json.dumps(chunk_ids, ensure_ascii=False)
            created_ids.append(int(material.id))

        await self.db.flush()
        return created_ids, skipped_ids, warnings

    async def project_atoms_to_writing_materials(
        self,
        *,
        project_id: int,
        current_user: User,
        kb_atom_ids: list[int],
    ) -> tuple[list[int], list[int], list[str]]:
        """将知识库原子素材影子复制为当前写标项目素材。"""
        project = await self.db.get(Project, project_id)
        if not project:
            raise NotFoundError("项目不存在")
        check_project_permission(project, current_user)
        if project.project_type != ProjectType.BID_WRITING:
            raise ServiceError("仅标书编写项目支持引用知识库素材")

        created_ids: list[int] = []
        skipped_ids: list[int] = []
        warnings: list[str] = []

        for atom_id in kb_atom_ids:
            atom = await self.db.get(KbAtom, atom_id)
            if not atom:
                skipped_ids.append(atom_id)
                warnings.append(f"知识库原子素材 {atom_id} 不存在，已跳过")
                continue
            kb_doc = await self.get_document(int(atom.document_id), current_user)
            if "bid_writing" not in (kb_doc.applicable_flows or []):
                skipped_ids.append(atom_id)
                warnings.append(f"知识资料 {kb_doc.title} 未标记适用于标书编写，已跳过")
                continue
            if not self._is_auto_usable(kb_doc) or atom.processing_status != "completed":
                skipped_ids.append(atom_id)
                warnings.append(f"知识库原子素材 {atom.title} 当前不可引用，已跳过")
                continue

            existing = await self.db.execute(
                select(WritingMaterial).where(
                    WritingMaterial.project_id == project_id,
                    WritingMaterial.kb_atom_id == atom_id,
                )
            )
            if existing.scalar_one_or_none():
                skipped_ids.append(atom_id)
                warnings.append(f"知识库原子素材 {atom.title} 已引用到当前项目，已跳过")
                continue

            atom_chunk_rows = await self.db.execute(
                select(KbAtomChunk)
                .where(KbAtomChunk.atom_id == atom_id)
                .order_by(KbAtomChunk.chunk_index)
            )
            atom_chunks = list(atom_chunk_rows.scalars().all())
            if not atom_chunks:
                skipped_ids.append(atom_id)
                warnings.append(f"知识库原子素材 {atom.title} 尚未完成分块索引，已跳过")
                continue
            material = WritingMaterial(
                project_id=project_id,
                document_id=None,
                material_type=MaterialType.text,
                title=atom.title,
                display_title=atom.title,
                content=atom.content,
                file_path=kb_doc.file_path,
                metadata_tags=json.dumps({
                    "source": "knowledge_base_atom",
                    "kb_doc_id": kb_doc.id,
                    "kb_atom_id": atom.id,
                    "authority": kb_doc.authority,
                    "version": kb_doc.version,
                    "business_type": atom.business_type,
                    "business_label": atom.business_label,
                    "atom_type": atom.atom_type,
                }, ensure_ascii=False),
                source_page=atom.page_start,
                source_page_start=atom.page_start,
                source_page_end=atom.page_end,
                processing_status=MaterialProcessingStatus.completed.value,
                resolved_label=atom.business_label or atom.business_type,
                label_conflict="matched",
                label_source="kb",
                material_role=MaterialRole.normal.value,
                split_review_status=SplitReviewStatus.none.value,
                kb_source_id=kb_doc.id,
                kb_atom_id=atom.id,
                kb_source_version=kb_doc.version,
                kb_snapshot_at=datetime.now(timezone.utc),
                kb_source_chunk_ids=json.dumps(atom.source_chunk_ids or [], ensure_ascii=False),
            )
            self.db.add(material)
            await self.db.flush()

            chunk_ids = await self._copy_kb_chunks_to_writing_material(
                material_id=int(material.id),
                project_id=project_id,
                chunks=atom_chunks,
            )
            material.chunk_ids = json.dumps(chunk_ids, ensure_ascii=False)
            created_ids.append(int(material.id))

        await self.db.flush()
        return created_ids, skipped_ids, warnings

    async def _copy_kb_chunks_to_writing_material(
        self,
        *,
        material_id: int,
        project_id: int,
        chunks: list[Any],
    ) -> list[int]:
        """将已完成索引的知识库分块快照复制为项目素材分块。

        引用阶段禁止重新分块/向量化；知识库 worker 已经完成重处理，
        这里仅复制文本、embedding 和来源页码，保证 API 请求轻量。
        """
        chunk_ids: list[int] = []
        for idx, chunk in enumerate(chunks):
            content = (getattr(chunk, "content", "") or "").replace("\x00", "")
            if not content.strip():
                continue
            source_fingerprint = (
                getattr(chunk, "chunk_fingerprint", None)
                or f"kb:{getattr(chunk, 'id', idx)}"
            )
            writing_chunk = WritingMaterialChunk(
                material_id=material_id,
                project_id=project_id,
                content=content,
                embedding=getattr(chunk, "embedding", None),
                chunk_fingerprint=str(source_fingerprint),
                embedding_version=str(
                    getattr(chunk, "embedding_version", None)
                    or getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")
                ),
                parent_block_id=getattr(chunk, "parent_block_id", None),
                section_path=getattr(chunk, "section_path", None),
                chunk_index=idx,
                char_count=len(content),
                page_num=getattr(chunk, "page_num", None),
            )
            self.db.add(writing_chunk)
            await self.db.flush()
            chunk_ids.append(int(writing_chunk.id))
        return chunk_ids

    async def _extract_content(
        self,
        path: Path,
        document_id: int,
    ) -> tuple[list[tuple[int, str]], int, str]:
        """复用写标素材底层处理能力提取知识资料文本。"""
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md"}:
            text_content = path.read_text(encoding="utf-8", errors="ignore")
            return [(1, text_content)], 1, text_content

        from app.core.config import settings
        from app.core.convert.office_to_pdf import normalize_office_path_for_parse
        from app.core.writer.material_content_extractor import MaterialContentExtractor

        resolved_path, parse_ext = await normalize_office_path_for_parse(
            path,
            timeout_sec=int(settings.OFFICE_CONVERT_TIMEOUT_SEC),
        )
        extractor = MaterialContentExtractor(enable_vl=True)
        image_save_dir = self.upload_dir / "images" / str(document_id)
        image_save_dir.mkdir(parents=True, exist_ok=True)
        parse_result = extractor.parse_only(
            file_path=str(resolved_path),
            ext=parse_ext,
            image_save_dir=image_save_dir,
        )
        if parse_result.images:
            await extractor.batch_vl_recognize(
                parse_result.images,
                project_id=0,
            )
        material_content = extractor.assemble_with_vl(parse_result)
        full_text = material_content.text or ""
        page_texts = material_content.page_texts or [(1, full_text)]
        return page_texts, material_content.page_count, full_text

    async def get_project_usage_config(
        self,
        *,
        project_id: int,
        current_user: User,
    ) -> "ProjectKnowledgeUsage":  # type: ignore[name-defined]
        """读取项目级知识库使用配置（带项目权限校验）。"""
        from app.core.knowledge.usage_config import (
            ProjectKnowledgeUsage,
            parse_usage_config,
        )

        project = await self.db.get(Project, project_id)
        if not project:
            raise NotFoundError("项目不存在")
        check_project_permission(project, current_user)
        if project.knowledge_usage_config is None:
            return ProjectKnowledgeUsage()
        return parse_usage_config(project.knowledge_usage_config)

    async def update_project_usage_config(
        self,
        *,
        project_id: int,
        current_user: User,
        enabled: bool,
        flow: Optional[str],
        selected_kb_document_ids: list[int],
        usage_policy: Optional[str] = None,
    ) -> "ProjectKnowledgeUsage":  # type: ignore[name-defined]
        """更新项目级知识库使用配置。

        校验所选资料属于当前用户、未停用、已索引、适用所选 flow，
        过滤后的有效 IDs 才会写入；空 IDs 自动按"未启用"清空配置。
        """
        from app.core.knowledge.usage_config import (
            ProjectKnowledgeUsage,
            build_usage_payload,
            parse_usage_config,
        )

        project = await self.db.get(Project, project_id)
        if not project:
            raise NotFoundError("项目不存在")
        check_project_permission(project, current_user)

        valid_ids: list[int] = []
        if enabled and flow and selected_kb_document_ids:
            seen: set[int] = set()
            for raw_id in selected_kb_document_ids:
                try:
                    int_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if int_id in seen:
                    continue
                seen.add(int_id)
                doc = await self.db.get(KbDocument, int_id)
                if not doc or doc.owner_id != current_user.id:
                    continue
                if not self._is_auto_usable(doc):
                    continue
                if flow not in (doc.applicable_flows or []):
                    continue
                valid_ids.append(int_id)

        payload = build_usage_payload(
            enabled=enabled,
            flow=flow or "",
            selected_kb_document_ids=valid_ids,
            usage_policy=usage_policy,
        )
        project.knowledge_usage_config = payload
        await self.db.flush()
        await self.db.refresh(project)
        return parse_usage_config(project.knowledge_usage_config)

    def _can_access(self, doc: KbDocument, current_user: User) -> bool:
        """判断用户是否可访问知识资料。"""
        return doc.owner_id == current_user.id

    def _is_auto_usable(self, doc: KbDocument) -> bool:
        """判断知识资料是否可自动召回/投影。"""
        today = datetime.now(timezone.utc).date()
        if not doc.enabled or doc.processing_status != "completed":
            return False
        if doc.review_status != KnowledgeReviewStatus.approved.value:
            return False
        if doc.superseded_by_id:
            return False
        if doc.valid_from and doc.valid_from > today:
            return False
        if doc.valid_to and doc.valid_to < today:
            return False
        return True

