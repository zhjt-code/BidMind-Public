"""项目级重活准入测试

锁定的风险:
- 单个项目独占全部 worker 槽位, 其它项目在 FIFO 后面无限期干等（本体）
- 为了"公平"而闲置容量: 只有一个项目时必须能借满全局容量
- 持有租约的 job 崩溃后份额永久泄漏, 名额再也回不来
- 无 project_id 的 job 被公平调度卡死
- 公平机制把健康 job 拖到 arq max_tries 上限而判定失败
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks.fairness.deferred_requeue import (
    ARQ_DEFAULT_MAX_TRIES,
    resolve_deferral_plan,
)
from app.core.tasks.fairness.project_admission import (
    claim_project_share,
    refresh_project_share,
    release_project_share,
    waiting_key,
)
from app.core.tasks.fairness.share_policy import (
    AdmissionSnapshot,
    ShareRequest,
    evaluate_admission,
    max_min_allocation,
)
from tests.fairness_support import T0 as _T0
from tests.fairness_support import FakeFairnessRedis as _FakeRedis

_CAPACITY = 5
_TARGET = 5
_RESOURCE = "llm"


async def _claim(redis: _FakeRedis, project: int | None, owner: str, now: int) -> bool:
    decision = await claim_project_share(
        redis,
        resource_type=_RESOURCE,
        project_id=project,
        owner=owner,
        capacity=_CAPACITY,
        now=now,
    )
    return decision.granted


def _request(project: str, owner: str, now: int = _T0, capacity: int = _CAPACITY) -> ShareRequest:
    return ShareRequest(
        project=project,
        field=f"{project}|{owner}",
        capacity=capacity,
        target_projects=_TARGET,
        now=now,
        holder_ttl=900,
        waiter_ttl=1800,
        probation=1.5,
    )


# ==================== max-min 份额算法 ====================

def test_single_demanding_project_takes_whole_capacity() -> None:
    """只有一个项目有需求时必须拿到全部容量, 否则就是为公平而闲置资源。"""

    assert max_min_allocation({"A": 6}, 5) == {"A": 5}


def test_capacity_is_split_evenly_when_all_projects_want_more() -> None:
    """5 个项目都想要更多时, 每人恰好 1 份, 不多不少。"""

    demand = {name: 4 for name in "ABCDE"}
    assert max_min_allocation(demand, 5) == {name: 1 for name in "ABCDE"}


def test_unused_share_flows_to_the_project_that_still_wants_it() -> None:
    """需求少的项目吃不完的份额必须回流给还想要的项目（工作保持性的来源）。"""

    allocation = max_min_allocation({"A": 6, "C": 1, "D": 1, "E": 1}, 5)

    assert allocation["C"] == 1 and allocation["D"] == 1 and allocation["E"] == 1
    assert allocation["A"] == 2, (
        f"C/D/E 只各用 1 份, 剩下的 2 份必须给还有需求的 A, 实际 {allocation.get('A')}"
    )
    assert sum(allocation.values()) == 5


# ==================== 核心验收场景 ====================

@pytest.mark.asyncio
async def test_one_project_cannot_occupy_every_slot() -> None:
    """ 本体: A 投 6 个 job, 只能占 1 个名额, 其余必须让给 B-E。"""

    redis = _FakeRedis()
    granted_for_a = [
        await _claim(redis, 101, f"job-a{i}", _T0) for i in range(6)
    ]

    assert granted_for_a[0] is True, "项目的第一个 job 必须无条件放行"
    assert not any(granted_for_a[1:]), (
        f"A 的后续 job 抢占了额外槽位: {granted_for_a}"
    )

    for project in (202, 303, 404, 505):
        assert await _claim(redis, project, f"job-{project}", _T0) is True, (
            f"项目 {project} 的首个 job 被 A 挤掉了"
        )

    assert redis.running_projects() == {"101", "202", "303", "404", "505"}
    assert len(redis.holders()) == 5, "总在途数必须恰好等于容量"
    assert len(redis.waiting()) == 5, "A 剩下的 5 个 job 应登记为排队中"


@pytest.mark.asyncio
async def test_lone_project_borrows_full_capacity_after_yield_window() -> None:
    """只有一个项目时必须能借满全局容量, 不得为了公平而闲置槽位。"""

    redis = _FakeRedis()
    assert await _claim(redis, 101, "job-0", _T0) is True

    for index in range(1, 5):
        assert await _claim(redis, 101, f"job-{index}", _T0) is False, (
            "首次申请只发基础份额, 借用必须先经过让路窗口"
        )
        assert await _claim(redis, 101, f"job-{index}", _T0 + 10) is True, (
            f"让路窗口已过且无人竞争, job-{index} 应能借用空闲份额"
        )

    assert len(redis.holders()) == _CAPACITY, (
        f"独占场景下应借满 {_CAPACITY} 个槽位, 实际 {len(redis.holders())}"
    )
    assert await _claim(redis, 101, "job-5", _T0 + 10) is False, "借用不得越过总容量"


@pytest.mark.asyncio
async def test_borrowing_stops_once_another_project_shows_demand() -> None:
    """独占项目借满后, 新项目一旦出现就必须拿到名额, 借用方不得再扩张。"""

    redis = _FakeRedis()
    await _claim(redis, 101, "job-0", _T0)
    for index in range(1, 4):
        await _claim(redis, 101, f"job-{index}", _T0)
        await _claim(redis, 101, f"job-{index}", _T0 + 10)
    assert len(redis.holders()) == 4, "前提: A 已借用到 4 个槽位"

    assert await _claim(redis, 999, "job-newcomer", _T0 + 20) is True, (
        "cohort 未满时新项目必须能立即加入"
    )
    assert await _claim(redis, 101, "job-4", _T0 + 20) is False, (
        "容量已满, A 不得继续扩张"
    )


# ==================== cohort 名额与防饥饿排队 ====================

@pytest.mark.asyncio
async def test_sixth_project_queues_when_cohort_is_full() -> None:
    """cohort 满员时第 6 个项目排队等待, 而不是挤掉别人。"""

    redis = _FakeRedis()
    for project in (101, 202, 303, 404, 505):
        assert await _claim(redis, project, f"job-{project}", _T0) is True

    assert await _claim(redis, 606, "job-606", _T0) is False
    decision = await claim_project_share(
        redis, resource_type=_RESOURCE, project_id=606,
        owner="job-606", capacity=_CAPACITY, now=_T0,
    )
    assert decision.reason == "capacity_full"


def test_longer_waiting_project_is_served_before_a_newcomer() -> None:
    """名额有限时按等待时长排队, 后来者不得插队（防饥饿）。"""

    snapshot = AdmissionSnapshot(
        holders={f"P{i}|job": str(_T0) for i in range(4)},
        waiting={"OLD|job-old": f"{_T0 - 600}:{_T0}"},
    )

    newcomer = evaluate_admission(snapshot, _request("NEW", "job-new", now=_T0))
    veteran = evaluate_admission(snapshot, _request("OLD", "job-old", now=_T0))

    assert newcomer.granted is False and newcomer.reason == "queued_behind_older", (
        "只剩 1 个名额时, 新项目必须让位给等待更久的项目"
    )
    assert veteran.granted is True, "等待最久的项目必须能拿到刚腾出的名额"


def test_newcomer_joins_immediately_when_cohort_has_room() -> None:
    """cohort 未满时新项目立即加入, 不因为有人排队就一起陪等。"""

    snapshot = AdmissionSnapshot(
        holders={"P1|job": str(_T0), "P2|job": str(_T0)},
        waiting={"OLD|job-old": f"{_T0 - 600}:{_T0}"},
    )

    outcome = evaluate_admission(snapshot, _request("NEW", "job-new", now=_T0))

    assert outcome.granted is True, "还剩 3 个名额却让新项目排队, 属于无谓闲置"


# ==================== 租约回收 ====================

@pytest.mark.asyncio
async def test_dead_holder_share_is_reclaimed_after_heartbeat_expires() -> None:
    """持有份额的 job 崩溃后, 份额必须能被回收, 不能永久占着名额。"""

    redis = _FakeRedis()
    for project in (101, 202, 303, 404, 505):
        assert await _claim(redis, project, f"job-{project}", _T0) is True
    assert await _claim(redis, 606, "job-606", _T0) is False, "前提: 名额已满"

    # 用推进时间戳制造"心跳停止", 不依赖真实等待
    dead_at = _T0 + 901
    assert await _claim(redis, 606, "job-606", dead_at) is True, (
        "全部持有者心跳都已过期, 名额应被回收并放行新申请"
    )
    assert redis.running_projects() == {"606"}, (
        f"过期持有者未被清理: {sorted(redis.running_projects())}"
    )


@pytest.mark.asyncio
async def test_live_holder_is_never_reclaimed_while_heartbeat_is_fresh() -> None:
    """持续续期的长任务运行再久也不得被当成残留清掉。"""

    redis = _FakeRedis()
    assert await _claim(redis, 101, "job-long", _T0) is True
    field = "101|job-long"

    for step in range(1, 6):
        now = _T0 + step * 800
        assert await refresh_project_share(
            redis, resource_type=_RESOURCE, field=field, now=now
        ) is True
        await _claim(redis, 202, f"job-probe-{step}", now)
        assert field in redis.holders(), (
            f"第 {step} 轮: 心跳新鲜的长任务份额被误清"
        )


@pytest.mark.asyncio
async def test_abandoned_waiter_stops_inflating_project_demand() -> None:
    """不再轮询的排队者必须被清掉, 否则已取消的 job 会永久虚高本项目需求。"""

    redis = _FakeRedis()
    waiting = redis.hashes.setdefault(waiting_key(_RESOURCE), {})
    waiting["101|job-ghost"] = f"{_T0}:{_T0}"

    await _claim(redis, 202, "job-live", _T0 + 1801)

    assert "101|job-ghost" not in redis.waiting(), (
        "超过 waiter TTL 仍未回来轮询的排队登记应被回收"
    )


@pytest.mark.asyncio
async def test_released_share_frees_the_slot_for_another_project() -> None:
    """正常释放后名额必须立刻可用。"""

    redis = _FakeRedis()
    for project in (101, 202, 303, 404, 505):
        await _claim(redis, project, f"job-{project}", _T0)
    assert await _claim(redis, 606, "job-606", _T0) is False

    await release_project_share(redis, resource_type=_RESOURCE, field="505|job-505")

    assert await _claim(redis, 606, "job-606", _T0) is True


# ==================== 无 project_id 的 job ====================

@pytest.mark.asyncio
async def test_jobs_without_project_id_are_never_gated() -> None:
    """knowledge_base_process_job 这类无 project_id 的 job 不得被公平调度卡死。"""

    redis = _FakeRedis()
    for project in (101, 202, 303, 404, 505):
        await _claim(redis, project, f"job-{project}", _T0)

    for index in range(3):
        decision = await claim_project_share(
            redis, resource_type="process", project_id=None,
            owner=f"kb-job-{index}", capacity=_CAPACITY, now=_T0,
        )
        assert decision.granted is True, "无项目归属的 job 被公平调度拒绝了"
        assert decision.reason == "no_project"


@pytest.mark.asyncio
async def test_redis_failure_degrades_to_pass_through() -> None:
    """Redis 不可用时必须放行: 公平调度是优化项, 不能让业务停摆。"""

    class _BrokenRedis(_FakeRedis):
        async def hgetall(self, key: str) -> dict[str, str]:
            raise RuntimeError("redis down")

    decision = await claim_project_share(
        _BrokenRedis(), resource_type=_RESOURCE, project_id=101,
        owner="job-a", capacity=_CAPACITY, now=_T0,
    )

    assert decision.granted is True and decision.reason == "degraded"


# ==================== 让位次数受 arq 重试预算硬约束 ====================

def test_deferral_budget_stays_below_arq_max_tries() -> None:
    """实测: arq 每次领取都 INCR 重试计数, job_try 超过 max_tries 会被判定失败。

    因此让位次数必须留出余量, 绝不能让公平机制把健康 job 拖成
    JobExecutionFailed('max 5 retries exceeded')。
    """

    plans = [resolve_deferral_plan(job_try) for job_try in range(1, ARQ_DEFAULT_MAX_TRIES + 2)]
    defer_count = sum(1 for plan in plans if plan.should_defer)

    assert defer_count <= ARQ_DEFAULT_MAX_TRIES - 2, (
        f"让位 {defer_count} 次会把 job 拖到 arq 重试上限, 最多只能让位 "
        f"{ARQ_DEFAULT_MAX_TRIES - 2} 次"
    )
    assert plans[-1].should_defer is False
    assert "budget_exhausted" in plans[-1].waiver_reason


def test_deferral_backs_off_instead_of_hammering() -> None:
    """争用持续存在时, 固定短间隔反复回来只会白白烧掉重试预算。"""

    delays = [
        resolve_deferral_plan(job_try).delay_seconds
        for job_try in range(1, 4)
        if resolve_deferral_plan(job_try).should_defer
    ]

    assert delays == sorted(delays) and delays[0] < delays[-1], (
        f"让位间隔未随次数增长: {delays}"
    )
