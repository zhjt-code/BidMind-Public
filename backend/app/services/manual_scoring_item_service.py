"""手动补录评分项服务。"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.core.review_contracts import serialize_json_list
from app.models.document import Document, DocumentType
from app.models.project import Project
from app.models.requirement import Requirement, RequirementCategory
from app.models.user import User
from app.schemas.requirement import ManualScoringItemCreate
from app.services.audit_service import audit_log
from app.services.project_access import get_project_or_raise


def build_manual_scoring_requirement_attrs(
    *,
    project_id: int,
    document_id: int,
    data: ManualScoringItemCreate,
) -> dict[str, Any]:
    """构造手动评分项落库字段。

    职责: 将人工输入转换为 Requirement 评分项全集所需字段。
    不做: 不访问数据库，不校验项目权限，不推断 AI 评分结构。
    """
    source_pages = sorted({int(page) for page in data.source_pages if int(page) >= 1})
    scoring_metadata: dict[str, Any] = {
        "source": "manual",
        "max_score": float(data.score_weight),
        "score_item_name": data.score_item_name.strip(),
        "score_rules": [item.strip() for item in data.score_rules if item.strip()],
        "deduction_rules": [item.strip() for item in data.deduction_rules if item.strip()],
        # P3-1: 人工补录的分类为人工指定, 不落未分类桶
        "category_basis": "manual",
    }
    for key, value in {
        "volume": data.volume,
        "volume_label": data.volume_label,
        "lot": data.lot,
    }.items():
        if isinstance(value, str) and value.strip():
            scoring_metadata[key] = value.strip()

    return {
        "project_id": project_id,
        "document_id": document_id,
        "category": RequirementCategory[data.category],
        "description": data.description.strip(),
        "is_mandatory": 0,
        "source_page": source_pages[0] if source_pages else None,
        "source_pages": serialize_json_list(source_pages),
        "keywords": serialize_json_list([data.score_item_name.strip()]),
        "sub_requirements": serialize_json_list([]),
        "requirement_kind": "scoring_item",
        "score_weight": float(data.score_weight),
        "is_key_review": 1,
        "scoring_metadata": scoring_metadata,
        "review_relevance": "actionable",
        "confirmed": 0,
    }


class ManualScoringItemService:
    """手动评分项服务。

    职责: 在招标要求确认阶段创建人工补录评分项。
    不做: 不触发重新提取、不启动审查、不修改既有评分项。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _get_project_or_raise(
        self,
        project_id: int,
        user: Optional[User] = None,
    ) -> Project:
        """获取项目并校验权限（委托 project_access 共享实现，S-07）"""
        return await get_project_or_raise(self.db, project_id, user)

    async def _get_tender_doc(self, project_id: int) -> Optional[Document]:
        result = await self.db.execute(
            select(Document).where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.TENDER,
            )
        )
        return result.scalar_one_or_none()

    async def create_manual_scoring_item(
        self,
        project_id: int,
        data: ManualScoringItemCreate,
        user: User,
    ) -> Requirement:
        """创建一条人工补录评分项。"""
        await self._get_project_or_raise(project_id, user)
        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            raise ServiceError("项目没有招标文档，请先上传招标文件")

        requirement = Requirement(
            **build_manual_scoring_requirement_attrs(
                project_id=project_id,
                document_id=tender_doc.id,
                data=data,
            )
        )
        self.db.add(requirement)
        await self.db.flush()
        await self.db.refresh(requirement)

        logger.info(
            f"手动补录评分项: id={requirement.id}, project_id={project_id}, score={requirement.score_weight}"
        )
        await audit_log(
            self.db,
            user_id=user.id,
            username=user.username,
            category="bid_review",
            action="create",
            resource_type="manual_scoring_item",
            resource_id=requirement.id,
            detail=f"补录评分项: {data.score_item_name}",
        )
        return requirement
