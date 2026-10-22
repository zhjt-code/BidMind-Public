"""向量化任务状态与队列辅助。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from arq.connections import ArqRedis
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.observability.metrics import vector_job_state_total, vector_pending_jobs_gauge
from app.core.sql_column_fit import fit_string_column as _fit_string_column
from app.models.vector_job_state import (
    ACTIVE_VECTOR_JOB_STATES,
    TERMINAL_VECTOR_JOB_STATES,
    VectorJobState,
    VectorJobStatus,
)

# ==================== 恢复判定 (共享给 files 路由 + writing_service) ====================


def _is_timestamp_stale(ts: Optional[datetime], window_sec: int) -> bool:
    """判定时间戳是否过期。None 视为未开始, 不算 stale。"""
    if ts is None:
        return False
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() > window_sec


def decide_recovery_from_state(
    job_state: Optional[VectorJobState],
    *,
    task_active: bool,
    heartbeat_timeout_sec: int,
    function_name: str = "process_document_job",
    redis_heartbeat_fresh: bool = False,
    arq_job_present: Optional[bool] = None,
) -> tuple[bool, str]:
    """基于 vector_job_states + worker alive + local task 综合判定是否需要恢复。

 返回 (should_recover, reason) 元组。reason 为空串代表不需要恢复。

 判定规则:
 - 终态 (COMPLETED/FAILED): 不恢复
 - ARQ 模式:
 - 已确认 ARQ job 在途时优先视为存活, 不因 worker alive 短暂过期恢复
 (: 避免 API fallback 与仍在途 Worker 同名双执行)
 - QUEUED 且无 worker 心跳 -> 丢失
 - CLAIMED/RUNNING 无 worker -> 丢失; 心跳过期且 ARQ in-progress 不在途 -> 丢失
 (: in-progress 在途则视为存活, 不被假性 stale 误恢复)
 - LOST -> 直接恢复
 - RECOVERING 且心跳过期且无 worker -> 再次恢复
 - RECOVERING 且心跳过期且 ARQ 无 job 痕迹 -> 再次恢复(幽灵 RECOVERING)
 - PENDING (兼容态) -> 按本地 task 判断
 - asyncio 模式 / 无状态行: 按 task_active 判断

 redis_heartbeat_fresh:
 调用方提供的 Redis 业务心跳新鲜度判断。当 worker 仍在持续推进 Redis 心跳
 但 vector_job_states 持久化心跳因长事务尚未提交而显得过期时, 必须传 True,
 以避免把"长事务进行中"的正常运行误判为 running_heartbeat_stale。

 arq_job_present:
 调用方探测的"ARQ 中是否仍存在该 job 痕迹"(job/result/in-progress/retry 任一,
 见 arq_job_present_async)。覆盖四类活跃态, 两种语义方向:
 - QUEUED / RECOVERING(默认不恢复): 仅当 arq_job_present is False(ARQ 确无痕迹)
 才判幽灵恢复 —— 幽灵 QUEUED / 幽灵 RECOVERING。
 - CLAIMED / RUNNING(默认按 stale 恢复): 仅当 arq_job_present is True(in-progress
 在途)才"反向"抑制恢复 ——: 心跳因 GIL 阻塞假性 stale, 但 ARQ 仍在跑,
 重投会污染活任务心跳并造重复 job。
 None 表示调用方未探测, 各分支均保持其原有默认行为。
 """
    # 延迟导入避免 app.core.arq_runtime 循环依赖
    from app.core.arq_runtime import has_alive_worker_for

    if job_state is None:
        # 无状态行 ≠ 任务死亡。POST 入队后 job_state 落库失败/queued 心跳
        # 过期的窗口内, ARQ 可能仍持有确定性 job(_job_id=task_name)。调用方探测到
        # 在途痕迹时禁止按 legacy_no_state 重投, 否则与在途 job 双执行。
        if arq_job_present is True:
            return False, ""
        if not task_active:
            return True, "legacy_no_state"
        return False, ""

    if job_state.status in TERMINAL_VECTOR_JOB_STATES:
        return False, ""

    if (job_state.queue_mode or "").lower() == "arq":
        has_worker = has_alive_worker_for(function_name)
        hb_stale = _is_timestamp_stale(job_state.last_heartbeat_at, heartbeat_timeout_sec)
        if redis_heartbeat_fresh:
            hb_stale = False

        if (
            arq_job_present is True
            and job_state.status
            in (
                VectorJobStatus.QUEUED,
                VectorJobStatus.CLAIMED,
                VectorJobStatus.RUNNING,
                VectorJobStatus.RECOVERING,
            )
        ):
            return False, ""

        if job_state.status == VectorJobStatus.QUEUED:
            if not has_worker:
                return True, "queued_no_worker"
            # worker 存活但 ARQ 中已无该 job 痕迹 -> 领取后中断的幽灵 QUEUED,
            # 不能仅凭 worker 存活就判健康, 否则将永远卡在 queued 无人恢复。
            if arq_job_present is False:
                return True, "queued_orphaned"
            return False, ""
        if job_state.status == VectorJobStatus.CLAIMED:
            if not has_worker:
                return True, "claimed_worker_gone"
            # 心跳 stale 不是"任务已死"的可靠信号 —— encode 重 CPU 操作持有 GIL
            # 阻塞事件循环, 会让同 worker 内并发任务的 keepalive 协程推不出心跳而假性 stale。
            # 若 ARQ in-progress 仍在(arq_job_present is True), 说明 job 真活着, 绝不能重投
            # (重投会覆盖活任务心跳致前端进度回跳, 并制造重复 job)。仅在未显式探测到"在途"时
            # 才按旧的 stale 语义恢复, 保持非探测调用方(如 writing 流)行为不变。
            if hb_stale and arq_job_present is not True:
                return True, "claimed_heartbeat_stale"
            return False, ""
        if job_state.status == VectorJobStatus.RUNNING:
            if not has_worker:
                return True, "running_worker_gone"
            # 同 CLAIMED —— ARQ in-progress 在途即视为存活, 不得凭假性 stale 重投。
            if hb_stale and arq_job_present is not True:
                return True, "running_heartbeat_stale"
            return False, ""
        if job_state.status == VectorJobStatus.LOST:
            return True, "state_lost"
        if job_state.status == VectorJobStatus.RECOVERING:
            if hb_stale and not has_worker:
                return True, "recovering_stuck"
            # 重投撞 keep_result 去重窗口被吞没 -> 状态写成 RECOVERING 但 ARQ 实无 job。
            # worker 存活致 "hb_stale and not has_worker" 永不触发(同 QUEUED queued_orphaned 盲区),
            # 任务永久卡死。心跳已陈旧且 ARQ 无该 job 痕迹 -> 幽灵 RECOVERING, 必须再次恢复。
            if hb_stale and arq_job_present is False:
                return True, "recovering_orphaned"
            return False, ""
        if job_state.status == VectorJobStatus.PENDING:
            if not task_active:
                return True, "pending_task_gone"
            return False, ""

    if not task_active:
        return True, "asyncio_task_gone"
    return False, ""


def resolve_recovery_count(
    mem_count: int,
    job_state: Optional[VectorJobState],
) -> int:
    """恢复计数取"进程内存态"与"DB 持久化 retry_count"的较大值。

 内存态 (tasks.get_recovery_count) 在 worker/API 进程重启后清零, 单靠它会让
 MAX_RECOVERY 永远触不到 -> 同一文档跨多次重启被无限重投; vector_job_states.retry_count
 由 reconciler 每次重投落库, 持久且跨进程, 用它兜底确保上限真正生效。
 """
    db_retry_count = int(getattr(job_state, "retry_count", 0) or 0) if job_state else 0
    return max(int(mem_count or 0), db_retry_count)


async def get_vector_job_state(db: AsyncSession, document_id: int) -> Optional[VectorJobState]:
    result = await db.execute(
        select(VectorJobState).where(VectorJobState.document_id == document_id)
    )
    return result.scalar_one_or_none()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_vector_job_state(
    db: AsyncSession,
    *,
    project_id: int,
    document_id: int,
    task_name: str,
    status: VectorJobStatus,
    queue_mode: str,
    stage: str | None = None,
    progress: str | None = None,
    degraded_to_cpu: bool = False,
    fallback_reason: str | None = None,
    recovery_mode: str | None = None,
    retry_count: int | None = None,
    shadow_summary: dict[str, Any] | None = None,
    last_error: str | None = None,
    chunk_fingerprint_version: str = "bge-m3-v1",
    # ── ARQ 状态机扩展字段 ────────────────────────────────────
    arq_job_id: str | None = None,
    worker_id: str | None = None,
    bump_enqueued_at: bool = False,
    bump_claimed_at: bool = False,
    bump_started_at: bool = False,
    bump_finished_at: bool = False,
    bump_heartbeat: bool = False,
) -> Optional[VectorJobState]:
    """创建或更新向量化任务状态。

    状态机字段采用"显式 bump_*=True 才覆盖"的语义,
    确保 queued/claimed/running/completed 之间的时间锚点不被后续心跳覆盖。

    删除一致性守卫:
    - 若项目已被取消(存在取消墓碑) 或目标文档在库中已被级联删除, 直接 no-op 返回 None,
      避免删除项目后 worker 继续写入触发 ForeignKeyViolationError。
    """
    # 守卫 1: 项目取消墓碑命中 -> 直接 no-op (删除后继续写会撞外键)
    from app.core.arq_runtime import is_project_cancelled  # 延迟导入避免循环

    if is_project_cancelled(project_id):
        logger.info(
            f"[job_state] 项目已取消, 跳过状态写入: project_id={project_id}, "
            f"document_id={document_id}, status={status.value}"
        )
        return None

    # 守卫 2: 文档已被删除 (项目级联) -> 直接 no-op
    # 只在非 PENDING/QUEUED 路径做这个昂贵检查, 入队时刻文档必然存在
    if status not in (VectorJobStatus.PENDING, VectorJobStatus.QUEUED):
        from app.models.document import Document  # 延迟导入避免循环

        doc_exists = await db.execute(
            select(Document.id).where(Document.id == document_id)
        )
        if doc_exists.scalar_one_or_none() is None:
            logger.info(
                f"[job_state] 目标文档已被删除, 跳过状态写入: "
                f"project_id={project_id}, document_id={document_id}, status={status.value}"
            )
            return None

    state = await get_vector_job_state(db, document_id)
    previous_status = state.status if state is not None else None

    # 守卫 3: 终态防倒退
    # 如果当前状态已经是 COMPLETED/FAILED, 拒绝被 QUEUED/RUNNING/RECOVERING/LOST 覆盖,
    # 防止 worker 完成提交后 API 侧的恢复路径竞态写入造成"已完成 -> 恢复中"的数据污染
    # (见 docs/bug_tracking.md ARQ 状态误判修复案例)。
    # 终态自身保持幂等刷新, 即 COMPLETED -> COMPLETED 与 FAILED -> FAILED 仍可继续。
    _DOWNGRADE_BLOCKED = (
        VectorJobStatus.QUEUED,
        VectorJobStatus.RUNNING,
        VectorJobStatus.RECOVERING,
        VectorJobStatus.LOST,
    )
    if (
        previous_status in TERMINAL_VECTOR_JOB_STATES
        and status in _DOWNGRADE_BLOCKED
    ):
        logger.warning(
            "[job_state] 拒绝终态倒退: previous={}, attempted={}, document_id={}, project_id={}",
            previous_status.value,
            status.value,
            document_id,
            project_id,
        )
        return state

    if state is None:
        state = VectorJobState(
            project_id=project_id,
            document_id=document_id,
            task_name=task_name,
            status=status,
            queue_mode=queue_mode,
            chunk_fingerprint_version=chunk_fingerprint_version,
        )
        db.add(state)

    columns = VectorJobState.__table__.c
    state.task_name = task_name
    state.status = status
    state.queue_mode = queue_mode
    state.stage = _fit_string_column(stage, columns.stage)
    state.progress = _fit_string_column(progress, columns.progress)
    state.degraded_to_cpu = 1 if degraded_to_cpu else 0
    state.fallback_reason = _fit_string_column(fallback_reason, columns.fallback_reason)
    state.recovery_mode = recovery_mode
    if retry_count is not None:
        state.retry_count = retry_count
    if shadow_summary is not None:
        state.shadow_summary = json.dumps(shadow_summary, ensure_ascii=False)
    if last_error is not None:
        state.last_error = last_error
    state.chunk_fingerprint_version = chunk_fingerprint_version

    # ARQ 运行态字段: 仅在显式 bump 时覆盖, 其余保持既有值
    if arq_job_id is not None:
        state.arq_job_id = arq_job_id
    if worker_id is not None:
        state.worker_id = worker_id

    now = _utcnow()
    if bump_enqueued_at:
        state.enqueued_at = now
    if bump_claimed_at:
        state.claimed_at = now
    if bump_started_at:
        state.started_at = now
    if bump_finished_at:
        state.finished_at = now
    if bump_heartbeat:
        state.last_heartbeat_at = now

    # 指标: 状态流转计数 + 未完成任务 gauge
    vector_job_state_total.labels(state=status.value, queue_mode=queue_mode).inc()
    previous_active = previous_status in ACTIVE_VECTOR_JOB_STATES
    current_active = status in ACTIVE_VECTOR_JOB_STATES
    if current_active and not previous_active:
        vector_pending_jobs_gauge.labels(queue_mode=queue_mode).inc()
    elif previous_active and not current_active:
        try:
            vector_pending_jobs_gauge.labels(queue_mode=queue_mode).dec()
        except ValueError:
            pass
    await db.flush()
    return state


async def enqueue_process_document_job(
    redis_pool: ArqRedis | None,
    *,
    project_id: int,
    document_id: int,
    skip_vl: bool = False,
) -> str | None:
    """优先走 ARQ 入队, 基于确定性 `_job_id` 实现文档级幂等。

    幂等语义:
      - 使用 `process_doc_{document_id}` 作为 `_job_id`, ARQ 内部通过 Redis 事务
        保证同一 job_id 在排队/运行/结果保留期内不会被重复入队
      - 结果保留期由 worker 侧 `keep_result_s` 控制 (默认 60s), 到期后解锁合法重投

    返回值:
      - 首次入队成功: ARQ job_id (即 deterministic_id)
      - ARQ 去重命中 (job is None): 同样返回 deterministic_id, 表示"任务已在队列"
        上层视同入队成功 -> 写 queued 心跳, 不回退 asyncio (避免并发跑两份)
      - 失败 (Redis 不可用 / enqueue 抛异常): None, 供上层回退 asyncio
    """
    if redis_pool is None:
        return None
    deterministic_id = f"process_doc_{document_id}"
    try:
        enqueue_kwargs = {"_job_id": deterministic_id}
        if bool(getattr(settings, "WORKER_RESOURCE_QUEUES_ENABLED", False)):
            from app.core.tasks.resource_policy import policy_for_function

            enqueue_kwargs["_queue_name"] = policy_for_function("process_document_job").queue_name
        job = await redis_pool.enqueue_job(
            "process_document_job",
            project_id,
            document_id,
            skip_vl,
            **enqueue_kwargs,
        )
        if job is None:
            logger.info(
                f"ARQ 去重命中: job_id={deterministic_id} 已在队列/运行/结果保留期, "
                f"跳过重复入队: project={project_id}, document={document_id}"
            )
            return deterministic_id
        job_id = getattr(job, "job_id", None) or deterministic_id
        logger.info(
            f"已投递 ARQ 文档处理任务: project={project_id}, document={document_id}, job_id={job_id}"
        )
        return job_id
    except Exception as exc:
        logger.warning(f"ARQ 文档任务投递失败, 回退 asyncio: {exc}")
        return None
