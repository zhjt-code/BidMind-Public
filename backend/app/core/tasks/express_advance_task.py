"""
职责: 执行一步到位推进的后台任务入口。
不做: 不实现确认/启动业务，不替代各阶段长任务。
"""

from __future__ import annotations

from loguru import logger

from app.core.database import AsyncSessionLocal
from app.services.express_flow.advance import advance_express_project
from app.services.express_flow.dispatch import get_express_advance_task_name


async def run_express_advance_task(project_id: int) -> None:
    async with AsyncSessionLocal() as db:
        try:
            result = await advance_express_project(db, project_id)
            await db.commit()
            logger.info(
                "express_advance 完成: project_id={}, result={}",
                project_id,
                result.get("action"),
            )
        except Exception:
            await db.rollback()
            logger.exception("express_advance 执行失败: project_id={}", project_id)
            raise


async def express_advance_job(ctx: dict, project_id: int) -> None:
    """ARQ 入口：按当前就绪态推进 express。"""
    _ = ctx
    await run_express_advance_task(project_id)


__all__ = [
    "express_advance_job",
    "get_express_advance_task_name",
    "run_express_advance_task",
]
