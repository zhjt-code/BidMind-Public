"""后台任务模块

任务在 FastAPI 进程内通过 asyncio.Task 执行。
心跳数据双写到内存和 Redis（Redis 不可用时降级为纯内存）。
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional

from loguru import logger

from app.core.config import settings
from app.core.tasks.collusion_task import get_collusion_task_name, run_collusion_task
from app.core.tasks.extract_task import get_extract_task_name, run_extract_task
from app.core.tasks.process_task import get_process_task_name, run_process_task
from app.core.tasks.review_task import run_review_task
from app.core.tasks.writing_clause_vectorize_task import (
    get_writing_clause_vectorize_task_name,
    run_writing_clause_vectorize_task,
)
from app.core.tasks.writing_compose_task import (
    get_writing_compose_task_name,
    run_writing_compose_task,
)
from app.core.tasks.writing_extract_task import (
    get_writing_extract_task_name,
    run_writing_extract_task,
)

# ---- 内存态 (asyncio 模式 + 兼容缓存) ----
_background_tasks: dict[str, asyncio.Task] = {}
_task_heartbeats: dict[str, dict[str, Any]] = {}
_recovery_counts: dict[str, int] = {}
_task_project_ids: dict[str, int] = {}

HEARTBEAT_TIMEOUT = 600
MAX_RECOVERY = 3

# ARQ 入队后 worker 真正领取前的宽限期：状态查询不得把该窗口误判为"任务丢失"
# 典型链路: API 同步入队 -> worker 5~30s 内领取 -> 第一条业务 heartbeat 覆盖 "queued"
# 宽限值必须大于 worker 领取上限, 这里取 120s 兼顾冷启动
ENQUEUE_GRACE_SEC = 120

# heartbeat 中用于标记"已入队待领取"的阶段常量; 统一写法避免四处硬编码
TASK_STAGE_QUEUED = "queued"

_HEARTBEAT_KEY_PREFIX = "bidmind:heartbeat:"
_HEARTBEAT_TTL = 3600

TASK_STAGE_WAITING_WORKER = "waiting_worker"
TASK_STAGE_FALLBACK_WAITING = "fallback_waiting"
TASK_STAGE_FALLBACK_RUNNING = "fallback_running"

# 本地 fallback 执行体在资源许可 owner 中的来源标识, 与 ARQ worker_id 区分
_FALLBACK_OWNER_PREFIX = "fallback"

TASK_TERMINAL_STAGES = {"completed", "failed", "cancelled", "canceled"}


@dataclass(frozen=True)
class TaskLiveness:
    """Status-query view of whether a background task is genuinely alive."""

    task_name: str
    state: str
    active: bool
    heartbeat: Optional[dict[str, Any]]
    stage: str
    age_seconds: Optional[float]
    reason: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state == "terminal"

    @property
    def is_stale(self) -> bool:
        return self.state == "stale"

    @property
    def is_missing(self) -> bool:
        return self.state == "missing"

    @property
    def is_live(self) -> bool:
        return self.state in {"live", "enqueue_grace"}


# ===================== create / cancel / query =====================

def create_background_task(
    coro: Coroutine[Any, Any, Any],
    name: str = "",
    task_type: str = "",
    project_id: Optional[int] = None,
    fallback_control: Optional[bool] = None,
    **_kw: Any,
) -> asyncio.Task:
    """创建后台任务（asyncio 模式）

    Args:
        coro: 协程对象
        name: 任务名称
        task_type: 任务类型 (process/extract/review/collusion)，注入日志上下文
        project_id: 关联项目 ID，注入日志上下文

    Returns:
        asyncio.Task
    """
    task_name = name or f"task_{id(coro)}"
    heavy_task_types = {
        "process",
        "extract",
        "review",
        "collusion",
        "writing_extract",
        "writing_outline",
        "writing_material_process",
        "writing_match",
        "writing_compose",
        "writing_compose_single",
        "writing_clause_vectorize",
    }
    should_control = (
        fallback_control
        if fallback_control is not None
        else task_type in heavy_task_types
    )
    if should_control:
        if not bool(getattr(settings, "BACKGROUND_FALLBACK_ENABLED", True)):
            # 兜底开关关闭: 不进入 permit 自旋循环, 不写 fallback_waiting 心跳;
            # 写终态 failed 让 dead-task 判定链路 (TASK_TERMINAL_STAGES 短路) 转 FAILED。
            # 禁止走 _create_local_task: 其 done callback 会 cleanup_task 清掉终态心跳。
            # 返回未注册的立即完成 Task, 满足签名; 调用方不依赖其执行体。
            update_heartbeat(
                task_name,
                stage="failed",
                progress="后台兜底已被关闭 (BACKGROUND_FALLBACK_ENABLED=false), 请检查 worker 服务后重试",
                extra={"queue_mode": "fallback_disabled", "project_id": project_id},
            )
            try:
                coro.close()
            except Exception:
                pass

            async def _immediate() -> None:
                return None

            return asyncio.create_task(_immediate())
        raw_coro = coro

        async def _controlled_fallback() -> None:
            from app.core.config import settings
            from app.core.redis import get_redis_pool
            from app.core.tasks.resource_permit import (
                build_permit_owner,
                fallback_permit_context,
            )

            redis = await get_redis_pool()
            # 同名 fallback 任务可能并发存在 (_background_tasks 只覆盖注册表, 不取消旧任务),
            # 因此 owner 不能直接用 task_name, 否则先退出者会删掉仍在运行者的 owner 记录
            permit_owner = build_permit_owner(
                worker_id=_FALLBACK_OWNER_PREFIX,
                function_name=task_name,
                project_id=project_id,
            )
            while True:
                update_heartbeat(
                    task_name,
                    stage=TASK_STAGE_FALLBACK_WAITING,
                    progress="等待本地兜底资源",
                    extra={"queue_mode": "asyncio_fallback", "project_id": project_id},
                )
                async with fallback_permit_context(redis, owner=permit_owner) as acquired:
                    if acquired:
                        update_heartbeat(
                            task_name,
                            stage=TASK_STAGE_FALLBACK_RUNNING,
                            progress="本地兜底执行中",
                            extra={"queue_mode": "asyncio_fallback", "project_id": project_id},
                        )
                        await raw_coro
                        return
                logger.warning(
                    f"本地 fallback 并发已满, 继续等待: task={task_name}, task_type={task_type}"
                )
                await asyncio.sleep(
                    float(getattr(settings, "WORKER_RESOURCE_PERMIT_POLL_INTERVAL", 1.0))
                )

        coro = _controlled_fallback()
    return _create_local_task(coro, task_name, task_type=task_type, project_id=project_id)


def _create_local_task(
    coro: Coroutine[Any, Any, Any],
    task_name: str,
    task_type: str = "",
    project_id: Optional[int] = None,
) -> asyncio.Task:
    """asyncio 模式: 在当前进程创建 Task"""
    # 同名任务重建前先清掉历史 heartbeat，避免读取到 Redis 中的旧 completed/failed 终态。
    cleanup_task(task_name)

    if task_type:
        raw_coro = coro
        # 任务级 incident_id: 每次后台任务启动生成独立链路 ID, 覆盖外层 HTTP 请求级 incident_id
        # 同一 task 内的所有错误日志将共享此 ID, 便于在错误日志列表按链路串联根因
        task_incident_id = uuid.uuid4().hex[:16]

        async def _wrapped() -> None:
            with logger.contextualize(
                task_type=task_type,
                project_id=project_id,
                source="backend",
                incident_id=task_incident_id,
            ):
                await raw_coro

        coro = _wrapped()

    task = asyncio.create_task(coro)
    _background_tasks[task_name] = task
    if project_id is not None:
        _task_project_ids[task_name] = project_id

    def task_done_callback(t: asyncio.Task) -> None:
        binding_project_id = _task_project_ids.get(task_name)
        try:
            exc = t.exception()
            if exc:
                logger.error(f"后台任务 [{task_name}] 异常: {exc}", exc_info=exc)
            else:
                logger.info(f"后台任务 [{task_name}] 完成")
        except asyncio.CancelledError:
            logger.warning(f"后台任务 [{task_name}] 被取消")
        finally:
            if binding_project_id is not None:
                _schedule_release_project_binding(binding_project_id)
            cleanup_task(task_name, preserve_newer_extract_heartbeat=True)

    task.add_done_callback(task_done_callback)

    from app.core.tasks.extract_progress_generation import (
        get_bound_extract_generation,
        remember_extract_task_owner,
    )

    bound_generation = get_bound_extract_generation(task_name)
    if bound_generation is not None:
        remember_extract_task_owner(task_name, bound_generation)
    logger.info(
        f"后台任务 [{task_name}] 已创建，当前活跃任务数: {len(_background_tasks)}"
    )
    return task


def get_active_tasks_count() -> int:
    """获取当前活跃的后台任务数量"""
    return len(_background_tasks)


def get_task(name: str) -> Optional[asyncio.Task]:
    """获取指定名称的后台任务"""
    return _background_tasks.get(name)


def is_task_active(name: str) -> bool:
    """判断指定任务是否仍在运行中"""
    task = _background_tasks.get(name)
    if task is not None and not task.done():
        heartbeat = _task_heartbeats.get(name)
        if heartbeat:
            elapsed = time.time() - heartbeat.get("last_heartbeat", 0)
            if elapsed > HEARTBEAT_TIMEOUT:
                logger.warning(
                    f"任务 [{name}] 心跳超时 ({elapsed:.0f}s > {HEARTBEAT_TIMEOUT}s)，"
                    f"最后阶段: {heartbeat.get('stage', 'unknown')}，主动取消"
                )
                cancel_task(name)
                return False
        return True

    hb = get_task_heartbeat(name)
    if hb:
        stage = str(hb.get("stage", "")).strip().lower()
        if stage in {"completed", "failed"}:
            return False
        elapsed = time.time() - hb.get("last_heartbeat", 0)
        if elapsed < HEARTBEAT_TIMEOUT:
            return True

    return False


def inspect_task_liveness(
    name: str,
    *,
    heartbeat_timeout: Optional[int] = None,
    arq_function: Optional[str] = None,
    grace_sec: int = ENQUEUE_GRACE_SEC,
    terminal_stages: Optional[set[str]] = None,
) -> TaskLiveness:
    """Return a stricter liveness verdict for status endpoints.

    `is_task_active()` intentionally keeps legacy compatibility by treating a
    fresh Redis heartbeat as alive. Status endpoints need a stronger contract:
    a stale heartbeat (and, only after the no-progress window, an ARQ heartbeat
    whose worker missing has passed the grace window) must not keep the UI
    spinning indefinitely.
    """

    timeout = heartbeat_timeout if heartbeat_timeout is not None else HEARTBEAT_TIMEOUT
    terminals = terminal_stages or TASK_TERMINAL_STAGES
    task = _background_tasks.get(name)
    heartbeat = get_task_heartbeat(name)
    stage = str((heartbeat or {}).get("stage") or "").strip().lower()

    age_seconds: Optional[float] = None
    if heartbeat and heartbeat.get("last_heartbeat") is not None:
        try:
            age_seconds = max(0.0, time.time() - float(heartbeat.get("last_heartbeat", 0)))
        except (TypeError, ValueError):
            age_seconds = None

    if stage in terminals:
        return TaskLiveness(
            task_name=name,
            state="terminal",
            active=False,
            heartbeat=heartbeat,
            stage=stage,
            age_seconds=age_seconds,
            reason=stage,
        )

    if stage == TASK_STAGE_QUEUED and is_task_in_enqueue_grace(name, grace_sec=grace_sec):
        return TaskLiveness(
            task_name=name,
            state="enqueue_grace",
            active=True,
            heartbeat=heartbeat,
            stage=stage,
            age_seconds=age_seconds,
            reason="queued_grace",
        )

    if task is not None and not task.done():
        if age_seconds is not None and age_seconds > timeout:
            logger.warning(
                f"任务 [{name}] 本地任务心跳过期 ({age_seconds:.0f}s > {timeout}s)，判定失活"
            )
            return TaskLiveness(
                task_name=name,
                state="stale",
                active=False,
                heartbeat=heartbeat,
                stage=stage,
                age_seconds=age_seconds,
                reason="local_heartbeat_stale",
            )
        return TaskLiveness(
            task_name=name,
            state="live",
            active=True,
            heartbeat=heartbeat,
            stage=stage,
            age_seconds=age_seconds,
            reason="local_task_active",
        )

    if not heartbeat:
        return TaskLiveness(
            task_name=name,
            state="missing",
            active=False,
            heartbeat=None,
            stage="",
            age_seconds=None,
            reason="no_heartbeat_or_task",
        )

    if age_seconds is None or age_seconds > timeout:
        # 无法证明心跳仍新鲜：缺 timestamp 与超时同属「不能证明活着」。
        # 心跳过期不等于任务已死。mark_task_queued 只在入队时写一次心跳，
        # worker 领取前无人刷新，正常排队也会撞上超时。队列侧确证仍持有该 job 时
        # 属于"等待领取"而非"卡死"，不得判失活写 FAILED。
        # 透传 arq_function，挽救以键类语义 + worker 存活为准而非入队墙钟。
        from app.core.tasks.lifecycle.liveness_evidence import (
            has_confirmed_pending_arq_job,
        )

        if has_confirmed_pending_arq_job(heartbeat, arq_function=arq_function):
            return TaskLiveness(
                task_name=name,
                state="live",
                active=True,
                heartbeat=heartbeat,
                stage=stage,
                age_seconds=age_seconds,
                reason="arq_job_pending",
            )
        return TaskLiveness(
            task_name=name,
            state="stale",
            active=False,
            heartbeat=heartbeat,
            stage=stage,
            age_seconds=age_seconds,
            reason="heartbeat_stale" if age_seconds is not None else "heartbeat_timestamp_missing",
        )

    if arq_function:
        try:
            from .lifecycle import is_worker_confirmed_missing

            queue_mode = str(heartbeat.get("queue_mode") or "").strip().lower()
            # 单次存活键缺失不是死亡证据；宽限确认后才进入死亡候选。
            # 存活键过期仍可能是共环堵塞；须同时越过无进展窗才判 stale。
            if queue_mode == "arq" and is_worker_confirmed_missing(arq_function):
                from app.core.tasks.lifecycle.liveness_evidence import (
                    heartbeat_past_no_progress_window,
                )

                if heartbeat_past_no_progress_window(heartbeat):
                    return TaskLiveness(
                        task_name=name,
                        state="stale",
                        active=False,
                        heartbeat=heartbeat,
                        stage=stage,
                        age_seconds=age_seconds,
                        reason=f"worker_missing:{arq_function}",
                    )
        except Exception as exc:
            logger.debug(f"任务活性检查 worker 探测失败: task={name}, err={exc}")

    return TaskLiveness(
        task_name=name,
        state="live",
        active=True,
        heartbeat=heartbeat,
        stage=stage,
        age_seconds=age_seconds,
        reason="fresh_heartbeat",
    )


def cancel_task(name: str) -> bool:
    """取消指定后台任务"""
    task = _background_tasks.get(name)
    if task and not task.done():
        project_id = _task_project_ids.get(name)
        task.cancel()
        if project_id is not None:
            _schedule_release_project_binding(project_id)
        _task_heartbeats.pop(name, None)
        return True
    return False


async def cancel_all_background_tasks(timeout: float = 10.0) -> None:
    """服务关闭时取消全部后台任务(文档处理/VL/审查等)。

    uvicorn 收到 Ctrl+C 后若未显式取消,asyncio.Task 会继续跑完,
    表现为「后端已停」仍出现 VL 日志。关闭链路中应先调用本函数。
    """
    names = list(_background_tasks.keys())
    if not names:
        return
    to_wait: list[asyncio.Task] = []
    for name in names:
        task = _background_tasks.get(name)
        if task and not task.done():
            task.cancel()
            to_wait.append(task)
            _task_heartbeats.pop(name, None)
    if not to_wait:
        return
    logger.info(f"关闭服务: 正在取消 {len(to_wait)} 个后台任务")
    try:
        await asyncio.wait_for(
            asyncio.gather(*to_wait, return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"关闭服务: {len(to_wait)} 个任务未在 {timeout}s 内响应取消,强制跳过"
        )


def cleanup_task(name: str, *, preserve_newer_extract_heartbeat: bool = False) -> None:
    """清理任务相关记录。

    preserve_newer_extract_heartbeat=True 用于本地 fallback 的 done 回调：
    旧代际任务结束时不得删除已被新代际占据的心跳。
    默认路径（任务重建前清理）只清心跳，保留 begin_extract_generation 的绑定。
    """
    _background_tasks.pop(name, None)
    _task_project_ids.pop(name, None)

    from app.core.tasks.extract_progress_generation import (
        clear_extract_generation_binding,
        pop_extract_task_owner,
        should_preserve_extract_heartbeat,
    )

    if preserve_newer_extract_heartbeat:
        owned = pop_extract_task_owner(name)
        if owned is not None and should_preserve_extract_heartbeat(name, owned):
            return
        _task_heartbeats.pop(name, None)
        _clear_redis_heartbeat(name)
        clear_extract_generation_binding(name)
        return

    pop_extract_task_owner(name)
    _task_heartbeats.pop(name, None)
    _clear_redis_heartbeat(name)


def _schedule_release_project_binding(project_id: int) -> None:
    """异步调度项目绑定释放，避免在同步路径阻塞。"""
    try:
        from app.core.ai import release_project_binding
        asyncio.create_task(release_project_binding(project_id))
    except Exception as e:
        logger.debug(f"调度项目解绑失败: project_id={project_id}, error={e}")


# ===================== heartbeat (双写: memory + Redis) =====================

def update_heartbeat(
    name: str,
    stage: str = "",
    progress: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """更新任务心跳 (同步, 内存 + Redis 双写)"""
    from app.core.tasks.extract_progress_generation import (
        write_extract_heartbeat_if_current,
    )
    from app.core.tasks.parallel_unit_progress import redis_stage_scoped_fields_to_drop

    extract_outcome = write_extract_heartbeat_if_current(
        name,
        stage=stage,
        progress=progress,
        extra=extra,
    )
    if extract_outcome is not None:
        if extract_outcome.accepted:
            # 本进程正在跑 fallback 时维护内存缓存；API/ARQ 入队侧不缓存，
            # 避免遮蔽 Redis 中 worker 写入的真实进度。
            if name in _background_tasks:
                _task_heartbeats[name] = dict(extract_outcome.snapshot)
            else:
                _task_heartbeats.pop(name, None)
        else:
            existing = _task_heartbeats.get(name)
            if existing is not None:
                existing["last_heartbeat"] = extract_outcome.snapshot.get(
                    "last_heartbeat", time.time()
                )
        return

    old_stage = str((_task_heartbeats.get(name) or {}).get("stage") or "")
    heartbeat_data: dict[str, Any] = {
        "last_heartbeat": time.time(),
        "stage": stage,
        "progress": progress,
    }
    if extra:
        heartbeat_data.update(extra)

    _task_heartbeats[name] = heartbeat_data

    try:
        from app.core.redis import get_sync_redis

        r = get_sync_redis()
        if r:
            key = f"{_HEARTBEAT_KEY_PREFIX}{name}"
            if not old_stage:
                try:
                    old_stage = str(r.hget(key, "stage") or "")
                except Exception:
                    old_stage = ""
            pipe = r.pipeline()
            from app.core.tasks.heartbeat_codec import encode_heartbeat_field

            for k, v in heartbeat_data.items():
                pipe.hset(key, k, encode_heartbeat_field(v))
            for field in redis_stage_scoped_fields_to_drop(
                old_stage, stage, heartbeat_data.keys()
            ):
                pipe.hdel(key, field)
            pipe.expire(key, _HEARTBEAT_TTL)
            pipe.execute()
    except Exception as e:
        logger.debug(f"Redis heartbeat write failed for [{name}]: {e}")


def get_task_heartbeat(name: str) -> Optional[dict[str, Any]]:
    """获取任务心跳 (先查内存, 未命中查 Redis)"""
    local = _task_heartbeats.get(name)
    if local:
        return local

    try:
        from app.core.redis import get_sync_redis

        r = get_sync_redis()
        if r:
            key = f"{_HEARTBEAT_KEY_PREFIX}{name}"
            raw = r.hgetall(key)
            if raw:
                from app.core.tasks.heartbeat_codec import decode_heartbeat_field

                parsed: dict[str, Any] = {}
                for k, v in raw.items():
                    try:
                        parsed[k] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        parsed[k] = v
                    parsed[k] = decode_heartbeat_field(parsed[k])
                if "last_heartbeat" in parsed:
                    parsed["last_heartbeat"] = float(parsed["last_heartbeat"])
                return parsed
    except Exception:
        pass

    return None


def _clear_redis_heartbeat(name: str) -> None:
    """清理 Redis 中的心跳记录"""
    try:
        from app.core.redis import get_sync_redis

        r = get_sync_redis()
        if r:
            r.delete(f"{_HEARTBEAT_KEY_PREFIX}{name}")
    except Exception:
        pass


# ===================== 入队即心跳: ARQ 宽限期保护 =====================

def mark_task_queued(
    name: str,
    *,
    queue_mode: str,
    arq_job_id: Optional[str] = None,
    project_id: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """任务刚入队时写入一条"queued"心跳。

    目的:
    - 防止 `get_*_status` 在 enqueue 与第一条业务心跳之间的窗口把任务误判为"丢失"
      并再次投递 (历史 BUG: review / process 重复运行, 触发唯一键冲突和计费告警)。
    - worker 一旦开始处理, 它自己的 `update_heartbeat` 会覆盖这条 "queued" 心跳。
    - 默认可不携带业务进度；skip-vl 等本轮已声明的分支真值应经 extra 写入，
      避免进度组装在 worker 预检前把未知当成“仍要展示该步”。

    关键: ARQ 模式下只写 Redis, 不写内存。
    因为 ARQ worker 是独立进程, 它的业务心跳只写到 Redis;
    如果 API 进程在内存里缓存了 "queued", `get_task_heartbeat` 会优先读内存,
    永远看不到 worker 写到 Redis 的真实进度, 导致进度条卡死。
    """
    heartbeat_extra: dict[str, Any] = {
        "queue_mode": queue_mode,
        "enqueued_at": time.time(),
    }
    if arq_job_id is not None:
        heartbeat_extra["arq_job_id"] = arq_job_id
    if project_id is not None:
        heartbeat_extra["project_id"] = project_id
    if extra:
        heartbeat_extra.update(extra)

    from app.core.tasks.extract_progress_generation import (
        get_bound_extract_generation,
        write_extract_heartbeat_if_current,
    )
    from app.core.tasks.parallel_unit_progress import redis_stage_scoped_fields_to_drop

    if get_bound_extract_generation(name) is not None:
        # 提取任务: 走代际条件写入。ARQ 模式仍避免本进程缓存遮蔽 worker 进度。
        if queue_mode == "arq":
            _task_heartbeats.pop(name, None)
        outcome = write_extract_heartbeat_if_current(
            name,
            stage=TASK_STAGE_QUEUED,
            progress="",
            extra=heartbeat_extra,
        )
        if (
            outcome is not None
            and outcome.accepted
            and queue_mode != "arq"
        ):
            _task_heartbeats[name] = dict(outcome.snapshot)
        return

    heartbeat_data: dict[str, Any] = {
        "last_heartbeat": time.time(),
        "stage": TASK_STAGE_QUEUED,
        "progress": "",
        **heartbeat_extra,
    }

    if queue_mode == "arq":
        # ARQ 模式: 只写 Redis, 不污染本进程内存
        # 确保同时清掉内存中可能残留的旧心跳
        _task_heartbeats.pop(name, None)
        try:
            from app.core.redis import get_sync_redis
            r = get_sync_redis()
            if r:
                key = f"{_HEARTBEAT_KEY_PREFIX}{name}"
                try:
                    old_stage = str(r.hget(key, "stage") or "")
                except Exception:
                    old_stage = ""
                from app.core.tasks.heartbeat_codec import encode_heartbeat_field

                pipe = r.pipeline()
                for k, v in heartbeat_data.items():
                    pipe.hset(key, k, encode_heartbeat_field(v))
                for field in redis_stage_scoped_fields_to_drop(
                    old_stage, TASK_STAGE_QUEUED, heartbeat_data.keys()
                ):
                    pipe.hdel(key, field)
                pipe.expire(key, _HEARTBEAT_TTL)
                pipe.execute()
        except Exception as e:
            logger.debug(f"Redis heartbeat write failed for [{name}]: {e}")
    else:
        # asyncio 模式: 同进程, 写内存 + Redis
        update_heartbeat(name, stage=TASK_STAGE_QUEUED, progress="", extra=heartbeat_extra)


async def dispatch_background_job(
    *,
    task_name: str,
    arq_function: str,
    fallback_factory: Callable[[], Coroutine[Any, Any, Any]],
    task_type: str = "",
    project_id: Optional[int] = None,
    arq_args: tuple[Any, ...] = (),
    arq_kwargs: Optional[dict[str, Any]] = None,
) -> tuple[str, Optional[str]]:
    """统一任务分派: ARQ 优先, 不可用则回退到同进程 asyncio, 同时写 queued 心跳。

    收敛三大业务入口与写标交互型任务入口共享的分派契约:

    1. ``enqueue_job_safe(arq_function, *arq_args, **arq_kwargs)`` 入队;
    2. 若返回 None (开关关闭 / Redis 异常 / worker 缺失 / enqueue 抛错),
       通过 ``fallback_factory`` 生成同一条 ``run_*_task`` 协程作为本地降级;
    3. 无论走 ARQ 还是 asyncio, 都写入一条 ``queued`` 心跳, 让
       ``is_task_active`` / ``is_task_in_enqueue_grace`` 在 worker 领取前
       不会把任务误判为丢失。

    该 helper 的硬性约束:
      - ``fallback_factory`` 必须返回同一条 ``run_*_task`` 协程, 严禁业务层
        在 fallback 路径上另起"旁路实现"。
      - 回调失败不抛异常: 返回的 ``queue_mode`` 可为 "arq" / "asyncio_fallback",
        调用方根据该字段决定日志与响应。

    Args:
        task_name: 任务逻辑名 (``get_*_task_name``), 作为心跳键。
        arq_function: ARQ 注册的 job 函数名, 如 ``review_project_job``。
        fallback_factory: 无参工厂, 生成 ``run_*_task(...)`` 协程。
        task_type: 日志上下文 ``task_type`` (review / collusion / writing_*)。
        project_id: 日志上下文 ``project_id``, 同时记录到心跳。
        arq_args: 传给 ``enqueue_job_safe`` 的位置参数。
        arq_kwargs: 传给 ``enqueue_job_safe`` 的关键字参数。

    Returns:
        ``(queue_mode, arq_job_id)``。``queue_mode`` 为 "arq" 或 "asyncio_fallback";
        ``arq_job_id`` 仅在 ARQ 路径有值, 失败回退时为 None。
    """
    from app.core.arq_runtime import enqueue_job_detailed

    arq_kwargs = arq_kwargs or {}
    dispatch = await enqueue_job_detailed(arq_function, *arq_args, **arq_kwargs)
    arq_job_id = dispatch.job_id
    if dispatch.queued and arq_job_id is not None:
        queue_mode = "arq"
    else:
        from app.core.config import settings

        wait_seconds = float(getattr(settings, "WORKER_WAIT_BEFORE_FALLBACK_SEC", 30.0))
        poll_interval = max(float(getattr(settings, "WORKER_WAIT_POLL_INTERVAL", 2.0)), 0.5)
        deadline = time.monotonic() + wait_seconds
        while dispatch.should_wait_for_worker and time.monotonic() < deadline:
            mark_task_queued(
                task_name,
                queue_mode="waiting_worker",
                arq_job_id=None,
                project_id=project_id,
            )
            update_heartbeat(
                task_name,
                stage=TASK_STAGE_WAITING_WORKER,
                progress=f"等待 worker 恢复: reason={dispatch.reason}",
                extra={
                    "queue_mode": "waiting_worker",
                    "arq_reason": dispatch.reason,
                    "target_queue": dispatch.queue_name,
                    "project_id": project_id,
                },
            )
            await asyncio.sleep(poll_interval)
            dispatch = await enqueue_job_detailed(arq_function, *arq_args, **arq_kwargs)
            arq_job_id = dispatch.job_id
            if dispatch.queued and arq_job_id is not None:
                queue_mode = "arq"
                mark_task_queued(
                    task_name,
                    queue_mode=queue_mode,
                    arq_job_id=arq_job_id,
                    project_id=project_id,
                )
                return queue_mode, arq_job_id

        if dispatch.allows_in_process_fallback:
            queue_mode = "asyncio_fallback"
            create_background_task(
                fallback_factory(),
                name=task_name,
                task_type=task_type,
                project_id=project_id,
                fallback_control=True,
            )
        else:
            # ARQ 已配置但入队失败 + 等待窗耗尽, 永不挪到 API 进程; 写终态心跳交由
            # 各流程既有 dead-task 判定 (TASK_TERMINAL_STAGES 短路) 转 FAILED,
            # 串标/审查经 MAX_RECOVERY 有界重试后也转 FAILED。
            queue_mode = "worker_unavailable"
            update_heartbeat(
                task_name,
                stage="failed",
                progress=(
                    f"Worker 不可用: reason={dispatch.reason}, "
                    f"请检查 worker 服务后重试"
                ),
                extra={
                    "queue_mode": queue_mode,
                    "arq_reason": dispatch.reason,
                    "target_queue": dispatch.queue_name,
                    "project_id": project_id,
                },
            )
            # 不调用 fallback_factory: 协程从未创建, 不会有 "never awaited" 告警
            return queue_mode, None

    mark_task_queued(
        task_name,
        queue_mode=queue_mode,
        arq_job_id=arq_job_id,
        project_id=project_id,
    )
    if queue_mode == "asyncio_fallback":
        update_heartbeat(
            task_name,
            stage=TASK_STAGE_FALLBACK_WAITING,
            progress=f"准备本地兜底: reason={dispatch.reason}",
            extra={
                "queue_mode": queue_mode,
                "arq_reason": dispatch.reason,
                "target_queue": dispatch.queue_name,
                "project_id": project_id,
            },
        )
    return queue_mode, arq_job_id


def is_task_in_enqueue_grace(
    name: str,
    grace_sec: int = ENQUEUE_GRACE_SEC,
) -> bool:
    """任务是否处于"刚入队但 worker 还没接管"的宽限窗口。

    用于各业务 `get_*_status` 的恢复判定: 若返回 True 则即使 `is_task_active` 为 False
    也不应重新投递任务。
    """
    heartbeat = get_task_heartbeat(name)
    if not heartbeat:
        return False

    stage = str(heartbeat.get("stage", "")).strip().lower()
    if stage != TASK_STAGE_QUEUED:
        return False

    enqueued_at = heartbeat.get("enqueued_at")
    try:
        if enqueued_at is None:
            enqueued_at = heartbeat.get("last_heartbeat", 0)
        elapsed = time.time() - float(enqueued_at)
    except (TypeError, ValueError):
        return False

    return 0 <= elapsed < grace_sec


# ===================== recovery counting =====================

def get_recovery_count(name: str) -> int:
    """获取任务的自动恢复次数"""
    return _recovery_counts.get(name, 0)


def increment_recovery_count(name: str) -> int:
    """递增任务恢复次数并返回新值"""
    _recovery_counts[name] = _recovery_counts.get(name, 0) + 1
    return _recovery_counts[name]


def reset_recovery_count(name: str) -> None:
    """重置任务恢复次数"""
    _recovery_counts.pop(name, None)


# ===================== 进度停滞缓存 =====================
#
# 设计说明(任务契约统一收口):
# - 串标 status 端点会比较"上一次观测到的 compared_count"与当前值, 判定是否停滞;
# - 该观察必须跨 API 实例一致, 否则多副本 / 滚动发布期间每台 API 都会独立判定停滞,
# 导致同一项目被重复触发恢复 (BUG-type: 语义分叉).
# - 因此主存储迁到 Redis; 本地字典仅作为 Redis 不可用时的降级兜底, 不允许改变外部语义.

_collusion_stall_cache: dict[int, dict[str, Any]] = {}
_COLLUSION_STALL_KEY_PREFIX = "bidmind:collusion_stall:"
_COLLUSION_STALL_TTL = 1800  # 30min, 远大于常见 stall_timeout


def _collusion_stall_key(project_id: int) -> str:
    return f"{_COLLUSION_STALL_KEY_PREFIX}{project_id}"


def read_collusion_stall_snapshot(project_id: int) -> Optional[dict[str, Any]]:
    """读取串标进度停滞快照.

    优先读取 Redis (跨实例一致), 失败时回落到本地字典, 始终返回可用字段:
        ``{"timestamp": float, "compared_count": int}`` 或 None.
    """
    try:
        from app.core.redis import get_sync_redis

        r = get_sync_redis()
        if r:
            raw = r.get(_collusion_stall_key(project_id))
            if raw:
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        data["timestamp"] = float(data.get("timestamp", 0.0))
                        data["compared_count"] = int(data.get("compared_count", 0))
                        return data
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
    except Exception as exc:
        logger.debug(f"[collusion_stall] Redis read failed: {exc}")

    return _collusion_stall_cache.get(project_id)


def write_collusion_stall_snapshot(
    project_id: int,
    *,
    timestamp: float,
    compared_count: int,
) -> None:
    """写入串标进度停滞快照 (Redis + 本地降级)."""
    snapshot = {"timestamp": float(timestamp), "compared_count": int(compared_count)}
    _collusion_stall_cache[project_id] = snapshot

    try:
        from app.core.redis import get_sync_redis

        r = get_sync_redis()
        if r:
            r.set(
                _collusion_stall_key(project_id),
                json.dumps(snapshot, ensure_ascii=False),
                ex=_COLLUSION_STALL_TTL,
            )
    except Exception as exc:
        logger.debug(f"[collusion_stall] Redis write failed: {exc}")


def get_collusion_stall_cache() -> dict[int, dict[str, Any]]:
    """兼容接口 (deprecated): 返回本地字典, 不再参与语义判定.

    保留仅供历史调用方使用. 新代码应调用
    ``read_collusion_stall_snapshot`` / ``write_collusion_stall_snapshot``.
    """
    return _collusion_stall_cache


def clear_collusion_cache(project_id: int) -> None:
    """清理指定项目的串标进度停滞缓存 (Redis + 本地)."""
    _collusion_stall_cache.pop(project_id, None)
    try:
        from app.core.redis import get_sync_redis

        r = get_sync_redis()
        if r:
            r.delete(_collusion_stall_key(project_id))
    except Exception as exc:
        logger.debug(f"[collusion_stall] Redis clear failed: {exc}")


__all__ = [
    "run_review_task",
    "run_extract_task",
    "get_extract_task_name",
    "run_process_task",
    "get_process_task_name",
    "run_collusion_task",
    "get_collusion_task_name",
    "create_background_task",
    "get_active_tasks_count",
    "get_task",
    "is_task_active",
    "inspect_task_liveness",
    "TaskLiveness",
    "cancel_task",
    "cancel_all_background_tasks",
    "update_heartbeat",
    "get_task_heartbeat",
    "cleanup_task",
    "HEARTBEAT_TIMEOUT",
    "MAX_RECOVERY",
    "ENQUEUE_GRACE_SEC",
    "TASK_STAGE_QUEUED",
    "TASK_TERMINAL_STAGES",
    "mark_task_queued",
    "dispatch_background_job",
    "is_task_in_enqueue_grace",
    "get_recovery_count",
    "increment_recovery_count",
    "reset_recovery_count",
    "get_collusion_stall_cache",
    "read_collusion_stall_snapshot",
    "write_collusion_stall_snapshot",
    "clear_collusion_cache",
    "run_writing_compose_task",
    "get_writing_compose_task_name",
    "run_writing_extract_task",
    "get_writing_extract_task_name",
    "run_writing_clause_vectorize_task",
    "get_writing_clause_vectorize_task_name",
]
