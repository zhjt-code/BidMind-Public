"""Redis backed resource permits for worker and fallback tasks.: ARQ Redis 默认返回 bytes。orphan 标记 / owner 心跳必须 decode 后再解析，
失败探测不得续 inflight TTL；无主宽限覆盖登记窗口而不是 occupancy TTL。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from loguru import logger

from app.core.config import settings
from app.core.tasks.resource_policy import ResourceType, max_jobs_for_resource


_RESOURCE_KEY_PREFIX = "bidmind:worker:resource:"
_FALLBACK_RESOURCE = "fallback"
_OWNER_SEGMENT_PLACEHOLDER = "none"


@dataclass(frozen=True)
class PermitSnapshot:
    """当前资源许可占用快照。"""

    resource_type: str
    inflight: int
    limit: int


def _resource_key(resource_type: str) -> str:
    return f"{_RESOURCE_KEY_PREFIX}{resource_type}:inflight"


def _owner_key(resource_type: str) -> str:
    return f"{_RESOURCE_KEY_PREFIX}{resource_type}:owners"


def _orphan_marker_key(resource_type: str) -> str:
    return f"{_RESOURCE_KEY_PREFIX}{resource_type}:orphan"


def _owner_segment(value: object) -> str:
    """归一化 owner 单个字段; owner 以 ":" 分段, 段内冒号会破坏分段语义。"""

    text = _redis_text(value).strip()
    if not text or text.lower() == "none":
        return _OWNER_SEGMENT_PLACEHOLDER
    return text.replace(":", "_")


def _redis_text(value: object) -> str:
    """把 Redis 返回值收成可解析文本。

    职责: ARQ 客户端默认 decode_responses=False，bytes 必须 decode。
    不做: 不访问 Redis、不改 TTL、不把 str(bytes) 的 repr 当正文。
    """

    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode()
        except UnicodeDecodeError:
            return ""
    return str(value)


def build_permit_owner(
    *,
    worker_id: object,
    function_name: object,
    job_id: object = None,
    project_id: object = None,
) -> str:
    """构造 job 级唯一的资源许可 owner 标识。

 同一 worker 上同一函数可能有多个并发 job, owner 必须带 job 级标识才能与在途 job
 一一对应。job_id 缺失时退化为随机串: 宁可多出一条 owner, 也不能让两个 job 共用
 同一条, 否则先退出者会删掉仍在运行者的 owner 记录。
 """

    job_segment = _owner_segment(job_id)
    if job_segment == _OWNER_SEGMENT_PLACEHOLDER:
        job_segment = f"anon-{uuid.uuid4().hex[:12]}"
    return ":".join(
        (
            _owner_segment(worker_id),
            _owner_segment(function_name),
            job_segment,
            _owner_segment(project_id),
        )
    )


def _permit_enabled() -> bool:
    return bool(getattr(settings, "WORKER_RESOURCE_PERMIT_ENABLED", True))


def _ttl_seconds(resource_type: Optional[str] = None) -> int:
    """许可占用 TTL, 决定持有者死亡后残留多久才自愈。

    秒级临界区 (gpu) 用独立短 TTL: 进程被杀留下的计数按分钟过期, 不能沿用
    job 级资源的小时级 TTL。持有期间心跳会持续续期, 短 TTL 不会误清长任务。
    """

    if resource_type == "gpu":
        return max(int(getattr(settings, "GPU_ADMISSION_PERMIT_TTL_SEC", 300)), 30)
    return int(getattr(settings, "WORKER_RESOURCE_PERMIT_TTL", 3600))


def _owner_heartbeat_interval(resource_type: Optional[str] = None) -> float:
    """owner 心跳续期间隔, 必须远小于超时阈值, 否则心跳过期不足以证明持有者已死。"""

    ttl = _ttl_seconds(resource_type)
    configured = getattr(settings, "WORKER_RESOURCE_PERMIT_HEARTBEAT_INTERVAL", None)
    default = max(min(float(ttl) / 12.0, 300.0), 5.0)
    return max(float(configured if configured is not None else default), 5.0)


def _orphan_grace_seconds() -> int:
    """无主计数需要连续存在多久才允许回收。"""

    return max(int(getattr(settings, "WORKER_RESOURCE_PERMIT_ORPHAN_GRACE", 60)), 1)


async def _decrement(redis_client: object, resource_type: str) -> None:
    try:
        value = await redis_client.decr(_resource_key(resource_type))  # type: ignore[attr-defined]
        if int(value or 0) <= 0:
            await redis_client.delete(_resource_key(resource_type))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"释放资源许可失败: resource={resource_type}, err={exc}")


async def acquire_resource_permit(
    redis_client: object,
    resource_type: ResourceType | str,
    *,
    limit: Optional[int] = None,
    wait_timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    owner: Optional[str] = None,
) -> bool:
    """尝试获取资源许可。

    Redis 不可用时返回 True, 保持后台任务兜底可运行; 观测指标会标记 degraded。
    """

    if not _permit_enabled() or redis_client is None:
        return True

    resource = str(resource_type)
    resolved_limit = max(int(limit if limit is not None else max_jobs_for_resource(resource)), 1)
    timeout = float(
        wait_timeout
        if wait_timeout is not None
        else getattr(settings, "WORKER_RESOURCE_PERMIT_WAIT_TIMEOUT", 30.0)
    )
    interval = max(
        float(
            poll_interval
            if poll_interval is not None
            else getattr(settings, "WORKER_RESOURCE_PERMIT_POLL_INTERVAL", 1.0)
        ),
        0.1,
    )
    deadline = time.monotonic() + timeout
    started_at = time.monotonic()

    try:
        await _reconcile_stale_owners(redis_client, resource)
        while True:
            current = await redis_client.incr(_resource_key(resource))  # type: ignore[attr-defined]
            if int(current) <= resolved_limit:
                # 失败探测不得续 inflight TTL，否则幽灵计数被等待循环养到永不过期。
                await redis_client.expire(_resource_key(resource), _ttl_seconds(resource))  # type: ignore[attr-defined]
                if owner and not await _register_owner(redis_client, resource, owner):
                    # owner 登记失败则计数没有对应主体, 宁可拒绝本次占用让调用方重排队
                    await _decrement(redis_client, resource)
                    _record_permit_metrics(
                        resource, resolved_limit, time.monotonic() - started_at, False
                    )
                    return False
                _record_permit_metrics(resource, int(current), time.monotonic() - started_at, True)
                return True

            await _decrement(redis_client, resource)
            if time.monotonic() >= deadline:
                _record_permit_metrics(resource, resolved_limit, time.monotonic() - started_at, False)
                return False
            await asyncio.sleep(interval)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"资源许可降级放行: resource={resource}, err={exc}")
        return True


async def _register_owner(redis_client: object, resource_type: str, owner: str) -> bool:
    """登记 owner 占用标记, 失败返回 False 由调用方回退计数。

    owner 写入失败会让 inflight 计数失去对应主体, 直接破坏 "owner 数 == inflight"
    不变量, 因此不能静默吞掉。
    """

    try:
        await redis_client.hset(  # type: ignore[attr-defined]
            _owner_key(resource_type),
            owner,
            str(int(time.time())),
        )
        await redis_client.expire(_owner_key(resource_type), _ttl_seconds(resource_type))  # type: ignore[attr-defined]
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"资源许可 owner 登记失败, 放弃本次占用: resource={resource_type}, "
            f"owner={owner}, err={exc}"
        )
        return False


def _owner_age_seconds(heartbeat: object, now: int) -> Optional[int]:
    """返回 owner 心跳距今秒数; 时间戳无法解析时返回 None 表示不可判定。"""

    try:
        return now - int(_redis_text(heartbeat).strip())
    except (TypeError, ValueError):
        return None


def _parse_orphan_marker(raw: object) -> tuple[Optional[int], int]:
    """解析无主计数观察标记 "{首次观察时间}:{无主数量}"。"""

    try:
        observed_at, excess = _redis_text(raw).split(":", 1)
        return int(observed_at), int(excess)
    except (TypeError, ValueError):
        return None, 0


async def _reclaim_ownerless_excess(
    redis_client: object,
    resource_type: str,
    excess: int,
) -> None:
    """回收持续无主的 inflight 计数 (有计数但没有任何 owner 认领)。

    只回收"连续观察满宽限期都没消失"的那部分: acquire 的 INCR 与 owner 登记之间存在
    窗口, 瞬时无主属正常现象; 无主量下降即重新计时, 因此被回收的永远是持续存在的下界,
    不会波及窗口期内的在途申请, 更不会碰到有 owner 认领的长任务。
    """

    marker_key = _orphan_marker_key(resource_type)
    if excess <= 0:
        await redis_client.delete(marker_key)  # type: ignore[attr-defined]
        return

    now = int(time.time())
    observed_at, observed_excess = _parse_orphan_marker(
        await redis_client.get(marker_key)  # type: ignore[attr-defined]
    )
    if observed_at is None or excess < observed_excess:
        await redis_client.set(marker_key, f"{now}:{excess}")  # type: ignore[attr-defined]
        await redis_client.expire(marker_key, _ttl_seconds(resource_type) * 2)  # type: ignore[attr-defined]
        return
    if now - observed_at < _orphan_grace_seconds():
        return

    for _ in range(observed_excess):
        await _decrement(redis_client, resource_type)
    await redis_client.delete(marker_key)  # type: ignore[attr-defined]
    logger.warning(
        f"回收无主资源许可计数: resource={resource_type}, count={observed_excess}, "
        f"observed_for={now - observed_at}s"
    )


async def _reconcile_stale_owners(redis_client: object, resource_type: str) -> None:
    """回收超时 owner 与持续无主的计数, 让 "owner 数 == inflight" 具备自愈能力。

 两类残留都只认时间证据: owner 侧看心跳是否过期 (持有期间 permit_context 会持续
 续期, 心跳过期即持有者已死); 无主侧看无主状态是否连续存在超过宽限期。
 绝不因 "owners 为空" 就删计数 -- 那会误清在途任务。
 """

    try:
        raw = await redis_client.get(_resource_key(resource_type))  # type: ignore[attr-defined]
        inflight = int(raw or 0)
        if inflight <= 0:
            await redis_client.delete(_orphan_marker_key(resource_type))  # type: ignore[attr-defined]
            return
        owners = await redis_client.hgetall(_owner_key(resource_type)) or {}  # type: ignore[attr-defined]
        now = int(time.time())
        ttl = _ttl_seconds(resource_type)
        for field, heartbeat in list(owners.items()):
            age = _owner_age_seconds(heartbeat, now)
            if age is None or age <= ttl:
                continue
            await redis_client.hdel(_owner_key(resource_type), field)  # type: ignore[attr-defined]
            await _decrement(redis_client, resource_type)
            logger.warning(
                f"回收超时资源许可: resource={resource_type}, owner={field}, age={age}s"
            )
        # 超时回收对 owner 与计数各扣一份, 因此无主差额仍是 inflight - len(owners)
        await _reclaim_ownerless_excess(redis_client, resource_type, inflight - len(owners))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"回收残留资源许可失败: resource={resource_type}, err={exc}")


async def release_resource_permit(redis_client: object, resource_type: ResourceType | str) -> None:
    """释放资源许可。"""

    if not _permit_enabled() or redis_client is None:
        return
    await _decrement(redis_client, str(resource_type))
    try:
        value = await redis_client.get(_resource_key(str(resource_type)))  # type: ignore[attr-defined]
        _record_inflight_metric(str(resource_type), max(int(value or 0), 0))
    except Exception:
        pass


async def clear_resource_occupancy(
    redis_client: object,
    resource_type: ResourceType | str,
) -> bool:
    """职责: 清掉指定资源的 inflight 计数、owner 和孤儿标记。
    不做: 不判断设备是否空闲, 不申请新的许可。
    """

    if redis_client is None:
        return False
    resource = str(resource_type)
    try:
        await redis_client.delete(_resource_key(resource))  # type: ignore[attr-defined]
        await redis_client.delete(_owner_key(resource))  # type: ignore[attr-defined]
        await redis_client.delete(_orphan_marker_key(resource))  # type: ignore[attr-defined]
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"清空资源许可失败: resource={resource}, err={exc}"
        )
        return False


async def release_resource_owner(
    redis_client: object,
    resource_type: ResourceType | str,
    owner: Optional[str],
) -> None:
    """释放资源 owner 标记。"""

    if not owner or redis_client is None:
        return
    try:
        await redis_client.hdel(_owner_key(str(resource_type)), owner)  # type: ignore[attr-defined]
    except Exception:
        pass


def _record_permit_metrics(
    resource_type: str,
    inflight: int,
    wait_seconds: float,
    acquired: bool,
) -> None:
    try:
        from app.core.observability.metrics import (
            fallback_active_jobs_gauge,
            worker_resource_inflight_gauge,
            worker_resource_permit_wait_seconds,
        )

        worker_resource_inflight_gauge.labels(resource_type=resource_type).set(inflight)
        worker_resource_permit_wait_seconds.labels(
            resource_type=resource_type,
            acquired=str(acquired).lower(),
        ).observe(wait_seconds)
        if resource_type == _FALLBACK_RESOURCE:
            fallback_active_jobs_gauge.labels(resource_type=resource_type).set(inflight)
    except Exception:
        pass


def _record_inflight_metric(resource_type: str, inflight: int) -> None:
    try:
        from app.core.observability.metrics import (
            fallback_active_jobs_gauge,
            worker_resource_inflight_gauge,
        )

        worker_resource_inflight_gauge.labels(resource_type=resource_type).set(inflight)
        if resource_type == _FALLBACK_RESOURCE:
            fallback_active_jobs_gauge.labels(resource_type=resource_type).set(inflight)
    except Exception:
        pass


async def _keep_owner_alive(redis_client: object, resource_type: str, owner: str) -> None:
    """持有期间周期性续期 owner 心跳与计数 TTL。

    没有续期就无法区分"已跑两小时的长任务"和"两小时前就死掉的残留", 超时回收只能靠猜;
    有了续期, "心跳过期" 才构成持有者已死的证据, 长任务也不会被误清。
    """

    interval = _owner_heartbeat_interval(resource_type)
    while True:
        await asyncio.sleep(interval)
        if not await _register_owner(redis_client, resource_type, owner):
            return
        try:
            await redis_client.expire(_resource_key(resource_type), _ttl_seconds(resource_type))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return


def start_owner_heartbeat(
    redis_client: object,
    resource_type: ResourceType | str,
    owner: Optional[str],
) -> Optional[asyncio.Task]:
    """为已获得的许可启动心跳续期; 持有者不续期, 残留就只能靠 TTL 猜。"""

    if redis_client is None or not owner or not _permit_enabled():
        return None
    return asyncio.create_task(_keep_owner_alive(redis_client, str(resource_type), owner))


async def stop_owner_heartbeat(task: Optional[asyncio.Task]) -> None:
    """停止心跳。必须先于删 owner 调用, 否则续期会把已释放的 owner 重新写回。"""

    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


@asynccontextmanager
async def permit_context(
    redis_client: object,
    resource_type: ResourceType | str,
    *,
    limit: Optional[int] = None,
    wait_timeout: Optional[float] = None,
    owner: Optional[str] = None,
) -> AsyncIterator[bool]:
    """资源许可上下文, 确保异常路径释放 token。"""

    acquired = await acquire_resource_permit(
        redis_client,
        resource_type,
        limit=limit,
        wait_timeout=wait_timeout,
        owner=owner,
    )
    heartbeat = start_owner_heartbeat(redis_client, resource_type, owner) if acquired else None
    try:
        yield acquired
    finally:
        await stop_owner_heartbeat(heartbeat)
        if acquired:
            await release_resource_owner(redis_client, resource_type, owner)
            await release_resource_permit(redis_client, resource_type)


@asynccontextmanager
async def fallback_permit_context(
    redis_client: object,
    *,
    owner: Optional[str] = None,
) -> AsyncIterator[bool]:
    """FastAPI 本地 fallback 专用许可。"""

    if not bool(getattr(settings, "BACKGROUND_FALLBACK_ENABLED", True)):
        yield False
        return

    async with permit_context(
        redis_client,
        _FALLBACK_RESOURCE,
        limit=int(getattr(settings, "BACKGROUND_FALLBACK_MAX_JOBS", 1)),
        wait_timeout=float(getattr(settings, "BACKGROUND_FALLBACK_WAIT_TIMEOUT", 1.0)),
        owner=owner,
    ) as acquired:
        yield acquired


def get_resource_inflight_sync(resource_type: str) -> int:
    """同步读取资源占用, 供探针使用。

 纯只读: 探针不承担残留修复职责, 否则会在真实在途任务存在时把计数清零;
 超时占用的回收统一由 acquire 路径的 _reconcile_stale_owners 负责。
 """

    try:
        from app.core.redis import get_sync_redis

        client = get_sync_redis()
        if client is None:
            return 0
        return max(int(client.get(_resource_key(resource_type)) or 0), 0)
    except Exception:
        return 0


def get_resource_snapshots_sync() -> list[PermitSnapshot]:
    """同步读取所有核心资源许可占用。"""

    resources = ["process", "llm", "report", "maintenance", "gpu", "default", _FALLBACK_RESOURCE]
    snapshots: list[PermitSnapshot] = []
    for resource in resources:
        if resource == _FALLBACK_RESOURCE:
            limit = int(getattr(settings, "BACKGROUND_FALLBACK_MAX_JOBS", 1))
        else:
            limit = max_jobs_for_resource(resource)  # type: ignore[arg-type]
        snapshots.append(
            PermitSnapshot(
                resource_type=resource,
                inflight=get_resource_inflight_sync(resource),
                limit=limit,
            )
        )
    return snapshots


def get_resource_owners_sync(resource_type: str) -> dict[str, str]:
    """同步读取资源占用 owner, 供探针使用。"""

    try:
        from app.core.redis import get_sync_redis

        client = get_sync_redis()
        if client is None:
            return {}
        raw = client.hgetall(_owner_key(resource_type))
        return {str(k): str(v) for k, v in (raw or {}).items()}
    except Exception:
        return {}
