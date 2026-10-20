"""章节图示持久化仓储。

职责: 读写 writing_section_diagrams 的 ready/failed 记录。
不做: 不编译 IR、不渲染 PNG、不调用 LLM。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.writing_section_diagram import WritingSectionDiagram


class SectionDiagramRepository:
    """Async repository for section diagram records."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_plan_visual(
        self,
        section_plan_id: int,
        visual_id: str,
    ) -> WritingSectionDiagram | None:
        result = await self.db.execute(
            select(WritingSectionDiagram).where(
                WritingSectionDiagram.section_plan_id == section_plan_id,
                WritingSectionDiagram.visual_id == visual_id,
            )
        )
        return result.scalars().first()

    async def get_ready_by_project(
        self,
        project_id: int,
    ) -> dict[int, WritingSectionDiagram]:
        """项目内全部 ready 图示记录, 供 Word/预览渲染消费。"""

        result = await self.db.execute(
            select(WritingSectionDiagram).where(
                WritingSectionDiagram.project_id == project_id,
                WritingSectionDiagram.status == "ready",
            )
        )
        return {int(record.id): record for record in result.scalars().all()}

    async def get_by_project_diagram(
        self,
        project_id: int,
        diagram_id: int,
    ) -> WritingSectionDiagram | None:
        """按项目与图示ID读取单条图示记录。"""

        result = await self.db.execute(
            select(WritingSectionDiagram).where(
                WritingSectionDiagram.project_id == project_id,
                WritingSectionDiagram.id == diagram_id,
            )
        )
        return result.scalars().first()

    async def save_ready_diagram(
        self,
        *,
        project_id: int,
        outline_node_id: int,
        section_plan_id: int,
        visual_id: str,
        diagram_type: str,
        title: str,
        ir_json: dict[str, Any],
        png_path: str,
        content_sha256: str,
    ) -> int:
        record = await self.get_by_plan_visual(section_plan_id, visual_id)
        if record is None:
            record = WritingSectionDiagram(
                project_id=project_id,
                outline_node_id=outline_node_id,
                section_plan_id=section_plan_id,
                visual_id=visual_id,
                diagram_type=diagram_type,
                title=title,
            )
            self.db.add(record)
        record.diagram_type = diagram_type
        record.title = title
        record.ir_json = ir_json
        record.png_path = png_path
        record.content_sha256 = content_sha256
        record.status = "ready"
        record.failure_code = None
        await self.db.commit()
        await self.db.refresh(record)
        return int(record.id)

    async def save_failed_diagram(
        self,
        *,
        project_id: int,
        outline_node_id: int,
        section_plan_id: int,
        visual_id: str,
        diagram_type: str,
        title: str,
        failure_code: str,
    ) -> int:
        record = await self.get_by_plan_visual(section_plan_id, visual_id)
        if record is None:
            record = WritingSectionDiagram(
                project_id=project_id,
                outline_node_id=outline_node_id,
                section_plan_id=section_plan_id,
                visual_id=visual_id,
                diagram_type=diagram_type,
                title=title,
            )
            self.db.add(record)
        record.status = "failed"
        record.failure_code = failure_code
        await self.db.commit()
        await self.db.refresh(record)
        return int(record.id)


class SessionScopedSectionDiagramRepository:
    """
    职责: 每次调用开独立短 Session 执行章节图示持久化。
    不做: 不复用外部 Session、不参与调用方事务、不编译 IR、不渲染 PNG。
    """

    async def get_by_plan_visual(
        self,
        section_plan_id: int,
        visual_id: str,
    ) -> WritingSectionDiagram | None:
        async with async_session_factory() as db:
            record = await SectionDiagramRepository(db).get_by_plan_visual(
                section_plan_id,
                visual_id,
            )
            db.expunge_all()
            return record

    async def get_ready_by_project(
        self,
        project_id: int,
    ) -> dict[int, WritingSectionDiagram]:
        async with async_session_factory() as db:
            records = await SectionDiagramRepository(db).get_ready_by_project(
                project_id,
            )
            db.expunge_all()
            return records

    async def save_ready_diagram(
        self,
        *,
        project_id: int,
        outline_node_id: int,
        section_plan_id: int,
        visual_id: str,
        diagram_type: str,
        title: str,
        ir_json: dict[str, Any],
        png_path: str,
        content_sha256: str,
    ) -> int:
        async with async_session_factory() as db:
            diagram_id = await SectionDiagramRepository(db).save_ready_diagram(
                project_id=project_id,
                outline_node_id=outline_node_id,
                section_plan_id=section_plan_id,
                visual_id=visual_id,
                diagram_type=diagram_type,
                title=title,
                ir_json=ir_json,
                png_path=png_path,
                content_sha256=content_sha256,
            )
            db.expunge_all()
            return diagram_id

    async def save_failed_diagram(
        self,
        *,
        project_id: int,
        outline_node_id: int,
        section_plan_id: int,
        visual_id: str,
        diagram_type: str,
        title: str,
        failure_code: str,
    ) -> int:
        async with async_session_factory() as db:
            diagram_id = await SectionDiagramRepository(db).save_failed_diagram(
                project_id=project_id,
                outline_node_id=outline_node_id,
                section_plan_id=section_plan_id,
                visual_id=visual_id,
                diagram_type=diagram_type,
                title=title,
                failure_code=failure_code,
            )
            db.expunge_all()
            return diagram_id


__all__ = ["SectionDiagramRepository", "SessionScopedSectionDiagramRepository"]
