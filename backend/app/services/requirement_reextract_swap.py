"""重新提取预清模块。

职责: 点击重新提取时清空该项目全部已提取清单与下游审查产物。
不做: 不分发后台任务、不修改项目状态、不执行 LLM 推理、不删文档/分块/向量。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bid_response import BidResponse
from app.models.requirement import Requirement
from app.models.review_fact import ReviewFact
from app.models.review_result import ReviewResult


async def count_existing_outputs(
    project_id: int,
    db: AsyncSession,
) -> dict[str, int]:
    """统计项目现有提取/审查产物数量，供 audit log 使用。

    职责: 只读查询，返回各表行数；不修改任何数据。
    不做: 不执行删除、不修改项目状态。
    """
    requirement_ids = select(Requirement.id).where(Requirement.project_id == project_id)
    counts = {
        "requirements": await db.scalar(
            select(func.count(Requirement.id)).where(Requirement.project_id == project_id)
        ),
        "bid_responses": await db.scalar(
            select(func.count(BidResponse.id)).where(
                BidResponse.requirement_id.in_(requirement_ids)
            )
        ),
        "review_results": await db.scalar(
            select(func.count(ReviewResult.id)).where(
                ReviewResult.project_id == project_id
            )
        ),
        "review_facts": await db.scalar(
            select(func.count(ReviewFact.id)).where(ReviewFact.project_id == project_id)
        ),
    }
    return {key: int(value or 0) for key, value in counts.items()}


async def clear_extracted_artifacts(
    project_id: int,
    db: AsyncSession,
) -> None:
    """清空重提取前的全部已提取条目与下游审查产物。

    职责: 删除 ReviewFact/ReviewResult/BidResponse，以及该项目全部 Requirement
          （含 scoring_item、已确认项、手工补录项）。
    不做: 不提交事务（由调用方统一 commit）；不删文档解析产物。
    """
    requirement_ids = select(Requirement.id).where(Requirement.project_id == project_id)

    await db.execute(delete(ReviewFact).where(ReviewFact.project_id == project_id))
    await db.execute(delete(ReviewResult).where(ReviewResult.project_id == project_id))
    await db.execute(
        delete(BidResponse).where(BidResponse.requirement_id.in_(requirement_ids))
    )
    await db.execute(
        delete(Requirement).where(Requirement.project_id == project_id)
    )
