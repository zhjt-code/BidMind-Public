"""
职责: 处理任务解析前的格式归一化（Word→PDF 并更新 Document.file_path）
不做: 解析、向量化、失败状态机
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from loguru import logger

from app.core.convert.office_to_pdf import OFFICE_SUFFIXES, convert_office_to_pdf
from app.core.observability.task_observation import TaskFlow, record_stage


async def run_convert_stage(
    *,
    file_path: str,
    document_id: int,
    heartbeat: Callable[..., None],
) -> str:
    """Office 文件转为 PDF 后回写 file_path；PDF 原件直通。"""
    suffix = Path(file_path).suffix.lower()
    if suffix not in OFFICE_SUFFIXES:
        return file_path

    from app.core.config import settings
    from app.core.tasks import process_task as ptm

    logger.info("[文档处理] 格式归一化: Office → PDF")
    heartbeat("parsing", "0/1")

    dest = await convert_office_to_pdf(
        file_path,
        timeout_sec=int(settings.OFFICE_CONVERT_TIMEOUT_SEC),
    )

    project_id: int | None = None
    async with ptm.async_session_factory() as db:
        document = await ptm._get_document(db, document_id)
        if document:
            document.file_path = str(dest)
            project_id = document.project_id
            await db.commit()

    await record_stage(
        flow=TaskFlow.PROCESS,
        task_type="process",
        project_id=project_id,
        stage="office_convert",
        labels={"document_id": document_id},
    )
    logger.info("[文档处理] 格式归一化完成: {}", dest.name)
    return str(dest)
