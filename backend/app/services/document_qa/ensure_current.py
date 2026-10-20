"""
职责: 保障项目存在当前有效的文档质检结果——legacy 项目(处理链未跑 QA)、
      质检随进程丢失或输入指纹已变(规则集/文件哈希)时, 把缺失的卷投递到处理队列补算
不做: 不在调用方进程内计算质检(补算由处理 worker 的 document_qa_job 执行)、
      不在调用方关键路径上等待、不重复投递指纹仍匹配或仍在途的卷
"""

from __future__ import annotations

import asyncio

from loguru import logger

_DETACHED_CURRENCY: set[asyncio.Task] = set()


def detach_document_qa_currency(project_id: int) -> asyncio.Task:
    """挂起当前性保障, 调用方不要 await。

    质检是独立结果域, 当前性保障不得挡住审查主链。同一项目已有进行中的挂起任务时直接返回该任务。
    """
    name = f"document-qa-currency-{project_id}"
    for task in _DETACHED_CURRENCY:
        if task.get_name() == name and not task.done():
            return task

    task = asyncio.create_task(_ensure_and_log(project_id), name=name)
    _DETACHED_CURRENCY.add(task)
    task.add_done_callback(_DETACHED_CURRENCY.discard)
    return task


async def _ensure_and_log(project_id: int) -> None:
    status = await ensure_document_qa_current(project_id)
    logger.info(f"[文档质检] 当前性保障结束: project={project_id}, status={status}")


async def ensure_document_qa_current(project_id: int) -> str:
    """确保 active envelope run 存在且与当前信封一致。返回状态串供日志。"""
    from app.core.config import settings

    if not bool(getattr(settings, "DOCUMENT_QA_ENABLED", True)):
        return "disabled"

    try:
        return await _ensure(project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[文档质检] 当前性保障失败(fail-open, 不影响审查): project={project_id}, {exc}",
        )
        return "failed"


async def _ensure(project_id: int) -> str:
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.core.tasks.document_qa_job import enqueue_document_qa
    from app.core.tasks.process.document_qa_finalize import (
        decide_envelope_finalize,
        docs_needing_qa_backfill,
        maybe_finalize_document_qa_envelope,
        _load_snapshot,
    )
    from app.core.tasks.process.document_qa_stage import document_qa_inflight
    from app.models.document_qa import DocumentQaRun
    from app.services.document_qa.fingerprint import envelope_fingerprint

    snapshot = await _load_snapshot(project_id)
    if not snapshot.project_exists or snapshot.project_type != "bid_review":
        return "not_applicable"
    if not snapshot.docs:
        return "no_bid_documents"

    # 已有与当前信封同指纹的 active run -> 无需动作
    fingerprint = envelope_fingerprint([
        {
            "document_id": doc.document_id,
            "bid_part": doc.bid_part,
            "file_hash": doc.file_hash,
            "content_hash": doc.content_hash,
        }
        for doc in snapshot.docs
    ])
    async with async_session_factory() as db:
        active = (await db.execute(
            select(DocumentQaRun).where(
                DocumentQaRun.project_id == project_id,
                DocumentQaRun.scope == "envelope",
                DocumentQaRun.is_active.is_(True),
            )
        )).scalars().first()
    if active is not None and active.input_fingerprint == fingerprint:
        return "already_current"

    # 缺 run 或 run 指纹与当前输入不一致: 投递到处理队列补算; 仍在途的卷交给其执行体收尾
    # (收尾时会再尝试信封收敛), 同一卷的重复投递由 job id 去重
    decision = decide_envelope_finalize(snapshot)
    pending: list[str] = []
    if decision.reason == "document_runs_not_ready":
        for doc in docs_needing_qa_backfill(snapshot.docs):
            if document_qa_inflight(doc.document_id):
                logger.info(
                    f"[文档质检] 单文档质检在途, 不补算: project={project_id}, "
                    f"document={doc.document_id}",
                )
                pending.append("in_progress")
                continue
            result = await enqueue_document_qa(project_id, doc.document_id)
            logger.info(
                f"[文档质检] 补算投递处理队列: project={project_id}, "
                f"document={doc.document_id}, result={result}",
            )
            pending.append("enqueued")

    final_decision = await maybe_finalize_document_qa_envelope(project_id)
    if pending and not final_decision.should_finalize:
        return "enqueued" if "enqueued" in pending else "in_progress"
    return final_decision.status
