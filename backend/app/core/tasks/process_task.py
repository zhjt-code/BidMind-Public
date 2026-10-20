"""文档处理后台任务

在后台执行完整的文档处理流程, 通过心跳机制报告进度。

流程:
1. PDF解析 (fitz + pdfplumber) -- 在进程池中执行，实现CPU并行
2. VL视觉模型识别图片 (如已配置)
3. 文本分块 (LangChain) -- 在线程中执行
4. 向量化存储 (bge-m3 + PGVector) -- encode 在线程中执行

心跳阶段: parsing -> vl_recognition -> chunking -> vectorizing -> completed

并发控制:
- DOC_PROCESS_CONCURRENCY 信号量限制同时处理的文档数
- PARSE_THREAD_WORKERS 独立进程池处理PDF解析（变量名沿用）

会话策略:
- 采用"迟获取、早释放"模式，每个阶段仅在需要数据库时获取短期session
- 避免长事务占用连接池，防止 QueuePool 耗尽
"""

import asyncio
import concurrent.futures
import multiprocessing
import threading
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability.task_observation import TaskFlow, observe_task, record_stage
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectType


class _VLQualityError(Exception):
    """VL识别质量门禁触发, 需要传播到项目级错误处理"""


def _compute_scan_concurrency(pool_healthy: int) -> int:
    """计算 scan_heavy 阶段整页 OCR 的并发上限。: 流程级公平调度已在 OcrFlowScheduler 中承接 per_image
 优先级与多流程份额控制; 本函数只限制 scan_heavy 本地 waiter 数,
 避免一次性为极大页数创建过多内存对象。

 公式 (pool_healthy 为 OCR HTTP 池中健康实例数):
 - pool_healthy <= 0: 池信息不可用, 兜底 2
 - pool_healthy >= 1: max(pool_healthy, 2)

 样例:
 - pool=0 -> 2 (fallback)
 - pool=1 -> 2
 - pool=3 -> 3
 - pool=5 -> 5 (单流程可让全部 OCR 实例参与)
 """
    if pool_healthy <= 0:
        return 2
    return max(pool_healthy, 2)


# ── 懒初始化的进程池和信号量 ─────────────────────────────────────────────────────

_parse_executor: Optional[concurrent.futures.ProcessPoolExecutor] = None
_parse_executor_lock = threading.Lock()


def _get_parse_executor() -> concurrent.futures.ProcessPoolExecutor:
    global _parse_executor
    with _parse_executor_lock:
        if _parse_executor is not None and getattr(_parse_executor, '_broken', False):
            logger.warning("[进程池] 检测到 BrokenProcessPool，正在重建")
            try:
                _parse_executor.shutdown(wait=False)
            except Exception:
                pass
            _parse_executor = None
        if _parse_executor is None:
            from app.core.config import settings
            _parse_executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=settings.PARSE_THREAD_WORKERS,
                mp_context=multiprocessing.get_context("spawn"),
            )
    return _parse_executor


def _reset_parse_executor() -> None:
    """强制重置进程池（在 BrokenProcessPool 或解析超时后调用）。

    关键: ``shutdown(wait=False)`` 不会终止运行中的子进程, 失控的解析(如病态/超大
    PDF)会在后台继续占满 CPU。因此先 ``terminate()`` 仍存活的子进程, 再 shutdown,
    确保解析超时熔断后立即释放算力。
    """
    global _parse_executor
    with _parse_executor_lock:
        if _parse_executor is not None:
            try:
                procs = list(getattr(_parse_executor, "_processes", {}).values())
            except Exception:
                procs = []
            for p in procs:
                try:
                    if p.is_alive():
                        p.terminate()
                except Exception:
                    pass
            try:
                _parse_executor.shutdown(wait=False)
            except Exception:
                pass
            _parse_executor = None
    logger.info("[进程池] 已重置，下次调用将重建")


def _render_pages_sync(
    file_path: str,
    page_render_list: list[tuple[int, str]],
    dpi: int = 200,
    page_clip_map: dict[int, tuple[float, float, float, float]] | None = None,
) -> dict[int, tuple[str, str]]:
    """在线程中批量渲染扫描密集页

    fitz 非线程安全, 因此整个"打开 → 渲染 → 关闭"必须在同一线程内完成。
    通过 asyncio.to_thread 调用, 避免阻塞事件循环。

    Args:
        file_path: PDF 文件路径
        page_render_list: [(page_num, context), ...] 待渲染页面及其上下文
        dpi: 渲染分辨率
        page_clip_map: 可选页码到正文裁剪框的映射

    Returns:
        {page_num: (base64, context)} 渲染缓存
    """
    import fitz as _fitz

    from app.core.parser.page_renderer import render_page_to_base64

    result: dict[int, tuple[str, str]] = {}
    doc = _fitz.open(file_path)
    try:
        for page_num, ctx in page_render_list:
            clip = (page_clip_map or {}).get(page_num)
            b64 = render_page_to_base64(doc[page_num - 1], dpi=dpi, clip=clip)
            result[page_num] = (b64, ctx)
    finally:
        doc.close()
    return result


_doc_process_sem: Optional[asyncio.Semaphore] = None


def _get_doc_process_sem() -> asyncio.Semaphore:
    global _doc_process_sem
    if _doc_process_sem is None:
        from app.core.config import settings
        _doc_process_sem = asyncio.Semaphore(settings.DOC_PROCESS_CONCURRENCY)
    return _doc_process_sem


# ── 公开接口 ──────────────────────────────────────────────────────────────────


def get_process_task_name(document_id: int) -> str:
    """获取任务名称"""
    return f"process_doc_{document_id}"


@observe_task(flow=TaskFlow.PROCESS, task_type="process")
async def run_process_task(
    project_id: int,
    document_id: int,
    *,
    skip_vl: bool = False,
    queue_mode: str = "asyncio",
    worker_id: Optional[str] = None,
) -> None:
    """执行文档处理后台任务（带全局并发限流）

    Args:
        project_id: 项目ID
        document_id: 文档ID
        skip_vl: 跳过VL图片识别(加速处理,可能遗漏扫描件中的人员/资质信息)
        queue_mode: "arq" 或 "asyncio", 决定状态机写入策略
        worker_id: ARQ 模式下由 worker 传入, 用于写入 vector_job_states.worker_id
    """
    async with _get_doc_process_sem():
        await _run_process_task_inner(
            project_id,
            document_id,
            skip_vl=skip_vl,
            queue_mode=queue_mode,
            worker_id=worker_id,
        )


# ── 内部实现 ──────────────────────────────────────────────────────────────────


async def _run_process_task_inner(
    project_id: int,
    document_id: int,
    *,
    skip_vl: bool = False,
    queue_mode: str = "asyncio",
    worker_id: Optional[str] = None,
) -> None:
    import shutil

    from app.core.arq_runtime import ProjectCancelledError
    from app.core.tasks import update_heartbeat
    from app.core.tasks.cancellation import make_project_cancel_check
    from app.core.tasks.process.preflight import run_preflight

    task_name = get_process_task_name(document_id)

    _base_extra: dict = {}

    def _hb(stage: str, progress: str = "", extra: Optional[dict] = None) -> None:
        merged = {**_base_extra, **(extra or {})}
        update_heartbeat(task_name, stage=stage, progress=progress, extra=merged or None)

    async def _stage(name: str) -> None:
        """落库一条阶段转换观测(phase=STAGE), 让进行中的任务也能在时间线看到当前阶段。"""
        await record_stage(
            flow=TaskFlow.PROCESS,
            task_type="process",
            project_id=project_id,
            stage=name,
            labels={"document_id": document_id},
        )

    try:
        preflight = await run_preflight(
            project_id=project_id,
            document_id=document_id,
            skip_vl=skip_vl,
            queue_mode=queue_mode,
            worker_id=worker_id,
            task_name=task_name,
            base_extra=_base_extra,
            heartbeat=_hb,
        )
    except asyncio.CancelledError:
        # preflight 写完 parsing 心跳但未提交 RUNNING 前被取消, 会留下幽灵 QUEUED。
        # 落 LOST 可恢复态后 re-raise, 保持 ARQ 取消语义。
        from app.core.tasks.process.failure_handler import handle_interrupt
        await handle_interrupt(
            project_id=project_id,
            document_id=document_id,
            task_name=task_name,
            queue_mode=queue_mode,
            worker_id=worker_id,
            heartbeat=_hb,
        )
        raise
    if preflight is None:
        return

    file_path = preflight.file_path
    doc_project_id = preflight.doc_project_id
    business_flow = preflight.business_flow
    effective_skip_vl = preflight.effective_skip_vl
    image_save_dir = preflight.image_save_dir

    try:
        _check_project_cancelled = make_project_cancel_check(doc_project_id)

        if image_save_dir.exists():
            shutil.rmtree(image_save_dir, ignore_errors=True)

        _check_project_cancelled()
        from app.core.tasks.process.convert_stage import run_convert_stage
        file_path = await run_convert_stage(
            file_path=file_path,
            document_id=document_id,
            heartbeat=_hb,
        )

        # ========== 阶段1: PDF解析（进程池，绕过GIL）==========
        _check_project_cancelled()
        await _stage("parse")
        from app.core.tasks.process.parse_stage import run_parse_stage

        parsed_doc, image_count = await run_parse_stage(
            file_path=file_path,
            image_save_dir=image_save_dir,
            document_id=document_id,
            heartbeat=_hb,
        )
        all_images = parsed_doc.all_images
        _check_project_cancelled()

        # ========== 阶段2: VL视觉模型识别 (无DB连接) ==========
        _check_project_cancelled()
        await _stage("vl_recognition")
        _total_ocr_ok = 0
        if effective_skip_vl:
            logger.info(f"[文档处理] 跳过VL识别(按策略), {image_count}张图片未处理")
        elif all_images:
            from app.core.tasks.process.vl_stage import run_vl_stage
            vl_result = await run_vl_stage(
                parsed_doc=parsed_doc,
                file_path=file_path,
                doc_project_id=doc_project_id,
                business_flow=business_flow,
                image_count=image_count,
                task_name=task_name,
            )
            _total_ocr_ok = vl_result.total_ocr_ok
        else:
            logger.info("[文档处理] 阶段2: 跳过VL识别(无图片)")
        # ========== 阶段2.5 / 3 / 3.5 ==========
        _check_project_cancelled()
        await _stage("chunking")
        from app.core.tasks.process.chunk_stage import run_chunk_stage

        chunk_result = await run_chunk_stage(
            parsed_doc=parsed_doc,
            document_id=document_id,
            effective_skip_vl=effective_skip_vl,
            base_extra=_base_extra,
            heartbeat=_hb,
            cancel_check=_check_project_cancelled,
        )
        chunks = chunk_result.chunks
        shadow_summary = chunk_result.shadow_summary

        # ========== 阶段4: 向量化存储 ==========
        _check_project_cancelled()
        await _stage("vectorizing")
        from app.core.tasks.process.state import DocumentImageOutcome
        from app.core.tasks.process.vectorize_stage import run_vectorize_stage

        db_chunks_count = await run_vectorize_stage(
            chunks=chunks,
            document_id=document_id,
            doc_project_id=doc_project_id,
            task_name=task_name,
            queue_mode=queue_mode,
            worker_id=worker_id,
            image_outcome=DocumentImageOutcome(
                ocr_ok_count=_total_ocr_ok,
                image_count=image_count,
                vl_skipped=bool(effective_skip_vl),
            ),
            shadow_summary=shadow_summary,
            heartbeat=_hb,
        )
        logger.info(
            f"[文档处理] ========== 完成 document_id={document_id}, "
            f"{parsed_doc.page_count}页, {db_chunks_count}向量 =========="
        )
        # 文档质检信封收敛(整本/分卷同口径; 缺卷不收敛; fail-open)
        from app.core.tasks.process.document_qa_finalize import (
            maybe_finalize_document_qa_envelope,
        )

        qa_decision = await maybe_finalize_document_qa_envelope(doc_project_id)
        _hb("completed", "处理完成", extra=qa_decision.to_heartbeat_extra())
        # 单文档质检以处理队列任务执行, 不占用本任务的槽位与心跳 
        try:
            from app.core.tasks.document_qa_job import enqueue_document_qa

            qa_dispatch = await enqueue_document_qa(doc_project_id, document_id)
            logger.info(f"[文档处理] 文档质检投递: document_id={document_id}, result={qa_dispatch}")
        except Exception as qa_err:
            logger.warning(
                f"[文档处理] 文档质检投递失败(不影响文档处理成功): "
                f"document_id={document_id}, error={qa_err}"
            )
        try:
            from app.core.tasks.process.tender_extract_prewarm import (
                maybe_enqueue_tender_extract_prewarm,
            )

            prewarm_decision = await maybe_enqueue_tender_extract_prewarm(
                project_id=doc_project_id,
                source_document_id=document_id,
            )
            _hb("completed", "处理完成", extra=prewarm_decision.to_heartbeat_extra())
            logger.info(
                "[文档处理] 招标要求提取预热判定: "
                f"project_id={doc_project_id}, document_id={document_id}, "
                f"status={prewarm_decision.status}, reason={prewarm_decision.reason}, "
                f"queue_mode={prewarm_decision.queue_mode}"
            )
        except Exception as prewarm_err:
            _hb(
                "completed",
                "处理完成",
                extra={
                    "extract_prewarm_status": "failed",
                    "extract_prewarm_reason": str(prewarm_err)[:200],
                },
            )
            logger.warning(
                "[文档处理] 招标要求提取预热调度失败(不影响文档处理成功): "
                f"project_id={doc_project_id}, document_id={document_id}, "
                f"error={prewarm_err}"
            )

    except ProjectCancelledError:
        # 删除/取消导致的中止: 不写 FAILED, 不再 upsert 状态表 (外键已可能失效)
        from app.core.tasks.process.failure_handler import handle_cancel
        await handle_cancel(
            doc_project_id=doc_project_id,
            document_id=document_id,
            heartbeat=_hb,
        )
        return
    except asyncio.CancelledError:
        # 处理阶段被取消(worker 优雅关闭/超时/abort), 落 LOST 可恢复态后 re-raise。
        from app.core.tasks.process.failure_handler import handle_interrupt
        await handle_interrupt(
            project_id=doc_project_id,
            document_id=document_id,
            task_name=task_name,
            queue_mode=queue_mode,
            worker_id=worker_id,
            heartbeat=_hb,
        )
        raise
    except Exception as e:
        from app.core.tasks.process.failure_handler import handle_failure
        await handle_failure(
            error=e,
            doc_project_id=doc_project_id,
            document_id=document_id,
            task_name=task_name,
            queue_mode=queue_mode,
            worker_id=worker_id,
            heartbeat=_hb,
        )


# ── 私有辅助 ──────────────────────────────────────────────────────────────────


async def _get_document(db: AsyncSession, document_id: int) -> Optional[Document]:
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    return result.scalar_one_or_none()


async def _get_project(db: AsyncSession, project_id: int) -> Optional[Project]:
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    return result.scalar_one_or_none()


async def _check_all_parsed(db: AsyncSession, project_id: int) -> bool:
    from app.services.document_parse_readiness import (
        project_required_documents_are_parsed,
    )

    project = await _get_project(db, project_id)
    if project is None:
        return False
    return await project_required_documents_are_parsed(db, project)


def resolve_effective_skip_vl(
    project_type: ProjectType | None,
    document_type: DocumentType,
    requested_skip_vl: bool,
) -> bool:
    """按项目类型+文档类型收口最终 skip_vl 策略。"""
    if project_type == ProjectType.BID_REVIEW:
        if document_type == DocumentType.TENDER:
            return requested_skip_vl
        if document_type == DocumentType.BID:
            return False
        return requested_skip_vl

    if project_type == ProjectType.BID_WRITING:
        if document_type == DocumentType.TENDER:
            return requested_skip_vl
        return False

    if project_type == ProjectType.COLLUSION_CHECK:
        return requested_skip_vl

    return requested_skip_vl


def business_flow_for_project_type(project_type: ProjectType | None) -> str:
    """将项目类型映射为 PromptRegistry/LLM 子任务使用的业务 flow。"""

    if project_type == ProjectType.COLLUSION_CHECK:
        return "collusion"
    return "bid_review"


def _get_document_image_dir(upload_dir: Path, project_id: int, document_id: int) -> Path:
    """返回文档级图片目录，避免同项目多文档图片互相覆盖。"""
    return upload_dir / str(project_id) / "images" / f"doc_{document_id}"
