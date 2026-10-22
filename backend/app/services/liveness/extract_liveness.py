"""
职责: 招标要求提取的失活判定与 FAILED 收敛。
不做: 不启动/重投提取、不渲染 steps、不判定 completed、不访问 Request。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tasks import (
    HEARTBEAT_TIMEOUT,
    TASK_TERMINAL_STAGES,
    TaskLiveness,
    inspect_task_liveness,
    update_heartbeat,
)
from app.models.project import Project, ProjectStatus

EXTRACT_ARQ_FUNCTION = "extract_requirements_job"
EXTRACT_TERMINAL_STAGES = frozenset(TASK_TERMINAL_STAGES) | {"失败"}

_EXTRACT_SETTLED_STATUSES = frozenset(
    {ProjectStatus.EXTRACTED, ProjectStatus.REVIEWING, ProjectStatus.COMPLETED}
)

# asyncio 兜底没有队列侧 job，禁止用 task_name 去「发明」一条 ARQ 生命。
_NON_ARQ_QUEUE_MODES = frozenset({"asyncio_fallback", "waiting_worker"})


def inspect_extract_task_liveness(task_name: str) -> TaskLiveness:
    """提取任务专用活性检查：中文「失败」算终态，ARQ 函数固定为提取 job。

 心跳丢失不等于执行体不在（TTL 3600s < job_timeout 7200s， 同形态）。
 inspect 在 missing 时不会查 ARQ；此处用心跳里的 job_id，否则用确定性
 task_name 补探。只有 has_confirmed 为真才改写成 live，无法证实则保持原判。
 """
    timeout = int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", HEARTBEAT_TIMEOUT))
    result = inspect_task_liveness(
        task_name,
        heartbeat_timeout=timeout,
        arq_function=EXTRACT_ARQ_FUNCTION,
        terminal_stages=EXTRACT_TERMINAL_STAGES,
    )
    if result.state not in {"stale", "missing"}:
        return result
    if not _extract_arq_execution_confirmed(task_name, result.heartbeat):
        return result
    return replace(
        result,
        state="live",
        active=True,
        reason="arq_job_pending",
    )


def _extract_arq_execution_confirmed(
    task_name: str,
    heartbeat: Optional[dict[str, Any]],
) -> bool:
    """能否证实提取 ARQ job 仍在排队/在途/重试。无法证实则 False。"""
    queue_mode = str((heartbeat or {}).get("queue_mode") or "").strip().lower()
    if queue_mode in _NON_ARQ_QUEUE_MODES:
        return False

    from app.core.tasks.lifecycle.liveness_evidence import (
        has_confirmed_pending_arq_job,
    )

    job_ids: list[str] = []
    heartbeat_job_id = str((heartbeat or {}).get("arq_job_id") or "").strip()
    if heartbeat_job_id:
        job_ids.append(heartbeat_job_id)
    if task_name and task_name not in job_ids:
        job_ids.append(task_name)

    for job_id in job_ids:
        probe = {**(heartbeat or {}), "queue_mode": "arq", "arq_job_id": job_id}
        if has_confirmed_pending_arq_job(probe, arq_function=EXTRACT_ARQ_FUNCTION):
            return True
    return False


def extract_heartbeat_is_midstage(heartbeat: Optional[dict[str, Any]]) -> bool:
    """心跳停在非终态阶段。空心跳不算中途静默（历史项目/TTL 清理）。"""
    if not heartbeat:
        return False
    stage = str(heartbeat.get("stage") or "").strip().lower()
    return bool(stage) and stage not in EXTRACT_TERMINAL_STAGES


def should_declare_extract_dead(
    liveness: TaskLiveness,
    project_status: ProjectStatus,
) -> bool:
    """执行体已不在，且提取工作尚未落到业务终态时，才允许宣告死亡。

 判死轴是执行体，不是心跳新鲜度，也不是落库条数。

 仍活（任一成立即不得宣告死亡）:
 - live / enqueue_grace：本进程 task、新鲜心跳、或 ARQ 未终局键+worker 存活
 - 心跳终态（completed/failed/cancelled/失败）：已终局，不是「判死」
 - 项目 EXTRACTED/REVIEWING/COMPLETED：业务已完成
 - 项目已 FAILED：不必再宣告
 - missing 且项目还不是 EXTRACTING：从未启动，保持 idle

 已死（找不到执行体，且工作未完成）:
 - stale/missing 且项目 EXTRACTING
 - stale 且心跳停在中间阶段（即使项目还是 UPLOADING，禁止回 idle 触发）

 活着但卡死由 extract_requirements_job 的 job_timeout 与 CancelledError 收尾杀，
 本谓词不另设墙钟。
 """
    if project_status in _EXTRACT_SETTLED_STATUSES:
        return False
    if project_status == ProjectStatus.FAILED:
        return False
    if liveness.is_live or liveness.is_terminal:
        return False
    if liveness.state not in {"stale", "missing"}:
        return False
    if liveness.state == "missing":
        return project_status == ProjectStatus.EXTRACTING
    return (
        project_status == ProjectStatus.EXTRACTING
        or extract_heartbeat_is_midstage(liveness.heartbeat)
    )


def build_extract_dead_message(liveness: TaskLiveness) -> str:
    """用户可见失败文案；原因码留给日志，不把内部 reason 原样甩到前端。"""
    if liveness.state == "missing":
        return "招标要求提取任务已丢失，请检查 Worker 后重试"
    if str(liveness.reason).startswith("worker_missing"):
        return "招标要求提取 Worker 不在线，请检查 Worker 后重试"
    return "招标要求提取任务已失活，请检查 Worker 后重试"


async def persist_extract_task_dead(
    db: AsyncSession,
    project: Project,
    *,
    task_name: str,
    liveness: TaskLiveness,
) -> str:
    """把未完成的提取收敛到 FAILED，并写失败心跳。幂等：已 FAILED 则只返回原文案。

    心跳/计费/TaskRun 只在项目 FAILED 提交成功之后写，避免 DB 仍是 EXTRACTING
    而心跳已是「失败」、后续轮询把未落库失败当成终态、不再重试 persist。
    """
    error_message = build_extract_dead_message(liveness)
    if project.status == ProjectStatus.FAILED:
        return (project.error_message or error_message)[:1000]

    project.status = ProjectStatus.FAILED
    project.error_message = error_message[:1000]
    try:
        await db.commit()
    except Exception as exc:
        logger.warning("提取失活落 FAILED 提交失败: project={} err={}", project.id, exc)
        try:
            await db.rollback()
        except Exception:
            pass
        return error_message[:1000]

    try:
        from app.billing.services.billing_guard import BillingGuard

        await BillingGuard.on_failed(db, project)
        await db.commit()
    except Exception as billing_err:
        logger.warning("提取失活释放预占异常(忽略): project={} err={}", project.id, billing_err)

    try:
        from app.models.task_run import TaskRunStatus
        from app.services.flow_retry_service import mark_review_task_run_by_task_name

        await mark_review_task_run_by_task_name(
            db,
            project_id=project.id,
            task_name=task_name,
            status=TaskRunStatus.failed.value,
            stage="failed",
            last_error=error_message,
        )
    except Exception as mark_err:
        logger.debug("提取失活 TaskRun 回写失败(忽略): {}", mark_err)

    try:
        update_heartbeat(task_name, stage="失败", progress=f"错误: {error_message}")
    except Exception as hb_err:
        logger.debug("提取失活失败心跳写入失败(忽略): {}", hb_err)

    logger.warning(
        "提取任务失活并转失败: project_id={} task={} state={} reason={} age={}",
        project.id,
        task_name,
        liveness.state,
        liveness.reason,
        liveness.age_seconds,
    )
    return error_message[:1000]
