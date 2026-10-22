"""显式重试服务。

按 `docs/architecture.md` 第五章维护 bid_writing 节点级显式重试。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, ServiceError
from app.models.project import Project, ProjectType
from app.models.task_run import TaskRun, TaskRunStatus, freeze_completed_duration
from app.models.user import User, UserRole
from app.models.writing_material import WritingMaterial
from app.models.writing_material_chunk import WritingMaterialChunk
from app.models.writing_material_image import WritingMaterialImage
from app.services.retry.failure_reason_resolver import (
    PersistedFailureSources,
    load_persisted_failure_sources,
    pick_failure_reason,
)
from app.services.retry.node_completion_gate import resolve_node_completion


@dataclass(frozen=True)
class RetryNodeSpec:
    """单个可重试节点的第五章契约摘要。"""

    node_key: str
    task_type: str
    action_label: str
    preserved_outputs: tuple[str, ...]
    reset_scope: tuple[str, ...]
    stages: frozenset[str]
    requires_target: bool = False


class RetryService:
    """节点级显式重试编排服务。"""

    FLOW_BID_WRITING = "bid_writing"
    NODE_WRITING_MATERIAL_PROCESS = "writing_material_process"
    NODE_WRITING_COMPOSE_SINGLE = "writing_compose_single"
    NODE_WRITING_COMPOSE = "writing_compose"
    NODE_WRITING_MATCH = "writing_match"
    NODE_WRITING_OUTLINE = "writing_outline"
    NODE_WRITING_EXTRACT = "writing_extract"
    NODE_WRITING_TENDER_PROCESS = "writing_tender_process"
    NODE_WRITING_RENDER = "writing_render"
    TASK_TYPE_WRITING_MATERIAL_PROCESS = "writing_material_process"
    RETRY_RUNNING_STATUSES = {
        TaskRunStatus.queued.value,
        TaskRunStatus.running.value,
    }

    WRITING_MATERIAL_PRESERVED_OUTPUTS = [
        "已上传素材文件",
        "已确认目录",
        "已完成素材文本/图片识别/分块索引/标签",
        "已人工调整的素材标题和拆分复核状态",
    ]
    WRITING_MATERIAL_RESET_SCOPE = [
        "processing_status='failed' 的素材或子素材",
        "孤儿 processing 素材的运行态",
        "失败素材关联的 image/chunk/tag 中间产物",
        "writing_material_process heartbeat",
    ]
    WRITING_MATERIAL_STAGES = {
        "parsing",
        "vl_recognition",
        "indexing",
        "image_classify",
        "label_resolve",
        "tagging",
        "failed",
    }
    NODE_SPECS: dict[str, RetryNodeSpec] = {
        NODE_WRITING_MATERIAL_PROCESS: RetryNodeSpec(
            node_key=NODE_WRITING_MATERIAL_PROCESS,
            task_type="writing_material_process",
            action_label="从素材处理继续重试",
            preserved_outputs=tuple(WRITING_MATERIAL_PRESERVED_OUTPUTS),
            reset_scope=tuple(WRITING_MATERIAL_RESET_SCOPE),
            stages=frozenset(WRITING_MATERIAL_STAGES),
        ),
        NODE_WRITING_COMPOSE_SINGLE: RetryNodeSpec(
            node_key=NODE_WRITING_COMPOSE_SINGLE,
            task_type="writing_compose_single",
            action_label="重新生成当前章节",
            preserved_outputs=(
                "当前章节上一版草稿",
                "其他章节草稿",
                "用户已编辑章节",
                "已确认绑定",
            ),
            reset_scope=(
                "目标章节本次失败 attempt 的中间态",
                "目标章节失败的生成任务状态",
                "writing_compose_single heartbeat",
            ),
            stages=frozenset({"queued", "starting", "composing", "failed"}),
            requires_target=True,
        ),
        NODE_WRITING_COMPOSE: RetryNodeSpec(
            node_key=NODE_WRITING_COMPOSE,
            task_type="writing_compose",
            action_label="从全文生成继续重试",
            preserved_outputs=(
                "已确认绑定",
                "已成功章节草稿",
                "用户已编辑章节",
                "已完成素材匹配结果",
            ),
            reset_scope=(
                "失败或缺失章节草稿",
                "未确认的生成中间态",
                "失败的 compose attempt 状态",
                "writing_compose heartbeat",
            ),
            stages=frozenset({"queued", "starting", "composing", "failed"}),
        ),
        NODE_WRITING_MATCH: RetryNodeSpec(
            node_key=NODE_WRITING_MATCH,
            task_type="writing_match",
            action_label="从素材匹配继续重试",
            preserved_outputs=(
                "已确认约束",
                "已确认目录",
                "已完成素材处理成果",
                "已人工确认绑定",
            ),
            reset_scope=(
                "未确认的绑定建议",
                "失败匹配缓存",
                "本次失败 attempt 的匹配任务状态",
                "writing_match heartbeat",
            ),
            stages=frozenset({
                "queued",
                "starting",
                "matching",
                "vectorizing",
                "classifying",
                "retrieving",
                "materials",
                "validating",
                "saving",
                "failed",
            }),
        ),
        NODE_WRITING_OUTLINE: RetryNodeSpec(
            node_key=NODE_WRITING_OUTLINE,
            task_type="writing_outline",
            action_label="从目录生成继续重试",
            preserved_outputs=("已确认约束", "渲染配置", "行业画像", "已人工调整目录"),
            reset_scope=(
                "失败的目录草稿",
                "未人工调整的目录生成中间态",
                "writing_outline heartbeat",
            ),
            stages=frozenset({"queued", "starting", "cleaning", "generating", "profiling", "failed"}),
        ),
        NODE_WRITING_EXTRACT: RetryNodeSpec(
            node_key=NODE_WRITING_EXTRACT,
            task_type="writing_extract",
            action_label="从约束提取继续重试",
            preserved_outputs=("招标文件解析结果", "已人工补录且确认的约束"),
            reset_scope=(
                "未确认的失败提取结果",
                "提取任务心跳",
                "本次失败 attempt 状态",
            ),
            stages=frozenset({
                "queued",
                "starting",
                "extracting",
                "extracting_hard_constraint",
                "extracting_structure_constraint",
                "extracting_generation_guide",
                "dedup",
                "saving",
                "render_profile",
                "industry_profile",
                "structure_analysis",
                "directory_extract",
                "failed",
            }),
        ),
        NODE_WRITING_TENDER_PROCESS: RetryNodeSpec(
            node_key=NODE_WRITING_TENDER_PROCESS,
            task_type="process",
            action_label="重新解析招标文件",
            preserved_outputs=("招标文件上传记录", "项目基础信息"),
            reset_scope=("失败的文档处理状态", "失败的 VectorJobState 运行态"),
            stages=frozenset({"queued", "parsing", "vl_recognition", "chunking", "vectorizing", "failed"}),
        ),
        NODE_WRITING_RENDER: RetryNodeSpec(
            node_key=NODE_WRITING_RENDER,
            task_type="writing_render",
            action_label="重新渲染文档",
            preserved_outputs=("全部章节草稿", "一致性检查结果", "渲染配置"),
            reset_scope=("失败的 WritingRenderArtifact 文件路径或缓存", "本次失败 render attempt 状态"),
            stages=frozenset({"rendering", "failed"}),
        ),
    }
    ARQ_FUNCTION_BY_NODE: dict[str, str] = {
        NODE_WRITING_MATERIAL_PROCESS: "writing_material_process_job",
        NODE_WRITING_COMPOSE_SINGLE: "writing_compose_single_job",
        NODE_WRITING_COMPOSE: "writing_compose_job",
        NODE_WRITING_MATCH: "writing_match_job",
        NODE_WRITING_OUTLINE: "writing_outline_job",
        NODE_WRITING_EXTRACT: "writing_extract_job",
        NODE_WRITING_TENDER_PROCESS: "process_document_job",
    }
    # NODE_WRITING_RENDER 同步执行渲染，不经过 ARQ worker。

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @classmethod
    def is_supported_node(cls, node_key: str) -> bool:
        """判断节点是否已接入显式重试。"""

        return node_key in cls.NODE_SPECS

    @classmethod
    def is_retry_running_status(cls, status: str | None) -> bool:
        """判断 TaskRun 状态是否代表仍在运行。"""

        return str(status or "") in cls.RETRY_RUNNING_STATUSES

    async def _get_project_or_raise(
        self,
        project_id: int,
        user: User,
        *,
        require_owner: bool = False,
    ) -> Project:
        project = await self.db.get(Project, project_id)
        if not project:
            raise ServiceError("项目不存在", status_code=404)
        if project.project_type != ProjectType.BID_WRITING:
            raise ServiceError("当前项目不是标书编写项目", status_code=400)
        if require_owner:
            if project.owner_id != user.id:
                raise ForbiddenError("仅项目所有者可执行此操作")
        elif user.role != UserRole.ADMIN and project.owner_id != user.id:
            raise ForbiddenError("无权限访问此项目")
        return project

    @classmethod
    def _task_run_key(cls, node_key: str, target_id: Optional[int] = None) -> str:
        if node_key == cls.NODE_WRITING_COMPOSE_SINGLE:
            if target_id is None:
                raise ServiceError("单章重试需要 target_id", status_code=400)
            return f"{node_key}:{target_id}"
        if target_id is not None:
            return f"{node_key}:{target_id}"
        return node_key

    @classmethod
    def _public_node_key(cls, task_run_key: str) -> str:
        return task_run_key.split(":", 1)[0]

    async def _latest_run(
        self,
        project_id: int,
        node_key: str,
        target_id: Optional[int] = None,
    ) -> Optional[TaskRun]:
        task_run_key = self._task_run_key(node_key, target_id)
        result = await self.db.execute(
            select(TaskRun)
            .where(
                TaskRun.project_id == project_id,
                TaskRun.flow == self.FLOW_BID_WRITING,
                TaskRun.node_key == task_run_key,
            )
            .order_by(TaskRun.attempt.desc(), TaskRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _latest_compose_single_run(self, project_id: int) -> Optional[TaskRun]:
        result = await self.db.execute(
            select(TaskRun)
            .where(
                TaskRun.project_id == project_id,
                TaskRun.flow == self.FLOW_BID_WRITING,
                TaskRun.node_key.like(f"{self.NODE_WRITING_COMPOSE_SINGLE}:%"),
            )
            .order_by(TaskRun.attempt.desc(), TaskRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @classmethod
    def _target_id_from_task_run_key(cls, task_run_key: str) -> Optional[int]:
        if ":" not in task_run_key:
            return None
        try:
            return int(task_run_key.split(":", 1)[1])
        except ValueError:
            return None

    async def _next_attempt(
        self,
        project_id: int,
        node_key: str,
        target_id: Optional[int] = None,
    ) -> int:
        task_run_key = self._task_run_key(node_key, target_id)
        result = await self.db.execute(
            select(func.max(TaskRun.attempt)).where(
                TaskRun.project_id == project_id,
                TaskRun.flow == self.FLOW_BID_WRITING,
                TaskRun.node_key == task_run_key,
            )
        )
        current = result.scalar_one_or_none()
        return int(current or 0) + 1

    async def _material_counts(self, project_id: int) -> dict[str, int]:
        result = await self.db.execute(
            select(
                WritingMaterial.processing_status,
                func.count(WritingMaterial.id),
            )
            .where(WritingMaterial.project_id == project_id)
            .group_by(WritingMaterial.processing_status)
        )
        counts = {"saved": 0, "processing": 0, "completed": 0, "failed": 0}
        for status, count in result.all():
            key = str(status or "")
            if key in counts:
                counts[key] = int(count or 0)
        return counts

    @staticmethod
    def _heartbeat_stage(heartbeat: dict[str, Any]) -> str:
        return str(heartbeat.get("phase") or heartbeat.get("stage") or "").strip()

    @staticmethod
    def _heartbeat_error(heartbeat: dict[str, Any]) -> str:
        progress = str(heartbeat.get("progress") or "")
        if "|" in progress:
            _, detail = progress.split("|", 1)
            return detail.strip()[:500]
        return progress.strip()[:500]

    def _spec(self, node_key: str) -> RetryNodeSpec:
        try:
            return self.NODE_SPECS[node_key]
        except KeyError:
            raise ServiceError(f"不支持的重试节点: {node_key}", status_code=400)

    def _build_state_payload(
        self,
        *,
        spec: RetryNodeSpec,
        target_id: Optional[int],
        latest: Optional[TaskRun],
        retryable: bool,
        retry_running: bool,
        heartbeat: Optional[dict[str, Any]] = None,
        fallback_error: Optional[str] = None,
        persisted: Optional[PersistedFailureSources] = None,
        node_completed: bool = False,
    ) -> dict[str, Any]:
        heartbeat = heartbeat or {}
        heartbeat_stage = self._heartbeat_stage(heartbeat)
        # 节点已完成/从未执行时清零全部失败字段, 防止旧 TaskRun 残留
        # 或过期心跳把该节点再包装成失败点
        failed_stage = None
        if not node_completed and (
            retryable or retry_running or (latest and latest.status == TaskRunStatus.failed.value)
        ):
            if heartbeat_stage in spec.stages:
                failed_stage = heartbeat_stage
            elif latest and latest.last_stage:
                failed_stage = latest.last_stage
            else:
                failed_stage = "unknown"

        last_error = None
        if not node_completed:
            last_error = pick_failure_reason(
                task_run_error=latest.last_error if latest else None,
                heartbeat_error=self._heartbeat_error(heartbeat),
                explicit_fallback=fallback_error,
                persisted=persisted,
            )

        failed_node = spec.node_key if (retryable or retry_running or failed_stage) else None
        return {
            "retryable": retryable,
            "failed_node": failed_node,
            "target_id": target_id,
            "failed_stage": failed_stage,
            "retry_action_label": spec.action_label,
            "preserved_outputs": list(spec.preserved_outputs),
            "reset_scope": list(spec.reset_scope),
            "last_attempt": int(latest.attempt) if latest else 0,
            "retry_running": retry_running,
            "last_error": last_error,
        }

    def _generic_task_name(self, node_key: str, project_id: int, target_id: Optional[int]) -> str:
        if node_key == self.NODE_WRITING_COMPOSE_SINGLE:
            if target_id is None:
                raise ServiceError("单章重试需要 target_id", status_code=400)
            from app.core.tasks.writing_compose_single_task import (
                get_writing_compose_single_task_name,
            )

            return get_writing_compose_single_task_name(project_id, target_id)
        if node_key == self.NODE_WRITING_COMPOSE:
            from app.core.tasks.writing_compose_task import get_writing_compose_task_name

            return get_writing_compose_task_name(project_id)
        if node_key == self.NODE_WRITING_MATCH:
            from app.core.tasks.writing_match_task import get_writing_match_task_name

            return get_writing_match_task_name(project_id)
        if node_key == self.NODE_WRITING_OUTLINE:
            from app.core.tasks.writing_outline_task import get_writing_outline_task_name

            return get_writing_outline_task_name(project_id)
        if node_key == self.NODE_WRITING_EXTRACT:
            from app.core.tasks.writing_extract_task import get_writing_extract_task_name

            return get_writing_extract_task_name(project_id)
        if node_key == self.NODE_WRITING_TENDER_PROCESS:
            document_id = target_id
            if document_id is None:
                raise ServiceError("招标文件解析重试需要 target_id=document_id", status_code=400)
            from app.core.tasks.process_task import get_process_task_name

            return get_process_task_name(document_id)
        if node_key == self.NODE_WRITING_RENDER:
            return f"writing_render_{project_id}"
        raise ServiceError(f"不支持的重试节点: {node_key}", status_code=400)

    async def _has_protected_outline_nodes(self, project_id: int) -> bool:
        from app.models.writing_outline_node import OutlineNodeStatus, WritingOutlineNode

        result = await self.db.execute(
            select(func.count(WritingOutlineNode.id)).where(
                WritingOutlineNode.project_id == project_id,
                WritingOutlineNode.status.in_(
                    (OutlineNodeStatus.user_edited, OutlineNodeStatus.finalized)
                ),
            )
        )
        return int(result.scalar_one() or 0) > 0

    async def get_generic_retry_state(
        self,
        project_id: int,
        *,
        node_key: str,
        target_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """返回非素材节点的第五章状态字段。"""

        from app.core.config import settings
        from app.core.tasks import inspect_task_liveness

        spec = self._spec(node_key)
        if spec.requires_target and target_id is None:
            raise ServiceError(f"{node_key} 需要 target_id", status_code=400)

        task_name = self._generic_task_name(node_key, project_id, target_id)
        liveness = inspect_task_liveness(
            task_name,
            heartbeat_timeout=int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180)),
            arq_function=self.ARQ_FUNCTION_BY_NODE.get(node_key),
        )
        heartbeat = liveness.heartbeat or {}
        task_live = liveness.is_live
        latest = await self._latest_run(project_id, node_key, target_id)
        retry_running = bool(
            task_live
            and latest is not None
            and latest.retry_requested_at is not None
            and self.is_retry_running_status(latest.status)
        )
        heartbeat_stage = str(heartbeat.get("stage") or "").strip().lower()
        retryable = bool(
            not task_live
            and (
                liveness.state in {"stale", "missing"}
                or heartbeat_stage == "failed"
                or (latest is not None and latest.status == TaskRunStatus.failed.value)
                or (latest is not None and self.is_retry_running_status(latest.status))
            )
        )
        # 目录保护会把 retryable 改回 False, 先固化"本次确实处于失败态"的判定,
        # 否则持久化兜底会被保护分支误关掉。
        failure_detected = bool(
            retryable
            or retry_running
            or (latest is not None and latest.status == TaskRunStatus.failed.value)
        )
        # 节点完成事实门控——已成功或从未执行的节点不得因心跳缺失/过期
        # 被标为失败点, 也不得吸附项目级失败文案。retry_running 时跳过门控,
        # 显式重试中的节点仍需完整展示运行态。
        node_completed = False
        if not retry_running:
            verdict = await resolve_node_completion(
                self.db,
                is_tender_process=(node_key == self.NODE_WRITING_TENDER_PROCESS),
                target_id=target_id,
                latest_run=latest,
                liveness_state=liveness.state,
            )
            node_completed = verdict.suppresses_failure
        if node_completed:
            retryable = False
            failure_detected = False
        protected_outline = False
        fallback_error = None
        if node_key == self.NODE_WRITING_OUTLINE and retryable:
            protected_outline = await self._has_protected_outline_nodes(project_id)
            if protected_outline:
                retryable = False
                fallback_error = "已检测到人工调整目录，系统不会自动清理或合并目录，请人工确认后重新生成"
        # 仅失败态才读持久化真源, 健康路径不产生额外查询
        persisted = None
        if failure_detected:
            persisted = await load_persisted_failure_sources(
                self.db,
                project_id=project_id,
                document_id=(
                    target_id if node_key == self.NODE_WRITING_TENDER_PROCESS else None
                ),
            )
        return self._build_state_payload(
            spec=spec,
            target_id=target_id,
            latest=latest,
            retryable=retryable,
            retry_running=retry_running,
            heartbeat=heartbeat,
            fallback_error=fallback_error,
            persisted=persisted,
            node_completed=node_completed,
        )

    async def get_retry_state(
        self,
        project_id: int,
        user: User,
        *,
        node_key: str,
        target_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """查询节点显式重试状态。"""

        if not self.is_supported_node(node_key):
            raise ServiceError(f"不支持的重试节点: {node_key}", status_code=400)
        await self._get_project_or_raise(project_id, user)
        if node_key == self.NODE_WRITING_COMPOSE_SINGLE and target_id is None:
            latest_single = await self._latest_compose_single_run(project_id)
            target_id = (
                self._target_id_from_task_run_key(latest_single.node_key)
                if latest_single is not None
                else None
            )
        if node_key == self.NODE_WRITING_MATERIAL_PROCESS:
            return await self.get_writing_material_retry_state(project_id)
        return await self.get_generic_retry_state(project_id, node_key=node_key, target_id=target_id)

    async def get_writing_material_retry_state(self, project_id: int) -> dict[str, Any]:
        """返回 `writing_material_process` 的第五章状态字段。"""

        from app.core.config import settings
        from app.core.tasks import inspect_task_liveness
        from app.core.tasks.writing_material_process_task import (
            get_writing_material_process_task_name,
        )

        node_key = self.NODE_WRITING_MATERIAL_PROCESS
        task_name = get_writing_material_process_task_name(project_id)
        liveness = inspect_task_liveness(
            task_name,
            heartbeat_timeout=int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180)),
            arq_function=self.ARQ_FUNCTION_BY_NODE.get(node_key),
        )
        heartbeat = liveness.heartbeat or {}
        task_live = liveness.is_live
        latest = await self._latest_run(project_id, node_key)
        counts = await self._material_counts(project_id)

        retry_running = bool(
            task_live
            and latest is not None
            and latest.retry_requested_at is not None
            and self.is_retry_running_status(latest.status)
        )
        orphan_processing = counts["processing"] > 0 and not task_live
        retryable = bool(
            not task_live
            and (
                counts["failed"] > 0
                or orphan_processing
                or (latest is not None and self.is_retry_running_status(latest.status))
            )
        )

        heartbeat_stage = self._heartbeat_stage(heartbeat)
        failed_stage = None
        if retryable or counts["failed"] > 0:
            if heartbeat_stage in self.WRITING_MATERIAL_STAGES:
                failed_stage = heartbeat_stage
            elif latest and latest.last_stage:
                failed_stage = latest.last_stage
            else:
                failed_stage = "unknown"

        # 仅失败态才读持久化真源, 健康路径不产生额外查询;
        # 素材节点没有文档级 VectorJobState, 只回落项目级失败原因。
        persisted = None
        if retryable or retry_running or counts["failed"] > 0:
            persisted = await load_persisted_failure_sources(self.db, project_id=project_id)
        last_error = pick_failure_reason(
            task_run_error=latest.last_error if latest else None,
            heartbeat_error=self._heartbeat_error(heartbeat),
            persisted=persisted,
        )

        failed_node = node_key if (retryable or retry_running or counts["failed"] > 0) else None
        return {
            "retryable": retryable,
            "failed_node": failed_node,
            "target_id": None,
            "failed_stage": failed_stage,
            "retry_action_label": "从素材处理继续重试",
            "preserved_outputs": list(self.WRITING_MATERIAL_PRESERVED_OUTPUTS),
            "reset_scope": list(self.WRITING_MATERIAL_RESET_SCOPE),
            "last_attempt": int(latest.attempt) if latest else 0,
            "retry_running": retry_running,
            "last_error": last_error,
        }

    async def begin_writing_material_attempt(
        self,
        *,
        project_id: int,
        user_id: Optional[int],
        task_name: str,
        explicit_retry: bool,
        target_material_ids: Optional[list[int]] = None,
        last_stage: Optional[str] = None,
    ) -> TaskRun:
        """创建新 attempt 记录，dispatch 结果稍后回填。"""

        attempt = await self._next_attempt(project_id, self.NODE_WRITING_MATERIAL_PROCESS)
        preserved_json = json.dumps(
            self.WRITING_MATERIAL_PRESERVED_OUTPUTS,
            ensure_ascii=False,
        )
        reset_summary: dict[str, Any] = {
            "scope": self.WRITING_MATERIAL_RESET_SCOPE,
            "target_material_ids": target_material_ids or [],
        }
        run = TaskRun(
            project_id=project_id,
            flow=self.FLOW_BID_WRITING,
            node_key=self.NODE_WRITING_MATERIAL_PROCESS,
            task_name=task_name,
            status=TaskRunStatus.queued.value,
            attempt=attempt,
            last_stage=last_stage,
            retry_requested_by=user_id if explicit_retry else None,
            retry_requested_at=datetime.now(timezone.utc) if explicit_retry else None,
            preserved_summary_json=preserved_json,
            reset_scope_json=json.dumps(reset_summary, ensure_ascii=False),
        )
        self.db.add(run)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def begin_attempt(
        self,
        *,
        project_id: int,
        node_key: str,
        user_id: Optional[int],
        task_name: str,
        explicit_retry: bool,
        target_id: Optional[int] = None,
        reset_extra: Optional[dict[str, Any]] = None,
        last_stage: Optional[str] = None,
    ) -> TaskRun:
        """创建通用节点 attempt 记录，dispatch 结果稍后回填。"""

        spec = self._spec(node_key)
        task_run_key = self._task_run_key(node_key, target_id)
        attempt = await self._next_attempt(project_id, node_key, target_id)
        reset_summary: dict[str, Any] = {
            "scope": list(spec.reset_scope),
            "target_id": target_id,
            **(reset_extra or {}),
        }
        run = TaskRun(
            project_id=project_id,
            flow=self.FLOW_BID_WRITING,
            node_key=task_run_key,
            task_name=task_name,
            status=TaskRunStatus.queued.value,
            attempt=attempt,
            last_stage=last_stage,
            retry_requested_by=user_id if explicit_retry else None,
            retry_requested_at=datetime.now(timezone.utc) if explicit_retry else None,
            preserved_summary_json=json.dumps(list(spec.preserved_outputs), ensure_ascii=False),
            reset_scope_json=json.dumps(reset_summary, ensure_ascii=False),
        )
        self.db.add(run)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def attach_dispatch_result(
        self,
        run: TaskRun,
        *,
        queue_mode: str,
        arq_job_id: Optional[str],
        last_stage: Optional[str] = None,
    ) -> None:
        """回填 dispatch 结果。"""

        try:
            await self.db.refresh(run)
        except Exception:
            pass
        run.current_arq_job_id = arq_job_id
        if run.status in self.RETRY_RUNNING_STATUSES:
            run.status = (
                TaskRunStatus.running.value
                if queue_mode == "asyncio_fallback"
                else TaskRunStatus.queued.value
            )
        if last_stage:
            run.last_stage = last_stage
        await self.db.commit()

    async def _retry_target_material_ids(self, project_id: int) -> list[int]:
        result = await self.db.execute(
            select(WritingMaterial.id).where(
                WritingMaterial.project_id == project_id,
                WritingMaterial.processing_status.in_(("failed", "saved", "processing")),
            )
        )
        return [int(row[0]) for row in result.all()]

    async def retry_writing_material_process(
        self,
        project_id: int,
        user: User,
    ) -> dict[str, Any]:
        """显式重试写标素材处理节点。"""

        from app.core.tasks import (
            cleanup_task,
            dispatch_background_job,
            is_task_active,
            is_task_in_enqueue_grace,
        )
        from app.core.tasks.writing_material_process_task import (
            get_writing_material_process_task_name,
            run_writing_material_process_task,
        )

        project = await self._get_project_or_raise(project_id, user)
        task_name = get_writing_material_process_task_name(project_id)
        if is_task_active(task_name) or is_task_in_enqueue_grace(task_name):
            state = await self.get_writing_material_retry_state(project_id)
            return {
                **state,
                "task_name": task_name,
                "queue_mode": "active",
                "message": "素材处理任务已在进行中",
            }

        target_ids = await self._retry_target_material_ids(project_id)
        if not target_ids:
            state = await self.get_writing_material_retry_state(project_id)
            return {
                **state,
                "task_name": task_name,
                "queue_mode": "idle",
                "message": "当前没有可重试的素材",
            }

        # 失败释放 hold 后显式重试必须先重建计费预占，
        # 余额不足直接 403，不得先删产物再拦截（保护现场）。
        from app.services.writing.material_billing_gate import (
            ensure_writing_billing_hold,
        )
        await ensure_writing_billing_hold(self.db, project, project.owner_id)

        # 仅重置失败/孤儿素材派生产物，保留 completed 素材和前置人工确认成果。
        await self.db.execute(
            delete(WritingMaterialChunk).where(WritingMaterialChunk.material_id.in_(target_ids))
        )
        await self.db.execute(
            delete(WritingMaterialImage).where(WritingMaterialImage.material_id.in_(target_ids))
        )
        await self.db.execute(
            update(WritingMaterial)
            .where(WritingMaterial.id.in_(target_ids))
            .values(
                processing_status="processing",
                content=None,
                metadata_tags=None,
                chunk_ids=None,
                content_label=None,
                resolved_label=None,
                label_conflict=None,
                label_source=None,
            )
        )
        await self.db.commit()

        cleanup_task(task_name)
        run = await self.begin_writing_material_attempt(
            project_id=project_id,
            user_id=user.id,
            task_name=task_name,
            explicit_retry=True,
            target_material_ids=target_ids,
            last_stage="retry_requested",
        )
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_material_process_job",
            fallback_factory=lambda: run_writing_material_process_task(project_id),
            task_type=self.TASK_TYPE_WRITING_MATERIAL_PROCESS,
            project_id=project_id,
            arq_args=(project_id,),
        )
        await self.attach_dispatch_result(
            run,
            queue_mode=queue_mode,
            arq_job_id=arq_job_id,
            last_stage="queued",
        )
        logger.info(
            f"[显式重试] 写标素材处理已投递: project={project_id}, "
            f"attempt={run.attempt}, targets={len(target_ids)}, queue_mode={queue_mode}"
        )
        state = await self.get_writing_material_retry_state(project_id)
        return {
            **state,
            "task_name": task_name,
            "queue_mode": queue_mode,
            "message": "素材处理重试已启动",
        }

    async def retry_writing_node(
        self,
        project_id: int,
        user: User,
        *,
        node_key: str,
        target_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """显式重试写标节点入口。"""

        if node_key == self.NODE_WRITING_MATERIAL_PROCESS:
            return await self.retry_writing_material_process(project_id, user)
        if node_key == self.NODE_WRITING_RENDER:
            return await self.retry_writing_render(project_id, user)
        return await self.retry_async_writing_node(
            project_id,
            user,
            node_key=node_key,
            target_id=target_id,
        )

    async def retry_async_writing_node(
        self,
        project_id: int,
        user: User,
        *,
        node_key: str,
        target_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """显式重试异步写标节点。"""

        from app.core.tasks import (
            cleanup_task,
            dispatch_background_job,
            is_task_active,
            is_task_in_enqueue_grace,
        )

        project = await self._get_project_or_raise(project_id, user)
        spec = self._spec(node_key)
        if spec.requires_target and target_id is None:
            raise ServiceError(f"{node_key} 需要 target_id", status_code=400)

        task_name = self._generic_task_name(node_key, project_id, target_id)
        if is_task_active(task_name) or is_task_in_enqueue_grace(task_name):
            state = await self.get_generic_retry_state(project_id, node_key=node_key, target_id=target_id)
            return {
                **state,
                "task_name": task_name,
                "queue_mode": "active",
                "message": "任务已在进行中",
            }
        if node_key == self.NODE_WRITING_OUTLINE and await self._has_protected_outline_nodes(project_id):
            raise ServiceError(
                "已检测到人工调整目录，显式重试不会自动清理或合并目录，请人工确认后重新生成",
                status_code=409,
            )

        # 显式重试与首次触发共享同一计费预占，必须先于任何产物重置执行；
        # writing_render 为纯 CPU 渲染走 retry_writing_render 独立入口，天然豁免。
        # 生成节点重试按目录字数追加 token 预算，估算超出冻结额度补差额。
        from app.services.writing.compose_estimate import (
            estimate_compose_token_budget,
        )
        from app.services.writing.material_billing_gate import (
            ensure_writing_billing_hold,
        )
        extra_input = extra_output = 0
        if node_key in (self.NODE_WRITING_COMPOSE, self.NODE_WRITING_COMPOSE_SINGLE):
            extra_input, extra_output = await estimate_compose_token_budget(
                self.db, project_id,
                node_id=(
                    target_id
                    if node_key == self.NODE_WRITING_COMPOSE_SINGLE
                    else None
                ),
            )
        await ensure_writing_billing_hold(
            self.db, project, project.owner_id,
            extra_input_tokens=extra_input,
            extra_output_tokens=extra_output,
        )

        await self._prepare_retry_reset(project_id, node_key=node_key, target_id=target_id)
        cleanup_task(task_name)
        run = await self.begin_attempt(
            project_id=project_id,
            node_key=node_key,
            user_id=user.id,
            task_name=task_name,
            explicit_retry=True,
            target_id=target_id,
            last_stage="retry_requested",
        )
        arq_function, fallback_factory, arq_args = self._dispatch_contract(
            project_id,
            node_key=node_key,
            target_id=target_id,
            explicit_retry=True,
            attempt=run.attempt,
        )
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function=arq_function,
            fallback_factory=fallback_factory,
            task_type=spec.task_type,
            project_id=project_id,
            arq_args=arq_args,
        )
        await self.attach_dispatch_result(
            run,
            queue_mode=queue_mode,
            arq_job_id=arq_job_id,
            last_stage="queued",
        )
        if node_key == self.NODE_WRITING_TENDER_PROCESS and target_id is not None:
            from app.core.config import settings
            from app.core.vectorizer.job_state import upsert_vector_job_state
            from app.models.vector_job_state import VectorJobStatus

            await upsert_vector_job_state(
                self.db,
                project_id=project_id,
                document_id=target_id,
                task_name=task_name,
                status=VectorJobStatus.QUEUED if queue_mode == "arq" else VectorJobStatus.PENDING,
                queue_mode=queue_mode,
                stage="queued",
                progress="0/0",
                chunk_fingerprint_version=str(
                    getattr(settings, "VECTOR_EMBEDDING_VERSION", "bge-m3-v1")
                ),
                arq_job_id=arq_job_id if queue_mode == "arq" else None,
                bump_enqueued_at=(queue_mode == "arq"),
            )
            await self.db.commit()
        logger.info(
            f"[显式重试] 写标节点已投递: project={project_id}, node={node_key}, "
            f"target={target_id}, attempt={run.attempt}, queue_mode={queue_mode}"
        )
        state = await self.get_generic_retry_state(project_id, node_key=node_key, target_id=target_id)
        return {
            **state,
            "task_name": task_name,
            "queue_mode": queue_mode,
            "message": f"{spec.action_label}已启动",
        }

    def _dispatch_contract(
        self,
        project_id: int,
        *,
        node_key: str,
        target_id: Optional[int],
        explicit_retry: bool,
        attempt: Optional[int] = None,
    ) -> tuple[str, Any, tuple[Any, ...]]:
        """返回 dispatch_background_job 所需的 ARQ/fallback 合同。"""

        if node_key == self.NODE_WRITING_COMPOSE_SINGLE:
            if target_id is None:
                raise ServiceError("单章重试需要 target_id", status_code=400)
            from app.core.tasks.writing_compose_single_task import run_writing_compose_single_task

            return (
                "writing_compose_single_job",
                lambda: run_writing_compose_single_task(project_id, target_id, explicit_retry),
                (project_id, target_id, explicit_retry),
            )
        if node_key == self.NODE_WRITING_COMPOSE:
            from app.core.tasks.writing_compose_task import run_writing_compose_task

            return (
                "writing_compose_job",
                lambda: run_writing_compose_task(project_id, explicit_retry),
                (project_id, explicit_retry),
            )
        if node_key == self.NODE_WRITING_MATCH:
            from app.core.tasks.writing_match_task import run_writing_match_task

            return (
                "writing_match_job",
                lambda: run_writing_match_task(project_id, explicit_retry),
                (project_id, explicit_retry),
            )
        if node_key == self.NODE_WRITING_OUTLINE:
            from app.core.tasks.writing_outline_task import run_writing_outline_task

            return (
                "writing_outline_job",
                lambda: run_writing_outline_task(project_id, explicit_retry),
                (project_id, explicit_retry),
            )
        if node_key == self.NODE_WRITING_EXTRACT:
            from app.core.tasks.writing_extract_task import run_writing_extract_task

            return (
                "writing_extract_job",
                lambda: run_writing_extract_task(project_id, explicit_retry, attempt),
                (project_id, explicit_retry, attempt),
            )
        if node_key == self.NODE_WRITING_TENDER_PROCESS:
            if target_id is None:
                raise ServiceError("招标文件解析重试需要 target_id=document_id", status_code=400)
            from app.core.tasks.process_task import run_process_task

            return (
                "process_document_job",
                lambda: run_process_task(project_id, target_id),
                (project_id, target_id),
            )
        raise ServiceError(f"不支持的重试节点: {node_key}", status_code=400)

    async def _prepare_retry_reset(
        self,
        project_id: int,
        *,
        node_key: str,
        target_id: Optional[int],
    ) -> None:
        """按节点契约执行重试前允许的最小重置。"""

        if node_key == self.NODE_WRITING_MATCH:
            from app.models.writing_binding import BindingStatus, WritingBinding

            await self.db.execute(
                delete(WritingBinding).where(
                    WritingBinding.project_id == project_id,
                    WritingBinding.status != BindingStatus.confirmed,
                )
            )
            await self.db.commit()
        elif node_key == self.NODE_WRITING_TENDER_PROCESS and target_id is not None:
            from app.models.document import Document
            from app.models.vector_job_state import VectorJobState, VectorJobStatus

            document = await self.db.get(Document, target_id)
            if not document or document.project_id != project_id:
                raise ServiceError("招标文件不存在", status_code=404)
            document.parsed = 0
            state = await self.db.execute(
                select(VectorJobState).where(VectorJobState.document_id == target_id)
            )
            job_state = state.scalar_one_or_none()
            if job_state:
                job_state.status = VectorJobStatus.PENDING
                job_state.stage = "queued"
                job_state.progress = "0/0"
                job_state.last_error = None
            await self.db.commit()

    async def retry_writing_render(self, project_id: int, user: User) -> dict[str, Any]:
        """同步渲染节点显式重试。

        渲染当前仍是同步入口: retry 调用即创建 attempt 并在请求内完成/失败。
        """

        await self._get_project_or_raise(project_id, user)
        spec = self._spec(self.NODE_WRITING_RENDER)
        task_name = self._generic_task_name(self.NODE_WRITING_RENDER, project_id, None)
        await self.begin_attempt(
            project_id=project_id,
            node_key=self.NODE_WRITING_RENDER,
            user_id=user.id,
            task_name=task_name,
            explicit_retry=True,
            last_stage="rendering",
        )
        await self._mark_latest_task_run(
            project_id,
            node_key=self.NODE_WRITING_RENDER,
            status=TaskRunStatus.running.value,
            stage="rendering",
            last_error=None,
        )
        try:
            from app.services.writing_service import WritingService

            await WritingService(self.db).render_docx(project_id, user)
            await self._mark_latest_task_run(
                project_id,
                node_key=self.NODE_WRITING_RENDER,
                status=TaskRunStatus.completed.value,
                stage="completed",
                last_error=None,
            )
        except Exception as exc:
            await self._mark_latest_task_run(
                project_id,
                node_key=self.NODE_WRITING_RENDER,
                status=TaskRunStatus.failed.value,
                stage="failed",
                last_error=str(exc),
            )
            raise
        latest = await self._latest_run(project_id, self.NODE_WRITING_RENDER)
        state = self._build_state_payload(
            spec=spec,
            target_id=None,
            latest=latest,
            retryable=False,
            retry_running=False,
        )
        return {
            **state,
            "task_name": task_name,
            "queue_mode": "sync",
            "message": "文档渲染重试已完成",
        }

    async def mark_writing_material_task_running(
        self,
        project_id: int,
        *,
        stage: str,
    ) -> None:
        await self._mark_latest_task_run(
            project_id,
            node_key=self.NODE_WRITING_MATERIAL_PROCESS,
            status=TaskRunStatus.running.value,
            stage=stage,
            last_error=None,
        )

    async def mark_writing_material_task_completed(self, project_id: int) -> None:
        await self._mark_latest_task_run(
            project_id,
            node_key=self.NODE_WRITING_MATERIAL_PROCESS,
            status=TaskRunStatus.completed.value,
            stage="completed",
            last_error=None,
        )

    async def mark_writing_material_task_failed(
        self,
        project_id: int,
        *,
        stage: str,
        error: str,
    ) -> None:
        await self._mark_latest_task_run(
            project_id,
            node_key=self.NODE_WRITING_MATERIAL_PROCESS,
            status=TaskRunStatus.failed.value,
            stage=stage,
            last_error=error,
        )

    async def _mark_latest_task_run(
        self,
        project_id: int,
        *,
        node_key: str,
        target_id: Optional[int] = None,
        status: str,
        stage: str,
        last_error: Optional[str],
    ) -> None:
        latest = await self._latest_run(project_id, node_key, target_id)
        if latest is None:
            return
        latest.status = status
        latest.last_stage = stage
        if last_error is not None:
            latest.last_error = str(last_error).strip().splitlines()[0][:500]
        elif status == TaskRunStatus.completed.value:
            latest.last_error = None
        if status == TaskRunStatus.completed.value:
            freeze_completed_duration(latest)
        try:
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            logger.warning(f"更新 TaskRun 状态失败(忽略): project={project_id}, err={exc}")

    async def mark_task_running(
        self,
        project_id: int,
        *,
        node_key: str,
        target_id: Optional[int] = None,
        stage: str,
    ) -> None:
        await self._mark_latest_task_run(
            project_id,
            node_key=node_key,
            target_id=target_id,
            status=TaskRunStatus.running.value,
            stage=stage,
            last_error=None,
        )

    async def mark_task_completed(
        self,
        project_id: int,
        *,
        node_key: str,
        target_id: Optional[int] = None,
    ) -> None:
        await self._mark_latest_task_run(
            project_id,
            node_key=node_key,
            target_id=target_id,
            status=TaskRunStatus.completed.value,
            stage="completed",
            last_error=None,
        )

    async def mark_task_failed(
        self,
        project_id: int,
        *,
        node_key: str,
        target_id: Optional[int] = None,
        stage: str,
        error: str,
    ) -> None:
        await self._mark_latest_task_run(
            project_id,
            node_key=node_key,
            target_id=target_id,
            status=TaskRunStatus.failed.value,
            stage=stage,
            last_error=error,
        )
