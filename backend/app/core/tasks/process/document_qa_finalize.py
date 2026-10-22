"""文档质检信封收敛

职责:
1. 判定信封是否就绪(齐套 + 全部 parsed + 各卷 document run 指纹匹配当前输入) —— 纯函数可单测
2. 就绪时以信封指纹幂等创建 envelope run: 复制各卷发现 + 跨卷检查 + 处置结转 + 置 active
3. fail-open: 失败只记日志与 failed run, 不阻断文档处理/审查主流程

整本(1卷)与分卷(商务+技术)走同一链路(档案驱动, 复用 envelope_is_complete)。
不做: 不解析 PDF、不重跑单文档检查、不新增 ARQ job(内联执行)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


@dataclass(frozen=True)
class EnvelopeQaDocFact:
    document_id: int
    bid_part: Optional[str]
    parsed: int
    file_hash: Optional[str]
    content_hash: Optional[str]
    run_status: Optional[str]  # 该文档最新 document run 状态; 无 run 为 None
    run_fingerprint: Optional[str] = None  # 最新 document run 的 input_fingerprint


@dataclass(frozen=True)
class EnvelopeQaSnapshot:
    project_exists: bool
    project_type: str
    qa_enabled: bool
    docs: list[EnvelopeQaDocFact] = field(default_factory=list)


@dataclass(frozen=True)
class EnvelopeQaDecision:
    should_finalize: bool
    reason: str
    status: str = "skipped"

    def to_heartbeat_extra(self) -> dict[str, str]:
        return {
            "document_qa_envelope_status": self.status,
            "document_qa_envelope_reason": self.reason,
        }


def document_run_is_current(doc: EnvelopeQaDocFact) -> bool:
    """最新 document run 是否匹配当前输入指纹(文件哈希+内容哈希+规则集)。

    completed/degraded 仍可能是陈旧规则或旧文件上的结果, 不能单凭状态视为就绪。
    """
    if doc.run_status not in ("completed", "degraded"):
        return False
    if not doc.run_fingerprint:
        return False
    from app.services.document_qa.fingerprint import document_run_fingerprint

    expected = document_run_fingerprint(
        document_id=doc.document_id,
        file_hash=doc.file_hash,
        content_hash=doc.content_hash,
    )
    return doc.run_fingerprint == expected


def docs_needing_qa_backfill(
    docs: list[EnvelopeQaDocFact],
) -> list[EnvelopeQaDocFact]:
    """需要按源文件补算的卷: 无 run、失败 run、或指纹与当前输入不一致。"""
    return [doc for doc in docs if not document_run_is_current(doc)]


def decide_envelope_finalize(snapshot: EnvelopeQaSnapshot) -> EnvelopeQaDecision:
    """信封收敛判定(纯函数): 缺卷/未解析/单卷 run 未就绪一律不收敛。"""
    from app.core.bid_envelope import envelope_is_complete

    if not snapshot.qa_enabled:
        return EnvelopeQaDecision(False, "disabled")
    if not snapshot.project_exists:
        return EnvelopeQaDecision(False, "project_not_found")
    if snapshot.project_type != "bid_review":
        return EnvelopeQaDecision(False, "not_bid_review_project")
    if not snapshot.docs:
        return EnvelopeQaDecision(False, "no_bid_documents")
    if not envelope_is_complete([doc.bid_part for doc in snapshot.docs]):
        return EnvelopeQaDecision(False, "envelope_incomplete")
    if any(doc.parsed != 1 for doc in snapshot.docs):
        return EnvelopeQaDecision(False, "documents_not_parsed")
    if docs_needing_qa_backfill(snapshot.docs):
        return EnvelopeQaDecision(False, "document_runs_not_ready")
    return EnvelopeQaDecision(True, "ready", status="ready")


async def _load_snapshot(project_id: int) -> EnvelopeQaSnapshot:
    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import async_session_factory
    from app.models.document import Document, DocumentType
    from app.models.document_qa import DocumentQaRun
    from app.models.project import Project

    qa_enabled = bool(getattr(settings, "DOCUMENT_QA_ENABLED", True))
    async with async_session_factory() as db:
        project = (await db.execute(
            select(Project).where(Project.id == project_id)
        )).scalar_one_or_none()
        if project is None:
            return EnvelopeQaSnapshot(
                project_exists=False, project_type="", qa_enabled=qa_enabled,
            )
        project_type = str(getattr(project.project_type, "value", project.project_type) or "")

        documents = (await db.execute(
            select(Document)
            .where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.BID,
            )
            .order_by(Document.id)
        )).scalars().all()

        docs: list[EnvelopeQaDocFact] = []
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
            docs.append(EnvelopeQaDocFact(
                document_id=document.id,
                bid_part=document.bid_part,
                parsed=int(document.parsed or 0),
                file_hash=document.file_hash,
                content_hash=document.content_hash,
                run_status=run.status if run is not None else None,
                run_fingerprint=run.input_fingerprint if run is not None else None,
            ))

    return EnvelopeQaSnapshot(
        project_exists=True,
        project_type=project_type,
        qa_enabled=qa_enabled,
        docs=docs,
    )


async def maybe_finalize_document_qa_envelope(project_id: int) -> EnvelopeQaDecision:
    """信封收敛入口(文档处理完成后与 legacy 补算路径共用)。"""
    try:
        snapshot = await _load_snapshot(project_id)
        decision = decide_envelope_finalize(snapshot)
        if not decision.should_finalize:
            return decision
        status = await _finalize(project_id, snapshot)
        return EnvelopeQaDecision(True, "ready", status=status)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 信封收敛失败(fail-open): project={project_id}, {exc}")
        return EnvelopeQaDecision(False, f"error:{str(exc)[:120]}", status="failed")


async def _finalize(project_id: int, snapshot: EnvelopeQaSnapshot) -> str:
    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import async_session_factory
    from app.core.document_qa.contracts import RULESET_VERSION
    from app.core.document_qa.envelope_analyzer import (
        EnvelopeDocFeatures,
        analyze_envelope,
        summarize_findings,
    )
    from app.models.document_qa import DocumentQaFinding, DocumentQaRun
    from app.services.document_qa.fingerprint import envelope_fingerprint
    from app.services.document_qa.persistence import (
        activate_envelope_run,
        carry_over_dispositions,
        insert_findings,
        try_insert_envelope_run,
    )

    signature = [
        {
            "document_id": doc.document_id,
            "bid_part": doc.bid_part,
            "file_hash": doc.file_hash,
            "content_hash": doc.content_hash,
        }
        for doc in snapshot.docs
    ]
    fingerprint = envelope_fingerprint(signature)
    ruleset_version = str(
        getattr(settings, "DOCUMENT_QA_RULESET_VERSION", "") or RULESET_VERSION
    )

    async with async_session_factory() as db:
        run_id = await try_insert_envelope_run(
            db,
            project_id=project_id,
            input_fingerprint=fingerprint,
            ruleset_version=ruleset_version,
            envelope_signature=signature,
        )
        if run_id is None:
            await db.commit()
            logger.info(
                f"[文档质检] 信封收敛跳过(同指纹已存在): project={project_id}"
            )
            return "already_current"

        # 汇集各卷 document run 的发现与特征
        doc_features: list[EnvelopeDocFeatures] = []
        all_rows: list[dict[str, Any]] = []
        for doc in snapshot.docs:
            doc_run = (await db.execute(
                select(DocumentQaRun)
                .where(
                    DocumentQaRun.project_id == project_id,
                    DocumentQaRun.scope == "document",
                    DocumentQaRun.document_id == doc.document_id,
                )
                .order_by(DocumentQaRun.id.desc())
            )).scalars().first()
            if doc_run is None:
                continue
            doc_features.append(EnvelopeDocFeatures(
                document_id=doc.document_id,
                bid_part=doc.bid_part,
                features=doc_run.features or {},
            ))
            doc_findings = (await db.execute(
                select(DocumentQaFinding).where(
                    DocumentQaFinding.run_id == doc_run.id
                )
            )).scalars().all()
            for row in doc_findings:
                all_rows.append({
                    "rule_code": row.rule_code,
                    "category": row.category,
                    "severity": row.severity,
                    "certainty": row.certainty,
                    "title": row.title,
                    "message": row.message,
                    "suggestion": row.suggestion,
                    "evidence": row.evidence,
                    "document_id": row.document_id,
                    "finding_fingerprint": row.finding_fingerprint,
                })

        # 跨卷检查(整本信封单文档时自然为空集)
        all_rows.extend(analyze_envelope(doc_features))

        await insert_findings(
            db, run_id=run_id, project_id=project_id, rows=all_rows,
        )
        await carry_over_dispositions(
            db, project_id=project_id, new_run_id=run_id,
        )

        run = (await db.execute(
            select(DocumentQaRun).where(DocumentQaRun.id == run_id)
        )).scalar_one()
        degraded = any(doc.run_status == "degraded" for doc in snapshot.docs)
        run.status = "degraded" if degraded else "completed"
        run.summary = summarize_findings(all_rows)
        run.checker_status = {
            str(doc.document_id): (doc.run_status or "missing")
            for doc in snapshot.docs
        }
        await activate_envelope_run(db, project_id=project_id, run_id=run_id)
        await db.commit()

    logger.info(
        f"[文档质检] 信封收敛完成: project={project_id}, run={run_id}, "
        f"findings={len(all_rows)}, status={'degraded' if degraded else 'completed'}"
    )
    return "finalized"
