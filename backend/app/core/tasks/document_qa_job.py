"""文档质检处理队列任务。

职责: 在处理 worker 上按文档执行单文档质检(重解析 -> 检查 -> 复核子任务 -> 落库)并尝试信封收敛;
      同时运行的质检数受许可限制, 拿不到许可就退回队列让出槽位
不做: 不在触发方进程内计算、不直接调用 LLM(复核交给 LLM worker 子任务)、不写处理进度心跳
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.config import settings

DOCUMENT_QA_JOB = "document_qa_job"
_PERMIT_RESOURCE = "document_qa"
# enqueue_job_detailed 对 ARQ 同 id 去重仍报 queued, 只能从缺失的 job_id 识别重复
_DUPLICATE_JOB_ID = "arq-job-unknown"


def document_qa_job_id(document_id: int) -> str:
    return f"document_qa_{document_id}"


async def enqueue_document_qa(project_id: int, document_id: int) -> str:
    """投递单文档质检。同一文档已在队列或运行中时按 job id 去重。返回投递结论供日志。"""
    from app.core.arq_runtime import enqueue_job_detailed

    dispatch = await enqueue_job_detailed(
        DOCUMENT_QA_JOB,
        project_id,
        document_id,
        _job_id=document_qa_job_id(document_id),
    )
    if not dispatch.queued:
        return f"not_queued:{dispatch.reason}"
    if dispatch.job_id == _DUPLICATE_JOB_ID:
        return "duplicate"
    return "queued"


def _noop_heartbeat(*_args: Any, **_kwargs: Any) -> None:
    return None


async def run_document_qa_for_document(project_id: int, document_id: int) -> str:
    """重解析源 PDF 跑单文档质检, 结束后尝试信封收敛。返回状态串供日志。"""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.core.parser.pdf_parser import PdfParser
    from app.core.tasks.cancellation import make_project_cancel_check
    from app.core.tasks.process.document_qa_finalize import maybe_finalize_document_qa_envelope
    from app.core.tasks.process.document_qa_stage import _mark_failed, run_document_qa_stage
    from app.models.document import Document

    make_project_cancel_check(project_id)()
    async with async_session_factory() as db:
        document = (await db.execute(
            select(Document).where(Document.id == document_id)
        )).scalar_one_or_none()
    file_path = document.file_path if document is not None else None
    if not file_path or not Path(file_path).exists():
        logger.warning(f"[文档质检] 源文件缺失, 跳过质检: document={document_id}, path={file_path}")
        return "skipped:source_missing"
    if Path(file_path).suffix.lower() != ".pdf":
        return "skipped:not_pdf"

    try:
        parsed_doc = await asyncio.to_thread(PdfParser().parse, file_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 重解析失败(fail-open): document={document_id}, {exc}")
        await _mark_failed(project_id, document_id, f"重解析失败: {exc}")
        return "failed"

    status = await run_document_qa_stage(
        parsed_doc=parsed_doc,
        file_path=file_path,
        project_id=project_id,
        document_id=document_id,
        heartbeat=_noop_heartbeat,
    )
    await maybe_finalize_document_qa_envelope(project_id)
    return status


def _defer_or_waive(ctx: dict, document_id: int) -> None:
    """许可已满: 重排预算内抛 Retry 退回队列(必须在业务代码之前), 用尽则放行防饥饿。"""
    from app.core.tasks.fairness.deferred_requeue import (
        defer_job_for_fairness,
        resolve_deferral_plan,
    )

    plan = resolve_deferral_plan(int(ctx.get("job_try") or 1))
    if plan.should_defer:
        logger.info(
            f"[文档质检] 质检许可已满, 退回队列: document={document_id}, "
            f"delay_s={plan.delay_seconds:.1f}"
        )
        defer_job_for_fairness(plan.delay_seconds)
    logger.info(
        f"[文档质检] 质检许可重排预算用尽, 放行以防饥饿: document={document_id}, "
        f"reason={plan.waiver_reason}"
    )


async def document_qa_job(ctx: dict, project_id: int, document_id: int) -> None:
    """ARQ 入口, 只在处理 worker 注册。"""
    from app.core.arq_runtime import ProjectCancelledError, is_project_cancelled_async
    from app.core.tasks.process.document_qa_stage import _load_gate
    from app.core.tasks.resource_permit import build_permit_owner, permit_context

    redis = ctx.get("redis")
    if await is_project_cancelled_async(redis, project_id):
        logger.info(f"[文档质检] 项目已删除/取消, 跳过质检任务: project={project_id}")
        return
    if await _load_gate(project_id, document_id) is None:
        return

    owner = build_permit_owner(
        worker_id=ctx.get("bidmind_worker_id"),
        function_name=DOCUMENT_QA_JOB,
        job_id=ctx.get("job_id"),
        project_id=project_id,
    )
    async with permit_context(
        redis,
        _PERMIT_RESOURCE,
        limit=max(int(getattr(settings, "DOCUMENT_QA_MAX_CONCURRENT", 1)), 1),
        wait_timeout=0,
        owner=owner,
    ) as acquired:
        if not acquired:
            _defer_or_waive(ctx, document_id)
        try:
            status = await run_document_qa_for_document(project_id, document_id)
        except ProjectCancelledError:
            logger.info(f"[文档质检] 质检任务因项目取消中止: project={project_id}")
            return
    logger.info(
        f"[文档质检] 质检任务结束: project={project_id}, document={document_id}, status={status}"
    )
