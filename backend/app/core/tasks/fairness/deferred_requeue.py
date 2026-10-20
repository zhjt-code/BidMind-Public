"""份额不足时的延迟重排

职责: 把拿不到项目份额的 job 立即退回队列, 释放它占用的 worker 槽位
不做: 不做准入判定、不重试业务失败、不写任务终态、不新建 job、不改 job_id

为什么必须"退回"而不是"原地等":
 worker 的槽位就是稀缺资源本身。若拿不到份额的 job 留在原地阻塞等待, 5 个槽位会被
 等待者占满, 其它项目的 job 连被 claim 的机会都没有 —— 比不做公平调度更糟。

对 arq 0.26.1 `Retry` 的实测结论（arq/worker.py 与真机跑通共同确认）:
 - `run_job` 每次领取都会 `INCR arq:retry:{job_id}`, 因此 **Retry 会消耗 max_tries 预算**;
 本项目所有 WorkerSettings 都没有覆盖 max_tries, 用的是 arq 默认值 5。
 - 预算耗尽时 job 不是继续排队, 而是被判定终态失败:
 `success=False, result=JobExecutionFailed('max 5 retries exceeded')`。
 - 因此 Retry 只能作为**有限次**的让位手段。本模块把可用次数卡在预算之内, 用尽后
 由调用方走防饥饿放行, 绝不让"公平调度"把一个健康 job 拖成失败。
 - 好消息: defer 期间 `arq:job:{job_id}` 仍然存在、job_id 不变、只是队列 score 被推到
 未来, 所以 的在途守卫与取消墓碑机制照常生效, 不会误判失活也不会重复投递。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn, Optional

from app.core.config import settings

# arq 默认 max_tries; 本项目未在任何 WorkerSettings 覆盖它（已实测确认）。
ARQ_DEFAULT_MAX_TRIES = 5

# 必须给业务真实失败留出至少一次重试, 因此让位次数上限 = max_tries - 2。
_RESERVED_TRIES_FOR_REAL_FAILURE = 2


@dataclass(frozen=True)
class DeferralPlan:
    """本次是否让位、让多久, 以及不让位时的豁免理由。"""

    should_defer: bool
    delay_seconds: float
    waiver_reason: str


def max_fairness_defers() -> int:
    """公平调度最多可以让位几次, 受 arq 重试预算硬约束。"""

    configured = int(getattr(settings, "WORKER_FAIRNESS_MAX_DEFERS", 3))
    budget = ARQ_DEFAULT_MAX_TRIES - _RESERVED_TRIES_FOR_REAL_FAILURE
    return max(min(configured, budget), 0)


def _base_delay() -> float:
    return max(float(getattr(settings, "WORKER_FAIRNESS_DEFER_DELAY", 2.0)), 0.1)


def _delay_cap() -> float:
    return max(float(getattr(settings, "WORKER_FAIRNESS_DEFER_DELAY_CAP", 30.0)), 0.1)


def resolve_deferral_plan(
    job_try: int,
    *,
    max_defers: Optional[int] = None,
    base_delay: Optional[float] = None,
    delay_cap: Optional[float] = None,
) -> DeferralPlan:
    """按 arq 重试预算决定本次是让位还是防饥饿放行。

    `job_try` 由 arq 注入 ctx, 首次执行为 1。已让位次数 = job_try - 1。
    退避逐次翻倍: 争用持续存在时, 反复以固定短间隔回来只会白白烧掉重试预算。
    """

    allowed = max_fairness_defers() if max_defers is None else max(int(max_defers), 0)
    used = max(int(job_try) - 1, 0)
    if used >= allowed:
        return DeferralPlan(
            should_defer=False,
            delay_seconds=0.0,
            waiver_reason=f"defer_budget_exhausted({used}/{allowed})",
        )

    base = _base_delay() if base_delay is None else max(float(base_delay), 0.1)
    cap = _delay_cap() if delay_cap is None else max(float(delay_cap), 0.1)
    return DeferralPlan(
        should_defer=True,
        delay_seconds=min(base * (2 ** used), cap),
        waiver_reason="",
    )


def defer_job_for_fairness(delay_seconds: float) -> NoReturn:
    """抛出 arq Retry, 让当前 job 立刻退回队列并释放 worker 槽位。

    必须在业务开始执行之前调用: `Retry` 继承自 RuntimeError, 一旦进入被 `except Exception`
    包裹的业务代码就会被吞掉, 变成"公平调度静默失效"。
    """

    from arq.worker import Retry

    raise Retry(defer=max(float(delay_seconds), 0.0))
