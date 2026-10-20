"""节点失败原因解析。

职责: 按固定优先级从持久化真源解析节点失败原因
不做: 不判定 retryable / 不写库 / 不做用户态文案格式化 / 不感知 HTTP

背景: 写标节点的失败原因原本只认 `TaskRun.last_error` 与运行时 heartbeat。
前者在"首轮运行未建 TaskRun"的节点上不存在, 后者在 asyncio 模式下任务结束即被
`cleanup_task` 清除、ARQ 模式下 1 小时过期, 导致 retry-state 返回 null,
前端只能显示"系统未返回具体原因"。真实原因其实已落在 `vector_job_states.last_error`
与 `projects.error_message`, 本模块把这两个持久化真源补进兜底链。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project, ProjectStatus
from app.models.vector_job_state import VectorJobState, VectorJobStatus

# 与 TaskRun.last_error / heartbeat 摘要保持同一截断长度, 避免同一错误在不同来源下长短不一
MAX_REASON_LENGTH = 500

# 仅这些状态下的 VectorJobState.last_error 才代表"当前失败原因":
# `upsert_vector_job_state` 只在显式传值时覆盖 last_error, 成功重跑不会清空历史值,
# 不做状态门控会把上一轮的陈旧错误显示到已恢复的文档上。
_FAILED_VECTOR_JOB_STATES = frozenset({VectorJobStatus.FAILED, VectorJobStatus.LOST})


@dataclass(frozen=True)
class PersistedFailureSources:
    """节点失败原因的持久化来源快照（按优先级从高到低排列）。"""

    vector_job_error: Optional[str] = None
    project_error: Optional[str] = None


def _normalized(reason: Optional[str]) -> Optional[str]:
    """去空白并截断；空串视同缺失。"""
    if not reason:
        return None
    text = str(reason).strip()
    if not text:
        return None
    return text[:MAX_REASON_LENGTH]


def pick_failure_reason(
    *,
    task_run_error: Optional[str],
    heartbeat_error: Optional[str],
    explicit_fallback: Optional[str] = None,
    persisted: Optional[PersistedFailureSources] = None,
) -> Optional[str]:
    """按优先级选出唯一失败原因。

    优先级: TaskRun -> heartbeat -> 调用方显式兜底 -> VectorJobState -> Project。

    前两者是节点级精确来源；`explicit_fallback` 是调用方针对特定节点给出的
    处置说明（如目录人工调整导致重试被阻断），比通用持久化来源更有指导价值；
    最后两者是持久化兜底，由 `load_persisted_failure_sources` 在取数阶段完成
    有效性门控。

    全部来源为空（含纯空白字符串）时返回 None，由调用方决定展示文案。
    """
    candidates = (
        task_run_error,
        heartbeat_error,
        explicit_fallback,
        persisted.vector_job_error if persisted else None,
        persisted.project_error if persisted else None,
    )
    for candidate in candidates:
        normalized = _normalized(candidate)
        if normalized:
            return normalized
    return None


async def load_persisted_failure_sources(
    db: AsyncSession,
    *,
    project_id: int,
    document_id: Optional[int] = None,
) -> PersistedFailureSources:
    """读取失败原因的两个持久化真源。

    - `document_id` 为 None 时不查询 `VectorJobState`（非文档解析类节点无对应记录）
    - `VectorJobState.last_error` 仅在状态为 FAILED/LOST 时采纳
    - `Project.error_message` 仅在项目状态为 FAILED 时采纳，避免把历史失败文案
      显示在已恢复或正在运行的项目上

    调用方应仅在已判定失败/可重试时调用，健康路径不应产生额外查询。
    """
    vector_job_error: Optional[str] = None
    if document_id is not None:
        result = await db.execute(
            select(VectorJobState).where(VectorJobState.document_id == document_id)
        )
        job_state = result.scalar_one_or_none()
        if job_state is not None and job_state.status in _FAILED_VECTOR_JOB_STATES:
            vector_job_error = _normalized(job_state.last_error)

    project_error: Optional[str] = None
    project = await db.get(Project, project_id)
    if project is not None and project.status == ProjectStatus.FAILED:
        project_error = _normalized(project.error_message)

    return PersistedFailureSources(
        vector_job_error=vector_job_error,
        project_error=project_error,
    )
