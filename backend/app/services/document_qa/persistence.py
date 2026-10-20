"""
职责: 文档质检 run/finding 的持久化——幂等插入、发现项落库、
      人工处置结转、旧 run 失活
不做: 不执行检查、不计算指纹、不做 HTTP 语义
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_qa import DocumentQaFinding, DocumentQaRun


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 run 内按 fingerprint 去重(同页同文本多次命中只留首条)。"""
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        fingerprint = str(row.get("finding_fingerprint") or "")
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(row)
    return deduped


async def insert_findings(
    db: AsyncSession,
    *,
    run_id: int,
    project_id: int,
    rows: list[dict[str, Any]],
) -> int:
    """批量插入发现项(run 内按 fingerprint 幂等)。返回插入条数。"""
    deduped = _dedupe_rows(rows)
    for row in deduped:
        db.add(DocumentQaFinding(
            run_id=run_id,
            project_id=project_id,
            document_id=row.get("document_id"),
            rule_code=str(row.get("rule_code") or ""),
            category=str(row.get("category") or ""),
            severity=str(row.get("severity") or ""),
            certainty=str(row.get("certainty") or ""),
            title=str(row.get("title") or "")[:200],
            message=str(row.get("message") or ""),
            suggestion=str(row.get("suggestion") or "") or None,
            evidence=row.get("evidence") or {},
            finding_fingerprint=str(row.get("finding_fingerprint") or ""),
            disposition="pending",
            disposition_version=0,
        ))
    await db.flush()
    return len(deduped)


async def persist_document_run(
    db: AsyncSession,
    *,
    project_id: int,
    document_id: int,
    input_fingerprint: str,
    ruleset_version: str,
    status: str,
    findings_rows: list[dict[str, Any]],
    checker_status: dict[str, str],
    features: dict[str, Any],
    error_message: Optional[str] = None,
) -> Optional[int]:
    """持久化文档级 run(同指纹幂等: 已有成功运行直接复用)。

    Returns: run_id; 复用既有成功 run 时返回其 id。
    """
    existing = (await db.execute(
        select(DocumentQaRun).where(
            DocumentQaRun.project_id == project_id,
            DocumentQaRun.scope == "document",
            DocumentQaRun.input_fingerprint == input_fingerprint,
        )
    )).scalar_one_or_none()
    if existing is not None:
        if existing.status in ("completed", "degraded"):
            return existing.id
        await db.delete(existing)  # 失败残留: 删除重算(级联清 findings)
        await db.flush()

    run = DocumentQaRun(
        project_id=project_id,
        document_id=document_id,
        scope="document",
        input_fingerprint=input_fingerprint,
        ruleset_version=ruleset_version,
        status=status,
        is_active=False,
        checker_status=checker_status,
        features=features,
        error_message=error_message,
    )
    db.add(run)
    await db.flush()
    await insert_findings(
        db, run_id=run.id, project_id=project_id, rows=findings_rows,
    )
    return run.id


async def try_insert_envelope_run(
    db: AsyncSession,
    *,
    project_id: int,
    input_fingerprint: str,
    ruleset_version: str,
    envelope_signature: list[dict[str, Any]],
) -> Optional[int]:
    """并发安全地插入 envelope run(ON CONFLICT DO NOTHING)。

    分卷两个 process job 同时到达 finalize 时, 唯一约束保证只有一个赢家;
    输家返回 None(skipped:duplicate)。既有失败 run 先删除让路。
    """
    existing = (await db.execute(
        select(DocumentQaRun).where(
            DocumentQaRun.project_id == project_id,
            DocumentQaRun.scope == "envelope",
            DocumentQaRun.input_fingerprint == input_fingerprint,
        )
    )).scalar_one_or_none()
    if existing is not None and existing.status == "failed":
        await db.delete(existing)
        await db.flush()
    elif existing is not None:
        return None

    stmt = (
        pg_insert(DocumentQaRun)
        .values(
            project_id=project_id,
            document_id=None,
            scope="envelope",
            input_fingerprint=input_fingerprint,
            ruleset_version=ruleset_version,
            status="running",
            is_active=False,
            envelope_signature=envelope_signature,
        )
        .on_conflict_do_nothing(constraint="uq_document_qa_run_fingerprint")
        .returning(DocumentQaRun.id)
    )
    run_id = (await db.execute(stmt)).scalar_one_or_none()
    return run_id


async def carry_over_dispositions(
    db: AsyncSession,
    *,
    project_id: int,
    new_run_id: int,
) -> int:
    """把上一 active envelope run 的非 pending 处置按 fingerprint 结转到新 run。"""
    previous = (await db.execute(
        select(DocumentQaRun)
        .where(
            DocumentQaRun.project_id == project_id,
            DocumentQaRun.scope == "envelope",
            DocumentQaRun.is_active.is_(True),
            DocumentQaRun.id != new_run_id,
        )
        .order_by(DocumentQaRun.id.desc())
    )).scalars().first()
    if previous is None:
        return 0

    prior_rows = (await db.execute(
        select(DocumentQaFinding).where(
            DocumentQaFinding.run_id == previous.id,
            DocumentQaFinding.disposition != "pending",
        )
    )).scalars().all()
    if not prior_rows:
        return 0

    by_fingerprint = {row.finding_fingerprint: row for row in prior_rows}
    new_rows = (await db.execute(
        select(DocumentQaFinding).where(
            DocumentQaFinding.run_id == new_run_id,
            DocumentQaFinding.finding_fingerprint.in_(list(by_fingerprint)),
        )
    )).scalars().all()
    carried = 0
    for row in new_rows:
        source = by_fingerprint[row.finding_fingerprint]
        row.disposition = source.disposition
        row.disposition_note = source.disposition_note
        row.disposition_version = source.disposition_version
        row.disposed_by = source.disposed_by
        row.disposed_at = source.disposed_at or datetime.utcnow()
        carried += 1
    if carried:
        await db.flush()
        logger.info(
            f"[文档质检] 处置结转: project={project_id}, run={new_run_id}, 条数={carried}"
        )
    return carried


async def activate_envelope_run(
    db: AsyncSession,
    *,
    project_id: int,
    run_id: int,
) -> None:
    """把指定 envelope run 置为唯一 active(旧 run 失活但保留审计)。"""
    await db.execute(
        update(DocumentQaRun)
        .where(
            DocumentQaRun.project_id == project_id,
            DocumentQaRun.scope == "envelope",
            DocumentQaRun.id != run_id,
        )
        .values(is_active=False)
    )
    await db.execute(
        update(DocumentQaRun)
        .where(DocumentQaRun.id == run_id)
        .values(is_active=True)
    )
