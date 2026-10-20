"""SectionPlan persistence repository.

职责: 读写 writing_section_plans 的 ready/failed 版本记录。
不做: 不调用 LLM、不构建 Prompt、不生成章节正文。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.writer.section_plan.contracts import (
    SectionPlanRequest,
    SectionPlanResolution,
)
from app.models.writing_section_plan import WritingSectionPlan


class SectionPlanRepository:
    """Async repository for SectionPlan records."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_ready_by_fingerprint(
        self,
        outline_node_id: int,
        input_fingerprint: str,
    ) -> SectionPlanResolution | None:
        result = await self.db.execute(
            select(WritingSectionPlan).where(
                WritingSectionPlan.outline_node_id == outline_node_id,
                WritingSectionPlan.input_fingerprint == input_fingerprint,
                WritingSectionPlan.status == "ready",
            )
        )
        plan = result.scalars().first()
        if plan is None:
            return None
        return SectionPlanResolution(
            mode="planned_reused",
            section_plan_id=plan.id,
            plan=plan.plan_payload,
        )

    async def _next_version(self, outline_node_id: int) -> int:
        result = await self.db.execute(
            select(func.max(WritingSectionPlan.version)).where(
                WritingSectionPlan.outline_node_id == outline_node_id,
            )
        )
        return int(result.scalar() or 0) + 1

    async def save_ready_plan(
        self,
        request: SectionPlanRequest,
        plan_payload: dict[str, Any],
    ) -> int:
        version = await self._next_version(request.outline_node_id)
        await self.db.execute(
            update(WritingSectionPlan)
            .where(
                WritingSectionPlan.outline_node_id == request.outline_node_id,
                WritingSectionPlan.is_current == 1,
            )
            .values(is_current=0)
        )
        record = WritingSectionPlan(
            project_id=request.project_id,
            outline_node_id=request.outline_node_id,
            version=version,
            schema_version="section_plan.v1",
            input_fingerprint=request.input_fingerprint,
            input_manifest={
                "constraint_ids": [item.clause_id for item in request.constraints],
                "evidence_keys": [item.evidence_key for item in request.evidence_catalog],
            },
            prompt_snapshot=request.prompt_snapshot.model_dump(mode="json"),
            plan_payload=plan_payload,
            status="ready",
            is_current=1,
            attempt_count=1,
            completed_at=datetime.now(timezone.utc),
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return int(record.id)

    async def save_failed_plan(
        self,
        request: SectionPlanRequest,
        failure_code: str,
        failure_reason: str,
    ) -> int:
        version = await self._next_version(request.outline_node_id)
        record = WritingSectionPlan(
            project_id=request.project_id,
            outline_node_id=request.outline_node_id,
            version=version,
            schema_version="section_plan.v1",
            input_fingerprint=request.input_fingerprint,
            input_manifest={
                "constraint_ids": [item.clause_id for item in request.constraints],
                "evidence_keys": [item.evidence_key for item in request.evidence_catalog],
            },
            prompt_snapshot=request.prompt_snapshot.model_dump(mode="json"),
            plan_payload=None,
            status="failed",
            is_current=0,
            attempt_count=1,
            failure_code=failure_code,
            failure_reason=failure_reason,
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return int(record.id)


class SessionScopedSectionPlanRepository:
    """
    职责: 每次调用开独立短 Session 执行 SectionPlan 持久化。
    不做: 不复用外部 Session、不参与调用方事务、不调用 LLM。
    """

    async def get_ready_by_fingerprint(
        self,
        outline_node_id: int,
        input_fingerprint: str,
    ) -> SectionPlanResolution | None:
        async with async_session_factory() as db:
            result = await SectionPlanRepository(db).get_ready_by_fingerprint(
                outline_node_id,
                input_fingerprint,
            )
            db.expunge_all()
            return result

    async def save_ready_plan(
        self,
        request: SectionPlanRequest,
        plan_payload: dict[str, Any],
    ) -> int:
        async with async_session_factory() as db:
            plan_id = await SectionPlanRepository(db).save_ready_plan(
                request,
                plan_payload,
            )
            db.expunge_all()
            return plan_id

    async def save_failed_plan(
        self,
        request: SectionPlanRequest,
        failure_code: str,
        failure_reason: str,
    ) -> int:
        async with async_session_factory() as db:
            plan_id = await SectionPlanRepository(db).save_failed_plan(
                request,
                failure_code,
                failure_reason,
            )
            db.expunge_all()
            return plan_id


__all__ = ["SectionPlanRepository", "SessionScopedSectionPlanRepository"]
