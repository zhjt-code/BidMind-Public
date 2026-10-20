"""文档处理任务失活/恢复 reconciler。

职责: 判定单个文档处理任务是否失活并执行转失败/重入队。
不做: HTTP 响应组装、鉴权(由调用方负责)。

设计:
- 完整迁移 ``files.get_process_status`` 中的"recovery 决策 + 二次确认 + 重入队 +
  MAX_RECOVERY 兜底"路径, 路由仅做响应组装与初始鉴权。
- 同一份契约同时被路由(用户轮询)与 reaper(定时巡检)调用, 杜绝双路径不一致。
- 所有 DB 状态读写在传入的 ``db`` session 上完成; commit 责任内置, 调用方无需额外 commit。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.observability.metrics import arq_job_lost_total, arq_job_retry_total
from app.core.redis import get_redis_pool
from app.core.arq_runtime import can_use_arq
from app.core.tasks import (
    MAX_RECOVERY,
    cancel_task,
    create_background_task,
    get_recovery_count,
    get_task_heartbeat,
    increment_recovery_count,
    is_task_active,
    is_task_in_enqueue_grace,
    reset_recovery_count,
    update_heartbeat,
)
from app.core.vectorizer.job_state import (
    decide_recovery_from_state,
    enqueue_process_document_job,
    get_vector_job_state,
    resolve_recovery_count,
    upsert_vector_job_state,
)
from app.models.document import Document
from app.models.project import Project, ProjectStatus
from app.models.vector_job_state import VectorJobStatus


@dataclass(frozen=True)
class DocumentProcessReconciliationResult:
    """文档处理任务失活/恢复判定结果。

    outcome:
      - ``noop``: 任务运行健康(或文档不存在), 不做任何状态变更
      - ``completed``: 文档已解析完成(初检或竞态二次确认), 已重置 recovery 计数
      - ``recovered``: 已重新入队恢复任务, 心跳/状态表已置 RECOVERING
      - ``max_recovery_exceeded``: 已达上限, 项目状态置为 FAILED
      - ``already_failed``: 心跳或 VectorJobState 已标记 failed, 未做新动作
    """

    outcome: str
    queue_mode: Optional[str] = None
    recovery_count: Optional[int] = None
    error_message: Optional[str] = None
    recover_reason: Optional[str] = None
    arq_job_id: Optional[str] = None


_ARQ_RECOVERY_GUARD_STATES = {
    VectorJobStatus.QUEUED,
    VectorJobStatus.CLAIMED,
    VectorJobStatus.RUNNING,
    VectorJobStatus.RECOVERING,
}


async def _resolve_already_failed_message(
    db: AsyncSession,
    *,
    project_id: int,
    document_id: int,
    heartbeat_error: Optional[str],
) -> str:
    """职责: 为 already_failed 解析用户可读失败原因（心跳 + 持久化兜底）。
    不做: 不改库、不判定是否应恢复。
    """
    from app.services.retry.failure_reason_resolver import (
        load_persisted_failure_sources,
        pick_failure_reason,
    )

    persisted = await load_persisted_failure_sources(
        db,
        project_id=project_id,
        document_id=document_id,
    )
    return (
        pick_failure_reason(
            task_run_error=None,
            heartbeat_error=heartbeat_error,
            persisted=persisted,
        )
        or "未知错误"
    )


async def _arq_job_still_present_for_recovery(
    job_state: object | None,
    task_name: str,
) -> bool:
    """职责: 判断恢复链路中同名 ARQ 文档处理 job 是否仍有在途痕迹。
    不做: 判定任务是否应恢复、修改心跳或写入数据库。
    """

    from app.core.arq_runtime import arq_job_present_async

    if job_state is None:
        # 无状态行时按确定性 job_id(task_name=process_doc_{id}) 补探。
        # POST「enqueue 成功但 job_state 未落库」或 queued 心跳过期(600s)后,
        # 队列可能仍持有该 job, 直接视为无痕迹会造成 legacy_no_state 双投。
        # Redis 不可用时 arq_job_present_async 保守返回 True(抑制恢复),
        # 防止心跳整体不可读引发全量误恢复风暴。
        pool = await get_redis_pool()
        return await arq_job_present_async(pool, task_name)
    if str(getattr(job_state, "queue_mode", "") or "").lower() != "arq":
        return False
    if getattr(job_state, "status", None) not in _ARQ_RECOVERY_GUARD_STATES:
        return False

    pool = await get_redis_pool()
    job_id = str(getattr(job_state, "arq_job_id", "") or task_name)
    return await arq_job_present_async(pool, job_id)


def _source_file_missing(document: object | None) -> bool:
    """职责: 判断文档源文件当前是否缺失(仅存在性)。
    不做: 不校验 PDF 完整性、不读文件内容; document 为 None 时不判缺失。
    """
    if document is None:
        return False
    path_str = str(getattr(document, "file_path", "") or "")
    if not path_str:
        return True
    try:
        return not Path(path_str).exists()
    except OSError:
        return True


async def _terminalize_missing_source_file(
    db: AsyncSession,
    *,
    project_id: int,
    document_id: int,
    task_name: str,
    job_state: object | None,
    recover_reason: Optional[str],
    source_path: str,
) -> DocumentProcessReconciliationResult:
    """职责: 源文件缺失时把任务与项目一次性置为失败终态并给出重传引导。
 不做: 不重入队、不删除文档记录、不清理磁盘。

 文案以「文件不存在」开头, 命中 flow_retry_service.is_unrecoverable_document_error
 的不可恢复分类, 使显式重试入口阻断无效重跑并引导重新上传(同一语义)。
 """
    err_msg = f"文件不存在: {source_path}，源文件缺失，重试无法恢复，请重新上传后再解析"
    logger.error(
        "文档处理恢复前发现源文件缺失, 终态化不再恢复: document_id={}, path={}, reason={}",
        document_id,
        source_path,
        recover_reason,
    )
    await upsert_vector_job_state(
        db,
        project_id=project_id,
        document_id=document_id,
        task_name=task_name,
        status=VectorJobStatus.FAILED,
        queue_mode=(getattr(job_state, "queue_mode", None) or "arq"),
        stage="failed",
        last_error=err_msg,
        bump_finished_at=True,
    )
    project_row = await db.execute(select(Project).where(Project.id == project_id))
    project = project_row.scalar_one_or_none()
    if project is not None:
        project.status = ProjectStatus.FAILED
        project.error_message = err_msg
    await db.commit()
    update_heartbeat(task_name, stage="failed", progress=err_msg[:200])
    reset_recovery_count(task_name)
    return DocumentProcessReconciliationResult(
        outcome="already_failed",
        error_message=err_msg,
        recover_reason=recover_reason,
    )


async def reconcile_document_processing(
    db: AsyncSession,
    *,
    project_id: int,
    document_id: int,
) -> DocumentProcessReconciliationResult:
    """判定并执行单个文档处理任务的失活恢复。

    步骤(严格对齐 ``files.get_process_status`` 原始路径):
      1. 读取 Document 与心跳; 若 parsed=1 直接返回 completed
      2. 若心跳 stage=failed 返回 already_failed(不再覆盖)
      3. 综合 vector_job_state + Redis 心跳新鲜度 + 本地 task 调 ``decide_recovery_from_state``
      4. 若需要恢复, 二次确认 parsed/job_state(竞态保护)
      5. 上限检查: 达到 MAX_RECOVERY 则项目置 FAILED
      6. 否则 increment + cancel + enqueue + upsert_vector_job_state(RECOVERING)
    """
    from app.core.tasks.process_task import get_process_task_name

    task_name = get_process_task_name(document_id)

    # ── 1. 文档当前状态 ────────────────────────────────────────────
    doc_result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
        )
    )
    document = doc_result.scalar_one_or_none()
    if document and document.parsed == 1:
        reset_recovery_count(task_name)
        return DocumentProcessReconciliationResult(outcome="completed")

    # ── 2. 心跳与活跃判定 ──────────────────────────────────────────
    heartbeat = get_task_heartbeat(task_name)
    job_state = await get_vector_job_state(db, document_id)

    # 失败态以心跳或持久化 VectorJobState.FAILED 为准；原因走兜底链，
    # 避免心跳被 cleanup 后把已失败文档误当成 processing，并丢掉已落库原因。
    if heartbeat and heartbeat.get("stage") == "failed":
        error_message = await _resolve_already_failed_message(
            db,
            project_id=project_id,
            document_id=document_id,
            heartbeat_error=str(heartbeat.get("progress") or "") or None,
        )
        return DocumentProcessReconciliationResult(
            outcome="already_failed",
            error_message=error_message,
        )
    if job_state is not None and job_state.status == VectorJobStatus.FAILED:
        error_message = await _resolve_already_failed_message(
            db,
            project_id=project_id,
            document_id=document_id,
            heartbeat_error=None,
        )
        return DocumentProcessReconciliationResult(
            outcome="already_failed",
            error_message=error_message,
        )

    task_active = is_task_active(task_name)

    hb_timeout = int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180))
    redis_hb_fresh = False
    if heartbeat:
        last_hb_raw = heartbeat.get("last_heartbeat")
        try:
            last_hb_ts = float(last_hb_raw) if last_hb_raw is not None else 0.0
        except (TypeError, ValueError):
            last_hb_ts = 0.0
        if last_hb_ts > 0:
            redis_hb_fresh = (time.time() - last_hb_ts) <= hb_timeout

    # ── 3. 综合判定 ────────────────────────────────────────────────

    # 已过入队宽限时探测 ARQ 中是否仍有该 job 痕迹
    # (job/result/in-progress/retry)。三种用途:
    # QUEUED/RECOVERING: ARQ 无痕 -> 幽灵任务需恢复;
    #  - CLAIMED/RUNNING: ARQ in-progress 在途 -> 任务真活着, 抑制因 GIL 阻塞导致的
    # 假性 stale 误恢复, 避免覆盖活任务心跳致前端进度回跳 + 重复入队;
    #  - job_state 为 None: 按确定性 job_id 补探, ARQ 在途则禁止 legacy_no_state
    # 重投(POST 落库失败/心跳过期窗口)。
    arq_job_present: Optional[bool] = None
    if not is_task_in_enqueue_grace(task_name):
        arq_job_present = await _arq_job_still_present_for_recovery(
            job_state, task_name
        )

    should_recover, recover_reason = decide_recovery_from_state(
        job_state,
        task_active=task_active,
        heartbeat_timeout_sec=hb_timeout,
        function_name="process_document_job",
        redis_heartbeat_fresh=redis_hb_fresh,
        arq_job_present=arq_job_present,
    )

    if not should_recover or (document and document.parsed == 1):
        return DocumentProcessReconciliationResult(outcome="noop")

    # ── 4. 竞态二次确认 ────────────────────────────────────────────
    recheck_parsed: Optional[int] = None
    try:
        recheck_result = await db.execute(
            select(Document.parsed).where(
                Document.id == document_id,
                Document.project_id == project_id,
            )
        )
        recheck_parsed = recheck_result.scalar_one_or_none()
    except Exception:
        pass
    recheck_state = await get_vector_job_state(db, document_id)
    if (
        recheck_parsed == 1
        or (recheck_state and recheck_state.status == VectorJobStatus.COMPLETED)
    ):
        logger.info(
            "文档处理在恢复分支二次确认中检测到已完成, 跳过恢复: "
            "document_id={}, task_name={}, recheck_parsed={}, recheck_status={}",
            document_id,
            task_name,
            recheck_parsed,
            (recheck_state.status.value if recheck_state else None),
        )
        reset_recovery_count(task_name)
        return DocumentProcessReconciliationResult(outcome="completed")

    latest_job_state = recheck_state or job_state
    if await _arq_job_still_present_for_recovery(latest_job_state, task_name):
        logger.info(
            "文档处理恢复前检测到 ARQ job 仍在途, 跳过 API fallback: "
            "document_id={}, task_name={}, recover_reason={}",
            document_id,
            task_name,
            recover_reason,
        )
        return DocumentProcessReconciliationResult(
            outcome="noop",
            recover_reason=recover_reason,
            arq_job_id=str(getattr(latest_job_state, "arq_job_id", "") or task_name),
        )

    # 恢复入队前校验源文件可达性。输入缺失属不可恢复失败, 重跑必然复现
    # 「文件不存在」, 直接终态化并引导重新上传, 不再消耗恢复配额。
    if _source_file_missing(document):
        return await _terminalize_missing_source_file(
            db,
            project_id=project_id,
            document_id=document_id,
            task_name=task_name,
            job_state=job_state,
            recover_reason=recover_reason,
            source_path=str(getattr(document, "file_path", "") or ""),
        )

    logger.warning(
        "文档处理任务判定丢失, reason={}, job_status={}, queue_mode={}, "
        "document_id={}, task_name={}",
        recover_reason,
        (job_state.status.value if job_state else None),
        (job_state.queue_mode if job_state else None),
        document_id,
        task_name,
    )
    arq_job_lost_total.labels(
        function="process_document_job",
        reason=recover_reason or "unknown",
    ).inc()

    # recovery_count 取"进程内存态"与"DB 持久化 retry_count"的较大值,
    # 确保 worker/API 重启清零内存计数后 MAX_RECOVERY 仍能触达(见 resolve_recovery_count)。
    recovery_count = resolve_recovery_count(get_recovery_count(task_name), job_state)
    recovery_skip_vl = bool((heartbeat or {}).get("vl_skipped"))

    # ── 5. 上限兜底 ────────────────────────────────────────────────
    if recovery_count >= MAX_RECOVERY:
        logger.error(
            "文档处理任务恢复次数已达上限({}), 放弃恢复: document_id={}",
            MAX_RECOVERY,
            document_id,
        )
        err_msg = (
            f"任务恢复失败(已重试{MAX_RECOVERY}次)，"
            "请检查服务器环境后重新上传文件"
        )
        # 终态闭环: 把 vector_job_state 落 FAILED 进入 TERMINAL 态, 否则它仍停在
        # RECOVERING, 下次巡检会再次进入恢复判定造成噪音/误恢复。FAILED 后 decide_recovery
        # 首行 TERMINAL 判定直接 no-op。
        await upsert_vector_job_state(
            db,
            project_id=project_id,
            document_id=document_id,
            task_name=task_name,
            status=VectorJobStatus.FAILED,
            queue_mode=(job_state.queue_mode if job_state else "arq"),
            stage="failed",
            last_error=err_msg,
            retry_count=recovery_count,
            bump_finished_at=True,
        )
        project_row = await db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = project_row.scalar_one_or_none()
        if project is not None:
            project.status = ProjectStatus.FAILED
        await db.commit()
        # 同步 Redis 心跳为 failed, 让前端轮询/路由立刻看到终态而非持续 recovering
        update_heartbeat(task_name, stage="failed", progress=err_msg)
        reset_recovery_count(task_name)
        return DocumentProcessReconciliationResult(
            outcome="max_recovery_exceeded",
            recovery_count=recovery_count,
            error_message=err_msg,
            recover_reason=recover_reason,
        )

    # ── 6. 重入队 ──────────────────────────────────────────────────
    increment_recovery_count(task_name)
    arq_job_retry_total.labels(
        function="process_document_job",
        reason=recover_reason or "unknown",
    ).inc()
    logger.warning(
        "文档处理任务丢失, 自动恢复({}/{}): document_id={}",
        recovery_count + 1,
        MAX_RECOVERY,
        document_id,
    )
    cancel_task(task_name)

    use_arq = await can_use_arq("process_document_job")
    pool = await get_redis_pool() if use_arq else None
    arq_job_id = await enqueue_process_document_job(
        pool,
        project_id=project_id,
        document_id=document_id,
        skip_vl=recovery_skip_vl,
    )
    queue_mode = "arq" if arq_job_id is not None else "asyncio_fallback"
    if queue_mode == "asyncio_fallback":
        # 路径与原路由一致: ARQ 不可用时本地启 asyncio 任务兜底
        from app.core.tasks.process_task import run_process_task

        create_background_task(
            run_process_task(
                project_id,
                document_id,
                skip_vl=recovery_skip_vl,
                queue_mode=queue_mode,
            ),
            name=task_name,
            task_type="process",
            project_id=project_id,
            fallback_control=True,
        )
    recover_extra: dict = {"queue_mode": queue_mode}
    if recovery_skip_vl:
        recover_extra["vl_skipped"] = True
    update_heartbeat(
        task_name,
        stage="recovering",
        progress="0/0",
        extra=recover_extra,
    )
    await upsert_vector_job_state(
        db,
        project_id=project_id,
        document_id=document_id,
        task_name=task_name,
        status=VectorJobStatus.RECOVERING,
        queue_mode=queue_mode,
        stage="recovering",
        progress=f"{recovery_count + 1}/{MAX_RECOVERY}",
        retry_count=recovery_count + 1,
        chunk_fingerprint_version=str(
            getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")
        ),
        arq_job_id=arq_job_id if queue_mode == "arq" else None,
        bump_enqueued_at=(queue_mode == "arq"),
    )
    await db.commit()

    return DocumentProcessReconciliationResult(
        outcome="recovered",
        queue_mode=queue_mode,
        recovery_count=recovery_count + 1,
        recover_reason=recover_reason,
        arq_job_id=arq_job_id if queue_mode == "arq" else None,
    )
