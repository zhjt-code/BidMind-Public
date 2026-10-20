"""项目管理服务

封装项目 CRUD 的全部业务逻辑与数据库操作，供路由层调用。
"""

import asyncio
from typing import Optional, Any

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.project import Project, ProjectStatus, ProjectType
from app.models.document import Document
from app.models.user import User, UserRole
from app.models.vector_job_state import VectorJobState
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.core.exceptions import NotFoundError, ForbiddenError, ServiceError
from app.core.tasks import cancel_task, get_task, clear_collusion_cache
from app.services.review_service import clear_review_cache
from app.services.audit_service import audit_log
from app.core.ai import release_project_binding
from app.core.arq_runtime import (
    abort_arq_job_best_effort,
    mark_project_cancelled_async,
)
from app.core.redis import get_redis_pool
from app.services.project_log_snapshot_service import backfill_project_log_snapshot
from app.services.project_task_cleanup import ProjectWritingTaskCleanup


class ProjectService:
    """项目管理服务

    通过构造函数注入 AsyncSession，所有方法均为 async。
    异常使用领域异常（ServiceError 体系），不引入任何 HTTP 概念。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_projects(
        self,
        user: User,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
    ) -> dict[str, Any]:
        """获取项目列表（分页）

        普通用户仅可见自己的项目，管理员可见全部。
        支持按状态筛选，非法状态值抛出 ServiceError。

        Returns:
            包含 items / total / page / page_size / total_pages 的字典
        """
        query = select(Project).options(selectinload(Project.owner))
        count_query = select(func.count(Project.id))

        if user.role != UserRole.ADMIN:
            query = query.where(Project.owner_id == user.id)
            count_query = count_query.where(Project.owner_id == user.id)

        if status_filter:
            try:
                status_enum = ProjectStatus(status_filter)
            except ValueError:
                raise ServiceError(f"无效的状态值: {status_filter}")
            query = query.where(Project.status == status_enum)
            count_query = count_query.where(Project.status == status_enum)

        total_result = await self.db.execute(count_query)
        total: int = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.order_by(Project.created_at.desc()).offset(offset).limit(page_size)

        result = await self.db.execute(query)
        projects = result.scalars().all()

        total_pages = (total + page_size - 1) // page_size

        logger.debug(f"查询项目列表: user={user.username}, page={page}, total={total}")

        return {
            "items": list(projects),
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    async def create(self, project_data: ProjectCreate, owner: User) -> Project:
        """创建项目

        状态初始化为 CREATED，类型取 project_data 中的值或默认 BID_REVIEW。
        创建后预加载 owner 关系并返回 ORM 对象。
        """
        project = Project(
            name=project_data.name,
            description=project_data.description,
            status=ProjectStatus.CREATED,
            owner_id=owner.id,
            project_type=project_data.project_type or ProjectType.BID_REVIEW,
        )

        self.db.add(project)
        await self.db.flush()
        await self.db.refresh(project, ["owner"])

        logger.info(f"创建项目: id={project.id}, name={project.name}, owner={owner.username}")
        await audit_log(
            self.db, user_id=owner.id, username=owner.username,
            category=project.project_type.value,
            action="create", resource_type="project", resource_id=project.id,
            detail=f"创建项目: {project.name}",
        )
        return project

    async def get(self, project_id: int, user: User) -> Project:
        """获取项目详情

        预加载 owner 关系，校验项目存在性和访问权限。
        """
        result = await self.db.execute(
            select(Project)
            .options(selectinload(Project.owner))
            .where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()

        if not project:
            raise NotFoundError(f"项目不存在: id={project_id}")

        self._check_permission(project, user)
        return project

    async def update(
        self,
        project_id: int,
        project_data: ProjectUpdate,
        user: User,
    ) -> Project:
        """更新项目

        仅更新 project_data 中显式提供的字段（exclude_unset）。
        """
        result = await self.db.execute(
            select(Project)
            .options(selectinload(Project.owner))
            .where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()

        if not project:
            raise NotFoundError(f"项目不存在: id={project_id}")

        self._check_permission(project, user)

        update_data = project_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(project, field, value)

        await self.db.flush()
        await self.db.refresh(project, ["owner"])

        logger.info(
            f"更新项目: id={project_id}, user={user.username}, fields={list(update_data.keys())}"
        )
        return project

    async def delete(self, project_id: int, user: User) -> dict[str, Any]:
        """删除项目 (强一致取消语义)

        流程 (不可重排, 每步都与"ARQ 删除一致性"相关):
          1. 权限校验 + 读出关联 ARQ job_id
          2. 写项目取消墓碑 (Redis), 让 worker 任何阶段能立即感知
          3. 尝试撤销 ARQ 队列中未消费的 job; 无法撤销的转为黑名单兜底
          4. 释放 project binding (阻止新 LLM 请求绑定到此项目)
          5. 取消 asyncio 本地任务, 等待协程终止 (超时放行)
          6. 清 review/collusion 缓存
          7. 删除项目实体 (级联 documents / vector_job_states 等)

        返回: 被删除项目 ID + 被取消的本地任务 + ARQ 任务撤销明细。
        """
        result = await self.db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()

        if not project:
            raise NotFoundError(f"项目不存在: id={project_id}")

        self._check_permission(project, user)

        project_name = project.name
        project_status = project.status.value
        project_category = project.project_type.value

        # --- 1. 预先读出所有可能在 ARQ 队列里的 job_id ---
        writing_cleanup = ProjectWritingTaskCleanup(self.db)
        writing_targets = await writing_cleanup.collect_targets(project_id)
        arq_job_ids = await self._collect_arq_job_ids(project_id)
        arq_job_ids = list(dict.fromkeys([*arq_job_ids, *writing_targets.arq_job_ids]))

        # --- 2. 写项目取消墓碑 (必须先写, 让 worker 立即具备退出条件) ---
        redis_pool = await get_redis_pool()
        await mark_project_cancelled_async(redis_pool, project_id)

        # --- 3. 尝试撤销 ARQ 队列任务 ---
        arq_cancel_results: list[dict[str, Any]] = []
        if redis_pool is not None and arq_job_ids:
            for job_id in arq_job_ids:
                try:
                    ok, method = await abort_arq_job_best_effort(redis_pool, job_id)
                    arq_cancel_results.append(
                        {"job_id": job_id, "aborted": ok, "method": method}
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        f"ARQ 任务撤销异常(忽略, 由墓碑兜底): job_id={job_id}, err={exc}"
                    )
                    arq_cancel_results.append(
                        {"job_id": job_id, "aborted": False, "method": "error"}
                    )
            logger.info(
                f"项目 {project_id} ARQ 任务撤销结果: {arq_cancel_results}"
            )

        # --- 4. 释放 project binding (必须在取消任务前做, 防止新 LLM 调用再绑) ---
        await release_project_binding(project_id)

        # --- 5. 取消 asyncio 本地任务 ---
        cancelled_tasks: list[str] = []
        tasks_to_await: list[asyncio.Task] = []

        for prefix in (
            "review_project_",
            "review_bid_extract_",
            "extract_project_",
            "collusion_project_",
            "writing_extract_",
            "writing_outline_",
            "writing_material_process_",
            "writing_match_",
            "writing_compose_",
            "writing_compose_single_",
            "writing_clause_vectorize_",
        ):
            task_name = f"{prefix}{project_id}"
            task_obj = get_task(task_name)
            if cancel_task(task_name):
                cancelled_tasks.append(task_name)
                if task_obj is not None and not task_obj.done():
                    tasks_to_await.append(task_obj)

        writing_cancel = writing_cleanup.cancel_local_tasks(writing_targets)
        cancelled_tasks.extend(writing_cancel.cancelled_names)
        tasks_to_await.extend(writing_cancel.tasks_to_await)

        doc_result = await self.db.execute(
            select(Document.id).where(Document.project_id == project_id)
        )
        for (doc_id,) in doc_result.fetchall():
            task_name = f"process_doc_{doc_id}"
            task_obj = get_task(task_name)
            if cancel_task(task_name):
                cancelled_tasks.append(task_name)
                if task_obj is not None and not task_obj.done():
                    tasks_to_await.append(task_obj)

        if cancelled_tasks:
            logger.info(f"已取消项目 {project_id} 的本地后台任务: {cancelled_tasks}")

        if tasks_to_await:
            logger.info(
                f"项目 {project_id}: 等待 {len(tasks_to_await)} 个本地任务终止..."
            )
            _done, pending = await asyncio.wait(
                tasks_to_await, timeout=15
            )
            if pending:
                logger.warning(
                    f"项目 {project_id}: {len(pending)} 个任务未在超时内终止，继续删除 "
                    "(已写墓碑, worker 会在下一个检查点自停)"
                )

        # --- 6. 清缓存 ---
        clear_review_cache(project_id)
        clear_collusion_cache(project_id)

        # --- 6.5 预占释放(P1/H6): 删除项目前先释放 HOLDING 预占,
        # 避免 hold.project_id 因 ondelete SET NULL 变 NULL 后无法定位。
        # refund_trial=False: 用户主动删除不返还免费试用(闭合"删除项目恢复试用"绕过),
        # 付费 HOLDING 预占仍照常释放。
        try:
            from app.billing.services.billing_guard import BillingGuard
            await BillingGuard.on_failed(self.db, project, refund_trial=False)
            await self.db.flush()
        except Exception as billing_err:
            logger.warning(
                f"删除项目时释放预占异常(不影响删除): "
                f"project={project_id}, err={billing_err}"
            )

        # --- 6.6 回填日志快照: 删除前把项目名/属主写入三表日志,
        # 保证已删项目仍可在运维监控按名追溯 (迁移 b9d4e1f6a2c8) ---
        try:
            owner_username = (
                await self.db.scalar(
                    select(User.username).where(User.id == project.owner_id)
                )
                if project.owner_id
                else None
            )
            await backfill_project_log_snapshot(
                self.db,
                project_id=project_id,
                project_name=project_name,
                owner_username=owner_username,
            )
            await self.db.flush()
        except Exception as snap_err:  # noqa: BLE001
            logger.warning(
                f"删除项目时回填日志快照异常(不影响删除): "
                f"project={project_id}, err={snap_err}"
            )

        # --- 7. 删除项目实体 ---
        # 避免提取/审查会话持锁时级联删无限等待；超时后由上层返回错误，
        # 墓碑已写入，worker 会在下一检查点自停，用户可重试删除。
        from sqlalchemy import text

        await self.db.execute(text("SET LOCAL lock_timeout = '15s'"))
        await self.db.delete(project)
        await self.db.flush()

        logger.info(
            f"删除项目: id={project_id}, name={project_name}, "
            f"status={project_status}, user={user.username}, "
            f"cancelled_local_tasks={len(cancelled_tasks)}, "
            f"arq_jobs_cancelled={sum(1 for r in arq_cancel_results if r.get('aborted'))}/{len(arq_cancel_results)}"
        )
        await audit_log(
            self.db, user_id=user.id, username=user.username,
            category=project_category,
            action="delete", resource_type="project", resource_id=project_id,
            detail=f"删除项目: {project_name}",
        )

        return {
            "deleted_project_id": project_id,
            "cancelled_tasks": cancelled_tasks,
            "arq_jobs_cancelled": arq_cancel_results,
        }

    async def _collect_arq_job_ids(self, project_id: int) -> list[str]:
        """收集项目关联的所有 ARQ job_id。

 来源:
 1. vector_job_states（文档处理 process_doc_*）
 2. 任务心跳 arq_job_id（extract/review/collusion/writing 等）

 心跳覆盖解决: 删除时 abort 清单漏掉活跃 extract_requirements_job。
 """
        job_ids: list[str] = []
        seen: set[str] = set()

        def _add(jid: str | None) -> None:
            if not jid or jid == "arq-job-unknown" or jid in seen:
                return
            seen.add(jid)
            job_ids.append(jid)

        try:
            result = await self.db.execute(
                select(VectorJobState.arq_job_id).where(
                    VectorJobState.project_id == project_id,
                    VectorJobState.arq_job_id.is_not(None),
                )
            )
            for (jid,) in result.fetchall():
                _add(jid)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"读取项目 {project_id} 的 arq_job_id 失败: {exc}")

        try:
            from app.core.tasks import get_task_heartbeat

            for prefix in (
                "extract_project_",
                "review_project_",
                "review_bid_extract_",
                "collusion_project_",
                "writing_extract_",
                "writing_outline_",
                "writing_material_process_",
                "writing_match_",
                "writing_compose_",
                "writing_compose_single_",
                "writing_clause_vectorize_",
                "process_doc_",
            ):
                if prefix == "process_doc_":
                    continue
                hb = get_task_heartbeat(f"{prefix}{project_id}")
                if isinstance(hb, dict):
                    _add(hb.get("arq_job_id"))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"从心跳收集项目 {project_id} 的 arq_job_id 失败: {exc}")

        return job_ids

    async def batch_delete(
        self, project_ids: list[int], user: User
    ) -> dict[str, Any]:
        """批量删除项目

        逐一执行权限校验和删除，单个失败不影响其他项目。
        """
        deleted: list[int] = []
        failed: list[dict[str, Any]] = []

        for pid in project_ids:
            try:
                await self.delete(pid, user=user)
                deleted.append(pid)
            except (NotFoundError, ForbiddenError, ServiceError) as e:
                failed.append({"id": pid, "reason": str(e)})

        logger.info(
            f"批量删除: user={user.username}, "
            f"deleted={len(deleted)}, failed={len(failed)}"
        )
        return {"deleted": deleted, "failed": failed}

    def _check_permission(
        self,
        project: Project,
        user: User,
        *,
        require_owner: bool = False,
    ) -> None:
        """校验用户对项目的操作权限

        管理员默认放行（除非 require_owner=True）；
        非管理员必须是项目所有者，否则抛出 ForbiddenError。
        """
        if user.role == UserRole.ADMIN and not require_owner:
            return

        if project.owner_id != user.id:
            raise ForbiddenError("无权限访问此项目")
