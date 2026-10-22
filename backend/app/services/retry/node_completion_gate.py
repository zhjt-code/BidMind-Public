"""节点完成事实门控。

职责: 基于持久化事实判定写标节点是否已完成或从未执行
不做: 不判 retryable 终值、不写库、不读心跳、不感知 HTTP

背景: `get_generic_retry_state` 曾把「心跳缺失/过期」直接当作节点
可重试，早已成功的招标解析节点在心跳 TTL 过期后被标成失败点，还吸附了
项目级失败文案（"writing 任务异常终止"），前端失败入口因此停在解析页。
本模块提供节点级完成事实，让失败判定只落在真实失败节点上:
 - 招标解析节点: `documents.parsed == 1` 即已完成（不依赖 TaskRun，兼容
 首轮无 TaskRun 与自动恢复不建 TaskRun 的路径）
 - 其余节点: 最近一次 TaskRun `status == completed` 即已完成
 - 从未执行: 无 TaskRun 且心跳 missing（非招标解析节点），无可重试对象
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.task_run import TaskRunStatus


@dataclass(frozen=True)
class NodeCompletionVerdict:
    """节点完成事实判定结果。"""

    completed: bool = False
    never_attempted: bool = False

    @property
    def suppresses_failure(self) -> bool:
        """已完成或从未执行的节点不得被标为失败点、不得吸附失败文案。"""
        return self.completed or self.never_attempted


async def resolve_node_completion(
    db: AsyncSession,
    *,
    is_tender_process: bool,
    target_id: Optional[int],
    latest_run: Optional[Any],
    liveness_state: str,
) -> NodeCompletionVerdict:
    """按持久化事实判定节点完成状态。

    Args:
        is_tender_process: 是否为招标文件解析节点（走文档事实而非 TaskRun）
        target_id: 招标解析节点的 document_id；其余节点可为 None
        latest_run: 该节点最近一次 TaskRun（可为 None）
        liveness_state: `inspect_task_liveness` 的 state（missing/stale/live/...）
    """
    if is_tender_process:
        if target_id is None:
            return NodeCompletionVerdict()
        doc = await db.get(Document, target_id)
        parsed = int(getattr(doc, "parsed", 0) or 0) if doc is not None else 0
        return NodeCompletionVerdict(completed=(parsed == 1))

    if latest_run is not None:
        return NodeCompletionVerdict(
            completed=(str(latest_run.status) == TaskRunStatus.completed.value)
        )

    # 无 TaskRun 且心跳彻底消失: 该节点从未被调度过（写标各节点触发时均
    # begin_attempt 建 TaskRun），没有可重试的失败对象
    return NodeCompletionVerdict(never_attempted=(liveness_state == "missing"))
