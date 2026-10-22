"""文档服务

处理文档上传、解析、分块、向量化等离线处理流程。
"""

import os
import shutil
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bid_envelope import (
    envelope_is_complete,
    envelope_mode_conflict,
    load_bid_envelope,
)
from app.core.config import settings
from app.core.convert.office_to_pdf import (
    ALLOWED_UPLOAD_SUFFIXES,
    UPLOAD_FORMAT_ERROR,
    office_convert_siblings,
    suffix_of,
)
from app.core.filename import sanitize_filename as _shared_sanitize_filename
from app.core.parser import ChunkPlanner, PdfParser
from app.core.parser.image_extractor import (
    build_natural_evidence_chunk,
    extract_context_for_images,
    generate_image_annotations,
    insert_annotation_near_anchor,
)
from app.core.vectorizer import VectorIndexer
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus, ProjectType
from app.schemas.document import ProcessingResult

_RUNNING_STATUSES = frozenset({
    ProjectStatus.EXTRACTING,
    ProjectStatus.OUTLINING,
    ProjectStatus.MATCHING,
    ProjectStatus.WRITING,
    ProjectStatus.REVIEWING,
})

_BID_REVIEW_UPLOAD_LOCKED_STATUSES = frozenset({
    ProjectStatus.UPLOADING,
    ProjectStatus.PARSING,
    ProjectStatus.PARSED,
    ProjectStatus.EXTRACTING,
    ProjectStatus.EXTRACTED,
    ProjectStatus.REVIEWING,
    ProjectStatus.COMPLETED,
})

_BID_REVIEW_RESETTABLE_STATUSES = frozenset({
    ProjectStatus.CREATED,
    ProjectStatus.UPLOADING,
    ProjectStatus.PARSING,
    ProjectStatus.FAILED,
})

def _sanitize_filename(name: str) -> str:
    """清理文件名，确保 Windows 兼容(委托共享实现)。"""
    return _shared_sanitize_filename(name, fallback="unnamed.pdf")


class DocumentService:
    """文档服务类
    
    负责文档的上传、存储和离线处理流程。
    
    离线处理流程:
    1. PDF解析 (pdfplumber)
    2. 文本分块 (LangChain)
    3. 向量化存储 (bge-m3 + PGVector)
    """
    
    def __init__(self, db: AsyncSession):
        """初始化文档服务
        
        Args:
            db: 数据库会话
        """
        self.db = db
        self.upload_dir = Path(settings.UPLOAD_DIR)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
    
    async def find_duplicate_document(
        self,
        project_id: int,
        file_hash: str,
        exclude_document_id: int,
    ) -> Optional[Document]:
        """查找同项目内字节指纹相同的其他文档(缺口D: 副本上传 fail-visible 提示)。"""
        if not file_hash:
            return None
        result = await self.db.execute(
            select(Document)
            .where(
                Document.project_id == project_id,
                Document.file_hash == file_hash,
                Document.id != exclude_document_id,
            )
            .order_by(Document.id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def save_uploaded_file(
        self,
        project_id: int,
        doc_type: DocumentType,
        filename: str,
        file_content: bytes,
        bid_part: Optional[str] = None,
    ) -> Document:
        """保存上传的文件
        
        Args:
            project_id: 项目ID
            doc_type: 文档类型
            filename: 原始文件名
            file_content: 文件内容
            bid_part: 投标信封卷角色(None=整本; 仅投标审查 BID 文档可指定)
            
        Returns:
            Document: 创建的文档记录
            
        Raises:
            ValueError: 项目不存在或文件类型错误
        """
        import time as _time

        # 验证项目存在
        project = await self._get_project(project_id)
        if not project:
            raise ValueError(f"项目不存在: id={project_id}")
        is_bid_review = project.project_type == ProjectType.BID_REVIEW
        if bid_part is not None and not (
            is_bid_review and doc_type == DocumentType.BID
        ):
            raise ValueError("仅投标审查项目的投标文件支持分卷上传")
        if is_bid_review and project.status in _BID_REVIEW_UPLOAD_LOCKED_STATUSES:
            raise ValueError(
                f"投标审查项目当前处于 {project.status.value} 状态，"
                "不能继续补传单个文件，请重新开始上传"
            )
        
        if suffix_of(filename) not in ALLOWED_UPLOAD_SUFFIXES:
            raise ValueError(UPLOAD_FORMAT_ERROR)
        
        # 生成存储路径
        project_dir = self.upload_dir / str(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)

        # 串标项目的 BID 文档：追加模式，不替换
        is_collusion_bid = (
            project.project_type == ProjectType.COLLUSION_CHECK
            and doc_type == DocumentType.BID
        )

        if is_collusion_bid:
            # 数量校验
            existing_count = await self._count_documents_by_type(project_id, DocumentType.BID)
            if existing_count >= settings.COLLUSION_MAX_BID_DOCS:
                raise ValueError(
                    f"串标项目最多允许上传 {settings.COLLUSION_MAX_BID_DOCS} 份投标文件，"
                    f"当前已有 {existing_count} 份"
                )
            # 用时间戳区分文件名，防止同名覆盖
            safe_name = _sanitize_filename(filename)
            file_path = project_dir / f"bid_{int(_time.time())}_{safe_name}"
        else:
            # 投标信封模式冲突: 整本与分卷(或跨档案卷别)禁止混装,
            # 已按某一模式上传后只能同卷替换或重新开始上传(同形态)
            if is_bid_review and doc_type == DocumentType.BID:
                envelope = await load_bid_envelope(self.db, project_id)
                if envelope.has_documents and envelope_mode_conflict(
                    envelope.parts, bid_part
                ):
                    raise ValueError(
                        "投标文件模式冲突：整本与分卷（商务标/技术标）不能混合上传，"
                        "如需切换模式请先重新开始上传"
                    )
            # 投标审查项目原逻辑：替换同类型(同卷角色)文档
            existing = await self._get_document_by_type(
                project_id, doc_type, bid_part=bid_part
            )
            if existing and project.status in _RUNNING_STATUSES:
                raise ValueError(
                    f"项目正在处理中({project.status.value})，"
                    f"请等待当前任务完成后再重新上传"
                )
            if existing:
                await self._delete_document(existing)
            safe_name = _sanitize_filename(filename)
            # 分卷文件磁盘名带卷角色, 防止商务/技术同名文件互相覆盖
            stem = f"{doc_type.value}_{bid_part}" if bid_part else doc_type.value
            file_path = project_dir / f"{stem}_{safe_name}"
        
        # 保存文件
        try:
            with open(file_path, 'wb') as f:
                f.write(file_content)
        except OSError as e:
            logger.error(f"文件保存失败: path={file_path}, errno={e.errno}, msg={e}")
            raise ValueError(f"文件保存失败(文件名或路径异常): {e}")
        
        # 创建文档记录(缺口D: 落字节指纹, 供同项目字节级副本检测)
        import hashlib as _hashlib

        document = Document(
            project_id=project_id,
            doc_type=doc_type,
            bid_part=bid_part,
            filename=filename,
            file_path=str(file_path),
            file_size=len(file_content),
            file_hash=_hashlib.sha256(file_content).hexdigest(),
            parsed=0
        )
        
        self.db.add(document)
        await self.db.flush()
        await self.db.refresh(document)
        
        # 投标审查必须以 tender+bid 双文件完整性作为项目状态边界。
        # 单份文件落库只代表暂存成功，不能把项目推进到 uploading。
        if is_bid_review:
            project.status = ProjectStatus.CREATED
        else:
            project.status = ProjectStatus.UPLOADING
        await self.db.flush()
        
        logger.info(
            f"文件上传成功: project_id={project_id}, "
            f"doc_type={doc_type.value}, filename={filename}"
        )
        
        return document
    
    async def process_document(self, document_id: int) -> ProcessingResult:
        """执行离线处理流程
        
        完整流程: PDF解析 → 文本分块 → 向量化存储
        
        Args:
            document_id: 文档ID
            
        Returns:
            ProcessingResult: 处理结果
        """
        # 获取文档
        document = await self._get_document(document_id)
        if not document:
            return ProcessingResult(
                document_id=document_id,
                success=False,
                message="文档不存在",
                error=f"Document not found: id={document_id}"
            )
        
        # 更新项目状态
        project = await self._get_project(document.project_id)
        if project:
            project.status = ProjectStatus.PARSING
            await self.db.flush()
        
        try:
            logger.info(f"开始处理文档: id={document_id}, file={document.filename}")
            
            # 1. PDF解析（含图片提取）
            image_save_dir = self._get_document_image_dir(
                document.project_id, document_id
            )
            if image_save_dir.exists():
                shutil.rmtree(image_save_dir, ignore_errors=True)
            
            parser = PdfParser()
            parsed_doc = parser.parse(
                document.file_path,
                image_save_dir=image_save_dir,
            )
            
            document.page_count = parsed_doc.page_count
            await self.db.flush()
            
            image_count = len(parsed_doc.all_images)
            logger.info(
                f"PDF解析完成: {parsed_doc.page_count} 页, "
                f"{image_count} 张图片"
            )
            
            # 2. VL视觉模型识别图片内容（如已配置视觉模型）
            all_images = parsed_doc.all_images
            if all_images:
                try:
                    from app.core.parser.image_recognition_service import recognize_images
                    await recognize_images(
                        all_images,
                        purpose="文档同步处理",
                    )
                except Exception as e:
                    logger.warning(f"VL视觉识别跳过或失败(不影响后续流程): {e}")

            # 3. 文本分块（将图片元数据+VL识别结果+上下文语义追加到对应页面文本中）
            planner = ChunkPlanner()
            all_pages = parsed_doc.pages
            for idx, p in enumerate(all_pages):
                page_text = p.text or ""

                # 为本页图片提取上下文语义信息
                if p.images:
                    window_texts = []
                    for offset in range(-2, 3):
                        ni = idx + offset
                        if 0 <= ni < len(all_pages) and ni != idx:
                            t = all_pages[ni].text or ""
                            if t.strip():
                                window_texts.append(t.strip())
                    context = extract_context_for_images(
                        current_page_text=page_text,
                        prev_page_text="\n".join(window_texts),
                    )
                    image_annotation = generate_image_annotations(
                        p.images, context=context
                    )
                    if image_annotation:
                        page_text = insert_annotation_near_anchor(
                            page_text, image_annotation
                        )
                p.text = page_text

            chunks = planner.plan_parsed_pages(all_pages)

            for idx, p in enumerate(all_pages):
                if not p.images:
                    continue
                window_texts = []
                for offset in range(-2, 3):
                    ni = idx + offset
                    if 0 <= ni < len(all_pages) and ni != idx:
                        t = all_pages[ni].text or ""
                        if t.strip():
                            window_texts.append(t.strip())
                evidence_text = build_natural_evidence_chunk(
                    page_num=p.page_num,
                    images=p.images,
                    context="\n".join(window_texts),
                )
                if evidence_text and len(evidence_text) >= 20:
                    chunk = planner.build_manual_chunk(
                        content=evidence_text,
                        page_num=p.page_num,
                        chunk_type="image_evidence",
                        parent_block_id=f"image-evidence:{p.page_num}",
                        section_path="图片证据",
                    )
                    chunk.chunk_index = len(chunks)
                    chunk.metadata["is_evidence"] = True
                    chunks.append(chunk)
            
            logger.info(f"文本分块完成: {len(chunks)} 个分块")
            
            # 4. 向量化存储
            indexer = VectorIndexer(self.db)
            db_chunks = await indexer.index_chunks(document_id, chunks)
            
            logger.info(f"向量化存储完成: {len(db_chunks)} 个向量")
            
            # 更新文档状态
            document.parsed = 1
            await self.db.flush()
            
            # 更新项目状态
            if project:
                from app.services.document_parse_readiness import (
                    apply_status_after_document_parse,
                    project_required_documents_are_parsed,
                )

                all_parsed = await project_required_documents_are_parsed(
                    self.db, project,
                )
                apply_status_after_document_parse(
                    project, all_required_parsed=all_parsed,
                )
                await self.db.flush()
            
            return ProcessingResult(
                document_id=document_id,
                success=True,
                page_count=parsed_doc.page_count,
                chunk_count=len(db_chunks),
                message="处理完成"
            )
            
        except Exception as e:
            logger.error(f"文档处理失败: {e}", exc_info=True)
            
            # 更新项目状态为失败
            if project:
                project.status = ProjectStatus.FAILED
                await self.db.flush()
            
            return ProcessingResult(
                document_id=document_id,
                success=False,
                message="处理失败",
                error=str(e)
            )
    
    async def get_project_documents(self, project_id: int) -> dict:
        """获取项目的所有文档

        Args:
            project_id: 项目ID

        Returns:
            dict: {"tender": Document | None, "bid": Document | None, "bids": list[Document]}
                  bid 字段向后兼容，保留第一份 BID 文档；
                  bids 字段包含全部 BID 文档（串标项目使用）。
        """
        result: dict = {"tender": None, "bid": None, "bids": []}

        stmt = select(Document).where(Document.project_id == project_id)
        docs_result = await self.db.execute(stmt)
        documents = docs_result.scalars().all()

        for doc in documents:
            if doc.doc_type == DocumentType.TENDER:
                result["tender"] = doc
            elif doc.doc_type == DocumentType.BID:
                result["bids"].append(doc)
                if result["bid"] is None:
                    result["bid"] = doc  # 向后兼容：保留第一份 BID

        return result
    
    async def _get_project(self, project_id: int) -> Optional[Project]:
        """获取项目"""
        result = await self.db.execute(
            select(Project).where(Project.id == project_id)
        )
        return result.scalar_one_or_none()
    
    async def _get_document(self, document_id: int) -> Optional[Document]:
        """获取文档"""
        result = await self.db.execute(
            select(Document).where(Document.id == document_id)
        )
        return result.scalar_one_or_none()
    
    async def get_document_by_id_and_project(
        self,
        document_id: int,
        project_id: int
    ) -> Optional[Document]:
        """通过ID和项目ID获取文档
        
        Args:
            document_id: 文档ID
            project_id: 项目ID
            
        Returns:
            Document: 文档对象，如果不存在或不属于该项目则返回None
        """
        result = await self.db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.project_id == project_id
            )
        )
        return result.scalar_one_or_none()
    
    async def _get_document_by_type(
        self,
        project_id: int,
        doc_type: DocumentType,
        bid_part: Optional[str] = None,
    ) -> Optional[Document]:
        """按类型(BID 文档再按卷角色)获取文档, 供上传同键替换使用。"""
        stmt = select(Document).where(
            Document.project_id == project_id,
            Document.doc_type == doc_type,
        )
        if doc_type == DocumentType.BID:
            # 信封语义: 同卷替换, 整本(NULL)与各分卷互不替换
            stmt = stmt.where(
                Document.bid_part.is_(None)
                if bid_part is None
                else Document.bid_part == bid_part
            )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _count_documents_by_type(
        self,
        project_id: int,
        doc_type: DocumentType
    ) -> int:
        """统计指定类型文档数量"""
        from sqlalchemy import func as _func
        result = await self.db.execute(
            select(_func.count()).where(
                Document.project_id == project_id,
                Document.doc_type == doc_type,
            )
        )
        return result.scalar() or 0
    
    async def _delete_document(self, document: Document) -> None:
        """删除文档及其文件"""
        if document.file_path:
            if os.path.exists(document.file_path):
                os.remove(document.file_path)
            for sibling in office_convert_siblings(document.file_path):
                try:
                    if sibling.exists():
                        sibling.unlink()
                except OSError as exc:
                    logger.warning(
                        "清理转换配对文件失败: path={} err={}", sibling, exc
                    )

        image_dir = self._get_document_image_dir(document.project_id, document.id)
        if image_dir.exists():
            shutil.rmtree(image_dir, ignore_errors=True)
        
        # 删除数据库记录（级联删除chunks）
        await self.db.delete(document)
        await self.db.flush()
        
        logger.info(f"删除文档: id={document.id}, file={document.filename}")
    
    async def _check_all_documents_parsed(self, project_id: int) -> bool:
        """检查项目的所有文档是否都已解析

        投标审查：招标文件 + 投标文件各1份，全部 parsed==1
        串标审查：任意数量 BID 文件（可选招标文件），全部 parsed==1
        """
        result = await self.db.execute(
            select(Document).where(Document.project_id == project_id)
        )
        docs = result.scalars().all()
        if not docs:
            return False
        return all(d.parsed == 1 for d in docs)

    async def has_bid_review_document_pair(self, project_id: int) -> bool:
        """检查投标审查项目文档是否齐套(招标文件 + 投标信封齐套)。

        投标信封齐套 = 整本 1 份, 或分卷(商务标+技术标)各 1 份。
        只校验文件记录存在性，不要求 parsed=1；文档处理入口用它阻止
        半成品(缺招标/缺卷)启动解析。
        """
        docs = await self.get_project_documents(project_id)
        if docs["tender"] is None:
            return False
        return envelope_is_complete(
            [doc.bid_part for doc in docs.get("bids", [])]
        )

    async def delete_unparsed_document(self, project_id: int, document_id: int) -> dict:
        """删除尚未解析的文档(门禁"返回更换文档"场景)。

        职责: 校验文档可删除性并删除文件与记录; bid_review 项目回退到 created。
        不做: 不删除已解析文档，不清理审查/提取等下游数据(此时尚不存在)。
        """
        document = await self.get_document_by_id_and_project(document_id, project_id)
        if not document:
            raise ValueError(f"文档不存在或不属于该项目: id={document_id}")
        if document.parsed == 1:
            raise ValueError("文档已解析完成，不能通过该入口删除")

        project = await self._get_project(project_id)
        if project and project.status in _RUNNING_STATUSES:
            raise ValueError(
                f"项目正在处理中({project.status.value})，不能删除文档"
            )

        await self._delete_document(document)

        if project and project.project_type == ProjectType.BID_REVIEW:
            project.status = ProjectStatus.CREATED
            await self.db.flush()

        return {"document_id": document_id, "deleted": True}

    async def reset_bid_review_upload(self, project_id: int) -> dict:
        """重置投标审查上传半成品状态。

        职责: 清理投标审查上传阶段产生的文档及其级联数据，并把项目回到 created。
        不做: 不恢复审查/提取任务，不处理串标或标书编写流程。
        """
        project = await self._get_project(project_id)
        if not project:
            raise ValueError(f"项目不存在: id={project_id}")
        if project.project_type != ProjectType.BID_REVIEW:
            raise ValueError("仅投标审查项目支持重置上传")
        if project.status not in _BID_REVIEW_RESETTABLE_STATUSES:
            raise ValueError(
                f"项目当前状态为 {project.status.value}，不能重置上传"
            )

        result = await self.db.execute(
            select(Document).where(Document.project_id == project_id)
        )
        documents = result.scalars().all()
        for document in documents:
            await self._delete_document(document)

        project.status = ProjectStatus.CREATED
        project.error_message = None
        await self.db.flush()
        return {"project_id": project_id, "status": project.status.value}

    def _get_document_image_dir(self, project_id: int, document_id: int) -> Path:
        """返回文档级图片目录。"""
        return self.upload_dir / str(project_id) / "images" / f"doc_{document_id}"
