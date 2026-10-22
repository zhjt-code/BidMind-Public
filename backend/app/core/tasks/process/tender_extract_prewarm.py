"""
职责: 在投标审查文档处理完成后按条件投递招标要求提取预热任务。
不做: 不解析PDF、不执行要求提取算法、不清理旧要求、不启动投标响应或审查任务。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger
from sqlalchemy import func, select

from app.core.bid_envelope import envelope_is_complete
from app.core.config import settings
from app.core.database import async_session_factory
from app.core.tasks import dispatch_background_job, is_task_active
from app.core.tasks.extract_task import get_extract_task_name, run_extract_task
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus, ProjectType
from app.models.requirement import Requirement


@dataclass(frozen=True)
class TenderExtractPrewarmSnapshot:
    project_exists: bool
    project_type: str
    project_status: str
    tender_parsed: bool
    bid_parsed: bool
    requirement_count: int
    extract_task_active: bool


@dataclass(frozen=True)
class TenderExtractPrewarmDecision:
    should_enqueue: bool
    reason: str
    status: str = "skipped"
    queue_mode: Optional[str] = None
    arq_job_id: Optional[str] = None

    def to_heartbeat_extra(self) -> dict[str, object]:
        data: dict[str, object] = {
            "extract_prewarm_status": self.status,
            "extract_prewarm_reason": self.reason,
        }
        if self.queue_mode:
            data["extract_prewarm_queue_mode"] = self.queue_mode
        if self.arq_job_id:
            data["extract_prewarm_arq_job_id"] = self.arq_job_id
        return data


def _value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")


def _bid_envelope_ready(bid_rows: list[tuple]) -> bool:
    """投标信封齐套且全部解析完成(整本1份 或 分卷各1份, 缺卷不算就绪)。

    bid_rows 元素为 (bid_part, parsed)。
    """
    if not bid_rows:
        return False
    if not envelope_is_complete([part for part, _ in bid_rows]):
        return False
    return all(int(parsed or 0) == 1 for _, parsed in bid_rows)


def decide_tender_extract_prewarm(
    snapshot: TenderExtractPrewarmSnapshot,
) -> TenderExtractPrewarmDecision:
    if not snapshot.project_exists:
        return TenderExtractPrewarmDecision(False, "project_not_found")
    if snapshot.project_type != ProjectType.BID_REVIEW.value:
        return TenderExtractPrewarmDecision(False, "not_bid_review_project")
    if snapshot.project_status in {
        ProjectStatus.REVIEWING.value,
        ProjectStatus.COMPLETED.value,
    }:
        return TenderExtractPrewarmDecision(False, "project_terminal_or_reviewing")
    if not snapshot.tender_parsed or not snapshot.bid_parsed:
        return TenderExtractPrewarmDecision(False, "documents_not_ready")
    if snapshot.requirement_count > 0:
        return TenderExtractPrewarmDecision(False, "requirements_exist")
    if snapshot.extract_task_active:
        return TenderExtractPrewarmDecision(False, "extract_task_active")
    return TenderExtractPrewarmDecision(True, "ready", status="ready")


async def _load_prewarm_snapshot(
    project_id: int,
    source_document_id: int,
) -> TenderExtractPrewarmSnapshot:
    async with async_session_factory() as db:
        project = await db.scalar(select(Project).where(Project.id == project_id))
        if project is None:
            return TenderExtractPrewarmSnapshot(
                project_exists=False,
                project_type="",
                project_status="",
                tender_parsed=False,
                bid_parsed=False,
                requirement_count=0,
                extract_task_active=False,
            )

        tender_count = await db.scalar(
            select(func.count(Document.id)).where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.TENDER,
                Document.parsed == 1,
            )
        )
        # 投标信封口径: 齐套且全部 parsed, 分卷只完成一卷不得触发预热
        bid_rows = (
            await db.execute(
                select(Document.bid_part, Document.parsed).where(
                    Document.project_id == project_id,
                    Document.doc_type == DocumentType.BID,
                )
            )
        ).all()
        requirement_count = await db.scalar(
            select(func.count(Requirement.id)).where(
                Requirement.project_id == project_id
            )
        )

    return TenderExtractPrewarmSnapshot(
        project_exists=True,
        project_type=_value(project.project_type),
        project_status=_value(project.status),
        tender_parsed=bool(tender_count),
        bid_parsed=_bid_envelope_ready(bid_rows),
        requirement_count=int(requirement_count or 0),
        extract_task_active=is_task_active(get_extract_task_name(project_id)),
    )


async def _ensure_prewarm_billing_hold(
    project_id: int,
) -> Optional[TenderExtractPrewarmDecision]:
    """预热派发前确保计费预占存在。

    返回 None 表示预检通过可继续派发；返回 Decision 表示应跳过预热。
    pre_check 幂等：已有 HOLDING 复用，免费试用不重复占名额。
    """
    from app.billing.services.billing_guard import BillingGuard
    from app.billing.services.wallet_service import InsufficientBalanceError

    async with async_session_factory() as db:
        project = await db.scalar(select(Project).where(Project.id == project_id))
        if project is None:
            return TenderExtractPrewarmDecision(False, "project_not_found")
        try:
            await BillingGuard.pre_check(db, project, project.owner_id)
            await db.commit()
        except InsufficientBalanceError as balance_err:
            logger.info(
                f"[提取预热] 余额不足，跳过自动预热(等待用户充值后手动触发): "
                f"project={project_id}, {balance_err}"
            )
            return TenderExtractPrewarmDecision(
                False, "insufficient_balance", status="skipped_billing",
            )
    return None


async def maybe_enqueue_tender_extract_prewarm(
    project_id: int,
    source_document_id: int,
) -> TenderExtractPrewarmDecision:
    if not settings.TENDER_EXTRACT_PREWARM_ENABLED:
        return TenderExtractPrewarmDecision(False, "disabled")

    snapshot = await _load_prewarm_snapshot(project_id, source_document_id)
    decision = decide_tender_extract_prewarm(snapshot)
    if not decision.should_enqueue:
        return decision

    # 预热是提取的自动派发主路径，同样必须先建 hold。
    # 余额不足按 教训静默跳过预热（不置败、不刷错误），
    # 用户手动触发提取时会得到带充值指引的 403。
    billing_decision = await _ensure_prewarm_billing_hold(project_id)
    if billing_decision is not None:
        return billing_decision

    task_name = get_extract_task_name(project_id)
    from app.core.tasks.extract_task import build_extract_dispatch_args

    task_name, generation, arq_args = build_extract_dispatch_args(project_id)
    queue_mode, arq_job_id = await dispatch_background_job(
        task_name=task_name,
        arq_function="extract_requirements_job",
        fallback_factory=lambda: run_extract_task(
            project_id, generation=generation
        ),
        task_type="extract",
        project_id=project_id,
        arq_args=arq_args,
    )
    return TenderExtractPrewarmDecision(
        should_enqueue=True,
        reason="ready",
        status="enqueued",
        queue_mode=queue_mode,
        arq_job_id=arq_job_id,
    )
