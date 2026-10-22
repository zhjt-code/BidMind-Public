""" 防御测试: gate_runner judge/CoVe evidence 的 DB 读取必须经
``composer._db_lock`` 串行化。

背景: ``SectionComposer`` 在 ``compose_all`` 阶段2/阶段3 用 as_completed / gather
并发执行, 所有协程共享同一个 ``composer.db`` (AsyncSession)。 已为
binding_loader / material_context / draft_persistence 的 DB 操作补 ``_db_lock``,
但 Task6 重构新增的 ``gate_runner.build_writer_judge_evidence`` 两处
``composer.db.execute`` 漏加锁; 只要一处绕锁, 并发协程即并发操作同一 session,
在云端 RDS 高延迟下必现 ``concurrent operations are not permitted (isce)``。

本测试固定 "judge evidence 的全部 db.execute 均在 _db_lock 内串行" 的行为,
并附一个反向自检用例: 去掉锁时必须能检测到并发重叠, 保证测试具备鉴别力。
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")


class _FakeResult:
    """模拟 ``Result``: scalars().all() 返回空列表。"""

    def scalars(self):
        return self

    def all(self):
        return []


class _ConcurrencyProbe:
    """模拟 AsyncSession.execute, 记录并发重叠峰值 (max_active)。

    若同一时刻有多于一个 execute 处于活动态, 说明共享 session 被并发使用,
    对应真实环境的 isce 根因。
    """

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def execute(self, *args, **kwargs):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            # 让出事件循环并保持占用, 放大并发窗口(模拟云端 RDS 高延迟)
            await asyncio.sleep(0.005)
        finally:
            self.active -= 1
        return _FakeResult()


class _NoLock:
    """不互斥的伪锁, 用于复现未加锁(修复前)的并发行为。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeComposer:
    """最小 composer 替身: 仅暴露 build_writer_judge_evidence 所需的 db / _db_lock。"""

    def __init__(self, lock) -> None:
        self.db = _ConcurrencyProbe()
        self._db_lock = lock


async def _run_concurrent_judge_evidence(lock, fanout: int = 8):
    from app.core.writer.composer import gate_runner

    composer = _FakeComposer(lock)

    async def _one():
        return await gate_runner.build_writer_judge_evidence(
            composer,
            node_id=1,
            source_material_ids=[1, 2, 3],
            source_clause_ids=[4, 5],
        )

    results = await asyncio.gather(*[_one() for _ in range(fanout)])
    return composer, results


async def test_judge_evidence_serialized_under_db_lock():
    """加锁后并发 judge evidence 的 db.execute 必须严格串行 (max_active==1)。"""
    composer, results = await _run_concurrent_judge_evidence(asyncio.Lock())

    # 8 协程 x 2 次查询(素材 + 硬约束) = 16, 证明两处 db 路径都被执行到
    assert composer.db.calls == 16, (
        f"预期 8 协程 x 2 次查询=16, 实际 {composer.db.calls}"
    )
    assert composer.db.max_active == 1, (
        f"_db_lock 未串行化 db 操作, 并发峰值={composer.db.max_active}, "
        "共享 AsyncSession 将触发 isce"
    )
    assert all(
        r == {"bound_materials": [], "hard_constraints": []} for r in results
    )


async def test_probe_detects_overlap_without_lock():
    """护栏自检: 去掉锁(伪锁)时必须检测到并发重叠, 证明上一个测试有鉴别力。

    若此用例失败, 说明并发探针本身失效, 则正向用例的 "max_active==1" 不可信。
    """
    composer, _ = await _run_concurrent_judge_evidence(_NoLock())

    assert composer.db.max_active > 1, (
        "无锁场景未检测到并发重叠, 说明并发探针失效, 防御测试不可信"
    )
