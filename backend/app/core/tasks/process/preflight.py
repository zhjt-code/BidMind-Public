"""文档处理预检阶段(Session 1)

职责:
1. 加载 document + 项目不匹配检查 -> 直接 FAILED 并 early return
2. 幂等守卫(parsed=1)-> early return(防 ARQ/asyncio/恢复路径重复执行)
3. 计算 effective_skip_vl + business_flow + image_save_dir
4. 写入 ProjectStatus.PARSING + RetryService.mark_task_running
5. VectorJobState upsert: CLAIMED(arq) -> RUNNING(parsing) + bump heartbeat
6. 在主入口处也检查 is_project_cancelled, 防止已取消项目仍触发业务/状态写入

返回 PreflightResult 给主编排器; None 表示决定 early return(失败/幂等)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from app.core.tasks.process.state import PreflightResult


async def run_preflight(
    *,
    project_id: int,
    document_id: int,
    skip_vl: bool,
    queue_mode: str,
    worker_id: Optional[str],
    task_name: str,
    base_extra: dict,
    heartbeat: Callable[..., None],
) -> Optional[PreflightResult]:
    """执行 Session 1 初始化与状态机入场。

    所有数据库操作均在单一短事务内完成, 退出 async with 前 commit, 释放行锁。
    """
    # 函数内 import: 避免主模块加载时与 process_task 形成循环依赖
    # 同时确保 monkeypatch 在每次调用时拿到最新的模块属性
    from app.core.config import settings
    from app.core.arq_runtime import is_project_cancelled
    from app.core.tasks import process_task as ptm
    from app.core.vectorizer.job_state import upsert_vector_job_state
    from app.models.project import ProjectStatus
    from app.models.vector_job_state import VectorJobStatus

    # 任务入口先检查一次, 避免在被删除的项目上启动任何业务/状态写入
    if is_project_cancelled(project_id):
        logger.info(
            f"[文档处理] 项目已取消/删除, 跳过执行: project_id={project_id}, document_id={document_id}"
        )
        return None

    # ===== Session 1: 初始化 + 设置解析状态 (秒级) =====
    # 通过 ptm.async_session_factory 而非顶层 import: 兼容外部测试 monkeypatch
    async with ptm.async_session_factory() as db:
        document = await ptm._get_document(db, document_id)
        if not document:
            logger.error(f"[文档处理] 文档不存在: {document_id}")
            return None

        doc_project_id = document.project_id
        if doc_project_id != project_id:
            mismatch_error = (
                f"项目与文档不匹配: project_id={project_id}, "
                f"doc_project_id={doc_project_id}, document_id={document_id}"
            )
            logger.error(f"[文档处理] {mismatch_error}")
            heartbeat("failed", mismatch_error)
            await upsert_vector_job_state(
                db,
                project_id=project_id,
                document_id=document_id,
                task_name=task_name,
                status=VectorJobStatus.FAILED,
                queue_mode=queue_mode,
                stage="failed",
                progress=mismatch_error[:200],
                last_error=mismatch_error,
                chunk_fingerprint_version=str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
                worker_id=worker_id,
                bump_finished_at=True,
                bump_heartbeat=True,
            )
            await db.commit()
            return None

        # 幂等守卫: 文档已解析则直接返回, 防止 ARQ/asyncio/恢复路径任一导致的重复执行
        # (即使上游 _job_id 去重失效, 该检查仍能避免重跑 VL/向量化造成的资源浪费和数据不一致)
        if document.parsed == 1:
            logger.warning(
                f"[文档处理] 文档已解析完成, 跳过重复处理: "
                f"document_id={document_id}, project_id={doc_project_id}, queue_mode={queue_mode}"
            )
            heartbeat("completed", "处理完成")
            return None

        file_path = str(document.file_path)
        project = await ptm._get_project(db, doc_project_id)
        effective_skip_vl = ptm.resolve_effective_skip_vl(
            project_type=(project.project_type if project else None),
            document_type=document.doc_type,
            requested_skip_vl=skip_vl,
        )
        business_flow = ptm.business_flow_for_project_type(
            project.project_type if project else None
        )
        if effective_skip_vl:
            base_extra["vl_skipped"] = True
        logger.info(
            "[文档处理] ========== 开始 document_id={}, requested_skip_vl={}, "
            "effective_skip_vl={}, project_type={}, document_type={}, queue_mode={}, worker_id={} ==========",
            document_id,
            skip_vl,
            effective_skip_vl,
            (project.project_type.value if project else "unknown"),
            document.doc_type.value,
            queue_mode,
            worker_id,
        )
        heartbeat("parsing", "初始化中")
        if project:
            project.status = ProjectStatus.PARSING

        try:
            from app.services.retry_service import RetryService

            await RetryService(db).mark_task_running(
                doc_project_id,
                node_key=RetryService.NODE_WRITING_TENDER_PROCESS,
                target_id=document_id,
                stage="parsing",
            )
        except Exception as retry_err:
            logger.debug(f"[文档处理] 更新显式重试运行态失败(忽略): {retry_err}")

        # ARQ 状态机: worker 刚领取 -> CLAIMED, 立即转 RUNNING 表示开始真实业务
        # asyncio 模式不写 claimed_at, 直接 RUNNING 即可
        await upsert_vector_job_state(
            db,
            project_id=doc_project_id,
            document_id=document_id,
            task_name=task_name,
            status=VectorJobStatus.RUNNING,
            queue_mode=queue_mode,
            stage="parsing",
            progress="初始化中",
            chunk_fingerprint_version=str(getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")),
            worker_id=worker_id,
            bump_claimed_at=(queue_mode == "arq"),
            bump_started_at=True,
            bump_heartbeat=True,
        )
        await db.commit()

    image_save_dir = ptm._get_document_image_dir(
        upload_dir=Path(settings.UPLOAD_DIR),
        project_id=doc_project_id,
        document_id=document_id,
    )

    return PreflightResult(
        file_path=file_path,
        doc_project_id=doc_project_id,
        project=project,
        business_flow=business_flow,
        effective_skip_vl=effective_skip_vl,
        image_save_dir=image_save_dir,
    )
