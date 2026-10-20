"""串标分析启动前文档可用性检查。

职责: 汇总串标分析启动前的文档可用性检查结果。
不做: 不执行文档解析、向量化、LLM提取或任务投递。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document, DocumentType
from app.models.vector_job_state import VectorJobState, VectorJobStatus


@dataclass(frozen=True)
class CollusionDocumentReadiness:
    """单份文档的串标分析可用性检查结果。"""

    document_id: int
    filename: str
    file_exists: bool
    file_size: int
    parsed: int
    chunk_count: int
    embedding_count: int
    vector_status: str | None
    blocking_reason: str | None
    retry_action: str | None

    @property
    def ready(self) -> bool:
        """该文档是否可进入串标分析。"""
        return self.blocking_reason is None


@dataclass(frozen=True)
class CollusionReadinessReport:
    """项目串标分析可用性检查结果。"""

    documents: list[CollusionDocumentReadiness]

    @property
    def ready(self) -> bool:
        """所有文档均可进入串标分析。"""
        return all(item.ready for item in self.documents)

    @property
    def blocking_documents(self) -> list[CollusionDocumentReadiness]:
        """存在阻断原因的文档列表。"""
        return [item for item in self.documents if not item.ready]


def _file_status(file_path: str | None) -> tuple[bool, int]:
    """返回文件存在性和实际大小。"""
    if not file_path:
        return False, 0
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return False, 0
    try:
        return True, int(path.stat().st_size)
    except OSError:
        return False, 0


def _normalize_status(status: VectorJobStatus | str | None) -> str | None:
    """将 VectorJobStatus 归一化为稳定字符串。"""
    if status is None:
        return None
    return status.value if isinstance(status, VectorJobStatus) else str(status)


def build_collusion_readiness_report(
    *,
    documents: list[Document],
    chunk_counts: dict[int, int],
    embedding_counts: dict[int, int],
    vector_statuses: dict[int, VectorJobStatus | str | None],
) -> CollusionReadinessReport:
    """从已查询的数据构造串标文档可用性报告。"""
    items: list[CollusionDocumentReadiness] = []
    for doc in documents:
        doc_id = int(doc.id)
        file_exists, actual_size = _file_status(getattr(doc, "file_path", None))
        parsed = int(getattr(doc, "parsed", 0) or 0)
        chunk_count = int(chunk_counts.get(doc_id, 0) or 0)
        embedding_count = int(embedding_counts.get(doc_id, 0) or 0)
        vector_status = _normalize_status(vector_statuses.get(doc_id))

        blocking_reason: str | None = None
        retry_action: str | None = None
        if not file_exists:
            blocking_reason = "源文件不存在或不可访问，请重新上传文件"
            retry_action = "reupload"
        elif actual_size <= 0:
            blocking_reason = "源文件为空，请重新上传文件"
            retry_action = "reupload"
        elif parsed != 1:
            blocking_reason = "文档尚未完成解析和向量化"
            retry_action = "reprocess"
        elif vector_status in {VectorJobStatus.FAILED.value, VectorJobStatus.LOST.value}:
            blocking_reason = "文档处理任务处于失败或丢失状态，请重新解析"
            retry_action = "reprocess"
        elif chunk_count <= 0:
            blocking_reason = "文档未生成有效文本分块，请重新解析或开启图片识别"
            retry_action = "reprocess"
        elif embedding_count <= 0:
            blocking_reason = "文档未生成有效向量，请重新解析或开启图片识别"
            retry_action = "reprocess"

        items.append(
            CollusionDocumentReadiness(
                document_id=doc_id,
                filename=str(getattr(doc, "filename", f"文档{doc_id}")),
                file_exists=file_exists,
                file_size=actual_size,
                parsed=parsed,
                chunk_count=chunk_count,
                embedding_count=embedding_count,
                vector_status=vector_status,
                blocking_reason=blocking_reason,
                retry_action=retry_action,
            )
        )
    return CollusionReadinessReport(documents=items)


async def assess_collusion_readiness(
    db: AsyncSession,
    project_id: int,
) -> CollusionReadinessReport:
    """查询并汇总项目投标文件的串标分析启动前可用性。"""
    docs = list(
        (
            await db.execute(
                select(Document).where(
                    Document.project_id == project_id,
                    Document.doc_type == DocumentType.BID,
                )
            )
        ).scalars().all()
    )
    doc_ids = [int(doc.id) for doc in docs]
    if not doc_ids:
        return CollusionReadinessReport(documents=[])

    chunk_rows = (
        await db.execute(
            select(Chunk.document_id, func.count(Chunk.id))
            .where(Chunk.document_id.in_(doc_ids))
            .where(func.length(func.trim(Chunk.content)) > 0)
            .group_by(Chunk.document_id)
        )
    ).all()
    embedding_rows = (
        await db.execute(
            select(Chunk.document_id, func.count(Chunk.id))
            .where(Chunk.document_id.in_(doc_ids))
            .where(Chunk.embedding.is_not(None))
            .group_by(Chunk.document_id)
        )
    ).all()
    vector_rows = (
        await db.execute(
            select(VectorJobState.document_id, VectorJobState.status)
            .where(VectorJobState.document_id.in_(doc_ids))
        )
    ).all()

    return build_collusion_readiness_report(
        documents=docs,
        chunk_counts={int(doc_id): int(count or 0) for doc_id, count in chunk_rows},
        embedding_counts={int(doc_id): int(count or 0) for doc_id, count in embedding_rows},
        vector_statuses={int(doc_id): status for doc_id, status in vector_rows},
    )


async def assess_collusion_document_readiness(
    db: AsyncSession,
    document_id: int,
) -> CollusionDocumentReadiness | None:
    """查询单份文档的串标处理可恢复性。"""
    doc = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if doc is None:
        return None
    chunk_count = int(
        (
            await db.execute(
                select(func.count(Chunk.id))
                .where(Chunk.document_id == document_id)
                .where(func.length(func.trim(Chunk.content)) > 0)
            )
        ).scalar()
        or 0
    )
    embedding_count = int(
        (
            await db.execute(
                select(func.count(Chunk.id))
                .where(Chunk.document_id == document_id)
                .where(Chunk.embedding.is_not(None))
            )
        ).scalar()
        or 0
    )
    vector_status = (
        await db.execute(
            select(VectorJobState.status).where(VectorJobState.document_id == document_id)
        )
    ).scalar_one_or_none()
    return build_collusion_readiness_report(
        documents=[doc],
        chunk_counts={document_id: chunk_count},
        embedding_counts={document_id: embedding_count},
        vector_statuses={document_id: vector_status},
    ).documents[0]
