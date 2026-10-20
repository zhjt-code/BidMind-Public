"""项目里程碑事件服务 (阶段三)。

提供 ``record_milestone`` 幂等写入函数, 供 task 装饰器或显式调用使用。

关键约束:
- 写失败静默, 不阻塞主流程 (与 task_observation_service 一致)
- 自动从 logger 上下文读取 incident_id, 形成与错误日志的关联
- 调用方传入 db 时, 加入该 session (事务由调用方控制); 否则自建独立 session
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.project_milestone_event import ProjectMilestoneEvent


async def record_milestone(
    *,
    project_id: int,
    event_key: str,
    summary: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    task_type: Optional[str] = None,
    incident_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    db: Optional[AsyncSession] = None,
) -> None:
    """记录一条项目里程碑事件。

    Args:
        project_id: 项目 ID, 必填 (无 project_id 的事件不应进入本表)
        event_key: 事件键, 约定 ``{阶段}.{结果}`` 形式, 如 ``extract.completed``
        summary: 一句话摘要, 例如 "成功提取 142 条招标要求"
        payload: 附加结构化数据, 序列化为 JSON 后写入 payload_json
        task_type: 业务任务类型, 与 system_error_logs.task_type 对齐
        incident_id: 链路 ID, 与错误日志关联; 未传时自动从 logger 上下文读取
        occurred_at: 事件发生时间, 默认 ``datetime.now(timezone.utc)``
        db: 可选 session, 传入时由调用方负责事务

    永不抛出异常, 失败仅 warning。
    """
    try:
        # 自动从 logger 上下文读取 incident_id (支持装饰器内透明传递)
        if incident_id is None:
            try:
                # loguru 的 contextualize 数据通过 logger.bind() 时不一定在 record extra 里
                # 这里直接从模块级 ContextVar 读取, 与 log_sink 行为一致
                from app.core.middleware import get_incident_id
                ctx_iid = get_incident_id()
                if ctx_iid:
                    incident_id = str(ctx_iid)[:32]
            except Exception:
                incident_id = None

        payload_text: Optional[str] = None
        if payload:
            try:
                payload_text = json.dumps(payload, ensure_ascii=False, default=str)[:4000]
            except Exception:
                payload_text = None

        record = ProjectMilestoneEvent(
            project_id=int(project_id),
            incident_id=incident_id,
            task_type=(task_type or "")[:50] or None,
            event_key=event_key[:80],
            summary=(summary or "")[:500] or None,
            payload_json=payload_text,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )

        if db is not None:
            db.add(record)
            return

        async with AsyncSessionLocal() as session:
            session.add(record)
            await session.commit()
    except Exception as e:  # 绝不抛出
        logger.warning(f"record_milestone 失败 (忽略): event_key={event_key}, err={e}")
