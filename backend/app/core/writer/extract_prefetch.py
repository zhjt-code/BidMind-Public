"""
职责: 启动/等待/取消写标提取期的评分收集与目录规格预取，各分支独立开短会话。
不做: 不改阶段顺序、不改进度权重、不落库评分替换、不落库目录规格。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger


@dataclass
class ExtractPrefetchTasks:
    """提取预取任务句柄。"""

    scoring: Optional[asyncio.Task] = None
    directory: Optional[asyncio.Task] = None


def is_extract_prefetch_enabled() -> bool:
    """读取写标提取预取开关。"""
    from app.core.config import settings

    return bool(getattr(settings, "WRITING_EXTRACT_PREFETCH_ENABLED", False))


def start_extract_prefetch(
    project_id: int,
    document_id: int,
) -> ExtractPrefetchTasks:
    """
    职责: 在独立会话中启动评分收集与目录规格预取。
    不做: 不 await 完成、不落库权威替换或目录规格。
    """
    from app.core.config import settings

    if not is_extract_prefetch_enabled():
        return ExtractPrefetchTasks()

    scoring_task: Optional[asyncio.Task] = None
    if bool(getattr(settings, "WRITING_SCORING_AUTHORITY_ENABLED", True)):
        scoring_task = asyncio.create_task(
            _prefetch_scoring_outcome(project_id, document_id),
            name=f"extract-prefetch-scoring:{project_id}",
        )
    directory_task = asyncio.create_task(
        _prefetch_directory_spec(project_id, document_id),
        name=f"extract-prefetch-directory:{project_id}",
    )
    logger.info(
        "写标提取预取已启动: project={}, scoring={}, directory={}",
        project_id,
        scoring_task is not None,
        True,
    )
    return ExtractPrefetchTasks(scoring=scoring_task, directory=directory_task)


async def _prefetch_scoring_outcome(project_id: int, document_id: int) -> Any:
    from app.core.database import async_session_factory
    from app.core.writer.scoring_source import collect_scoring_outcome

    async with async_session_factory() as db:
        return await collect_scoring_outcome(db, project_id, document_id)


async def _prefetch_directory_spec(project_id: int, document_id: int) -> Any:
    from app.core.ai import get_review_client
    from app.core.database import async_session_factory
    from app.core.writer.bid_directory_extractor import collect_directory_spec

    async with async_session_factory() as db:
        llm_client = get_review_client(project_id=project_id)
        return await collect_directory_spec(
            db=db,
            llm_client=llm_client,
            project_id=project_id,
            document_id=document_id,
            progress_callback=None,
        )


async def await_scoring_prefetch(
    tasks: Optional[ExtractPrefetchTasks],
) -> Any:
    """
    职责: 等待评分收集预取；失败返回 None 供主流程回退内联。
    不做: 不落库、不重试。
    """
    if tasks is None or tasks.scoring is None:
        return None
    try:
        return await tasks.scoring
    except Exception as exc:
        logger.warning("写标评分预取失败(回退内联): err={!r}", exc)
        return None


async def await_directory_prefetch(
    tasks: Optional[ExtractPrefetchTasks],
) -> Any:
    """
    职责: 等待目录规格预取；失败返回 None 供主流程回退内联。
    不做: 不落库、不重试。
    """
    if tasks is None or tasks.directory is None:
        return None
    try:
        return await tasks.directory
    except Exception as exc:
        logger.warning("写标目录规格预取失败(回退内联): err={!r}", exc)
        return None


async def cancel_extract_prefetch(tasks: Optional[ExtractPrefetchTasks]) -> None:
    """
    职责: 取消尚未完成的提取预取任务。
    不做: 不回滚已落库数据（预取本身不落库）。
    """
    if tasks is None:
        return
    pending = [
        task
        for task in (tasks.scoring, tasks.directory)
        if task is not None and not task.done()
    ]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
