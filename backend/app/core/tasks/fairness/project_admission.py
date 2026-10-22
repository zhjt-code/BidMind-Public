"""项目级重活准入

职责: 在 Redis 上维护"活跃项目租约", 串行化地把 share_policy 的裁决结果落盘
不做: 不计算份额（交给 share_policy）、不延迟重排 job（交给 deferred_requeue）、不执行业务

为什么需要它:
 `WORKER_LLM_MAX_JOBS=5` 只约束"同时跑 5 个 job", 同一项目完全可以独占这 5 个槽位,
 其余项目在 FIFO 后面无限期干等 —— "5 个 job" 不等于"5 个项目"。本模块把并发的计量
 单位换成 project_id, 让 `WORKER_TARGET_RUNNING_TASKS` 这个此前没有任何代码读取过的
 配置项真正生效。

租约归并:
 租约属于 (resource_type, project_id) 而不是单个 job。同一项目的多个 job 共享同一份
 额度, 子任务不会各自占一个租约; 项目内能跑几个 job 由 share_policy 的 max-min 份额
 决定, 而不是由 job 数量决定。

原子性为什么用互斥锁而不是单条 Lua 脚本:
 "读快照 -> 算份额 -> 占位"必须串行, 否则并发申请会各自读到旧快照并同时通过, 直接
 超发份额。把整段逻辑塞进 Lua 能一次往返完成, 但那样份额算法就只存在于 Lua 里, 防御
 测试要么得搭 Lua 运行时、要么得在测试里重写一遍算法 —— 而这段算法恰恰是最需要被
 测住的部分。因此改为: 算法留在纯 Python, 用一把短 TTL 的 Redis 互斥锁串行化临界区。
 准入判定每个 job 只发生一次（不是每次 LLM 请求）, 多出的几次往返可以忽略。
 锁抢不到时降级为无锁执行: 后果只是短暂超发一个名额, 下一轮裁决即自愈; 反过来若因
 为拿不到锁就拒绝准入, 会让 job 无谓地反复重排, 代价大得多。
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
from app.core.tasks.fairness.share_policy import (
    AdmissionOutcome,
    AdmissionSnapshot,
    ShareRequest,
    evaluate_admission,
)

_KEY_PREFIX = "bidmind:fairness:"
_NO_PROJECT = "none"
_CTX_HOLD_KEY = "bidmind_project_share"

_LOCK_TTL_MS = 3000
_LOCK_ATTEMPTS = 25
_LOCK_RETRY_SECONDS = 0.006


@dataclass(frozen=True)
class ShareDecision:
    """单次项目份额裁决结果。"""

    granted: bool
    allow: int
    project_running: int
    total_running: int
    cohort_size: int
    reason: str
    field: str


@dataclass
class ProjectShareHold:
    """已持有的项目份额; 释放时靠它定位 Redis 字段并停掉心跳。"""

    resource_type: str
    field: str
    heartbeat: Optional[asyncio.Task] = None


def holders_key(resource_type: str) -> str:
    return f"{_KEY_PREFIX}{resource_type}:holders"


def waiting_key(resource_type: str) -> str:
    return f"{_KEY_PREFIX}{resource_type}:waiting"


def _lock_key(resource_type: str) -> str:
    return f"{_KEY_PREFIX}{resource_type}:lock"


def project_key(project_id: object) -> str:
    """归一化项目标识; 无 project_id 的 job 统一落到占位符。"""

    text = "" if project_id is None else str(project_id).strip()
    if not text or text.lower() == "none":
        return _NO_PROJECT
    return text.replace("|", "_")


def build_share_field(project_id: object, owner: object) -> str:
    """构造 job 级唯一的份额字段; "|" 是项目段与 owner 段的分隔符, 段内不得出现。"""

    owner_text = str(owner or "anonymous").replace("|", "_")
    return f"{project_key(project_id)}|{owner_text}"


def fairness_enabled() -> bool:
    return bool(getattr(settings, "WORKER_PROJECT_FAIRNESS_ENABLED", True))


def target_project_count() -> int:
    """同时保证进展的项目数上限, 即产品承诺里的"5 个不同项目"。"""

    return max(int(getattr(settings, "WORKER_TARGET_RUNNING_TASKS", 5)), 1)


def holder_ttl_seconds() -> int:
    return max(int(getattr(settings, "WORKER_FAIRNESS_HOLDER_TTL", 900)), 30)


def waiter_ttl_seconds() -> int:
    return max(int(getattr(settings, "WORKER_FAIRNESS_WAITER_TTL", 1800)), 60)


def borrow_probation_seconds() -> float:
    return max(float(getattr(settings, "WORKER_FAIRNESS_BORROW_PROBATION", 1.5)), 0.0)


def _heartbeat_interval() -> float:
    """心跳间隔必须远小于 holder TTL, 否则"心跳过期"不足以证明持有者已死。"""

    ttl = holder_ttl_seconds()
    configured = getattr(settings, "WORKER_FAIRNESS_HOLDER_HEARTBEAT_INTERVAL", None)
    default = max(min(float(ttl) / 10.0, 120.0), 5.0)
    return max(float(configured if configured is not None else default), 1.0)


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _as_mapping(raw: object) -> dict[str, str]:
    if not raw:
        return {}
    return {_text(k): _text(v) for k, v in dict(raw).items()}  # type: ignore[arg-type]


@asynccontextmanager
async def _admission_lock(redis_client: object, resource_type: str) -> AsyncIterator[bool]:
    """串行化准入临界区; 抢不到锁时降级放行而不是拒绝。"""

    key = _lock_key(resource_type)
    token = uuid.uuid4().hex
    acquired = False
    try:
        for _ in range(_LOCK_ATTEMPTS):
            if await redis_client.set(key, token, nx=True, px=_LOCK_TTL_MS):  # type: ignore[attr-defined]
                acquired = True
                break
            await asyncio.sleep(_LOCK_RETRY_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"准入锁获取失败, 降级为无锁裁决: resource={resource_type}, err={exc}")

    try:
        yield acquired
    finally:
        if acquired:
            with suppress(Exception):
                if _text(await redis_client.get(key)) == token:  # type: ignore[attr-defined]
                    await redis_client.delete(key)  # type: ignore[attr-defined]


async def claim_project_share(
    redis_client: object,
    *,
    resource_type: str,
    project_id: object,
    owner: object,
    capacity: int,
    now: Optional[int] = None,
) -> ShareDecision:
    """裁决本 job 能否立即占用所属项目的份额。

    Redis 不可用时一律放行: 公平调度是优化项, 不能因为它让业务停摆。
    """

    field = build_share_field(project_id, owner)
    if not fairness_enabled() or redis_client is None:
        return ShareDecision(True, capacity, 0, 0, 0, "disabled", field)
    if project_key(project_id) == _NO_PROJECT:
        # 无 project_id 的 job（知识库处理/维护类）没有项目归属, 让它们参与项目公平调度
        # 只会被永久推迟; 直接放行, 并发仍由既有资源许可兜住。
        return ShareDecision(True, capacity, 0, 0, 0, "no_project", field)

    request = ShareRequest(
        project=project_key(project_id),
        field=field,
        capacity=max(int(capacity), 1),
        target_projects=target_project_count(),
        now=int(time.time()) if now is None else int(now),
        holder_ttl=holder_ttl_seconds(),
        waiter_ttl=waiter_ttl_seconds(),
        probation=borrow_probation_seconds(),
    )

    try:
        async with _admission_lock(redis_client, resource_type):
            snapshot = AdmissionSnapshot(
                holders=_as_mapping(
                    await redis_client.hgetall(holders_key(resource_type))  # type: ignore[attr-defined]
                ),
                waiting=_as_mapping(
                    await redis_client.hgetall(waiting_key(resource_type))  # type: ignore[attr-defined]
                ),
            )
            outcome = evaluate_admission(snapshot, request)
            await _apply_outcome(redis_client, resource_type, request, outcome)
        return ShareDecision(
            granted=outcome.granted,
            allow=outcome.allow,
            project_running=outcome.project_running,
            total_running=outcome.total_running,
            cohort_size=outcome.cohort_size,
            reason=outcome.reason,
            field=field,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"项目份额裁决失败, 降级放行: resource={resource_type}, err={exc}")
        return ShareDecision(True, capacity, 0, 0, 0, "degraded", field)


async def _apply_outcome(
    redis_client: object,
    resource_type: str,
    request: ShareRequest,
    outcome: AdmissionOutcome,
) -> None:
    """把裁决结果与残留清理一并落盘。"""

    holders = holders_key(resource_type)
    waiting = waiting_key(resource_type)
    key_ttl = holder_ttl_seconds() * 2

    for stale in outcome.stale_holders:
        await redis_client.hdel(holders, stale)  # type: ignore[attr-defined]
        logger.warning(f"回收心跳过期的项目份额: resource={resource_type}, field={stale}")
    for stale in outcome.stale_waiters:
        await redis_client.hdel(waiting, stale)  # type: ignore[attr-defined]

    if outcome.granted:
        await redis_client.hset(holders, request.field, str(request.now))  # type: ignore[attr-defined]
        await redis_client.hdel(waiting, request.field)  # type: ignore[attr-defined]
        await redis_client.expire(holders, key_ttl)  # type: ignore[attr-defined]
    else:
        await redis_client.hset(waiting, request.field, outcome.waiting_value)  # type: ignore[attr-defined]
        await redis_client.expire(waiting, key_ttl)  # type: ignore[attr-defined]


async def refresh_project_share(
    redis_client: object,
    *,
    resource_type: str,
    field: str,
    now: Optional[int] = None,
) -> bool:
    """续期份额心跳; 返回 False 表示续期失败, 调用方应停止续期循环。"""

    if redis_client is None:
        return False
    timestamp = int(time.time()) if now is None else int(now)
    try:
        await redis_client.hset(  # type: ignore[attr-defined]
            holders_key(resource_type), field, str(timestamp)
        )
        await redis_client.expire(  # type: ignore[attr-defined]
            holders_key(resource_type), holder_ttl_seconds() * 2
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"项目份额心跳续期失败: resource={resource_type}, err={exc}")
        return False


async def release_project_share(
    redis_client: object,
    *,
    resource_type: str,
    field: str,
) -> None:
    """释放份额, 同时清掉本 job 可能残留的排队登记。"""

    if redis_client is None:
        return
    try:
        await redis_client.hdel(holders_key(resource_type), field)  # type: ignore[attr-defined]
        await redis_client.hdel(waiting_key(resource_type), field)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"释放项目份额失败: resource={resource_type}, err={exc}")


async def _keep_share_alive(redis_client: object, resource_type: str, field: str) -> None:
    """持有期间周期性续期, 让"心跳过期"成为持有者已死的可信证据。"""

    interval = _heartbeat_interval()
    while True:
        await asyncio.sleep(interval)
        if not await refresh_project_share(
            redis_client, resource_type=resource_type, field=field
        ):
            return


def _attach_hold(
    ctx: dict, redis_client: object, resource_type: str, field: str, *, beat: bool
) -> None:
    hold = ProjectShareHold(resource_type=resource_type, field=field)
    if beat and redis_client is not None:
        hold.heartbeat = asyncio.create_task(
            _keep_share_alive(redis_client, resource_type, field)
        )
    ctx[_CTX_HOLD_KEY] = hold


async def claim_project_share_or_defer(
    ctx: dict,
    *,
    resource_type: str,
    project_id: object,
    owner: object,
    capacity: int,
) -> ShareDecision:
    """worker 入口的公平准入闸门: 拿不到份额就抛 Retry 让出 worker 槽位。

    拿到份额时把凭据挂在 ctx 上, 由 `release_project_share_after_job` 在 job 收场时归还
    —— 走 ARQ 的 after_job_end 钩子而不是在调用侧包 try/finally, 是为了不给已超复杂度
    阈值的 worker.py 再加一层嵌套。
    """

    from app.core.tasks.fairness.deferred_requeue import (
        defer_job_for_fairness,
        resolve_deferral_plan,
    )

    redis_client = ctx.get("redis")
    decision = await claim_project_share(
        redis_client,
        resource_type=resource_type,
        project_id=project_id,
        owner=owner,
        capacity=capacity,
    )
    if decision.granted:
        _attach_hold(
            ctx,
            redis_client,
            resource_type,
            decision.field,
            beat=decision.reason not in ("disabled", "no_project", "degraded"),
        )
        return decision

    plan = resolve_deferral_plan(int(ctx.get("job_try") or 1))
    if not plan.should_defer:
        # 重排预算耗尽: 宁可让本项目暂时超份额, 也绝不能让公平机制把健康 job 拖成失败。
        logger.info(
            f"[Fairness] 重排预算耗尽, 放行以防饥饿: resource={resource_type}, "
            f"project={project_id}, reason={decision.reason}, waiver={plan.waiver_reason}"
        )
        await _force_hold(redis_client, resource_type, decision.field)
        _attach_hold(ctx, redis_client, resource_type, decision.field, beat=True)
        return ShareDecision(
            granted=True,
            allow=decision.allow,
            project_running=decision.project_running,
            total_running=decision.total_running,
            cohort_size=decision.cohort_size,
            reason="starvation_waiver",
            field=decision.field,
        )

    logger.info(
        f"[Fairness] 项目份额不足, 让出 worker 槽位延迟重排: resource={resource_type}, "
        f"project={project_id}, reason={decision.reason}, allow={decision.allow}, "
        f"running={decision.project_running}, cohort={decision.cohort_size}, "
        f"defer={plan.delay_seconds:.1f}s"
    )
    defer_job_for_fairness(plan.delay_seconds)


async def _force_hold(redis_client: object, resource_type: str, field: str) -> None:
    """防饥饿放行时补登持有记录, 否则这份在途占用不会被任何人看见。"""

    if redis_client is None:
        return
    try:
        await redis_client.hset(  # type: ignore[attr-defined]
            holders_key(resource_type), field, str(int(time.time()))
        )
        await redis_client.hdel(waiting_key(resource_type), field)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"防饥饿放行补登失败: resource={resource_type}, err={exc}")


async def release_project_share_after_job(ctx: dict) -> None:
    """ARQ after_job_end 钩子: job 无论成功/失败/被重排都在此归还份额。"""

    hold = ctx.pop(_CTX_HOLD_KEY, None)
    if not isinstance(hold, ProjectShareHold):
        return
    if hold.heartbeat is not None:
        # 必须先停心跳再删记录, 否则续期会把已释放的份额重新写回去
        hold.heartbeat.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await hold.heartbeat
    await release_project_share(
        ctx.get("redis"), resource_type=hold.resource_type, field=hold.field
    )
