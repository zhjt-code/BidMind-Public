"""强制重新提取招标要求服务。"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ServiceError
from app.core.tasks import dispatch_background_job, is_task_active
from app.core.tasks.extract_task import get_extract_task_name, run_extract_task
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus, ProjectType
from app.models.user import User, UserRole
from app.services.audit_service import audit_log
from app.services.requirement_reextract_swap import (
    clear_extracted_artifacts,
    count_existing_outputs,
)


class RequirementReextractService:
    """
    职责: 从已解析文档重新生成投标审查的招标要求确认清单。
    不做: 不重新解析文档、不执行投标响应提取、不执行 AI 对比审查。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def reextract_from_parsed_documents(
        self,
        project_id: int,
        current_user: User,
    ) -> dict[str, Any]:
        """清空全部已提取清单后，重新入队招标要求提取任务。

 ：点击重新提取必须预清全部 Requirement（含评分项、已确认项、
 手工补录），避免新旧清单叠加。提取失败时确认页为空，由用户再次发起。
 """
        project = await self._get_bid_review_project(project_id)
        if current_user.role != UserRole.ADMIN and project.owner_id != current_user.id:
            raise ForbiddenError("无权限重新提取此项目的招标要求")
        await self._ensure_ready_for_reextract(project_id)

        # 重新提取同样是重 LLM 动作，预检必须先于清理旧产物执行，
        # 余额不足时既不扣费也不破坏用户已确认的清单。
        from app.billing.services.balance_gate_error import raise_insufficient_balance
        from app.billing.services.billing_guard import BillingGuard
        from app.billing.services.wallet_service import InsufficientBalanceError
        try:
            await BillingGuard.pre_check(self.db, project, project.owner_id)
        except InsufficientBalanceError as balance_err:
            raise_insufficient_balance(balance_err)

        counts = await count_existing_outputs(project_id, self.db)
        await clear_extracted_artifacts(project_id, self.db)

        project.status = ProjectStatus.EXTRACTING
        project.error_message = None
        project.extraction_warnings = None
        await audit_log(
            self.db,
            user_id=current_user.id,
            username=current_user.username,
            category="bid_review",
            action="reextract",
            resource_type="requirement",
            resource_id=project_id,
            detail=(
                "重新提取招标要求: "
                f"requirements={counts['requirements']}, "
                f"bid_responses={counts['bid_responses']}, "
                f"review_results={counts['review_results']}, "
                f"review_facts={counts['review_facts']}"
            ),
        )
        await self.db.commit()

        task_name = get_extract_task_name(project_id)
        from app.core.tasks.extract_task import build_extract_dispatch_args

        task_name, generation, arq_args = build_extract_dispatch_args(project_id)
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="extract_requirements_job",
            fallback_factory=lambda: run_extract_task(
                project_id, generation=generation
            ),
            task_type="extract",
            project_id=project_id,
            arq_args=arq_args,
        )
        logger.info(
            "重新提取招标要求任务已启动: "
            f"project_id={project_id}, queue_mode={queue_mode}, arq_job_id={arq_job_id}"
        )
        return {
            "project_id": project_id,
            "status": "started",
            "queue_mode": queue_mode,
            "arq_job_id": arq_job_id,
            "cleared": counts,
            "run_id": generation.run_id,
            "revision": 0,
        }

    async def _get_bid_review_project(self, project_id: int) -> Project:
        project = await self.db.scalar(select(Project).where(Project.id == project_id))
        if project is None:
            raise NotFoundError(f"项目不存在: id={project_id}")
        if project.project_type != ProjectType.BID_REVIEW:
            raise ServiceError("仅投标审查项目支持重新提取招标要求", status_code=400)
        return project

    async def _ensure_ready_for_reextract(self, project_id: int) -> None:
        task_names = [
            get_extract_task_name(project_id),
            f"review_project_{project_id}",
        ]
        if any(is_task_active(task_name) for task_name in task_names):
            raise ServiceError("当前项目已有提取或审查任务运行中，请稍后再试", status_code=400)

        tender_doc = await self.db.scalar(
            select(Document).where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.TENDER,
                Document.parsed == 1,
            )
        )
        if tender_doc is None:
            raise ServiceError("项目没有已解析的招标文档，请先完成文档处理", status_code=400)

        bid_doc_count = await self.db.scalar(
            select(func.count(Document.id)).where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.BID,
                Document.parsed == 1,
            )
        )
        if not bid_doc_count:
            raise ServiceError("项目没有已解析的投标文档，请先完成文档处理", status_code=400)

