"""标书编写阶段 4：内容生成与草稿管理

承载 8 个方法：trigger_compose / compose_single_node / get_compose_single_status
+ list_drafts / get_draft / update_draft / toggle_lock / get_consistency_issues。

接口约定（Task 5 Step 6）：
- ``project_resolver`` 注入：lambda 包装 ``_get_project_or_raise``
- ``render_invalidator`` 注入：lambda 包装 ``_invalidate_render_artifact``
- ``ensure_preview_ready`` 注入：lambda 包装 ``_ensure_preview_ready``
- ``ensure_render_artifact`` 注入：lambda 包装 ``_ensure_render_artifact``
- ``load_cached_consistency`` 注入：lambda 包装 ``_load_cached_consistency``
所有 callable late-binding 至 WritingService 实例，保持
``test_writer_render_artifact_cache`` 等 ``service._ensure_render_artifact = AsyncMock(...)``
monkey patch 契约。
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, Awaitable, Callable, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ServiceError
from app.core.xml_compat import sanitize_xml_compatible_text
from app.core.writer.clause_coverage_diagnostics import (
    build_clause_coverage_diagnostics,
)
from app.core.writer.numbering import strip_existing_number
from app.core.writer.section_structure import normalize_section_html
from app.models.project import Project, ProjectStatus
from app.models.user import User
from app.models.writing_binding import BindingType, WritingBinding
from app.models.writing_clause import WritingClause
from app.models.writing_outline_node import OutlineNodeStatus, WritingOutlineNode
from app.models.writing_render_artifact import WritingRenderArtifact
from app.models.writing_section_draft import WritingSectionDraft
from app.services.writing.clause_readiness import WritingClauseReadinessService
from app.services.writing.progress_steps import _parse_percent_detail

_COMPOSE_SKIP_STATUSES = {
    ProjectStatus.COMPOSED,
    ProjectStatus.FORMATTING,
    ProjectStatus.COMPLETED,
}


class ComposeService:
    """内容生成阶段子服务"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        project_resolver: Callable[..., Awaitable[Project]],
        render_invalidator: Callable[[int], Awaitable[None]],
        ensure_preview_ready: Callable[[Project], None],
        ensure_render_artifact: Callable[..., Awaitable[WritingRenderArtifact]],
        load_cached_consistency: Callable[[Optional[WritingRenderArtifact]], Optional[list[dict]]],
    ) -> None:
        self.db = db
        self._resolve_project = project_resolver
        self._invalidate_render = render_invalidator
        self._ensure_preview_ready = ensure_preview_ready
        self._ensure_render_artifact = ensure_render_artifact
        self._load_cached_consistency = load_cached_consistency

    async def trigger_compose(
        self, project_id: int, user: User, *, force: bool = False,
    ) -> dict[str, str]:
        """触发全文生成(后台任务)

        Args:
            force: 为 True 时跳过状态检查,用于用户手动点击"重新生成全文"
        """
        project = await self._resolve_project(project_id, user)
        await WritingClauseReadinessService(self.db).ensure_visible_clauses_confirmed(
            project_id
        )

        if not force and project.status in _COMPOSE_SKIP_STATUSES:
            return {"task_name": "", "message": "内容已生成,无需重复执行"}

        from app.core.tasks.writing_compose_task import (
            run_writing_compose_task,
            get_writing_compose_task_name,
        )
        from app.services.writing.generation_admission import (
            WritingGenerationAdmissionService,
            WritingGenerationClaimRequest,
        )

        task_name = get_writing_compose_task_name(project_id)

        # 结算后重跑全文必须新建预占，否则 token 落入下一基线永不计费
        # 生成阶段按目录字数追加 token 预算，估算超出冻结额度时补差额
        from app.services.writing.compose_estimate import (
            estimate_compose_token_budget,
        )
        from app.services.writing.material_billing_gate import (
            ensure_writing_billing_hold,
        )
        est_input, est_output = await estimate_compose_token_budget(
            self.db, project_id,
        )
        await ensure_writing_billing_hold(
            self.db, project, project.owner_id,
            extra_input_tokens=est_input,
            extra_output_tokens=est_output,
        )

        from app.core.tasks import dispatch_background_job
        from app.services.retry_service import RetryService

        retry_svc = RetryService(self.db)
        claim = await WritingGenerationAdmissionService(self.db).claim_generation(
            WritingGenerationClaimRequest(
                project_id=project_id,
                node_key=RetryService.NODE_WRITING_COMPOSE,
                task_name=task_name,
                user_id=getattr(user, "id", None),
                target_id=None,
                explicit_retry=False,
            )
        )
        if not claim.claimed:
            return {
                "task_name": claim.active_task_name or task_name,
                "message": "生成任务进行中",
            }

        try:
            await self._invalidate_render(project_id)
            await self.db.commit()
            queue_mode, arq_job_id = await dispatch_background_job(
                task_name=task_name,
                arq_function="writing_compose_job",
                fallback_factory=lambda: run_writing_compose_task(project_id),
                task_type="writing_compose",
                project_id=project_id,
                arq_args=(project_id,),
            )
        except Exception as exc:
            await retry_svc.mark_task_failed(
                project_id,
                node_key=RetryService.NODE_WRITING_COMPOSE,
                stage="dispatch_failed",
                error=str(exc),
            )
            raise
        await retry_svc.attach_dispatch_result(
            claim.task_run,
            queue_mode=queue_mode,
            arq_job_id=arq_job_id,
            last_stage="queued",
        )
        return {
            "task_name": task_name,
            "message": "全文生成已启动",
            "queue_mode": queue_mode,
        }

    async def compose_single_node(
        self, project_id: int, node_id: int, user: User,
    ) -> dict[str, Any]:
        """触发单章节生成(后台任务).

 BUG-collusion sibling: 原实现在请求内同步调用 LLM + gate + save_draft,
 长章节可达数十秒, 与全文生成任务存在"旁路实现"。改造后复用统一任务契约,
 worker 承载执行, API 只负责触发并立即返回, 前端通过 compose/status 轮询结果。
 """
        project = await self._resolve_project(project_id, user)
        await WritingClauseReadinessService(self.db).ensure_visible_clauses_confirmed(
            project_id
        )
        node = await self.db.get(WritingOutlineNode, node_id)
        if not node or node.project_id != project_id:
            raise NotFoundError("节点不存在")

        from app.core.tasks import dispatch_background_job
        from app.core.tasks.writing_compose_single_task import (
            get_writing_compose_single_task_name,
            run_writing_compose_single_task,
        )
        from app.services.writing.generation_admission import (
            WritingGenerationAdmissionService,
            WritingGenerationClaimRequest,
        )

        task_name = get_writing_compose_single_task_name(project_id, node_id)

        # 单章重生成同样是结算后可触发的 LLM 入口，必须确保预占存在
        # 按该节点目标字数追加预算，无字数由通用重估兜底
        from app.services.writing.compose_estimate import (
            estimate_compose_token_budget,
        )
        from app.services.writing.material_billing_gate import (
            ensure_writing_billing_hold,
        )
        est_input, est_output = await estimate_compose_token_budget(
            self.db, project_id, node_id=node_id,
        )
        await ensure_writing_billing_hold(
            self.db, project, project.owner_id,
            extra_input_tokens=est_input,
            extra_output_tokens=est_output,
        )

        from app.services.retry_service import RetryService

        retry_svc = RetryService(self.db)
        claim = await WritingGenerationAdmissionService(self.db).claim_generation(
            WritingGenerationClaimRequest(
                project_id=project_id,
                node_key=RetryService.NODE_WRITING_COMPOSE_SINGLE,
                task_name=task_name,
                user_id=getattr(user, "id", None),
                target_id=node_id,
                explicit_retry=False,
            )
        )
        if not claim.claimed:
            return {
                "task_name": claim.active_task_name or task_name,
                "message": "单章节生成任务已在进行中",
                "queue_mode": "active",
            }

        try:
            queue_mode, arq_job_id = await dispatch_background_job(
                task_name=task_name,
                arq_function="writing_compose_single_job",
                fallback_factory=lambda: run_writing_compose_single_task(project_id, node_id),
                task_type="writing_compose_single",
                project_id=project_id,
                arq_args=(project_id, node_id),
            )
        except Exception as exc:
            await retry_svc.mark_task_failed(
                project_id,
                node_key=RetryService.NODE_WRITING_COMPOSE_SINGLE,
                target_id=node_id,
                stage="dispatch_failed",
                error=str(exc),
            )
            raise
        await retry_svc.attach_dispatch_result(
            claim.task_run,
            queue_mode=queue_mode,
            arq_job_id=arq_job_id,
            last_stage="queued",
        )
        logger.info(
            f"[单章生成] 触发: project={project_id}, node={node_id}, "
            f"queue_mode={queue_mode}, arq_job_id={arq_job_id}"
        )
        return {
            "task_name": task_name,
            "message": "单章节生成已启动",
            "queue_mode": queue_mode,
        }

    async def get_compose_single_status(
        self, project_id: int, node_id: int, user: User,
    ) -> dict[str, Any]:
        """查询单章节生成任务心跳, 用于前端轮询."""
        await self._resolve_project(project_id, user)

        from app.core.config import settings
        from app.core.tasks import inspect_task_liveness
        from app.core.tasks.writing_compose_single_task import (
            get_writing_compose_single_task_name,
        )

        task_name = get_writing_compose_single_task_name(project_id, node_id)
        liveness = inspect_task_liveness(
            task_name,
            heartbeat_timeout=int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180)),
            arq_function="writing_compose_single_job",
        )
        heartbeat = liveness.heartbeat or {}

        progress_raw = str(heartbeat.get("progress", ""))
        progress_pct, detail = _parse_percent_detail(progress_raw)
        stage = str(heartbeat.get("stage", ""))
        if liveness.state in {"stale", "missing"}:
            stage = "failed"
            detail = detail or "单章生成任务异常终止，请重试"

        return {
            "active": liveness.is_live,
            "stage": stage,
            "progress": progress_raw,
            "progress_pct": progress_pct,
            "current_detail": detail,
        }

    async def get_clause_coverage(
        self, project_id: int, user: User,
    ) -> dict:
        """约束消费覆盖诊断: 全局分母/已覆盖 + 未覆盖三类可行动原因。

        只读聚合 clauses(已确认) + 当前草稿 source_clause_ids + clause_to_node 绑定,
        供写标内容页顶部全局概览展示"为何未消费"(待人工分配/未匹配/待生成),
        替代逐节点重复的黑箱数字。计算落在 clause_coverage_diagnostics 纯函数。
        """
        await self._resolve_project(project_id, user)
        clauses = (await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.confirmed == 1,
            )
        )).scalars().all()
        drafts = (await self.db.execute(
            select(WritingSectionDraft).join(
                WritingOutlineNode,
                WritingSectionDraft.outline_node_id == WritingOutlineNode.id,
            ).where(
                WritingOutlineNode.project_id == project_id,
                WritingSectionDraft.is_current == 1,
            )
        )).scalars().all()
        bindings = (await self.db.execute(
            select(WritingBinding).where(
                WritingBinding.project_id == project_id,
                WritingBinding.binding_type == BindingType.clause_to_node,
                WritingBinding.clause_id.isnot(None),
            )
        )).scalars().all()
        diagnostics = build_clause_coverage_diagnostics(
            list(clauses), list(drafts), list(bindings),
        )
        return asdict(diagnostics)

    async def list_drafts(
        self, project_id: int, user: User,
    ) -> list[WritingSectionDraft]:
        """全部草稿"""
        await self._resolve_project(project_id, user)
        result = await self.db.execute(
            select(WritingSectionDraft).join(
                WritingOutlineNode,
                WritingSectionDraft.outline_node_id == WritingOutlineNode.id,
            ).where(
                WritingOutlineNode.project_id == project_id,
                WritingSectionDraft.is_current == 1,
            ).order_by(WritingOutlineNode.depth, WritingOutlineNode.sort_order)
        )
        return list(result.scalars().all())

    async def get_draft(
        self, project_id: int, node_id: int, user: User,
    ) -> WritingSectionDraft:
        """单章节草稿"""
        await self._resolve_project(project_id, user)
        result = await self.db.execute(
            select(WritingSectionDraft).join(
                WritingOutlineNode,
                WritingSectionDraft.outline_node_id == WritingOutlineNode.id,
            ).where(
                WritingSectionDraft.outline_node_id == node_id,
                WritingOutlineNode.project_id == project_id,
                WritingSectionDraft.is_current == 1,
            )
        )
        draft = result.scalars().first()
        if not draft:
            raise NotFoundError("草稿不存在")
        return draft

    async def update_draft(
        self, project_id: int, node_id: int,
        user: User, content: str,
    ) -> WritingSectionDraft:
        """用户编辑保存"""
        await self._resolve_project(project_id, user)
        draft = await self.get_draft(project_id, node_id, user)
        if draft.is_locked:
            raise ServiceError("该章节已锁定，请先解锁")

        node = await self.db.get(WritingOutlineNode, node_id)
        clean_title = strip_existing_number(node.title) if node else ""
        normalized_content = sanitize_xml_compatible_text(
            normalize_section_html((content or "").strip(), clean_title),
        )

        old_content = draft.content or ""
        content_changed = normalized_content != old_content

        draft.content = normalized_content
        draft.word_count = len(re.sub(r"\s+", "", normalized_content)) if normalized_content else 0

        if content_changed:
            draft.is_user_edited = 1
            if node:
                node.status = OutlineNodeStatus.user_edited
            await self._invalidate_render(project_id)

        await self.db.commit()
        await self.db.refresh(draft)
        return draft

    async def toggle_lock(
        self, project_id: int, node_id: int, user: User,
    ) -> WritingSectionDraft:
        """锁定/解锁"""
        await self._resolve_project(project_id, user)
        draft = await self.get_draft(project_id, node_id, user)
        draft.is_locked = 0 if draft.is_locked else 1
        await self.db.commit()
        await self.db.refresh(draft)
        return draft

    async def get_consistency_issues(
        self, project_id: int, user: User,
    ) -> list[dict]:
        """一致性检查结果"""
        project = await self._resolve_project(project_id, user)
        self._ensure_preview_ready(project)
        if hasattr(self.db, "execute"):
            result = await self.db.execute(
                select(WritingRenderArtifact).where(
                    WritingRenderArtifact.project_id == project_id,
                )
            )
            cached = self._load_cached_consistency(result.scalars().first())
            if cached is not None:
                return cached

        artifact = await self._ensure_render_artifact(project_id)
        cached = self._load_cached_consistency(artifact)
        if cached is not None:
            return cached

        artifact = await self._ensure_render_artifact(
            project_id,
            force_rebuild=True,
            explicit_render=False,
        )
        cached = self._load_cached_consistency(artifact)
        if cached is None:
            raise ServiceError("一致性结果生成失败")
        return cached


__all__ = ["ComposeService"]
