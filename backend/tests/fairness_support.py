"""项目级公平调度测试支撑件

职责: 为公平调度测试提供 Redis 替身与一个可控的 worker 调度模拟器
不做: 不含断言、不被 pytest 收集为用例
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks.fairness.project_admission import (
    ProjectShareHold,
    claim_project_share_or_defer,
    holders_key,
    release_project_share_after_job,
    waiting_key,
)

T0 = 1_700_000_000


class FakeFairnessRedis:
    """只实现准入路径用到的原语: hash 读写 + SET NX PX 互斥锁。"""

    def __init__(self, resource: str = "llm") -> None:
        self.resource = resource
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, str] = {}

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hset(self, key: str, field_name: str, value: str) -> int:
        self.hashes.setdefault(key, {})[field_name] = value
        return 1

    async def hdel(self, key: str, field_name: str) -> int:
        self.hashes.get(key, {}).pop(field_name, None)
        return 1

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def set(self, key: str, value: str, nx: bool = False, px: int = 0):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def delete(self, key: str) -> int:
        self.strings.pop(key, None)
        return 1

    def holders(self) -> dict[str, str]:
        return dict(self.hashes.get(holders_key(self.resource), {}))

    def waiting(self) -> dict[str, str]:
        return dict(self.hashes.get(waiting_key(self.resource), {}))

    def running_projects(self) -> set[str]:
        return {field_name.split("|", 1)[0] for field_name in self.holders()}


@dataclass
class _QueuedJob:
    """队列中的一个 job; score 模拟 ARQ 队列 zset 的可领取时间。"""

    project: int | None
    job_id: str
    score: float
    job_try: int = 1
    seq: int = 0


@dataclass
class WorkerSim:
    """确定性的 worker 调度模拟器。

    刻意复刻 ARQ 的三条真实行为, 好让"让出槽位"这件事能被真正验证:
      1. 槽位有限（max_jobs）, 槽位占满就不再领取新 job;
      2. 队列按 score 先小后大领取, score 相同按入队顺序;
      3. Retry 让 job 保持同一个 job_id 回到队列, score 推到未来, job_try 加一。
    """

    redis: FakeFairnessRedis
    slots: int
    capacity: int
    resource: str = "llm"
    clock: float = float(T0)
    queue: list[_QueuedJob] = field(default_factory=list)
    running: dict[str, dict] = field(default_factory=dict)
    start_order: list[str] = field(default_factory=list)
    defer_events: list[tuple[str, int]] = field(default_factory=list)
    _seq: int = 0

    def bind_clock(self, monkeypatch) -> None:
        """把准入判定的时间源接到模拟时钟上; 时间条件必须靠推进时间戳构造, 不 sleep。"""

        monkeypatch.setattr(time, "time", lambda: self.clock)

    async def __aenter__(self) -> "WorkerSim":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.crash_running()

    async def crash_running(self) -> None:
        """模拟 worker 进程被 kill: 心跳停摆, 但 Redis 里的持有记录原样留下。"""

        beats = [
            ctx[key].heartbeat
            for ctx in self.running.values()
            for key in ("bidmind_project_share",)
            if isinstance(ctx.get(key), ProjectShareHold)
            and ctx[key].heartbeat is not None
        ]
        self.running.clear()
        for beat in beats:
            beat.cancel()
        if beats:
            await asyncio.gather(*beats, return_exceptions=True)

    def submit(self, project: int | None, job_id: str) -> None:
        self._seq += 1
        self.queue.append(
            _QueuedJob(project=project, job_id=job_id, score=self.clock, seq=self._seq)
        )

    def advance(self, seconds: float) -> None:
        self.clock += seconds

    async def drain_once(self) -> None:
        """领取当前可领取的 job, 直到槽位占满或无可领取项。"""

        from arq.worker import Retry

        while len(self.running) < self.slots:
            claimable = [job for job in self.queue if job.score <= self.clock]
            if not claimable:
                return
            job = min(claimable, key=lambda item: (item.score, item.seq))
            self.queue.remove(job)

            ctx = {
                "redis": self.redis,
                "job_try": job.job_try,
                "job_id": job.job_id,
            }
            try:
                await claim_project_share_or_defer(
                    ctx,
                    resource_type=self.resource,
                    project_id=job.project,
                    owner=job.job_id,
                    capacity=self.capacity,
                )
            except Retry as retry:
                # 关键: 槽位从未被占用, job 立刻回到队列
                self.defer_events.append((job.job_id, job.job_try))
                job.job_try += 1
                job.score = self.clock + (retry.defer_score or 0) / 1000.0
                self.queue.append(job)
                continue

            self.running[job.job_id] = ctx
            self.start_order.append(job.job_id)

    async def complete(self, job_id: str) -> None:
        ctx = self.running.pop(job_id)
        await release_project_share_after_job(ctx)

    def started_index(self, job_id: str) -> int:
        return self.start_order.index(job_id) if job_id in self.start_order else -1
