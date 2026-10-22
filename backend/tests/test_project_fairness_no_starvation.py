"""项目级公平调度端到端无饥饿测试

这里跑的是完整链路: 真实的准入裁决 + 真实的 Retry 让位 + 一个复刻 ARQ 领取行为的
worker 模拟器。锁定的风险:
- A 项目投 6 个 job 就把 5 个 worker 槽位全占了, B-E 永远排不上（本体）
- 拿不到份额的 job 原地阻塞, 把槽位耗在等待上（比不做公平调度更糟）
- 某个项目被无限期推迟, 永远拿不到进展
- 只有一个项目时槽位闲置
- 无 project_id 的 job 被公平调度卡死
- 公平机制把健康 job 拖到 arq 重试上限而判定失败
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.fairness_support import FakeFairnessRedis, WorkerSim

_SLOTS = 5
_CAPACITY = 5


def _sim(monkeypatch, slots: int = _SLOTS, capacity: int = _CAPACITY) -> WorkerSim:
    sim = WorkerSim(redis=FakeFairnessRedis(), slots=slots, capacity=capacity)
    sim.bind_clock(monkeypatch)
    return sim


@pytest.mark.asyncio
async def test_backlog_project_cannot_delay_the_other_four_projects(monkeypatch) -> None:
    """核心验收场景: A 投 6 个 job, B/C/D/E 各 1 个。

 B-E 的首个 job 必须先于 A 的第二个 job 开始执行 —— 这是 的直接映射。
 """

    async with _sim(monkeypatch) as sim:
        for index in range(6):
            sim.submit(101, f"a{index}")
        for project in (202, 303, 404, 505):
            sim.submit(project, f"p{project}")

        await sim.drain_once()

        assert sim.start_order[0] == "a0", "先入队的 job 应先开跑"
        for project in (202, 303, 404, 505):
            assert f"p{project}" in sim.start_order, (
                f"项目 {project} 的首个 job 根本没开跑: 执行顺序={sim.start_order}"
            )
        assert "a1" not in sim.start_order, (
            f"A 的第二个 job 抢在其它项目之前开跑了: 执行顺序={sim.start_order}"
        )
        assert sorted(sim.start_order) == ["a0", "p202", "p303", "p404", "p505"]
        assert len(sim.running) == 5, "5 个槽位应全部在跑真业务"


@pytest.mark.asyncio
async def test_deferred_jobs_never_occupy_a_worker_slot(monkeypatch) -> None:
    """让位的 job 必须立刻退回队列; 若原地等待, 槽位会被等待者占满。"""

    async with _sim(monkeypatch) as sim:
        for index in range(20):
            sim.submit(101, f"a{index}")

        await sim.drain_once()

        assert len(sim.running) == 1, (
            f"单个项目在让路窗口内只应占 1 个槽位, 实际 {len(sim.running)}"
        )
        assert len(sim.defer_events) >= 1, "其余 job 应通过让位退回队列"
        assert len(sim.queue) == 19, (
            f"让位的 job 必须回到队列而不是占着槽位: 队列 {len(sim.queue)}"
        )


@pytest.mark.asyncio
async def test_lone_project_fills_every_slot_after_the_yield_window(monkeypatch) -> None:
    """只有一个项目时必须借满全部槽位, 不得为了公平而闲置。"""

    async with _sim(monkeypatch) as sim:
        for index in range(8):
            sim.submit(101, f"a{index}")

        await sim.drain_once()
        assert len(sim.running) == 1, "让路窗口内先只发基础份额"

        sim.advance(10)  # 越过让路窗口与让位退避
        await sim.drain_once()

        assert len(sim.running) == _SLOTS, (
            f"独占场景应跑满 {_SLOTS} 个槽位, 实际 {len(sim.running)}"
        )


@pytest.mark.asyncio
async def test_every_project_keeps_making_progress_across_rounds(monkeypatch) -> None:
    """5 个项目各投 4 个 job, 反复推进后每个项目都必须单调取得进展。"""

    async with _sim(monkeypatch) as sim:
        projects = [101, 202, 303, 404, 505]
        for project in projects:
            for index in range(4):
                sim.submit(project, f"p{project}-{index}")

        finished: dict[int, int] = {project: 0 for project in projects}
        for _round in range(12):
            await sim.drain_once()
            for job_id in list(sim.running):
                await sim.complete(job_id)
                finished[int(job_id.split("-")[0][1:])] += 1
            sim.advance(5)

        assert all(count > 0 for count in finished.values()), (
            f"有项目一次都没跑完过: {finished}"
        )
        assert min(finished.values()) >= 3, (
            f"存在明显落后的项目, 公平性不成立: {finished}"
        )
        assert sum(finished.values()) == 20, f"仍有 job 未完成: {finished}"


@pytest.mark.asyncio
async def test_backlog_project_is_not_starved_by_the_others(monkeypatch) -> None:
    """防饥饿的另一侧: 让位方最终也必须跑完, 不能被永远推迟。"""

    async with _sim(monkeypatch) as sim:
        for index in range(6):
            sim.submit(101, f"a{index}")
        for project in (202, 303, 404, 505):
            sim.submit(project, f"p{project}")

        completed: list[str] = []
        for _round in range(20):
            await sim.drain_once()
            for job_id in list(sim.running):
                await sim.complete(job_id)
                completed.append(job_id)
            sim.advance(5)

        a_jobs = [job_id for job_id in completed if job_id.startswith("a")]
        assert len(a_jobs) == 6, (
            f"积压项目的 job 未能全部跑完, 被饿死了: 已完成 {sorted(a_jobs)}"
        )
        assert len(completed) == 10


@pytest.mark.asyncio
async def test_deferral_never_pushes_a_job_past_the_arq_retry_budget(monkeypatch) -> None:
    """公平调度绝不能把健康 job 拖到 arq max_tries 上限而判定失败。"""

    from app.core.tasks.fairness.deferred_requeue import ARQ_DEFAULT_MAX_TRIES

    async with _sim(monkeypatch) as sim:
        for project in (202, 303, 404, 505, 606):
            sim.submit(project, f"p{project}")
        for index in range(3):
            sim.submit(101, f"a{index}")

        for _round in range(15):
            await sim.drain_once()
            sim.advance(30)

        worst_try = max((job.job_try for job in sim.queue), default=1)
        worst_deferred = max((attempt for _, attempt in sim.defer_events), default=0)

        assert worst_try <= ARQ_DEFAULT_MAX_TRIES, (
            f"某个 job 的 job_try 已达 {worst_try}, 会被 arq 判定 max retries exceeded"
        )
        assert worst_deferred <= ARQ_DEFAULT_MAX_TRIES - 2, (
            f"让位次数 {worst_deferred} 越过了安全预算"
        )


@pytest.mark.asyncio
async def test_job_without_project_id_runs_even_when_cohort_is_full(monkeypatch) -> None:
    """knowledge_base_process_job 无 project_id, 不得被项目公平调度卡死。"""

    async with _sim(monkeypatch) as sim:
        for project in (101, 202, 303, 404):
            sim.submit(project, f"p{project}")
        sim.submit(None, "kb-job")

        await sim.drain_once()

        assert "kb-job" in sim.start_order, (
            f"无项目归属的 job 被公平调度挡住了: 执行顺序={sim.start_order}"
        )
        assert sim.defer_events == [], "无 project_id 的 job 不应触发让位"


@pytest.mark.asyncio
async def test_crashed_holder_frees_its_slot_for_a_waiting_project(monkeypatch) -> None:
    """持有份额的 job 崩溃（不再续期心跳）后, 名额必须能被等待中的项目接手。"""

    async with _sim(monkeypatch) as sim:
        for project in (101, 202, 303, 404, 505):
            sim.submit(project, f"p{project}")
        sim.submit(606, "p606")

        await sim.drain_once()
        assert "p606" not in sim.start_order, "前提: 第 6 个项目应在排队"

        await sim.crash_running()  # 心跳停摆, Redis 记录原样留着
        sim.advance(1000)  # 用推进时间戳越过 holder TTL, 不 sleep 真实时间
        await sim.drain_once()

        assert "p606" in sim.start_order, (
            f"崩溃持有者的份额没有被回收, 等待项目永远起不来: {sim.start_order}"
        )


@pytest.mark.asyncio
async def test_live_holder_keeps_its_slot_while_heartbeat_is_fresh(monkeypatch) -> None:
    """与上一条互为对照: 心跳仍在续期的长任务不得被当成崩溃而回收。"""

    from app.core.tasks.fairness.project_admission import refresh_project_share

    async with _sim(monkeypatch) as sim:
        for project in (101, 202, 303, 404, 505):
            sim.submit(project, f"p{project}")
        sim.submit(606, "p606")
        await sim.drain_once()

        for _ in range(4):
            sim.advance(400)
            for field_name in list(sim.redis.holders()):
                await refresh_project_share(
                    sim.redis, resource_type="llm", field=field_name
                )
            await sim.drain_once()

        assert "p606" not in sim.start_order, (
            "心跳新鲜的持有者被误判为崩溃, 名额被超发"
        )
        assert len(sim.redis.holders()) == 5
