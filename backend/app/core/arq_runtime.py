"""ARQ 运行时辅助

职责:
- Worker 进程在启动/保活/关闭时通过 Redis 暴露 alive 心跳
- API/Service 层通过 can_use_arq() 统一判定能否走 ARQ 调度
- 只要 worker 心跳在新鲜窗口内，并且 VECTOR_PREFER_ARQ=true，才真正入队

心跳键格式:
    bidmind:arq_worker:alive:{worker_id}  -> JSON(worker_id, host, pid, functions, last_seen)

设计原则:
- 读写错误全部降级为 False/空列表，绝不因 Redis 抖动打断业务
- sync/async 双入口: worker 侧以 async 为主; API/服务判定允许用 sync 避免额外协程成本
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger

from app.core.config import settings

ARQ_WORKER_ALIVE_KEY_PREFIX = "bidmind:arq_worker:alive:"
ARQ_WORKER_SCAN_MATCH = f"{ARQ_WORKER_ALIVE_KEY_PREFIX}*"

# 项目级取消墓碑: 删除项目时写入, worker 任何阶段读到即视为"需要立即退出"
# 自增主键保证新建项目不会复用旧 ID, TTL 墓碑可安全留存
PROJECT_CANCEL_KEY_PREFIX = "bidmind:project_cancelled:"

# ARQ 队列级取消黑名单: 记录被撤销但可能还在队列/已被领取的 job_id
# worker 侧进入任务前先比对, 命中即立即返回
ARQ_JOB_CANCEL_KEY_PREFIX = "bidmind:arq_job_cancelled:"

DEFAULT_WORKER_HEARTBEAT_TTL = 180
DEFAULT_WORKER_HEARTBEAT_INTERVAL = 15
DEFAULT_WORKER_FRESHNESS_WINDOW = 180
DEFAULT_PROJECT_CANCEL_TTL = 86400  # 24h, 足以覆盖最长任务的自然终止
DEFAULT_ARQ_JOB_CANCEL_TTL = 3600   # 1h, 足以覆盖 queued -> claimed 的窗口


@dataclass
class ArqDispatchResult:
    """ARQ 入队判定结果。

    保留结构化原因, 避免调用方只能看到 job_id=None 而无法判断为何回退。
    """

    queued: bool
    function_name: str
    job_id: Optional[str] = None
    reason: str = ""
    queue_name: Optional[str] = None
    alive_workers: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def should_wait_for_worker(self) -> bool:
        """是否属于 worker 暂不可用, 可先等待再 fallback。

        覆盖全部非配置类入队失败原因: worker_missing / function_worker_missing /
        redis_unavailable / enqueue_error。prefer_arq_disabled 属配置层异常,
        不应让任务无限死等; available/queued 属于已成功路径。
        """

        return (
            not self.queued
            and self.reason
            not in {"prefer_arq_disabled", "available", "queued"}
        )

    @property
    def allows_in_process_fallback(self) -> bool:
        """职责: 判定分派结果能否落到 API 同进程执行。

        不做: 不决定等待时长、不写心跳、不替代 dispatch_background_job 的死循环防御。
        仅当 ARQ 根本没启用 (prefer_arq_disabled) 且后台 fallback 开关打开时,
        才允许同进程执行; ARQ 已配置但故障时永不挪到 API 进程。
        """

        if self.reason != "prefer_arq_disabled":
            return False
        return bool(getattr(settings, "BACKGROUND_FALLBACK_ENABLED", True))


class ProjectCancelledError(Exception):
    """项目已删除/取消导致任务应立即停止。

    与普通业务失败区分: 捕获此异常的收尾路径必须走"静默清理", 不写 FAILED 态、
    不写超长 last_error、不再触发状态表 upsert。
    """

    def __init__(self, project_id: int, detail: str = "") -> None:
        self.project_id = project_id
        self.detail = detail
        super().__init__(f"project {project_id} cancelled: {detail}" if detail else f"project {project_id} cancelled")


def _ttl_seconds() -> int:
    return int(getattr(settings, "ARQ_WORKER_HEARTBEAT_TTL", DEFAULT_WORKER_HEARTBEAT_TTL))


def _interval_seconds() -> int:
    return int(getattr(settings, "ARQ_WORKER_HEARTBEAT_INTERVAL", DEFAULT_WORKER_HEARTBEAT_INTERVAL))


def _freshness_window_seconds() -> int:
    return int(getattr(settings, "ARQ_WORKER_FRESHNESS_WINDOW", DEFAULT_WORKER_FRESHNESS_WINDOW))


def _project_cancel_ttl() -> int:
    return int(getattr(settings, "ARQ_PROJECT_CANCEL_TTL", DEFAULT_PROJECT_CANCEL_TTL))


def _arq_job_cancel_ttl() -> int:
    return int(getattr(settings, "ARQ_JOB_CANCEL_TTL", DEFAULT_ARQ_JOB_CANCEL_TTL))


def _alive_key(worker_id: str) -> str:
    return f"{ARQ_WORKER_ALIVE_KEY_PREFIX}{worker_id}"


def _project_cancel_key(project_id: int) -> str:
    return f"{PROJECT_CANCEL_KEY_PREFIX}{project_id}"


def _arq_job_cancel_key(job_id: str) -> str:
    return f"{ARQ_JOB_CANCEL_KEY_PREFIX}{job_id}"


def generate_worker_id() -> str:
    """生成 worker 唯一标识: host-pid-短uuid。重启必定变化，便于区分进程实例。"""
    host = socket.gethostname() or "unknown"
    pid = os.getpid()
    short = uuid.uuid4().hex[:6]
    return f"{host}-{pid}-{short}"


def _build_worker_info(
    worker_id: str,
    functions: list[str],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "worker_id": worker_id,
        "host": socket.gethostname() or "unknown",
        "pid": os.getpid(),
        "functions": list(functions or []),
        "last_seen": int(time.time()),
    }
    if extra:
        info.update(extra)
    return info


def _parse_worker_info(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    try:
        text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        info = json.loads(text)
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        return None
    if not isinstance(info, dict):
        return None
    return info


def _is_fresh(info: dict[str, Any]) -> bool:
    try:
        last_seen = int(info.get("last_seen", 0))
    except (TypeError, ValueError):
        return False
    if last_seen <= 0:
        return False
    return (int(time.time()) - last_seen) <= _freshness_window_seconds()


# ==================== Worker 侧 (async) ====================

async def record_worker_heartbeat_async(
    redis_client: Any,
    worker_id: str,
    functions: list[str],
    extra: Optional[dict[str, Any]] = None,
) -> bool:
    """异步写入 worker 心跳。Redis 不可用时静默降级为 False。"""
    if redis_client is None:
        return False
    info = _build_worker_info(worker_id, functions, extra)
    try:
        await redis_client.set(
            _alive_key(worker_id),
            json.dumps(info, ensure_ascii=False),
            ex=_ttl_seconds(),
        )
        return True
    except Exception as exc:
        logger.debug(f"worker 心跳写入失败: worker_id={worker_id}, err={exc}")
        return False


async def clear_worker_heartbeat_async(redis_client: Any, worker_id: str) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.delete(_alive_key(worker_id))
    except Exception as exc:
        logger.debug(f"worker 心跳清理失败: worker_id={worker_id}, err={exc}")


async def heartbeat_refresh_loop(
    redis_client: Any,
    worker_id: str,
    functions: list[str],
    stop_event: asyncio.Event,
    extra_provider: Optional[Callable[[], dict[str, Any]]] = None,
) -> None:
    """后台保活任务: 按 interval 周期刷新心跳，直到 stop_event 触发。"""
    interval = _interval_seconds()
    expected_next = time.monotonic()
    while not stop_event.is_set():
        now = time.monotonic()
        lag_ms = max(int((now - expected_next) * 1000), 0)
        extra = {"event_loop_lag_ms": lag_ms}
        if extra_provider is not None:
            try:
                extra.update(extra_provider() or {})
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"worker 心跳 extra_provider 失败: worker_id={worker_id}, err={exc}")
        await record_worker_heartbeat_async(redis_client, worker_id, functions, extra=extra)
        if lag_ms > int(getattr(settings, "WORKER_EVENT_LOOP_LAG_WARN_MS", 5000)):
            logger.warning(f"worker event loop 延迟过高: worker_id={worker_id}, lag_ms={lag_ms}")
        expected_next = time.monotonic() + interval
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break


# ==================== API/Service 侧 ====================

async def scan_alive_workers_async(redis_client: Any) -> list[dict[str, Any]]:
    if redis_client is None:
        return []
    workers: list[dict[str, Any]] = []
    try:
        async for key in redis_client.scan_iter(match=ARQ_WORKER_SCAN_MATCH):
            raw = await redis_client.get(key)
            info = _parse_worker_info(raw)
            if info is None:
                continue
            if not _is_fresh(info):
                continue
            workers.append(info)
    except Exception as exc:
        logger.debug(f"async 扫描 worker 心跳失败: {exc}")
    return workers


def scan_alive_workers_sync() -> list[dict[str, Any]]:
    """同步扫描存活 worker, 供 FastAPI 请求链路和心跳查询使用。"""
    from app.core.redis import get_sync_redis

    client = get_sync_redis()
    if client is None:
        return []
    workers: list[dict[str, Any]] = []
    try:
        for key in client.scan_iter(match=ARQ_WORKER_SCAN_MATCH):
            raw = client.get(key)
            info = _parse_worker_info(raw)
            if info is None:
                continue
            if not _is_fresh(info):
                continue
            workers.append(info)
    except Exception as exc:
        logger.debug(f"sync 扫描 worker 心跳失败: {exc}")
    return workers


def has_alive_worker_for(function_name: Optional[str] = None) -> bool:
    """检测当前是否存在支持给定函数的新鲜 worker 心跳。"""
    workers = scan_alive_workers_sync()
    if not workers:
        return False
    if function_name is None:
        return True
    for w in workers:
        funcs = w.get("functions") or []
        if function_name in funcs:
            return True
    return False


async def has_alive_worker_for_async(
    redis_client: Any,
    function_name: Optional[str] = None,
) -> bool:
    workers = await scan_alive_workers_async(redis_client)
    if not workers:
        return False
    if function_name is None:
        return True
    for w in workers:
        funcs = w.get("functions") or []
        if function_name in funcs:
            return True
    return False


async def can_use_arq(function_name: Optional[str] = None) -> bool:
    """统一 ARQ 可用性判定:
      1. settings.VECTOR_PREFER_ARQ 必须为 True (运维开关)
      2. ARQ Redis 连接池可用 (基础设施)
      3. 至少存在一个支持该函数的新鲜 worker 心跳 (消费者)

    三者任一不满足即返回 False，由调用方回退到 asyncio 后台任务。
    """
    if not bool(getattr(settings, "VECTOR_PREFER_ARQ", False)):
        return False

    from app.core.redis import get_redis_pool

    pool = await get_redis_pool()
    if pool is None:
        return False

    return await has_alive_worker_for_async(pool, function_name)


async def diagnose_arq_availability(function_name: Optional[str] = None) -> ArqDispatchResult:
    """返回 ARQ 可用性诊断结果, 不执行入队。"""

    if not bool(getattr(settings, "VECTOR_PREFER_ARQ", False)):
        return ArqDispatchResult(
            queued=False,
            function_name=function_name or "",
            reason="prefer_arq_disabled",
        )

    from app.core.redis import get_redis_pool

    pool = await get_redis_pool()
    if pool is None:
        return ArqDispatchResult(
            queued=False,
            function_name=function_name or "",
            reason="redis_unavailable",
        )

    workers = await scan_alive_workers_async(pool)
    if not workers:
        return ArqDispatchResult(
            queued=False,
            function_name=function_name or "",
            reason="worker_missing",
            alive_workers=[],
        )
    if function_name is not None:
        for worker in workers:
            if function_name in (worker.get("functions") or []):
                return ArqDispatchResult(
                    queued=False,
                    function_name=function_name,
                    reason="available",
                    alive_workers=workers,
                )
        return ArqDispatchResult(
            queued=False,
            function_name=function_name,
            reason="function_worker_missing",
            alive_workers=workers,
        )

    return ArqDispatchResult(
        queued=False,
        function_name="",
        reason="available",
        alive_workers=workers,
    )


_ARQ_ARG_STR_PREVIEW_LIMIT = 80


def _summarize_arq_args(args: tuple) -> str:
    """生成 ARQ 任务参数摘要, 避免大对象/base64 被打印到日志。

    标量(int/float/bool/None)与短字符串保留 repr; 长字符串、容器、字节串
    替换为 ``<type:len=N>`` 形式; 自定义对象只显示类型名。
    """
    parts: list[str] = []
    for a in args:
        if a is None or isinstance(a, bool):
            parts.append(repr(a))
        elif isinstance(a, (int, float)):
            parts.append(repr(a))
        elif isinstance(a, str):
            if len(a) <= _ARQ_ARG_STR_PREVIEW_LIMIT:
                parts.append(repr(a))
            else:
                parts.append(f"<str:len={len(a)}>")
        elif isinstance(a, (bytes, bytearray)):
            parts.append(f"<{type(a).__name__}:len={len(a)}>")
        elif isinstance(a, dict):
            parts.append(f"<dict:keys={len(a)}>")
        elif isinstance(a, (list, tuple, set, frozenset)):
            parts.append(f"<{type(a).__name__}:len={len(a)}>")
        else:
            parts.append(f"<{type(a).__name__}>")
    return f"({', '.join(parts)})"


async def enqueue_job_detailed(
    function_name: str,
    *args: Any,
    **kwargs: Any,
) -> ArqDispatchResult:
    """在满足 ARQ 条件时入队, 并返回结构化结果。"""

    availability = await diagnose_arq_availability(function_name)
    try:
        from app.core.tasks.resource_policy import policy_for_function

        policy = policy_for_function(function_name)
        queue_name = policy.queue_name
    except Exception:
        queue_name = None
    availability.queue_name = queue_name
    if availability.reason != "available":
        logger.info(
            f"ARQ 不可用, 暂不入队: fn={function_name}, reason={availability.reason}, "
            f"queue={queue_name}, alive_workers={len(availability.alive_workers)}"
        )
        return availability

    from app.core.redis import get_redis_pool

    pool = await get_redis_pool()
    if pool is None:
        return ArqDispatchResult(
            queued=False,
            function_name=function_name,
            reason="redis_unavailable",
            queue_name=queue_name,
            alive_workers=availability.alive_workers,
        )

    try:
        enqueue_kwargs = dict(kwargs)
        if queue_name and bool(getattr(settings, "WORKER_RESOURCE_QUEUES_ENABLED", False)):
            enqueue_kwargs["_queue_name"] = queue_name
        job = await pool.enqueue_job(function_name, *args, **enqueue_kwargs)
        job_id = getattr(job, "job_id", None) if job is not None else None
        resolved = job_id or "arq-job-unknown"
        logger.info(
            f"已投递 ARQ 任务: fn={function_name}, job_id={resolved}, "
            f"queue={queue_name}, args={_summarize_arq_args(args)}"
        )
        return ArqDispatchResult(
            queued=True,
            function_name=function_name,
            job_id=resolved,
            reason="queued",
            queue_name=queue_name,
            alive_workers=availability.alive_workers,
        )
    except Exception as exc:
        logger.warning(f"ARQ enqueue 失败, 回退候选: fn={function_name}, err={exc}")
        return ArqDispatchResult(
            queued=False,
            function_name=function_name,
            reason="enqueue_error",
            queue_name=queue_name,
            alive_workers=availability.alive_workers,
            error=str(exc),
        )


async def enqueue_job_safe(
    function_name: str,
    *args: Any,
    **kwargs: Any,
) -> Optional[str]:
    """在满足 can_use_arq 的前提下入队 ARQ 任务, 失败返回 None。

    调用方模式:
        job_id = await enqueue_job_safe("review_project_job", project_id)
        if job_id is None:
            create_background_task(run_review_task(project_id), ...)  # 回退

    返回 None 的场景:
      - VECTOR_PREFER_ARQ=false
      - Redis 不可用
      - 没有支持该 function 的新鲜 worker
      - enqueue_job 自身抛异常
    """
    result = await enqueue_job_detailed(function_name, *args, **kwargs)
    return result.job_id if result.queued else None


def can_use_arq_sync(function_name: Optional[str] = None) -> bool:
    """同步版判定: 仅在已知同步上下文且 Redis 池已就绪时调用。

    未直接持有 async 池, 所以只能通过 sync 心跳扫描 + settings 开关判断;
    Redis 层面连通性通过 scan_alive_workers_sync 内部处理 (失败返回 [])
    """
    if not bool(getattr(settings, "VECTOR_PREFER_ARQ", False)):
        return False
    return has_alive_worker_for(function_name)


# ==================== 项目取消墓碑 (删除一致性核心) ====================

async def mark_project_cancelled_async(
    redis_client: Any,
    project_id: int,
    *,
    reason: str = "project_deleted",
) -> bool:
    """写入项目取消墓碑 (async 版, 供 API/Service 侧在删除项目时调用)。

    - 成功返回 True, Redis 不可用时返回 False 但不抛异常
    - TTL 覆盖"最长可能还在运行的任务", 到期自动清理
    """
    if redis_client is None:
        return False
    try:
        payload = json.dumps({
            "project_id": project_id,
            "reason": reason,
            "marked_at": int(time.time()),
        }, ensure_ascii=False)
        await redis_client.set(
            _project_cancel_key(project_id),
            payload,
            ex=_project_cancel_ttl(),
        )
        logger.info(f"已写入项目取消墓碑: project_id={project_id}, reason={reason}")
        return True
    except Exception as exc:
        logger.warning(f"写入项目取消墓碑失败: project_id={project_id}, err={exc}")
        return False


def mark_project_cancelled_sync(project_id: int, *, reason: str = "project_deleted") -> bool:
    """同步版墓碑写入, 供不便使用 async Redis 的同步路径使用。"""
    from app.core.redis import get_sync_redis

    client = get_sync_redis()
    if client is None:
        return False
    try:
        payload = json.dumps({
            "project_id": project_id,
            "reason": reason,
            "marked_at": int(time.time()),
        }, ensure_ascii=False)
        client.set(_project_cancel_key(project_id), payload, ex=_project_cancel_ttl())
        return True
    except Exception as exc:
        logger.debug(f"sync 写入项目取消墓碑失败: project_id={project_id}, err={exc}")
        return False


async def clear_project_cancelled_async(redis_client: Any, project_id: int) -> None:
    """清理项目取消墓碑 (测试/恢复用, 正常不需手动调用)。"""
    if redis_client is None:
        return
    try:
        await redis_client.delete(_project_cancel_key(project_id))
    except Exception as exc:
        logger.debug(f"清理项目取消墓碑失败: project_id={project_id}, err={exc}")


def is_project_cancelled(project_id: int) -> bool:
    """同步判定项目是否已取消/删除 (worker 侧高频检查用)。

    Redis 不可用时返回 False, 避免因基础设施抖动误伤正常任务。
    """
    from app.core.redis import get_sync_redis

    client = get_sync_redis()
    if client is None:
        return False
    try:
        raw = client.get(_project_cancel_key(project_id))
        return raw is not None
    except Exception as exc:
        logger.debug(f"查询项目取消墓碑失败: project_id={project_id}, err={exc}")
        return False


async def is_project_cancelled_async(redis_client: Any, project_id: int) -> bool:
    """async 版项目取消判定, 供 worker 任务内部长流程使用。"""
    if redis_client is None:
        # 兜底走 sync 路径, 避免 ctx.redis 临时不可用时丢失取消信号
        return is_project_cancelled(project_id)
    try:
        raw = await redis_client.get(_project_cancel_key(project_id))
        return raw is not None
    except Exception as exc:
        logger.debug(f"async 查询项目取消墓碑失败: project_id={project_id}, err={exc}")
        return False


def raise_if_project_cancelled(project_id: int) -> None:
    """同步检查点: 命中墓碑则抛 ProjectCancelledError, 由上层统一处理。"""
    if is_project_cancelled(project_id):
        raise ProjectCancelledError(project_id)


async def raise_if_project_cancelled_async(
    redis_client: Any,
    project_id: int,
) -> None:
    """async 检查点: 命中墓碑则抛 ProjectCancelledError。"""
    if await is_project_cancelled_async(redis_client, project_id):
        raise ProjectCancelledError(project_id)


# ==================== ARQ 队列级任务取消 ====================

async def mark_arq_job_cancelled_async(
    redis_client: Any,
    job_id: str,
    *,
    reason: str = "project_deleted",
) -> bool:
    """把具体 ARQ job_id 记入取消黑名单, worker 领取后比对立即丢弃。

    用于兜底那些无法直接从 ARQ 队列里物理移除的 job (例如已被 pop 但未开始)。
    """
    if redis_client is None or not job_id:
        return False
    try:
        payload = json.dumps({
            "job_id": job_id,
            "reason": reason,
            "marked_at": int(time.time()),
        }, ensure_ascii=False)
        await redis_client.set(
            _arq_job_cancel_key(job_id),
            payload,
            ex=_arq_job_cancel_ttl(),
        )
        return True
    except Exception as exc:
        logger.debug(f"写入 ARQ job 取消黑名单失败: job_id={job_id}, err={exc}")
        return False


async def is_arq_job_cancelled_async(redis_client: Any, job_id: Optional[str]) -> bool:
    if redis_client is None or not job_id:
        return False
    try:
        raw = await redis_client.get(_arq_job_cancel_key(job_id))
        return raw is not None
    except Exception as exc:
        logger.debug(f"查询 ARQ job 取消黑名单失败: job_id={job_id}, err={exc}")
        return False


async def arq_job_present_async(redis_client: Any, job_id: Optional[str]) -> bool:
    """探测 ARQ 中是否仍存在该 job 的任何痕迹(排队/运行/结果/重试)。

 用于区分"持久化状态停在 QUEUED 但 ARQ 已无 job"的幽灵任务:
 job 被领取后在 RUNNING 落库前中断 -> 状态机停在 QUEUED, 而 ARQ job 失败/结果
 过期后从 Redis 消失, 仅凭 worker 存活无法判定其健康。

 返回语义(保守优先, 宁可漏判不可误恢复):
 - True: 仍存在任一 key, 或 redis 不可用/查询异常(无法证伪即视为在途)
 - False: 四类 key 全部不存在, 可判定为 ARQ 侧已无该 job
 """
    if redis_client is None or not job_id:
        return True
    try:
        from arq.constants import (
            in_progress_key_prefix,
            job_key_prefix,
            result_key_prefix,
            retry_key_prefix,
        )

        keys = [
            f"{job_key_prefix}{job_id}",
            f"{result_key_prefix}{job_id}",
            f"{in_progress_key_prefix}{job_id}",
            f"{retry_key_prefix}{job_id}",
        ]
        total = await redis_client.exists(*keys)
        return bool(total)
    except Exception as exc:
        logger.debug(f"探测 ARQ job 存在性失败, 保守视为在途: job_id={job_id}, err={exc}")
        return True


async def abort_arq_job_best_effort(
    redis_client: Any,
    job_id: str,
) -> tuple[bool, str]:
    """尝试用 arq 原生能力撤销队列任务, 失败则返回 (False, reason)。

    返回 (abort_ok, method):
      - method="native_abort": 调用 Job.abort() 成功
      - method="tombstone": 原生无法撤销, 改写黑名单由 worker 侧拦截
      - method="unavailable": 既无法 abort 也无法写黑名单
    """
    if redis_client is None or not job_id:
        return False, "unavailable"

    # 优先使用 arq 自带的 Job.abort(): 若任务尚未出队或 worker 支持 abort_jobs, 会被清掉
    try:
        from arq.jobs import Job  # noqa: PLC0415

        job = Job(job_id, redis_client)
        aborted = await job.abort(timeout=0.5)
        if aborted:
            logger.info(f"已通过 ARQ 原生 abort 取消任务: job_id={job_id}")
            return True, "native_abort"
    except Exception as exc:
        logger.debug(f"ARQ 原生 abort 不可用, 回退黑名单: job_id={job_id}, err={exc}")

    # 兜底: 写黑名单, 让 worker 领取后立即判定为取消
    ok = await mark_arq_job_cancelled_async(redis_client, job_id)
    return ok, "tombstone" if ok else "unavailable"
