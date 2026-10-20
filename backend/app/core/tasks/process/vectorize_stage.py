"""文档处理阶段4: 向量化存储

职责:
1. 向量化前主动释放 GPU 缓存(降低与 OCR/VL 的显存竞争)
2. 启动 keepalive(10s 周期 + 30s 节流的 DB 心跳, 防御)
3. Session 4a: 单独提交 RUNNING(vectorizing), 释放行锁让 keepalive 可见
4. Session 4b (关键约束): VectorIndexer.index_chunks → document.parsed=1
 → project.status=PARSED/UPLOADING → VectorJobState.COMPLETED 必须在同一 async with 块原子提交
5. completed 心跳 + RetryService.mark_task_completed

行为契约:
- vectorizing extra 字段映射: vectorizing_substage / vectorizing_meta / degraded_to_cpu
 / fallback_reason / recovery_mode / device_cooldown_sec / stick_to_cpu
- 所有进度字符串格式 f"{done}/{total}" 保持(前端依赖)
- index_chunks 抛异常 → 主编排器外层 except 走 FAILED, 此处不可吞
- finally 块负责 keepalive 收尾, 即使 index_chunks 异常也必须停止 keepalive
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from loguru import logger

from app.core.tasks.process.state import DocumentImageOutcome, VectorizeKeepaliveState


async def run_vectorize_stage(
    *,
    chunks: list,
    document_id: int,
    doc_project_id: int,
    task_name: str,
    queue_mode: str,
    worker_id: Optional[str],
    image_outcome: DocumentImageOutcome,
    shadow_summary: Optional[Any],
    heartbeat: Callable[..., None],
) -> int:
    """执行 阶段4 向量化(GPU释放 + keepalive + Session 4a + Session 4b 原子事务).

    Returns: 成功落库的 chunks 数量(供主编排器日志)
    """
    from app.core.config import settings
    from app.core.tasks import process_task as ptm
    from app.core.tasks.cancellation import make_project_cancel_check
    from app.core.vectorizer import VectorIndexer
    from app.core.vectorizer.job_state import upsert_vector_job_state
    from app.models.vector_job_state import VectorJobStatus

    chunk_count = len(chunks)

    _check_project_cancelled = make_project_cancel_check(doc_project_id)
    _check_project_cancelled()

    # 向量化前主动释放 OCR/VL 阶段残留的 GPU 缓存，降低显存竞争
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            mem_alloc = torch.cuda.memory_allocated() / 1024 / 1024
            mem_reserved = torch.cuda.memory_reserved() / 1024 / 1024
            logger.info(
                f"[文档处理] 向量化前GPU显存: "
                f"allocated={mem_alloc:.0f}MB, reserved={mem_reserved:.0f}MB"
            )
    except Exception:
        pass

    logger.info(f"[文档处理] 阶段4: 向量化存储 ({chunk_count}个chunk)")
    heartbeat("vectorizing", f"0/{chunk_count}", extra={
        "vectorizing_substage": "wait_lock",
        "degraded_to_cpu": False,
    })

    # 共享状态: 替代原 _vec_keepalive_state dict, 字段映射保持一致
    state = VectorizeKeepaliveState(total=chunk_count)
    _vec_keepalive_stop = asyncio.Event()

    # DB 心跳节流 (防御): 独立短事务推进 last_heartbeat_at,
    # 避免主事务长持有行锁导致 keepalive 不可见 -> API 误判 running_heartbeat_stale
    _DB_HB_MIN_INTERVAL = 30.0
    _vec_db_hb_state: dict = {"last_ts": 0.0}
    _embedding_version = str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1"))

    async def _persist_vec_heartbeat(force: bool = False) -> None:
        _check_project_cancelled()
        now_ts = time.time()
        if not force and (now_ts - _vec_db_hb_state["last_ts"]) < _DB_HB_MIN_INTERVAL:
            return
        _vec_db_hb_state["last_ts"] = now_ts
        try:
            async with ptm.async_session_factory() as hb_db:
                await upsert_vector_job_state(
                    hb_db,
                    project_id=doc_project_id,
                    document_id=document_id,
                    task_name=task_name,
                    status=VectorJobStatus.RUNNING,
                    queue_mode=queue_mode,
                    stage="vectorizing",
                    progress=f"{state.done}/{state.total}",
                    chunk_fingerprint_version=_embedding_version,
                    worker_id=worker_id,
                    bump_heartbeat=True,
                )
                await hb_db.commit()
        except Exception as exc:
            logger.warning(f"[文档处理] DB 心跳独立提交失败 doc={document_id}: {exc}")

    async def _vectorize_keepalive():
        while not _vec_keepalive_stop.is_set():
            _check_project_cancelled()
            heartbeat("vectorizing", f"{state.done}/{state.total}", extra={
                "vectorizing_substage": state.substage,
                "vectorizing_meta": state.meta,
                "degraded_to_cpu": state.degraded_to_cpu,
                "fallback_reason": state.fallback_reason,
            })
            await _persist_vec_heartbeat()
            try:
                await asyncio.wait_for(_vec_keepalive_stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    keepalive_vec_task = asyncio.create_task(_vectorize_keepalive())

    def _on_encode_progress(done: int, total: int) -> None:
        state.done = done
        heartbeat("vectorizing", f"{done}/{total}", extra={
            "vectorizing_substage": state.substage,
            "degraded_to_cpu": state.degraded_to_cpu,
            "fallback_reason": state.fallback_reason,
        })

    def _on_vector_phase(
        phase: str,
        done: int,
        total: int,
        meta: dict,
    ) -> None:
        state.substage = phase
        state.done = done
        state.meta = meta
        if meta.get("degraded_to_cpu"):
            state.degraded_to_cpu = True
            # 仅当新 meta 真的携带非空 fallback_reason 时才覆盖,
            # 否则保留首个已记录的原因(修复 flush 阶段 meta 无 reason 导致被覆盖为 None 的观测 bug)
            new_reason = meta.get("fallback_reason")
            if new_reason:
                state.fallback_reason = new_reason
        if meta.get("recovery_mode"):
            state.recovery_mode = meta["recovery_mode"]
        cooldown_sec = meta.get("device_cooldown_sec")
        if cooldown_sec:
            state.device_cooldown_sec = cooldown_sec
        if meta.get("stick_to_cpu"):
            state.stick_to_cpu = True
        heartbeat("vectorizing", f"{done}/{total}", extra={
            "vectorizing_substage": phase,
            "vectorizing_meta": meta,
            "degraded_to_cpu": state.degraded_to_cpu,
            "fallback_reason": state.fallback_reason,
            "recovery_mode": state.recovery_mode,
            "device_cooldown_sec": state.device_cooldown_sec,
            "stick_to_cpu": state.stick_to_cpu,
        })
        # DB 心跳由 _vectorize_keepalive 周期内 _persist_vec_heartbeat 节流驱动,
        # 这里不再尝试从可能位于线程上下文的回调中跨 loop 调度, 避免 RuntimeError。

    # ===== Session 4a: 单独提交向量化开始的 RUNNING 状态 =====
    # 把这个 upsert 放到独立短事务里立刻 commit, 释放 vector_job_states 的行锁,
    # 后续 keepalive 的独立短事务才能正常更新 last_heartbeat_at。
    # 否则主事务会持有行锁直到 index_chunks 完成, 导致 DB 心跳被阻塞 -> API 误恢复。
    async with ptm.async_session_factory() as db:
        _check_project_cancelled()
        await upsert_vector_job_state(
            db,
            project_id=doc_project_id,
            document_id=document_id,
            task_name=task_name,
            status=VectorJobStatus.RUNNING,
            queue_mode=queue_mode,
            stage="vectorizing",
            progress=f"0/{chunk_count}",
            shadow_summary=shadow_summary.to_dict() if shadow_summary else None,
            chunk_fingerprint_version=_embedding_version,
            worker_id=worker_id,
            bump_heartbeat=True,
        )
        await db.commit()
    _vec_db_hb_state["last_ts"] = time.time()

    # ===== Session 4b (关键约束): chunks 持久化 + parsed=1 + COMPLETED 原子事务 =====
    # chunks 持久化与 document.parsed 必须在同一事务内原子提交,
    # 否则跨会话读不到 embedding (同源风险)。
    db_chunks_count = 0
    try:
        async with ptm.async_session_factory() as db:
            _check_project_cancelled()
            indexer = VectorIndexer(db)
            db_chunks = await indexer.index_chunks(
                document_id,
                chunks,
                progress_callback=_on_encode_progress,
                phase_callback=_on_vector_phase,
                cancel_check=_check_project_cancelled,
            )
            db_chunks_count = len(db_chunks)
            _check_project_cancelled()
            if db_chunks_count <= 0:
                raise ValueError(
                    "未生成有效文本分块或向量，请检查文档是否为扫描件、空文件或已损坏，"
                    "必要时开启图片识别后重新上传/解析"
                )

            heartbeat("vectorizing", f"{chunk_count}/{chunk_count}", extra={
                "vectorizing_substage": "done",
                "degraded_to_cpu": state.degraded_to_cpu,
                "fallback_reason": state.fallback_reason,
            })
            logger.info(
                f"[文档处理] 向量化完成: {db_chunks_count}个向量"
                + (f" (CPU降级, reason={state.fallback_reason})"
                   if state.degraded_to_cpu else "")
            )

            document = await ptm._get_document(db, document_id)
            _check_project_cancelled()
            if document:
                document.parsed = 1
                # OCR计数与图片来源必须在 parsed=1 同一事务持久化,
                # 计费明细依赖三者区分"OCR为0"是无图片/跳过/走VL通道还是计数丢失
                document.ocr_image_count = image_outcome.ocr_ok_count
                document.image_count = image_outcome.image_count
                document.vl_skipped = image_outcome.vl_skipped

            project = await ptm._get_project(db, doc_project_id)
            _check_project_cancelled()
            if project:
                from app.services.document_parse_readiness import (
                    apply_status_after_document_parse,
                )

                all_parsed = await ptm._check_all_parsed(db, doc_project_id)
                apply_status_after_document_parse(
                    project, all_required_parsed=all_parsed,
                )

            await upsert_vector_job_state(
                db,
                project_id=doc_project_id,
                document_id=document_id,
                task_name=task_name,
                status=VectorJobStatus.COMPLETED,
                queue_mode=queue_mode,
                stage="completed",
                progress=f"{chunk_count}/{chunk_count}",
                degraded_to_cpu=state.degraded_to_cpu,
                fallback_reason=state.fallback_reason,
                recovery_mode=state.recovery_mode,
                shadow_summary=shadow_summary.to_dict() if shadow_summary else None,
                chunk_fingerprint_version=str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
                worker_id=worker_id,
                bump_finished_at=True,
                bump_heartbeat=True,
            )

            _check_project_cancelled()
            await db.commit()
    finally:
        _vec_keepalive_stop.set()
        try:
            await keepalive_vec_task
        except Exception as exc:
            from app.core.arq_runtime import ProjectCancelledError
            if isinstance(exc, ProjectCancelledError):
                raise
            raise

    heartbeat("completed", "处理完成")
    try:
        async with ptm.async_session_factory() as retry_db:
            from app.models.task_run import TaskRunStatus
            from app.services.flow_retry_service import mark_review_task_run_by_task_name
            from app.services.retry_service import RetryService

            await RetryService(retry_db).mark_task_completed(
                doc_project_id,
                node_key=RetryService.NODE_WRITING_TENDER_PROCESS,
                target_id=document_id,
            )
            await mark_review_task_run_by_task_name(
                retry_db,
                project_id=doc_project_id,
                task_name=task_name,
                status=TaskRunStatus.completed.value,
                stage="completed",
                last_error=None,
            )
    except Exception as retry_err:
        logger.debug(f"[文档处理] 更新显式重试完成态失败(忽略): {retry_err}")

    return db_chunks_count
