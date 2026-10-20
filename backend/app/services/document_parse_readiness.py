"""
职责: 判断项目所需文档是否均已处理完成，并给出上传阶段的下一项目状态
不做: 不写库、不启动提取/分析、不计费、不处理文档内容
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bid_envelope import envelope_is_complete
from app.core.config import settings
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus, ProjectType

_UPLOAD_PHASE = frozenset({
    ProjectStatus.CREATED,
    ProjectStatus.UPLOADING,
    ProjectStatus.PARSING,
    ProjectStatus.PARSED,
})


@dataclass(frozen=True)
class DocumentParseFact:
    doc_type: DocumentType
    parsed: int
    bid_part: str | None = None


def required_documents_are_parsed(
    project_type: ProjectType,
    documents: Iterable[DocumentParseFact],
    *,
    min_collusion_bids: int,
) -> bool:
    """所需文档是否都已 parsed=1，且数量满足该类型启动下一节点的最低要求。"""
    docs = list(documents)
    if not docs:
        return False
    if any(doc.parsed != 1 for doc in docs):
        return False

    has_tender = any(doc.doc_type == DocumentType.TENDER for doc in docs)
    bid_count = sum(1 for doc in docs if doc.doc_type == DocumentType.BID)

    if project_type == ProjectType.BID_WRITING:
        return has_tender
    if project_type == ProjectType.COLLUSION_CHECK:
        return bid_count >= min_collusion_bids
    # 投标审查: 投标侧按信封齐套(整本1份 或 分卷各1份), 缺卷不得视为齐套
    return has_tender and envelope_is_complete(
        [doc.bid_part for doc in docs if doc.doc_type == DocumentType.BID]
    )


def next_status_after_document_parse(
    current: ProjectStatus,
    *,
    all_required_parsed: bool,
) -> ProjectStatus | None:
    """上传阶段内：齐套则 parsed，否则 uploading。下游/终态返回 None（不改）。"""
    if current not in _UPLOAD_PHASE:
        return None
    return ProjectStatus.PARSED if all_required_parsed else ProjectStatus.UPLOADING


async def project_required_documents_are_parsed(
    db: AsyncSession,
    project: Project,
) -> bool:
    """读库后判定齐套；min_collusion_bids 取当前配置。"""
    result = await db.execute(
        select(Document).where(Document.project_id == project.id)
    )
    facts = [
        DocumentParseFact(
            doc_type=doc.doc_type,
            parsed=int(doc.parsed or 0),
            bid_part=doc.bid_part,
        )
        for doc in result.scalars().all()
    ]
    return required_documents_are_parsed(
        project.project_type,
        facts,
        min_collusion_bids=settings.COLLUSION_MIN_BID_DOCS,
    )


def apply_status_after_document_parse(
    project: Project,
    *,
    all_required_parsed: bool,
) -> bool:
    """按齐套结果改上传阶段状态。返回是否发生变更。"""
    nxt = next_status_after_document_parse(
        project.status,
        all_required_parsed=all_required_parsed,
    )
    if nxt is None or nxt == project.status:
        return False
    project.status = nxt
    return True
