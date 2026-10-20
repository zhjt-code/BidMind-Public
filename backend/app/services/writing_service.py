"""标书编写服务层（Task 5 重构后）

封装标书编写全流程的业务编排，供路由层调用。
本模块经 Task 5 拆分为"thin orchestrator + 子服务"模式：

- :class:`WritingService` 仅做入口承接、状态机（``get_project_status``）、
  渲染私有 helper、共享 helper（``_get_project_or_raise`` / ``_ensure_preview_ready``
  等）；其余业务方法均委派给 :mod:`app.services.writing` 子包内 6 个子服务。
- 模块级纯函数（``_build_writing_steps`` / ``_build_upload_doc_steps`` 等）
  已迁移至 :mod:`app.services.writing.progress_steps`，本模块仅做 re-export
  以兑现 ``test_progress_standardization`` 等历史契约。

新功能开发应优先在子服务内实现，仅在确需暴露给路由 / 任务 / 测试时再
通过本模块的 thin forwarder 暴露。详见
``app/services/writing/__init__.py`` 与 ``docs/refactor_execution_plan.md``。
"""

import asyncio
import json
import time  # noqa: F401  保留以兼容外部模块通过 writing_service.time 的引用历史
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ServiceError
from app.core.permissions import check_project_permission
from app.core.tasks import inspect_task_liveness, is_task_active
from app.core.tasks.writing_extract_task import (
    get_writing_extract_task_name,
    run_writing_extract_task,
)
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus, ProjectType
from app.models.user import User
from app.models.writing_binding import WritingBinding
from app.models.writing_clause import WritingClause
from app.models.writing_material import WritingMaterial
from app.models.writing_material_image import WritingMaterialImage
from app.models.writing_outline_node import OutlineNodeType, WritingOutlineNode
from app.models.writing_render_artifact import WritingRenderArtifact
from app.models.writing_section_draft import WritingSectionDraft

# 进度组装纯函数下沉到 services/writing/progress_steps.py（Task 5 Step 1）。
# 此处保留全部 re-export 以兼容历史 import 路径：
#   from app.services.writing_service import _build_upload_doc_steps, _build_upload_extract_steps, _build_writing_steps
# 被 backend/tests/test_progress_standardization.py 作为契约锚点使用。
from app.services.writing.progress_steps import (  # noqa: F401
    _WRITING_PROGRESS_CONFIG,
    _WRITING_UPLOAD_DOC_LABELS,
    _build_upload_doc_steps,
    _build_upload_extract_steps,
    _build_writing_steps,
    _infer_writing_progress_phase,
    _parse_fraction_progress,
    _parse_percent_detail,
    _step_status,
    resolve_writing_upload_vl_skipped,
)


def _writing_status_payload(
    *,
    project_id: int,
    name: str,
    status: str,
    error_message: Optional[str],
    overall_status: str,
    overall_progress: int,
    steps: list,
    message: Optional[str],
    current_stage: str,
    current_detail: Optional[str],
    is_recovering: bool = False,
    recovery_count: int = 0,
) -> dict[str, Any]:
    """get_project_status 各分支 12 键响应载荷的统一构造器（批次二 L-9）。

    职责: 按既有响应契约拼装状态载荷，键集与键序与历史逐字一致
    不做: 不承担阶段推导与恢复判定——"何时返回哪段"的逻辑留在调用方
    """
    return {
        "id": project_id,
        "name": name,
        "status": status,
        "error_message": error_message,
        "overall_status": overall_status,
        "overall_progress": overall_progress,
        "steps": steps,
        "message": message,
        "is_recovering": is_recovering,
        "recovery_count": recovery_count,
        "current_stage": current_stage,
        "current_detail": current_detail,
    }


# =============================================================================
# get_project_status 内部状态视图
# -----------------------------------------------------------------------------
# 拆方法重构 (R2-5, 2026-05): 把 501 行单方法 get_project_status 拆为多步私有方法
# 时, 用以下三个私有 dataclass 收纳"入口快照 + 中间状态 + 步骤派生", 避免方法签名
# 爆炸成 10+ 参数。仅本模块内部使用 (`_` 前缀), 不对外暴露契约。
# 参考样板: app.services.review_service 的 _ReviewCounts / _ReviewStageView /
# _ReviewStepsView (v1 implementation_plan.md 2026-05-12 条目)。
# =============================================================================


@dataclass
class _WritingStatusContext:
    """get_project_status 入口查询快照（project + tender_doc + 心跳超时）."""

    project: Project
    tender_doc: Optional[Document]
    task_hb_timeout: int


@dataclass
class _WritingStageView:
    """get_project_status 编排过程的可变中间状态.

    重构前对应 `overall_status` / `overall_progress` / `current_stage` /
    `current_detail` / `message` / `recovery_count` / `is_recovering` /
    `heartbeat` / `task_active` 等局部变量。
    """

    overall_status: str = "idle"
    overall_progress: int = 0
    current_stage: str = ""
    current_detail: str = ""
    message: str = ""
    recovery_count: int = 0
    is_recovering: bool = False
    heartbeat: Optional[dict[str, Any]] = None
    task_active: bool = False


@dataclass
class _WritingStepsView:
    """get_project_status 的 steps[] 派生结果 + overall 进度/状态."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    overall_progress: int = 0
    overall_status: str = "idle"
    current_stage: str = ""
    current_detail: str = ""


class WritingService:
    """标书编写服务"""

    _TARGET_WORD_COUNT_LIMITS = {
        OutlineNodeType.data_fill: (100, 3000),
        OutlineNodeType.free_writing: (100, 5000),
    }

    _render_locks: ClassVar[dict[int, asyncio.Lock]] = {}
    _MATERIAL_DB_CONTENT_PREVIEW_LIMIT: ClassVar[int] = 50000

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        # 子服务（按 Task 5 拆分顺序逐步装配）：
        # 业务阶段子服务接受 `db` + 编排器共享 helper（如 _get_project_or_raise
        # / _get_tender_doc）作为依赖注入，事务边界由各子服务内部 commit 管理。
        # 关键：所有 callable 必须用 lambda 包装实现 late binding，否则测试在
        # WritingService(db) 之后 monkey patch service._get_project_or_raise
        # 等实例方法时，子服务捕获的旧 bound method 不会被更新（contract
        # 见 test_writing_extract_trigger.py / test_writing_clause_*）。
        from app.services.writing.binding_service import BindingService
        from app.services.writing.clause_service import ClauseService
        from app.services.writing.compose_service import ComposeService
        from app.services.writing.match_service import MatchService
        from app.services.writing.material_service import MaterialService
        from app.services.writing.outline_service import OutlineService
        from app.services.writing.render_service import RenderService

        self._clause = ClauseService(
            db,
            project_resolver=lambda *a, **kw: self._get_project_or_raise(*a, **kw),
            tender_doc_resolver=lambda *a, **kw: self._get_tender_doc(*a, **kw),
            ensure_vectorize_task_trigger=lambda pid: self._ensure_clause_vectorize_task(pid),
        )
        self._outline = OutlineService(
            db,
            project_resolver=lambda *a, **kw: self._get_project_or_raise(*a, **kw),
            render_invalidator=lambda pid: self._invalidate_render_artifact(pid),
        )
        self._match = MatchService(
            db,
            project_resolver=lambda *a, **kw: self._get_project_or_raise(*a, **kw),
        )
        self._binding = BindingService(
            db,
            project_resolver=lambda *a, **kw: self._get_project_or_raise(*a, **kw),
        )
        self._render = RenderService(
            db,
            project_resolver=lambda *a, **kw: self._get_project_or_raise(*a, **kw),
            ensure_preview_ready=lambda project: self._ensure_preview_ready(project),
            ensure_render_artifact=lambda *a, **kw: self._ensure_render_artifact(*a, **kw),
        )
        self._compose = ComposeService(
            db,
            project_resolver=lambda *a, **kw: self._get_project_or_raise(*a, **kw),
            render_invalidator=lambda pid: self._invalidate_render_artifact(pid),
            ensure_preview_ready=lambda project: self._ensure_preview_ready(project),
            ensure_render_artifact=lambda *a, **kw: self._ensure_render_artifact(*a, **kw),
            load_cached_consistency=lambda artifact: self._load_cached_consistency(artifact),
        )
        self._material = MaterialService(
            db,
            project_resolver=lambda *a, **kw: self._get_project_or_raise(*a, **kw),
            refresh_material_title_derivatives_hook=lambda mat: self._refresh_material_title_derivatives(mat),
        )

    # ==================== Material 共享 helper（thin forwarder 到 MaterialService） ====================
    # WritingService._build_material_db_content / _attach_effective_title 等被外部测试
    # 通过类名直接调用（WritingService._build_material_db_content(...)），故必须保留
    # 同名 staticmethod / classmethod 接口转发到 MaterialService 实现。

    @classmethod
    def _build_material_db_content(cls, text: Optional[str]) -> Optional[str]:
        from app.services.writing.material_service import MaterialService as _MS
        return _MS._build_material_db_content(text)

    @classmethod
    def _build_material_preview_excerpt(cls, text: Optional[str]) -> str:
        from app.services.writing.material_service import MaterialService as _MS
        return _MS._build_material_preview_excerpt(text)

    @staticmethod
    def _attach_effective_title(material: WritingMaterial) -> None:
        from app.services.writing.material_service import MaterialService as _MS
        _MS._attach_effective_title(material)

    @staticmethod
    def _image_review_status(image: WritingMaterialImage) -> str:
        from app.services.writing.material_service import MaterialService as _MS
        return _MS._image_review_status(image)

    @staticmethod
    def _select_refined_child_title(
        material: WritingMaterial,
        images: list[WritingMaterialImage],
    ) -> str | None:
        from app.services.writing.material_service import MaterialService as _MS
        return _MS._select_refined_child_title(material, images)

    @staticmethod
    def _refresh_material_title_labels(
        material: WritingMaterial,
        images: list[WritingMaterialImage],
    ) -> None:
        from app.services.writing.material_service import MaterialService as _MS
        _MS._refresh_material_title_labels(material, images)

    async def _refresh_material_title_derivatives(
        self,
        material: WritingMaterial,
    ) -> None:
        """根据当前有效标题刷新文件名标签、标签决议与元数据打标。

        thin forwarder 暴露给测试 monkey patch
        ``service._refresh_material_title_derivatives = AsyncMock(...)`` 使用。
        """
        await self._material._refresh_material_title_derivatives(material)

    async def _refine_sub_material_titles(
        self,
        processing_materials: list[WritingMaterial],
    ) -> int:
        """thin forwarder：测试通过 ``service._refine_sub_material_titles(...)`` 直接调用。"""
        return await self._material._refine_sub_material_titles(processing_materials)

    # ==================== 项目 ====================

    async def get_project_status(
        self, project_id: int, user: User,
    ) -> dict[str, Any]:
        """获取项目状态(含后台任务心跳信息).

        R2-5 拆分后: 主方法仅承担线性编排, 实际状态机分支由
        ``_resolve_upload_phase_payload``(A1-A7) 与
        ``_resolve_post_extract_payload``(B1-B7) 承接, 不变更对外契约。
        """
        ctx = await self._load_writing_status_context(project_id, user)
        upload_payload = await self._resolve_upload_phase_payload(ctx)
        if upload_payload is not None:
            return upload_payload
        return await self._resolve_post_extract_payload(ctx)

    # ==================== get_project_status 子方法 (R2-5 重构) ====================
    # "搬家不改语义": 把 501 行单方法 get_project_status 拆为多步私有方法。
    # 单步副作用边界已在每个 docstring 中标注。
    # ==============================================================================

    async def _resolve_upload_phase_payload(
        self, ctx: _WritingStatusContext,
    ) -> Optional[dict[str, Any]]:
        """A1-A7 上传/解析/提取期分支编排.

        返回 None 表示 status 不属于上传期或上传期分支无 return 出口
        (会 fall-through 到主方法 _resolve_post_extract_payload 编排器)。
        副作用: 可能触发 db.commit / dispatch / retry_service.mark_task_failed。
        """
        from app.core.tasks import (
            get_recovery_count,
            get_task_heartbeat,
        )
        from app.core.tasks.process_task import get_process_task_name
        from app.core.vectorizer.job_state import get_vector_job_state
        from app.models.vector_job_state import VectorJobStatus as _VJS

        project = ctx.project
        tender_doc = ctx.tender_doc
        task_hb_timeout = ctx.task_hb_timeout
        project_id = project.id

        upload_related_statuses = {
            ProjectStatus.UPLOADING,
            ProjectStatus.PARSING,
            ProjectStatus.PARSED,
            ProjectStatus.EXTRACTING,
            ProjectStatus.EXTRACTED,
        }
        if project.status not in upload_related_statuses or not tender_doc:
            return None

        tender_doc_id = tender_doc.id
        project_name = project.name
        project_status_value = project.status.value
        project_error_message = project.error_message
        process_task_name = get_process_task_name(tender_doc_id)
        extract_task_name = get_writing_extract_task_name(project_id)

        process_heartbeat = get_task_heartbeat(process_task_name)
        extract_liveness = inspect_task_liveness(
            extract_task_name,
            heartbeat_timeout=task_hb_timeout,
            arq_function="writing_extract_job",
        )
        extract_heartbeat = extract_liveness.heartbeat
        process_active = is_task_active(process_task_name)
        extract_active = extract_liveness.active

        if project.status == ProjectStatus.PARSED:
            return self._build_doc_completed_payload(ctx, process_task_name)

        if project.status in {ProjectStatus.UPLOADING, ProjectStatus.PARSING}:
            # 恢复决策统一收口到共享 reconciler(含 全部
            # 守卫: ARQ 在途探测、DB retry_count 取大、终态闭环、源文件校验)。
            # 写标侧禁止复制恢复逻辑, 只按 outcome 渲染载荷。
            from app.services.liveness.document_processing_reconciler import (
                reconcile_document_processing,
            )

            reconcile_result = await reconcile_document_processing(
                self.db, project_id=project_id, document_id=tender_doc_id
            )
            job_state = await get_vector_job_state(self.db, tender_doc_id)
            if (
                reconcile_result.outcome == "completed"
                or tender_doc.parsed == 1
                or (job_state is not None and job_state.status == _VJS.COMPLETED)
            ):
                return self._build_doc_completed_payload(ctx, process_task_name)
            if reconcile_result.outcome == "max_recovery_exceeded" or (
                reconcile_result.outcome == "already_failed" and not process_heartbeat
            ):
                # max_recovery: reconciler 已置项目 FAILED;
                # already_failed 无心跳: 用持久化原因渲染失败载荷(原实现无渲染出口)。
                error_message = (
                    reconcile_result.error_message or "文档处理失败，请重新上传文件"
                )
                return _writing_status_payload(
                    project_id=project_id,
                    name=project_name,
                    status=(
                        ProjectStatus.FAILED.value
                        if reconcile_result.outcome == "max_recovery_exceeded"
                        else project_status_value
                    ),
                    error_message=error_message,
                    overall_status="failed",
                    overall_progress=0,
                    steps=[],
                    message=error_message,
                    recovery_count=get_recovery_count(process_task_name),
                    current_stage="failed",
                    current_detail=error_message,
                )
            if reconcile_result.outcome == "recovered":
                process_active = True
                recovered_hb = get_task_heartbeat(process_task_name) or {}
                process_heartbeat = {
                    "stage": "parsing",
                    "progress": "",
                }
                if "vl_skipped" in recovered_hb:
                    process_heartbeat["vl_skipped"] = recovered_hb["vl_skipped"]
            # already_failed 且心跳仍在: fall through 走 stage=failed 的分步渲染

            if process_heartbeat or process_active:
                process_stage = str((process_heartbeat or {}).get("stage", "parsing"))
                process_progress = str((process_heartbeat or {}).get("progress", ""))
                vl_skipped = resolve_writing_upload_vl_skipped(
                    heartbeat_vl_skipped=(process_heartbeat or {}).get("vl_skipped"),
                    document_vl_skipped=getattr(tender_doc, "vl_skipped", None),
                )
                process_recovery_count = get_recovery_count(process_task_name)
                is_recovering = process_recovery_count > 0 and process_progress == ""
                steps, overall_progress, current_stage, current_detail, message = _build_upload_doc_steps(
                    doc_stage=process_stage,
                    progress_raw=process_progress,
                    vl_skipped=vl_skipped,
                    overall_status="failed" if process_stage == "failed" else "running",
                    current_detail=_WRITING_UPLOAD_DOC_LABELS.get(process_stage, "处理中"),
                )
                return _writing_status_payload(
                    project_id=project_id,
                    name=project_name,
                    status=project_status_value,
                    error_message=project_error_message,
                    overall_status="failed" if process_stage == "failed" else "running",
                    overall_progress=overall_progress,
                    steps=steps,
                    message="恢复中..." if is_recovering else message,
                    is_recovering=is_recovering,
                    recovery_count=process_recovery_count,
                    current_stage=current_stage,
                    current_detail="恢复中..." if is_recovering else current_detail,
                )
            if tender_doc.parsed == 1:
                return self._build_doc_completed_payload(ctx, process_task_name)

        if project.status in {ProjectStatus.EXTRACTING, ProjectStatus.EXTRACTED}:
            if (
                project.status == ProjectStatus.EXTRACTING
                and extract_liveness.state in {"stale", "missing"}
            ):
                error_message = (
                    "约束提取任务异常终止，请从失败节点继续重试"
                    if extract_liveness.state == "missing"
                    else "约束提取任务心跳超时，请从失败节点继续重试"
                )
                logger.warning(
                    "[写标] 约束提取任务失活: project={}, task={}, state={}, reason={}, age={}",
                    project_id,
                    extract_task_name,
                    extract_liveness.state,
                    extract_liveness.reason,
                    extract_liveness.age_seconds,
                )
                try:
                    from app.services.retry_service import RetryService

                    await RetryService(self.db).mark_task_failed(
                        project_id,
                        node_key=RetryService.NODE_WRITING_EXTRACT,
                        stage=extract_liveness.stage or "failed",
                        error=error_message,
                    )
                except Exception as exc:
                    logger.warning(
                        "[写标] 回写约束提取 TaskRun 失败(忽略): project={}, err={}",
                        project_id,
                        exc,
                    )
                steps, overall_progress, message = _build_upload_extract_steps(
                    current_stage="failed",
                    extract_overall_progress=0,
                    current_detail=error_message,
                    overall_status="failed",
                    vl_skipped=resolve_writing_upload_vl_skipped(
                        document_vl_skipped=getattr(tender_doc, "vl_skipped", None) if tender_doc else None,
                    ),
                )
                return _writing_status_payload(
                    project_id=project.id,
                    name=project.name,
                    status=project.status.value,
                    error_message=error_message,
                    overall_status="failed",
                    overall_progress=overall_progress,
                    steps=steps,
                    message=message,
                    recovery_count=get_recovery_count(extract_task_name),
                    current_stage="failed",
                    current_detail=error_message,
                )
            if extract_heartbeat or extract_active:
                hb_stage = str((extract_heartbeat or {}).get("stage", "starting"))
                hb_progress = str((extract_heartbeat or {}).get("progress", ""))
                extract_progress, extract_detail = _parse_percent_detail(hb_progress)
                if hb_stage in {"failed", "cancelled", "canceled"} and project.status != ProjectStatus.FAILED:
                    project.status = ProjectStatus.FAILED
                    project.error_message = extract_detail or "约束提取任务失败，请从失败节点继续重试"
                    await self.db.commit()
                    try:
                        from app.services.retry_service import RetryService

                        await RetryService(self.db).mark_task_failed(
                            project_id,
                            node_key=RetryService.NODE_WRITING_EXTRACT,
                            stage="failed",
                            error=project.error_message,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[写标] 回写约束提取失败终态失败(忽略): project={}, err={}",
                            project_id,
                            exc,
                        )
                steps, overall_progress, message = _build_upload_extract_steps(
                    current_stage=hb_stage,
                    extract_overall_progress=extract_progress,
                    current_detail=extract_detail,
                    overall_status="failed" if hb_stage == "failed" else ("completed" if hb_stage == "completed" else "running"),
                    vl_skipped=resolve_writing_upload_vl_skipped(
                        document_vl_skipped=getattr(tender_doc, "vl_skipped", None) if tender_doc else None,
                    ),
                )
                return _writing_status_payload(
                    project_id=project.id,
                    name=project.name,
                    status=project.status.value,
                    error_message=project.error_message,
                    overall_status="failed" if hb_stage == "failed" else ("completed" if hb_stage == "completed" else "running"),
                    overall_progress=overall_progress,
                    steps=steps,
                    message=message,
                    recovery_count=get_recovery_count(extract_task_name),
                    current_stage=hb_stage,
                    current_detail=extract_detail,
                )
            if project.status == ProjectStatus.EXTRACTED:
                steps, overall_progress, message = _build_upload_extract_steps(
                    current_stage="completed",
                    extract_overall_progress=100,
                    current_detail="处理完成",
                    overall_status="completed",
                    vl_skipped=resolve_writing_upload_vl_skipped(
                        document_vl_skipped=getattr(tender_doc, "vl_skipped", None) if tender_doc else None,
                    ),
                )
                return _writing_status_payload(
                    project_id=project.id,
                    name=project.name,
                    status=project.status.value,
                    error_message=project.error_message,
                    overall_status="completed",
                    overall_progress=overall_progress,
                    steps=steps,
                    message=message,
                    recovery_count=get_recovery_count(extract_task_name),
                    current_stage="completed",
                    current_detail="处理完成",
                )
        return None

    async def _resolve_post_extract_payload(
        self, ctx: _WritingStatusContext,
    ) -> dict[str, Any]:
        """B1-B7 后续阶段(OUTLINING / MATCHING / WRITING / COMPOSED / COMPLETED / FAILED)编排器.

        副作用: 可能触发 db.commit / db.refresh / retry_service.mark_task_failed /
        release_project_binding。
        """
        from app.core.tasks import get_recovery_count
        from app.core.tasks.writing_compose_task import get_writing_compose_task_name
        from app.core.tasks.writing_match_task import get_writing_match_task_name
        from app.core.tasks.writing_outline_task import get_writing_outline_task_name

        project = ctx.project
        task_hb_timeout = ctx.task_hb_timeout
        project_id = project.id

        task_name_map = {
            ProjectStatus.EXTRACTING: get_writing_extract_task_name(project_id),
            ProjectStatus.OUTLINING: get_writing_outline_task_name(project_id),
            ProjectStatus.MATCHING: get_writing_match_task_name(project_id),
            ProjectStatus.WRITING: get_writing_compose_task_name(project_id),
        }
        task_arq_function_map = {
            ProjectStatus.EXTRACTING: "writing_extract_job",
            ProjectStatus.OUTLINING: "writing_outline_job",
            ProjectStatus.MATCHING: "writing_match_job",
            ProjectStatus.WRITING: "writing_compose_job",
        }

        task_name = task_name_map.get(project.status)
        task_active = False
        heartbeat = None

        if task_name:
            liveness = inspect_task_liveness(
                task_name,
                heartbeat_timeout=task_hb_timeout,
                arq_function=task_arq_function_map.get(project.status),
            )
            task_active = liveness.is_live
            heartbeat = liveness.heartbeat
            if liveness.state in {"stale", "missing"}:
                failed_status = project.status.value
                error_message = f"{failed_status} 任务异常终止，请从失败节点继续重试"
                logger.warning(
                    f"孤儿任务检测: project={project_id}, "
                    f"status={failed_status}, task={task_name}, "
                    f"state={liveness.state}, reason={liveness.reason}, 回退为 FAILED"
                )
                try:
                    from app.services.retry_service import RetryService

                    node_key_map = {
                        ProjectStatus.EXTRACTING: RetryService.NODE_WRITING_EXTRACT,
                        ProjectStatus.OUTLINING: RetryService.NODE_WRITING_OUTLINE,
                        ProjectStatus.MATCHING: RetryService.NODE_WRITING_MATCH,
                        ProjectStatus.WRITING: RetryService.NODE_WRITING_COMPOSE,
                    }
                    await RetryService(self.db).mark_task_failed(
                        project_id,
                        node_key=node_key_map.get(ProjectStatus(failed_status), RetryService.NODE_WRITING_COMPOSE),
                        stage=liveness.stage or "failed",
                        error=error_message,
                    )
                    from app.core.ai import release_project_binding
                    await release_project_binding(project_id)
                except Exception as exc:
                    logger.warning(
                        "[写标] 通用异步任务失败态回写异常(忽略): project={}, err={}",
                        project_id,
                        exc,
                    )
                # 必须持久化 Project 终态。此前只标记 TaskRun 并在内存叠加
                # failed 心跳，DB 里项目仍是 OUTLINING/WRITING，于是前端 1Hz 轮询与
                # maintenance sweep 每次都重新进入本分支、重复告警。写入 FAILED 后
                # task_name_map 不含该状态，后续查询走 FAILED 短路，副作用只发生一次。
                project.status = ProjectStatus.FAILED
                project.error_message = error_message
                await self.db.commit()
                # 回读权威值：本方法 docstring 声明的副作用契约，同时为
                # expire_on_commit 若改回 True 时保留防线
                await self.db.refresh(project)
                heartbeat = {
                    **(heartbeat or {}),
                    "stage": "failed",
                    "progress": f"失败|{error_message}",
                }
                task_active = True
            elif liveness.is_terminal:
                task_active = True
                if liveness.stage in {"failed", "cancelled", "canceled"}:
                    failed_status = project.status.value
                    error_message = f"{failed_status} 任务失败，请从失败节点继续重试"
                    project.status = ProjectStatus.FAILED
                    project.error_message = error_message
                    if heartbeat is not None:
                        heartbeat = {**heartbeat, "stage": "failed", "progress": f"失败|{error_message}"}
                    await self.db.commit()
                    try:
                        from app.services.retry_service import RetryService

                        node_key_map = {
                            ProjectStatus.EXTRACTING: RetryService.NODE_WRITING_EXTRACT,
                            ProjectStatus.OUTLINING: RetryService.NODE_WRITING_OUTLINE,
                            ProjectStatus.MATCHING: RetryService.NODE_WRITING_MATCH,
                            ProjectStatus.WRITING: RetryService.NODE_WRITING_COMPOSE,
                        }
                        await RetryService(self.db).mark_task_failed(
                            project_id,
                            node_key=node_key_map.get(ProjectStatus(failed_status), RetryService.NODE_WRITING_COMPOSE),
                            stage="failed",
                            error=error_message,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[写标] 通用异步任务终态失败回写异常(忽略): project={}, err={}",
                            project_id,
                            exc,
                        )

        overall_status = "idle"
        overall_progress = 0
        current_stage = ""
        current_detail = ""
        recovery_count = 0

        completed_statuses = {
            ProjectStatus.EXTRACTED,
            ProjectStatus.OUTLINED,
            ProjectStatus.MATCHED,
            ProjectStatus.COMPOSED,
            ProjectStatus.COMPLETED,
        }
        if project.status in completed_statuses:
            overall_status = "completed"
            overall_progress = 100
            current_stage = "completed"
        elif project.status == ProjectStatus.FAILED:
            overall_status = "failed"
            current_stage = "failed"
            current_detail = project.error_message or ""

        if task_active and heartbeat:
            hb_stage = heartbeat.get("stage", "")
            hb_progress = heartbeat.get("progress", "")
            if hb_stage == "completed":
                overall_status = "completed"
                overall_progress = 100
            elif hb_stage == "failed":
                overall_status = "failed"
            else:
                overall_status = "running"
            current_stage = hb_stage

            overall_progress, current_detail = _parse_percent_detail(hb_progress)
            recovery_count = get_recovery_count(task_name)

        phase = _infer_writing_progress_phase(project.status.value, current_stage)
        steps = _build_writing_steps(
            phase=phase,
            current_stage=current_stage,
            overall_progress=overall_progress,
            current_detail=current_detail,
            overall_status=overall_status,
        )

        message = current_detail
        if not message:
            if overall_status == "completed":
                message = "处理完成"
            elif overall_status == "failed":
                message = project.error_message or "处理失败"
            elif overall_status == "running":
                message = current_stage or "处理中"

        return _writing_status_payload(
            project_id=project.id,
            name=project.name,
            status=project.status.value,
            error_message=project.error_message,
            overall_status=overall_status,
            overall_progress=overall_progress,
            steps=steps,
            message=message,
            recovery_count=recovery_count,
            current_stage=current_stage,
            current_detail=current_detail,
        )

    async def _load_writing_status_context(
        self, project_id: int, user: User,
    ) -> _WritingStatusContext:
        """加载 get_project_status 入口查询快照, 无 DB 写副作用."""
        from app.core.config import settings as _arq_settings

        project = await self._get_project_or_raise(project_id, user)
        tender_doc = await self._get_tender_doc(project_id)
        task_hb_timeout = int(getattr(_arq_settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180))
        return _WritingStatusContext(
            project=project,
            tender_doc=tender_doc,
            task_hb_timeout=task_hb_timeout,
        )

    def _build_doc_completed_payload(
        self,
        ctx: _WritingStatusContext,
        process_task_name: str,
    ) -> dict[str, Any]:
        """A1 文档处理完成出口 payload.

        副作用: ``reset_recovery_count(process_task_name)``。
        参考原闭包 (重构前 L319-341): 5 步标准化进度 + overall_status="running"。
        """
        from app.core.tasks import get_recovery_count, reset_recovery_count

        reset_recovery_count(process_task_name)
        tender_doc = ctx.tender_doc
        steps, overall_progress, current_stage, current_detail, message = _build_upload_doc_steps(
            doc_stage="completed",
            progress_raw="",
            vl_skipped=resolve_writing_upload_vl_skipped(
                document_vl_skipped=getattr(tender_doc, "vl_skipped", None) if tender_doc else None,
            ),
            overall_status="completed",
            current_detail="文档处理完成，准备提取约束",
        )
        project = ctx.project
        return _writing_status_payload(
            project_id=project.id,
            name=project.name,
            status=project.status.value,
            error_message=project.error_message,
            overall_status="running",
            overall_progress=overall_progress,
            steps=steps,
            message=message,
            recovery_count=get_recovery_count(process_task_name),
            current_stage=current_stage,
            current_detail=current_detail,
        )

    # ==================== 阶段1: 约束提取（CRUD 转发到 ClauseService） ====================
    #
    # 注意 trigger_extract 与 _ensure_clause_vectorize_task 保留在主类，因为它们依赖
    # `app.services.writing_service` 模块级 import（is_task_active /
    # create_background_task）作为测试 patch 目标，迁移到 sub-service 会导致
    # `monkeypatch.setattr("app.services.writing_service.is_task_active", ...)`
    # 失效，破坏 test_writing_extract_trigger / test_writing_clause_confirm_fast_path
    # 现有 patch 契约。

    async def trigger_extract(
        self, project_id: int, user: User,
    ) -> dict[str, str]:
        """触发约束提取后台任务"""
        project = await self._get_project_or_raise(project_id, user)

        if project.status not in (ProjectStatus.CREATED, ProjectStatus.UPLOADING,
                                  ProjectStatus.PARSING, ProjectStatus.PARSED,
                                  ProjectStatus.EXTRACTED,
                                  ProjectStatus.FAILED):
            raise ServiceError(f"当前状态 {project.status.value} 不允许触发提取")

        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            logger.warning(
                "[写标] 约束提取启动被拦截: 未找到招标文件, project_id={}, status={}",
                project_id,
                project.status.value,
            )
            raise ServiceError("未找到招标文件，请先上传并完成招标文件解析")
        if tender_doc.parsed != 1:
            logger.warning(
                "[写标] 约束提取启动被拦截: 招标文件未解析完成, "
                "project_id={}, status={}, document_id={}, parsed={}",
                project_id,
                project.status.value,
                tender_doc.id,
                tender_doc.parsed,
            )
            raise ServiceError("招标文件尚未解析完成，请等待处理完成后再提取约束")

        # 计费预检: pre_check 内部已实现"项目存在 HOLDING 预占则跳过重复预扣"
        # 与"按次增量计费(R2)"语义,首次启动新建 hold,重跑按增量结算
        from app.billing.services.balance_gate_error import raise_insufficient_balance
        from app.billing.services.billing_guard import BillingGuard
        from app.billing.services.wallet_service import InsufficientBalanceError
        try:
            await BillingGuard.pre_check(self.db, project, project.owner_id)
            await self.db.commit()
        except InsufficientBalanceError as e:
            raise_insufficient_balance(e)

        task_name = get_writing_extract_task_name(project_id)
        if is_task_active(task_name):
            return {"task_name": task_name, "message": "约束提取任务进行中"}
        from app.core.tasks import dispatch_background_job
        from app.services.retry_service import RetryService

        retry_svc = RetryService(self.db)
        run = await retry_svc.begin_attempt(
            project_id=project_id,
            node_key=RetryService.NODE_WRITING_EXTRACT,
            user_id=getattr(user, "id", None),
            task_name=task_name,
            explicit_retry=False,
            last_stage="queued",
        )
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_extract_job",
            fallback_factory=lambda: run_writing_extract_task(
                project_id,
                expected_attempt=run.attempt,
            ),
            task_type="writing_extract",
            project_id=project_id,
            arq_args=(project_id, False, run.attempt),
        )
        await retry_svc.attach_dispatch_result(
            run,
            queue_mode=queue_mode,
            arq_job_id=arq_job_id,
            last_stage="queued",
        )
        return {
            "task_name": task_name,
            "message": "约束提取已启动",
            "queue_mode": queue_mode,
        }

    async def _ensure_clause_vectorize_task(self, project_id: int) -> None:
        """确保约束向量化后台任务已启动。

        保留在主类，因为 test_writing_clause_confirm_fast_path 通过
        ``monkeypatch.setattr("app.services.writing_service.is_task_active", ...)``
        和 ``app.services.writing_service.create_background_task`` patch 此函数行为。
        """
        from app.core.tasks import dispatch_background_job
        from app.core.tasks.writing_clause_vectorize_task import (
            get_writing_clause_vectorize_task_name,
            run_writing_clause_vectorize_task,
        )

        task_name = get_writing_clause_vectorize_task_name(project_id)
        if is_task_active(task_name):
            return
        await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_clause_vectorize_job",
            fallback_factory=lambda: run_writing_clause_vectorize_task(project_id),
            task_type="writing_clause_vectorize",
            project_id=project_id,
            arq_args=(project_id,),
        )

    async def list_clauses(
        self, project_id: int, user: User,
        layer: Optional[str] = None,
    ) -> list[WritingClause]:
        """获取约束列表"""
        return await self._clause.list_clauses(project_id, user, layer)

    async def create_clause(
        self,
        project_id: int,
        user: User,
        data: dict[str, Any],
    ) -> WritingClause:
        """手动新增约束"""
        return await self._clause.create_clause(project_id, user, data)

    async def update_clause(
        self, project_id: int, clause_id: int,
        user: User, data: dict[str, Any],
    ) -> WritingClause:
        """编辑约束"""
        return await self._clause.update_clause(project_id, clause_id, user, data)

    async def delete_clause(
        self, project_id: int, clause_id: int, user: User,
    ) -> None:
        """删除约束并重排顺序"""
        await self._clause.delete_clause(project_id, clause_id, user)

    async def confirm_clauses(
        self, project_id: int, clause_ids: list[int], user: User,
    ) -> int:
        """批量确认约束并异步触发向量化。"""
        return await self._clause.confirm_clauses(project_id, clause_ids, user)

    async def get_scoring_report(
        self, project_id: int, user: User,
    ) -> Optional[dict[str, Any]]:
        """评分项按卷满分核计报告（转发到 ClauseService）。"""
        return await self._clause.get_scoring_report(project_id, user)

    async def _get_tender_doc(self, project_id: int) -> Optional[Document]:
        """获取项目对应的招标文档（跨阶段共享 helper，保留在主类）"""
        result = await self.db.execute(
            select(Document).where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.TENDER,
            )
        )
        return result.scalars().first()

    async def _vectorize_confirmed_clauses(self, project_id: int) -> int:
        """约束向量化 worker 入口（ARQ writing_clause_vectorize_job 直接调用）。

        必须保留在 WritingService 实例上以兼容
        ``backend/app/core/tasks/writing_clause_vectorize_task.py`` 与
        ``test_writing_clause_confirm_fast_path.py`` 的 monkeypatch 契约。
        """
        return await self._clause._vectorize_confirmed_clauses(project_id)

    # ==================== 阶段2: 目录（转发到 OutlineService） ====================

    async def generate_outline(
        self, project_id: int, user: User, *, force: bool = False,
    ) -> dict[str, Any]:
        """触发目录树生成(后台任务)"""
        return await self._outline.generate_outline(project_id, user, force=force)

    async def get_outline(
        self, project_id: int, user: User,
    ) -> list[WritingOutlineNode]:
        """获取目录树"""
        return await self._outline.get_outline(project_id, user)

    async def update_outline_node(
        self, project_id: int, node_id: int,
        user: User, data: dict[str, Any],
    ) -> WritingOutlineNode:
        """编辑目录节点"""
        return await self._outline.update_outline_node(project_id, node_id, user, data)

    async def delete_outline_node(
        self, project_id: int, node_id: int, user: User,
    ) -> None:
        """删除目录节点"""
        await self._outline.delete_outline_node(project_id, node_id, user)

    async def reorder_outline(
        self, project_id: int, node_ids: list[int],
        parent_id: Optional[int], user: User,
    ) -> None:
        """拖拽排序"""
        await self._outline.reorder_outline(project_id, node_ids, parent_id, user)

    async def _recalculate_outline_depths(self, project_id: int) -> None:
        """按 parent_id 关系重算 depth；保留 thin forwarder 兼容
        ``test_writer_outline_reorder`` 直接调用 ``service._recalculate_outline_depths``。
        """
        await self._outline._recalculate_outline_depths(project_id)

    # ==================== 素材（thin forwarder 到 MaterialService） ====================

    async def list_materials(
        self, project_id: int, user: User,
    ) -> list[WritingMaterial]:
        """素材列表"""
        return await self._material.list_materials(project_id, user)

    async def update_material_split_state(
        self,
        project_id: int,
        material_id: int,
        action: str,
        user: User,
    ) -> WritingMaterial:
        """更新拆分素材的使用状态。"""
        return await self._material.update_material_split_state(
            project_id, material_id, action, user,
        )

    async def update_material_title(
        self,
        project_id: int,
        material_id: int,
        display_title: str,
        user: User,
    ) -> WritingMaterial:
        """更新素材展示标题。"""
        return await self._material.update_material_title(
            project_id, material_id, display_title, user,
        )

    async def batch_update_material_titles(
        self,
        project_id: int,
        items: list[dict[str, Any]],
        user: User,
    ) -> dict[str, Any]:
        """批量更新素材标题。"""
        return await self._material.batch_update_material_titles(
            project_id, items, user,
        )

    async def get_material_preview_path(
        self,
        project_id: int,
        material_id: int,
        user: User,
    ) -> Path:
        """获取素材原文预览文件路径。"""
        return await self._material.get_material_preview_path(
            project_id, material_id, user,
        )

    async def save_material(
        self, project_id: int, user: User,
        filename: str, file_bytes: bytes,
    ) -> WritingMaterial:
        """保存素材文件（阶段1：仅保存，不做 LLM 处理）。"""
        return await self._material.save_material(
            project_id, user, filename, file_bytes,
        )

    async def process_materials_batch(
        self, project_id: int, user: User,
    ) -> dict:
        """触发素材批处理后台任务（阶段2）。"""
        return await self._material.process_materials_batch(project_id, user)

    async def _execute_material_batch(
        self,
        project_id: int,
        task_name: Optional[str] = None,
    ) -> dict:
        """执行素材批处理实际业务（ARQ worker 入口）。

        thin forwarder：测试通过
        ``WritingService(db)._execute_material_batch(...)`` 直接调用，
        ARQ ``writing_material_process_task`` 同样以该路径分派。
        """
        return await self._material._execute_material_batch(project_id, task_name)

    async def _persist_and_classify_images(
        self,
        project_id: int,
        pending_materials: list[WritingMaterial],
        parse_results: dict,
        hash_to_first: dict[str, object],
    ) -> None:
        """thin forwarder：Phase 3.5 图片持久化 + 分类。"""
        await self._material._persist_and_classify_images(
            project_id, pending_materials, parse_results, hash_to_first,
        )

    async def _mark_unclassified_images_unknown(
        self,
        material_ids: list[int],
    ) -> None:
        """thin forwarder：兜底将 needs_review 图标标为 unknown。"""
        await self._material._mark_unclassified_images_unknown(material_ids)

    async def _resolve_material_labels(
        self,
        project_id: int,
        materials: list[WritingMaterial],
    ) -> None:
        """thin forwarder：素材标签决议。"""
        await self._material._resolve_material_labels(project_id, materials)

    async def get_batch_process_status(
        self,
        project_id: int,
        user: User,
    ) -> dict:
        """获取素材批处理状态。"""
        return await self._material.get_batch_process_status(project_id, user)

    async def delete_material(
        self,
        project_id: int,
        material_id: int,
        user: User,
    ) -> None:
        """删除素材。"""
        await self._material.delete_material(project_id, material_id, user)

    async def get_material_checklist(
        self, project_id: int, user: User,
    ) -> list[dict]:
        """素材缺失清单"""
        return await self._material.get_material_checklist(project_id, user)

    # ==================== 阶段3: 匹配（转发到 MatchService） ====================

    async def trigger_match(
        self, project_id: int, user: User,
        *, reset_reviewed_bindings: bool = False,
    ) -> dict[str, str]:
        """触发三元图匹配(后台任务)"""
        return await self._match.trigger_match(
            project_id, user, reset_reviewed_bindings=reset_reviewed_bindings
        )

    async def _vectorize_outline_nodes(
        self,
        project_id: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> int:
        """目录节点向量化 worker 入口；保留 thin forwarder 兼容
        ``writing_match_task.py`` 与 ``test_writer_text_material_consumption``
        ``service._vectorize_outline_nodes = AsyncMock(...)`` 实例 monkeypatch。
        """
        return await self._match._vectorize_outline_nodes(project_id, progress_callback)

    # _build_outline_embedding_text 保留为 staticmethod 暴露给 test_writer_text_material_consumption
    # 的 ``WritingService._build_outline_embedding_text(node, node_map)`` 类直接调用。
    from app.services.writing.match_service import (
        MatchService as _MatchServiceForBind,  # noqa: PLC0415
    )
    _build_outline_embedding_text = staticmethod(_MatchServiceForBind._build_outline_embedding_text)
    del _MatchServiceForBind

    # ==================== 三元绑定（转发到 BindingService） ====================

    async def list_bindings(
        self, project_id: int, user: User,
    ) -> list[WritingBinding]:
        """绑定列表"""
        return await self._binding.list_bindings(project_id, user)

    async def update_binding(
        self, project_id: int, binding_id: int,
        user: User, data: dict[str, Any],
    ) -> WritingBinding:
        """调整绑定"""
        return await self._binding.update_binding(project_id, binding_id, user, data)

    async def confirm_bindings(
        self, project_id: int, binding_ids: list[int], user: User,
    ) -> int:
        """批量确认绑定"""
        return await self._binding.confirm_bindings(project_id, binding_ids, user)

    # ==================== 阶段4: 内容生成（转发到 ComposeService） ====================

    async def trigger_compose(
        self, project_id: int, user: User, *, force: bool = False,
    ) -> dict[str, str]:
        """触发全文生成(后台任务)"""
        return await self._compose.trigger_compose(project_id, user, force=force)

    async def compose_single_node(
        self, project_id: int, node_id: int, user: User,
    ) -> dict[str, Any]:
        """触发单章节生成(后台任务)"""
        return await self._compose.compose_single_node(project_id, node_id, user)

    async def get_compose_single_status(
        self, project_id: int, node_id: int, user: User,
    ) -> dict[str, Any]:
        """查询单章节生成任务心跳"""
        return await self._compose.get_compose_single_status(project_id, node_id, user)

    async def list_drafts(
        self, project_id: int, user: User,
    ) -> list[WritingSectionDraft]:
        """全部草稿"""
        return await self._compose.list_drafts(project_id, user)

    async def get_draft(
        self, project_id: int, node_id: int, user: User,
    ) -> WritingSectionDraft:
        """单章节草稿"""
        return await self._compose.get_draft(project_id, node_id, user)

    async def update_draft(
        self, project_id: int, node_id: int,
        user: User, content: str,
    ) -> WritingSectionDraft:
        """用户编辑保存"""
        return await self._compose.update_draft(project_id, node_id, user, content)

    async def toggle_lock(
        self, project_id: int, node_id: int, user: User,
    ) -> WritingSectionDraft:
        """锁定/解锁"""
        return await self._compose.toggle_lock(project_id, node_id, user)

    async def get_consistency_issues(
        self, project_id: int, user: User,
    ) -> list[dict]:
        """一致性检查结果"""
        return await self._compose.get_consistency_issues(project_id, user)

    async def get_clause_coverage(
        self, project_id: int, user: User,
    ) -> dict:
        """约束消费覆盖诊断（全局分母/已覆盖 + 未覆盖三类原因）"""
        return await self._compose.get_clause_coverage(project_id, user)

    # ==================== 阶段5: 输出（3 个 public 转发到 RenderService） ====================
    #
    # 注意：7 个 render 私有方法（_get_render_artifact / _build_render_snapshot /
    # _invalidate_render_artifact / _refresh_fixed_template_drafts /
    # _mark_project_completed_after_render / _ensure_render_artifact /
    # _get_render_lock / _load_cached_consistency）以及 _render_locks ClassVar
    # 与 _ensure_preview_ready staticmethod 全部保留在主类，因为
    # test_writer_render_artifact_cache 期望它们之间通过 self.method 互调时
    # 能正确响应 service._xxx = AsyncMock(...) 的实例 monkey patch
    # （sub-service 内部 self 不同实例，无法做到这一点）。

    async def render_docx(
        self, project_id: int, user: User,
    ) -> bytes:
        """显式重新渲染 Word 文档"""
        return await self._render.render_docx(project_id, user)

    async def get_html_preview(
        self, project_id: int, user: User,
    ) -> str:
        """HTML 预览"""
        return await self._render.get_html_preview(project_id, user)

    async def get_rendered_docx(
        self, project_id: int, user: User, *, allow_blocking_issues: bool = False,
    ) -> bytes:
        """下载当前渲染快照对应的 Word 文档"""
        return await self._render.get_rendered_docx(
            project_id,
            user,
            allow_blocking_issues=allow_blocking_issues,
        )

    # ==================== Render 内部方法（保留在主类，sub-service 协作锚点） ====================

    @classmethod
    def _get_render_lock(cls, project_id: int) -> asyncio.Lock:
        lock = cls._render_locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._render_locks[project_id] = lock
        return lock

    @staticmethod
    def _load_cached_consistency(
        artifact: Optional[WritingRenderArtifact],
    ) -> Optional[list[dict]]:
        if (
            artifact is None
            or not artifact.consistency_issues_json
            or artifact.consistency_render_version != artifact.render_version
        ):
            return None
        try:
            raw = json.loads(artifact.consistency_issues_json)
        except json.JSONDecodeError:
            logger.warning(
                "一致性缓存解析失败，忽略缓存: project={}",
                artifact.project_id,
            )
            return None
        return raw if isinstance(raw, list) else None

    async def _get_render_artifact(
        self, project_id: int,
    ) -> Optional[WritingRenderArtifact]:
        result = await self.db.execute(
            select(WritingRenderArtifact).where(
                WritingRenderArtifact.project_id == project_id,
            ).execution_options(populate_existing=True)
        )
        return result.scalars().first()

    async def _invalidate_render_artifact(self, project_id: int) -> None:
        """清空当前项目渲染快照，强制后续预览/下载重建。"""
        artifact = await self._get_render_artifact(project_id)
        if artifact is None:
            return

        artifact.preview_html = None
        artifact.docx_blob = None
        artifact.docx_size = None
        artifact.rendered_at = None
        artifact.consistency_issues_json = None
        artifact.consistency_checked_at = None
        artifact.consistency_render_version = None

    async def _build_render_snapshot(
        self, project_id: int,
    ) -> tuple[str, bytes, list[dict]]:
        from app.core.writer.consistency_checker import get_consistency_checker
        from app.core.writer.doc_renderer import (
            get_doc_renderer,
            get_html_preview_builder,
        )
        from app.core.writer.section_composer import get_section_composer

        await self._refresh_fixed_template_drafts(
            project_id,
            get_section_composer(db=self.db, project_id=project_id),
        )
        builder = get_html_preview_builder(self.db)
        preview_html = await builder.build(project_id)
        renderer = get_doc_renderer(self.db)
        docx_bytes = await renderer.render(project_id)
        # compose 已在结算前刷新一致性缓存，快照重建优先消费有效缓存，
        # 避免结算后（预览/导出）再烧一次一致性 LLM；缓存失效（用户编辑草稿等）才重查。
        artifact = await self._get_render_artifact(project_id)
        issues = self._load_cached_consistency(artifact)
        if issues is None:
            checker = get_consistency_checker(db=self.db, project_id=project_id)
            issues = await checker.check(project_id)
        return preview_html, docx_bytes, issues

    async def _refresh_fixed_template_drafts(
        self,
        project_id: int,
        composer: Any,
    ) -> None:
        """预览/导出前刷新 fixed_template 草稿，确保旧项目吃到最新模板修复。"""
        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
                WritingOutlineNode.node_type == OutlineNodeType.fixed_template,
            ).order_by(WritingOutlineNode.sort_order.asc(), WritingOutlineNode.id.asc())
        )
        nodes = list(result.scalars().all())
        refreshed = 0
        for node in nodes:
            payload = await composer._fill_template(node)
            current_draft = await composer._load_current_draft(node.id)
            if current_draft and (current_draft.content or "") == payload.content:
                continue
            from app.core.writer.composer.draft_persistence import DraftWriteRequest

            payload_gate_flags = getattr(payload, "gate_flags", None)
            await composer._save_draft(
                node,
                DraftWriteRequest(
                    content=payload.content,
                    source_material_ids=payload.source_material_ids,
                    source_clause_ids=payload.source_clause_ids,
                    gate_flags=payload_gate_flags or None,
                ),
            )
            refreshed += 1
        if refreshed > 0:
            logger.info(
                "预览前刷新 fixed_template 草稿: project={}, refreshed={}",
                project_id,
                refreshed,
            )

    async def _mark_project_completed_after_render(
        self, project_id: int, project: Project,
    ) -> None:
        if project.status == ProjectStatus.COMPLETED:
            return

        project.status = ProjectStatus.COMPLETED
        await self.db.commit()
        try:
            from app.core.ai import release_project_binding

            await release_project_binding(project_id)
        except Exception:
            pass

    async def _ensure_render_artifact(
        self,
        project_id: int,
        *,
        force_rebuild: bool = False,
        explicit_render: bool = False,
    ) -> WritingRenderArtifact:
        artifact = await self._get_render_artifact(project_id)
        if (
            artifact
            and not force_rebuild
            and artifact.preview_html
            and artifact.docx_blob
        ):
            project = await self.db.get(Project, project_id)
            if project and project.status == ProjectStatus.COMPOSED:
                await self._mark_project_completed_after_render(project_id, project)
            return artifact

        lock = self._get_render_lock(project_id)
        async with lock:
            artifact = await self._get_render_artifact(project_id)
            if (
                artifact
                and not force_rebuild
                and artifact.preview_html
                and artifact.docx_blob
            ):
                project = await self.db.get(Project, project_id)
                if project and project.status == ProjectStatus.COMPOSED:
                    await self._mark_project_completed_after_render(project_id, project)
                return artifact

            project = await self.db.get(Project, project_id)
            if not project:
                raise NotFoundError("项目不存在")

            previous_status = project.status
            if explicit_render:
                project.status = ProjectStatus.FORMATTING
                await self.db.commit()

            try:
                preview_html, docx_bytes, issues = await self._build_render_snapshot(project_id)
            except Exception:
                if explicit_render:
                    project.status = previous_status
                    await self.db.commit()
                raise

            if artifact is None:
                artifact = WritingRenderArtifact(
                    project_id=project_id,
                    render_version=1,
                )
                self.db.add(artifact)
            elif force_rebuild and explicit_render:
                artifact.render_version += 1

            artifact.preview_html = preview_html
            artifact.docx_blob = docx_bytes
            artifact.docx_size = len(docx_bytes)
            artifact.rendered_at = datetime.now(timezone.utc)
            artifact.consistency_issues_json = json.dumps(issues, ensure_ascii=False)
            artifact.consistency_checked_at = datetime.now(timezone.utc)
            artifact.consistency_render_version = artifact.render_version
            try:
                await self.db.commit()
            except IntegrityError:
                await self.db.rollback()
                existing = await self._get_render_artifact(project_id)
                if existing is None:
                    raise
                return existing
            await self.db.refresh(artifact)

            if explicit_render or previous_status == ProjectStatus.COMPOSED:
                await self._mark_project_completed_after_render(project_id, project)

            return artifact

    @staticmethod
    def _ensure_preview_ready(project: Project) -> None:
        ready_statuses = {
            ProjectStatus.COMPOSED,
            ProjectStatus.FORMATTING,
            ProjectStatus.COMPLETED,
        }
        if project.status not in ready_statuses:
            raise ServiceError(f"当前状态 {project.status.value} 不允许预览或导出")

    async def _get_project_or_raise(
        self, project_id: int, user: User,
        require_owner: bool = False,
    ) -> Project:
        """获取项目并校验权限"""
        project = await self.db.get(Project, project_id)
        if not project:
            raise NotFoundError("项目不存在")
        if project.project_type != ProjectType.BID_WRITING:
            raise ServiceError("该项目不是标书编写类型")

        check_project_permission(project, user, require_owner=require_owner)
        return project
