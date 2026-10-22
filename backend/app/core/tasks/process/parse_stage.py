"""文档处理阶段1: PDF 解析

职责:
1. 启动 60s 心跳保活任务(防 心跳超时误恢复)
2. 进程池调用 parse_pdf_in_process(绕过 GIL), 并施加整体硬超时
3. 防御: 命中 BrokenProcessPool 重建进程池后重试一次
4. 超时熔断: 解析超过 PARSE_STAGE_TIMEOUT_SEC 时终止失控子进程并抛可读错误
5. Session 2: 保存 page_count

返回 parsed_doc(含 pages / all_images / page_count) + image_count。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Callable

from loguru import logger


async def run_parse_stage(
    *,
    file_path: str,
    image_save_dir: Any,
    document_id: int,
    heartbeat: Callable[..., None],
) -> tuple[Any, int]:
    """执行 PDF 解析(进程池) + Session 2 保存 page_count.

    Returns:
        (parsed_doc, image_count)
    """
    # 函数内 import: 兼容外部测试 monkeypatch process_task 模块属性
    from app.core.config import settings
    from app.core.parser.parse_worker import parse_pdf_in_process
    from app.core.tasks import process_task as ptm

    logger.info("[文档处理] 阶段1: PDF解析")
    heartbeat("parsing", "0/1")

    _keepalive_stop = asyncio.Event()

    async def _heartbeat_keepalive():
        while not _keepalive_stop.is_set():
            heartbeat("parsing", "0/1")
            try:
                await asyncio.wait_for(_keepalive_stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    keepalive_task = asyncio.create_task(_heartbeat_keepalive())

    loop = asyncio.get_running_loop()
    # 解析跑在独立子进程且无法回传页级进度, 心跳保活又让看门狗永不恢复,
    # 此硬超时是“永久卡死/超慢大文件”唯一的兜底。下限 60s 防误配过小。
    timeout_sec = max(60, int(getattr(settings, "PARSE_STAGE_TIMEOUT_SEC", 1800)))

    async def _parse_with_pool() -> Any:
        try:
            return await loop.run_in_executor(
                ptm._get_parse_executor(),
                parse_pdf_in_process,
                file_path,
                str(image_save_dir),
            )
        except concurrent.futures.process.BrokenProcessPool:
            # 防御: 进程池崩溃后重建并重试一次, 避免无限重试
            logger.warning("[文档处理] 进程池损坏, 重建后重试一次")
            ptm._reset_parse_executor()
            return await loop.run_in_executor(
                ptm._get_parse_executor(),
                parse_pdf_in_process,
                file_path,
                str(image_save_dir),
            )

    try:
        try:
            parsed_doc = await asyncio.wait_for(_parse_with_pool(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            # 终止失控子进程(shutdown(wait=False) 不会杀运行中任务), 再抛可读错误,
            # 交由上层 handle_failure 写 FAILED + system_error_logs, 时间线即可见红色终态。
            logger.error(
                f"[文档处理] PDF解析超时(>{timeout_sec}s), 终止解析子进程: {file_path}"
            )
            ptm._reset_parse_executor()
            raise TimeoutError(
                f"PDF解析超时(超过{timeout_sec}秒)，文件可能过大、为扫描件或已损坏，请检查后重试"
            )
    finally:
        _keepalive_stop.set()
        await keepalive_task

    # ===== 完整性闸门(缺口D): 损坏/空壳文档 fail-visible, 禁止流成"0项假成功" =====
    from app.core.parser.pdf_integrity import (
        assess_parsed_document_integrity,
        normalized_content_hash,
    )

    integrity = assess_parsed_document_integrity(parsed_doc)
    if not integrity.usable:
        logger.error(
            "[文档处理] 解析产物完整性检查未通过: document_id={} pages={} chars={} "
            "tables={} images={} reason={}",
            document_id,
            integrity.page_count,
            integrity.char_count,
            integrity.table_count,
            integrity.image_count,
            integrity.reason,
        )
        raise ValueError(f"文档不可用: {integrity.reason}，请更换文件后重试")

    # ===== Session 2: 保存解析结果 (秒级) =====
    content_hash = normalized_content_hash(parsed_doc)
    async with ptm.async_session_factory() as db:
        document = await ptm._get_document(db, document_id)
        if document:
            document.page_count = parsed_doc.page_count
            document.content_hash = content_hash
            await db.commit()
    heartbeat("parsing", "1/1")

    image_count = len(parsed_doc.all_images)
    logger.info(
        f"[文档处理] PDF解析完成: {parsed_doc.page_count}页, "
        f"{image_count}张图片"
    )

    return parsed_doc, image_count
