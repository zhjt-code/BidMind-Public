"""
职责: 文档质检结果读取与发现项处置——组装结果页视图、报告载荷、
      乐观锁处置更新
不做: 不执行检查、不创建 run、不碰 FastAPI Request/Response
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.document import Document, DocumentType
from app.models.document_qa import DocumentQaFinding, DocumentQaRun
from app.models.user import User
from app.services.project_access import get_project_or_raise


def _finding_to_dict(row: DocumentQaFinding) -> dict[str, Any]:
    return {
        "id": row.id,
        "rule_code": row.rule_code,
        "category": row.category,
        "severity": row.severity,
        "certainty": row.certainty,
        "title": row.title,
        "message": row.message,
        "suggestion": row.suggestion,
        "evidence": row.evidence or {},
        "document_id": row.document_id,
        "disposition": row.disposition,
        "disposition_note": row.disposition_note,
        "version": row.disposition_version,
        "fingerprint": row.finding_fingerprint,
    }


def _summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """按发现项现势计算汇总(处置随人工操作变化, 不用 run.summary 快照)。"""
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_certainty: dict[str, int] = {}
    by_disposition: dict[str, int] = {}
    for item in findings:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
        by_severity[item["severity"]] = by_severity.get(item["severity"], 0) + 1
        by_certainty[item["certainty"]] = by_certainty.get(item["certainty"], 0) + 1
        by_disposition[item["disposition"]] = by_disposition.get(item["disposition"], 0) + 1
    return {
        "total": len(findings),
        "by_category": by_category,
        "by_severity": by_severity,
        "by_certainty": by_certainty,
        "by_disposition": by_disposition,
    }


async def _load_active_run(
    db: AsyncSession, project_id: int,
) -> Optional[DocumentQaRun]:
    return (await db.execute(
        select(DocumentQaRun)
        .where(
            DocumentQaRun.project_id == project_id,
            DocumentQaRun.scope == "envelope",
            DocumentQaRun.is_active.is_(True),
        )
        .order_by(DocumentQaRun.id.desc())
    )).scalars().first()


def image_page_count(features: Optional[dict[str, Any]]) -> Optional[int]:
    """单文档 run 记录的扫描件/图片页数; 旧 run 没有该字段时返回 None, 前端不显示。"""
    pages = (features or {}).get("image_pages")
    return len(pages) if isinstance(pages, list) else None


async def _build_scan_documents(
    db: AsyncSession, project_id: int,
) -> list[dict[str, Any]]:
    documents = (await db.execute(
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.doc_type == DocumentType.BID,
        )
        .order_by(Document.id)
    )).scalars().all()
    items: list[dict[str, Any]] = []
    for document in documents:
        run = (await db.execute(
            select(DocumentQaRun)
            .where(
                DocumentQaRun.project_id == project_id,
                DocumentQaRun.scope == "document",
                DocumentQaRun.document_id == document.id,
            )
            .order_by(DocumentQaRun.id.desc())
        )).scalars().first()
        items.append({
            "document_id": document.id,
            "bid_part": document.bid_part,
            "filename": document.filename,
            "page_count": document.page_count,
            "status": run.status if run is not None else "missing",
            "image_page_count": image_page_count(run.features) if run is not None else None,
        })
    return items


def _merge_checker_status(doc_runs: list[DocumentQaRun]) -> list[dict[str, str]]:
    """合并各卷检查器状态: 任一卷失败即失败, 全 deferred 才 deferred。"""
    rank = {"failed": 3, "skipped": 2, "deferred": 1, "ok": 0}
    merged: dict[str, str] = {}
    for run in doc_runs:
        for code, status in (run.checker_status or {}).items():
            current = merged.get(code)
            if current is None or rank.get(status, 0) > rank.get(current, 0):
                merged[code] = status
    return [{"code": code, "status": status} for code, status in sorted(merged.items())]


async def load_document_quality_view(
    db: AsyncSession,
    project_id: int,
    user: User,
) -> dict[str, Any]:
    """结果页文档质检视图: scan 覆盖信息 + 现势汇总 + 全部发现项。"""
    await get_project_or_raise(db, project_id, user)

    run = await _load_active_run(db, project_id)
    scan_documents = await _build_scan_documents(db, project_id)

    if run is None:
        has_any_doc_run = any(d["status"] != "missing" for d in scan_documents)
        status = "building" if has_any_doc_run else "missing"
        return {
            "scan": {
                "status": status,
                "scope": "envelope",
                "ruleset_version": None,
                "generated_at": None,
                "documents": scan_documents,
                "checkers": [],
                "error_message": None,
            },
            "summary": _summarize([]),
            "findings": [],
        }

    finding_rows = (await db.execute(
        select(DocumentQaFinding)
        .where(DocumentQaFinding.run_id == run.id)
        .order_by(DocumentQaFinding.id)
    )).scalars().all()
    findings = [_finding_to_dict(row) for row in finding_rows]

    doc_runs = (await db.execute(
        select(DocumentQaRun).where(
            DocumentQaRun.project_id == project_id,
            DocumentQaRun.scope == "document",
        ).order_by(DocumentQaRun.id)
    )).scalars().all()

    return {
        "scan": {
            "status": run.status,
            "scope": run.scope,
            "ruleset_version": run.ruleset_version,
            "generated_at": run.created_at.isoformat() if run.created_at else None,
            "documents": scan_documents,
            "checkers": _merge_checker_status(doc_runs),
            "error_message": run.error_message,
        },
        "summary": _summarize(findings),
        "findings": findings,
    }


async def update_finding_disposition(
    db: AsyncSession,
    project_id: int,
    finding_id: int,
    user: User,
    *,
    disposition: str,
    note: Optional[str],
    expected_version: int,
) -> dict[str, Any]:
    """人工处置发现项(乐观锁防两位审核人互相覆盖)。"""
    await get_project_or_raise(db, project_id, user)

    row = (await db.execute(
        select(DocumentQaFinding).where(
            DocumentQaFinding.id == finding_id,
            DocumentQaFinding.project_id == project_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"发现项不存在: id={finding_id}")
    if row.disposition_version != expected_version:
        raise ConflictError(
            "该发现项已被其他人处置，请刷新后重试"
        )

    row.disposition = disposition
    row.disposition_note = (note or "").strip() or None
    row.disposition_version = expected_version + 1
    row.disposed_by = user.id
    row.disposed_at = datetime.utcnow()
    await db.commit()

    logger.info(
        "文档质检处置: project={}, finding={}, disposition={}, by={}",
        project_id, finding_id, disposition, user.username,
    )
    return _finding_to_dict(row)


async def load_report_payload(
    db: AsyncSession, project_id: int,
) -> Optional[dict[str, Any]]:
    """报告章节载荷: 无 active run 返回 None(章节显示未执行)。"""
    run = await _load_active_run(db, project_id)
    if run is None:
        return None
    finding_rows = (await db.execute(
        select(DocumentQaFinding)
        .where(DocumentQaFinding.run_id == run.id)
        .order_by(DocumentQaFinding.severity, DocumentQaFinding.id)
    )).scalars().all()
    findings = [_finding_to_dict(row) for row in finding_rows]
    return {
        "scan": {
            "status": run.status,
            "ruleset_version": run.ruleset_version,
            "generated_at": run.created_at.isoformat() if run.created_at else None,
            "error_message": run.error_message,
        },
        "summary": _summarize(findings),
        "findings": findings,
    }
