"""Dispatcher 请求级项目公平队列测试

锁定的风险:
- 一个项目的扇出把其它项目的 LLM 请求压在 FIFO 队尾饿死
- 为了轮转而闲置容量: 单项目时必须能跑满全局并发
- 空闲项目累积配额, 回来时一次性抢走一大把许可
- 排队请求被外层 deadline 取消后并发槽位永久泄漏
- 轮转发放突破容量上限
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.dispatcher_concurrency import _ResizableSemaphore
from app.core.ai.project_fair_queue import (
    ProjectFairQueue,
    fair_request_slot,
    queue_for_source,
)


class _FakeSource:
    """容量源替身: 只提供公平队列真正依赖的两个原语。"""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.available = capacity
        self.peak_in_use = 0

    def try_acquire(self) -> bool:
        if self.available <= 0:
            return False
        self.available -= 1
        self.peak_in_use = max(self.peak_in_use, self.capacity - self.available)
        return True

    def release(self) -> None:
        self.available += 1


async def _settle() -> None:
    """让已就绪的协程推进到下一个 await 点。"""

    for _ in range(6):
        await asyncio.sleep(0)


class _Waiters:
    """管理排队中的请求任务, 退出时统一取消, 不给事件循环留残留任务。"""

    def __init__(self, queue: ProjectFairQueue) -> None:
        self._queue = queue
        self._tasks: list[asyncio.Task] = []
        self.granted: list[str] = []

    async def spawn(self, key: str, label: str) -> asyncio.Task:
        async def _wait() -> None:
            await self._queue.acquire_slot(key)
            self.granted.append(label)

        task = asyncio.create_task(_wait())
        self._tasks.append(task)
        await _settle()
        return task

    async def __aenter__(self) -> "_Waiters":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_other_project_is_served_before_the_backlog_of_the_first() -> None:
    """ 请求级映射: A 排了 5 个请求, B 的第一个请求不得等在第 6 位。"""

    source = _FakeSource(1)
    queue = ProjectFairQueue(source, quantum=1.0)

    await queue.acquire_slot("A")  # 唯一的槽位先被 A 占住
    async with _Waiters(queue) as waiters:
        for index in range(5):
            await waiters.spawn("A", f"a{index}")
        await waiters.spawn("B", "b0")
        assert queue.pending == 6

        queue.release_slot("A")
        await _settle()
        assert waiters.granted == ["a0"]

        queue.release_slot("A")
        await _settle()
        assert waiters.granted == ["a0", "b0"], (
            f"B 的请求排在 A 的 5 个积压之后才被发放: {waiters.granted}"
        )


@pytest.mark.asyncio
async def test_five_projects_all_progress_under_contention() -> None:
    """5 个项目同时争用 5 份容量时, 每个项目都必须拿到许可。"""

    source = _FakeSource(5)
    queue = ProjectFairQueue(source, quantum=1.0)

    async with _Waiters(queue) as waiters:
        for index in range(10):
            await waiters.spawn("A", f"a{index}")
        for project in ("B", "C", "D", "E"):
            await waiters.spawn(project, project.lower())

        for _ in range(5):
            queue.release_slot("A")
            await _settle()

        served_projects = {label[0].upper() for label in waiters.granted}
        assert served_projects == {"A", "B", "C", "D", "E"}, (
            f"有项目在轮转中被跳过: 已发放={waiters.granted}"
        )


@pytest.mark.asyncio
async def test_single_project_uses_whole_capacity_without_queueing() -> None:
    """只有一个项目时不得引入任何排队延迟, 必须直接跑满容量。"""

    source = _FakeSource(5)
    queue = ProjectFairQueue(source, quantum=1.0)

    for _ in range(5):
        assert await queue.acquire_slot("A") is True

    assert source.available == 0 and queue.pending == 0
    assert source.peak_in_use == 5, (
        f"单项目未能借满容量, 峰值仅 {source.peak_in_use}"
    )


@pytest.mark.asyncio
async def test_idle_project_does_not_accumulate_quota() -> None:
    """项目排空后配额必须清零, 否则它回来时能一次性抢走一大把许可。"""

    source = _FakeSource(1)
    queue = ProjectFairQueue(source, quantum=1.0)

    await queue.acquire_slot("A")
    async with _Waiters(queue) as waiters:
        await waiters.spawn("B", "b0")
        for index in range(3):
            await waiters.spawn("A", f"a{index}")

        # 把 B 发放掉, 使 B 的队列排空
        for _ in range(4):
            queue.release_slot("A")
            await _settle()

        assert "b0" in waiters.granted, "前提: B 的请求应已被发放"
        assert queue._flows["B"].deficit == 0.0, (
            f"排空的项目仍保留 {queue._flows['B'].deficit} 配额, 回来时会一次性突发"
        )


@pytest.mark.asyncio
async def test_capacity_is_never_exceeded_by_rotation() -> None:
    """轮转只决定发给谁, 绝不能凭空造出许可。"""

    source = _FakeSource(3)
    queue = ProjectFairQueue(source, quantum=1.0)

    async with _Waiters(queue) as waiters:
        for project in ("A", "B", "C", "D", "E"):
            for index in range(4):
                await waiters.spawn(project, f"{project}{index}")
        await _settle()

        assert source.available >= 0
        assert source.peak_in_use <= 3, (
            f"并发峰值 {source.peak_in_use} 超过容量 3"
        )
        assert len(waiters.granted) <= 3


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_a_slot() -> None:
    """外层 deadline 取消排队请求后, 队列状态必须干净, 容量不得漏掉。"""

    source = _FakeSource(1)
    queue = ProjectFairQueue(source, quantum=1.0)

    await queue.acquire_slot("A")
    async with _Waiters(queue) as waiters:
        task = await waiters.spawn("B", "b0")
        assert queue.pending == 1

        task.cancel()
        await _settle()

        assert queue.pending == 0
        queue.release_slot("A")
        assert source.available == 1, (
            f"取消排队者后容量对不上: available={source.available}"
        )


@pytest.mark.asyncio
async def test_slot_granted_at_the_moment_of_cancellation_is_returned() -> None:
    """许可已发出却撞上取消时必须原样归还, 否则每超时一次就永久漏一个槽位。"""

    source = _FakeSource(1)
    queue = ProjectFairQueue(source, quantum=1.0)

    await queue.acquire_slot("A")
    async with _Waiters(queue) as waiters:
        task = await waiters.spawn("B", "b0")

        # 同一个同步区间内先发放再取消, 复刻 wait_for 超时与发放竞争的窗口
        queue.release_slot("A")
        task.cancel()
        await _settle()

        assert waiters.granted == [], "前提: 该请求应以取消收场"
        assert source.available == 1, (
            f"发放后被取消的许可没有归还, 容量永久少了一格: available={source.available}"
        )


# ==================== 容量源原语 ====================

@pytest.mark.asyncio
async def test_resizable_semaphore_try_acquire_matches_real_capacity() -> None:
    """非阻塞获取必须与 Semaphore 的真实可用量一致, 且不抢在排队者身前。"""

    semaphore = _ResizableSemaphore(2)

    assert semaphore.try_acquire() is True
    assert semaphore.try_acquire() is True
    assert semaphore.try_acquire() is False, "容量已耗尽却仍然拿到了许可"

    semaphore.release()
    assert semaphore.try_acquire() is True

    waiter = asyncio.create_task(semaphore.acquire())
    await _settle()
    assert semaphore.try_acquire() is False, "有人在排队时不得插队获取"

    semaphore.release()
    await _settle()
    assert waiter.done()


@pytest.mark.asyncio
async def test_shrunk_capacity_is_not_handed_out_by_the_fair_queue() -> None:
    """AIMD 欠账式缩容期间, 归还的许可要先抵欠账, 公平队列不得抢先发出去。"""

    semaphore = _ResizableSemaphore(3)
    queue = ProjectFairQueue(semaphore, quantum=1.0)

    for _ in range(3):
        assert await queue.acquire_slot("A") is True
    semaphore.shrink(2)  # 无空闲槽位可扣, 全部转为欠账

    async with _Waiters(queue) as waiters:
        await waiters.spawn("B", "b0")

        queue.release_slot("A")
        await _settle()
        assert waiters.granted == [], "第一份归还应先抵缩容欠账, 不得发放"

        queue.release_slot("A")
        await _settle()
        assert waiters.granted == [], "第二份归还仍在抵欠账"

        queue.release_slot("A")
        await _settle()
        assert waiters.granted == ["b0"], "欠账还清后才轮到排队者拿到许可"


# ==================== 接线开关 ====================

def test_fair_slot_is_bypassed_when_disabled(monkeypatch) -> None:
    """关掉开关必须完整退回裸 Semaphore, 这是回滚路径。"""

    monkeypatch.setattr(
        "app.core.ai.project_fair_queue.fair_queue_enabled", lambda: False
    )
    source = _FakeSource(3)

    assert fair_request_slot(source, purpose="review", project_id=1) is source


def test_same_source_shares_one_queue_across_projects() -> None:
    """同一容量源必须共用一个轮转队列, 否则各项目各转各的等于没有公平。"""

    source = _FakeSource(3)

    assert queue_for_source(source) is queue_for_source(source)


def test_projects_are_isolated_but_purposes_are_not_mixed() -> None:
    """轮转键必须区分 purpose 与项目, 避免 review 与 vision 互相干扰。"""

    source = _FakeSource(3)
    review_a = fair_request_slot(source, purpose="review", project_id=1)
    review_b = fair_request_slot(source, purpose="review", project_id=2)
    vision_a = fair_request_slot(source, purpose="vision", project_id=1)

    keys = {slot._key for slot in (review_a, review_b, vision_a)}
    assert keys == {"review:1", "review:2", "vision:1"}
