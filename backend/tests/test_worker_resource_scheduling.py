"""Worker 资源调度合同测试。"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks.resource_permit import (
    _orphan_grace_seconds,
    _owner_age_seconds,
    _parse_orphan_marker,
    _reconcile_stale_owners,
    _register_owner,
    acquire_resource_permit,
    build_permit_owner,
    get_resource_inflight_sync,
    permit_context,
    release_resource_owner,
    release_resource_permit,
)
from app.core.tasks.orchestrator import guard_writing_material_process
from app.core.tasks.resource_policy import (
    policy_for_function,
    resource_type_for_function,
)
from app.models.project import ProjectStatus


_INFLIGHT_KEY_SUFFIX = ":inflight"
_OWNERS_KEY_SUFFIX = ":owners"


def _inflight_key(resource: str) -> str:
    return f"bidmind:worker:resource:{resource}{_INFLIGHT_KEY_SUFFIX}"


def _owners_key(resource: str) -> str:
    return f"bidmind:worker:resource:{resource}{_OWNERS_KEY_SUFFIX}"


class _Clock:
    """可控时钟: 时间条件必须用推进时间戳构造, 不允许 sleep 真实时间。"""

    def __init__(self, start: int = 1_700_000_000) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def decr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True

    async def delete(self, key: str) -> int:
        self.values.pop(key, None)
        return 1

    async def get(self, key: str) -> str | None:
        value = self.values.get(key)
        return None if value is None else str(value)

    async def hset(self, key: str, field: str, value: str) -> int:
        owners = self.values.setdefault(key, {})  # type: ignore[assignment]
        owners[field] = value  # type: ignore[index]
        return 1

    async def hdel(self, key: str, field: str) -> int:
        owners = self.values.get(key)
        if isinstance(owners, dict):
            owners.pop(field, None)
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        owners = self.values.get(key)
        return dict(owners) if isinstance(owners, dict) else {}

    async def set(self, key: str, value: str) -> bool:
        self.values[key] = value  # type: ignore[assignment]
        return True

    def inflight_of(self, resource: str) -> int:
        return int(self.values.get(_inflight_key(resource), 0) or 0)

    def owners_of(self, resource: str) -> dict[str, str]:
        owners = self.values.get(_owners_key(resource))
        return dict(owners) if isinstance(owners, dict) else {}

    def assert_owner_count_matches_inflight(self, resource: str, expected: int) -> None:
        """ 核心不变量: 每个在途占用都必须有且只有一条 owner 记录。"""

        assert self.inflight_of(resource) == expected, (
            f"inflight 期望 {expected}, 实际 {self.inflight_of(resource)}"
        )
        assert len(self.owners_of(resource)) == expected, (
            f"owner 数期望 {expected}, 实际 {len(self.owners_of(resource))}: "
            f"{sorted(self.owners_of(resource))}"
        )


class _BytesReplyRedis(_FakeRedis):
    """模拟 ARQ Redis decode_responses=False，GET 返回 bytes。"""

    async def get(self, key: str) -> bytes | None:
        value = self.values.get(key)
        if value is None or isinstance(value, dict):
            return None
        return str(value).encode()


class _OwnerWriteFailingRedis(_FakeRedis):
    """模拟 owner 登记失败 (Redis HSET 报错) 的降级场景。"""

    async def hset(self, key: str, field: str, value: str) -> int:
        raise RuntimeError("hset unavailable")


class _FakeSyncRedis:
    """探针专用同步 Redis 替身, 记录是否发生过写操作。"""

    def __init__(self, inflight: int, owners: dict[str, str] | None = None) -> None:
        self._inflight = inflight
        self._owners = owners or {}
        self.deleted_keys: list[str] = []

    def get(self, key: str) -> str | None:
        return str(self._inflight)

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._owners)

    def delete(self, key: str) -> int:
        self.deleted_keys.append(key)
        return 1


class _FakeProject:
    def __init__(self, status: ProjectStatus) -> None:
        self.status = status


class _FakeScalarResult:
    def __init__(self, value: int | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> int | None:
        return self._value


class _FakeDb:
    def __init__(self, status: ProjectStatus | None, outline_id: int | None = 1) -> None:
        self._project = None if status is None else _FakeProject(status)
        self._outline_id = outline_id

    async def get(self, model, project_id: int):  # noqa: ANN001
        return self._project

    async def execute(self, stmt):  # noqa: ANN001
        return _FakeScalarResult(self._outline_id)


def test_resource_policy_maps_core_jobs() -> None:
    """核心 job 必须映射到预期资源类型，避免后续新增任务绕过调度。"""

    assert resource_type_for_function("process_document_job") == "process"
    assert resource_type_for_function("review_bid_extract_job") == "llm"
    assert resource_type_for_function("review_project_job") == "llm"
    assert resource_type_for_function("document_image_vl_job") == "llm"
    assert resource_type_for_function("writing_compose_job") == "llm"
    assert resource_type_for_function("flush_metrics_job") == "maintenance"
    # GPU 是 CUDA 临界区许可，不是 worker 入口队列映射。
    assert policy_for_function("unknown_job").resource_type != "gpu"
    assert policy_for_function("unknown_job").resource_type == "default"


def test_business_jobs_are_not_mapped_to_gpu_queue() -> None:
    """GPU 只作为 CUDA admission 许可, 业务 job 不应直接映射到 gpu worker 队列。"""

    business_jobs = [
        "process_document_job",
        "writing_material_process_job",
        "writing_clause_vectorize_job",
        "extract_requirements_job",
        "review_bid_extract_job",
        "review_project_job",
        "collusion_analyze_job",
        "document_image_vl_job",
        "writing_image_vl_job",
        "writing_image_classify_job",
        "writing_extract_job",
        "writing_outline_job",
        "writing_match_job",
        "writing_compose_job",
        "writing_compose_single_job",
        "express_advance_job",
        "flush_metrics_job",
        "regression_runner_job",
    ]

    assert all(resource_type_for_function(job) != "gpu" for job in business_jobs)


@pytest.mark.asyncio
async def test_resource_permit_releases_on_exception() -> None:
    """permit_context 即使业务抛错也必须释放 inflight。"""

    redis = _FakeRedis()

    with pytest.raises(RuntimeError):
        async with permit_context(redis, "llm", limit=1, wait_timeout=0.1) as acquired:
            assert acquired is True
            raise RuntimeError("boom")

    assert all((value <= 0 if isinstance(value, int) else not value) for value in redis.values.values())


@pytest.mark.asyncio
async def test_resource_permit_blocks_when_limit_reached() -> None:
    """超过资源上限时应返回未获取许可，不可继续增加 inflight。"""

    redis = _FakeRedis()

    async with permit_context(redis, "process", limit=1, wait_timeout=0.1) as acquired:
        assert acquired is True
        async with permit_context(redis, "process", limit=1, wait_timeout=0.1) as second:
            assert second is False


@pytest.mark.asyncio
async def test_gpu_permit_waits_then_acquires_when_slot_frees() -> None:
    """形态: 许可占满时后来者应等待, 槽位释放后拿到 GPU 而不是立刻失败。
    同形态第二实体用 gpu 资源, 与 process 上限拒绝形成对照。
    """

    redis = _FakeRedis()
    held = await acquire_resource_permit(
        redis, "gpu", limit=1, wait_timeout=0.0, poll_interval=0.05, owner="holder"
    )
    assert held is True

    waiter = asyncio.create_task(
        acquire_resource_permit(
            redis,
            "gpu",
            limit=1,
            wait_timeout=1.0,
            poll_interval=0.05,
            owner="waiter",
        )
    )
    await asyncio.sleep(0.12)
    assert waiter.done() is False

    await release_resource_owner(redis, "gpu", "holder")
    await release_resource_permit(redis, "gpu")
    acquired = await asyncio.wait_for(waiter, timeout=1.5)
    assert acquired is True
    redis.assert_owner_count_matches_inflight("gpu", 1)
    assert "waiter" in redis.owners_of("gpu")


@pytest.mark.asyncio
async def test_gpu_permit_times_out_so_caller_can_cpu_fallback() -> None:
    """形态: 等待窗耗尽仍占满时返回 False, 由 admission 走 cpu_fallback。
    同形态第二实体仍用 gpu, 与立刻拿到许可的 holder 对照。
    """

    redis = _FakeRedis()
    held = await acquire_resource_permit(
        redis, "gpu", limit=1, wait_timeout=0.0, owner="holder"
    )
    assert held is True

    started = time.monotonic()
    acquired = await acquire_resource_permit(
        redis, "gpu", limit=1, wait_timeout=0.25, poll_interval=0.05, owner="waiter"
    )
    elapsed = time.monotonic() - started
    assert acquired is False
    assert elapsed >= 0.2
    redis.assert_owner_count_matches_inflight("gpu", 1)
    assert "holder" in redis.owners_of("gpu")
    assert "waiter" not in redis.owners_of("gpu")


@pytest.mark.asyncio
async def test_resource_permit_records_and_clears_owner() -> None:
    """permit owner 应随上下文释放，便于诊断残留占用。"""

    redis = _FakeRedis()
    async with permit_context(redis, "llm", limit=1, owner="worker-1:review") as acquired:
        assert acquired is True
        owner_keys = [value for key, value in redis.values.items() if key.endswith(":owners")]
        assert owner_keys and isinstance(owner_keys[0], dict)
        assert "worker-1:review" in owner_keys[0]

    owner_keys = [value for key, value in redis.values.items() if key.endswith(":owners")]
    assert not owner_keys or not owner_keys[0]


def test_permit_owner_is_unique_per_job() -> None:
    """同 worker 同函数的不同 job 必须得到不同 owner, 这是 的根因所在。"""

    owner_a = build_permit_owner(
        worker_id="worker-1",
        function_name="review_project_job",
        job_id="job-a",
        project_id=515,
    )
    owner_b = build_permit_owner(
        worker_id="worker-1",
        function_name="review_project_job",
        job_id="job-b",
        project_id=515,
    )

    assert owner_a == "worker-1:review_project_job:job-a:515"
    assert owner_b == "worker-1:review_project_job:job-b:515"
    assert owner_a != owner_b, (
        f"同 worker 同函数的两个 job 共用了 owner {owner_a}, "
        f"先退出者会删掉仍在运行者的 owner 记录"
    )


def test_permit_owner_without_project_id_uses_explicit_placeholder() -> None:
    """knowledge_base_process_job 无 project_id, 占位必须显式而非空串, 避免分段歧义。"""

    owner = build_permit_owner(
        worker_id="worker-1",
        function_name="knowledge_base_process_job",
        job_id="job-kb",
        project_id=None,
    )

    assert owner == "worker-1:knowledge_base_process_job:job-kb:none"
    assert owner.count(":") == 3
    assert "::" not in owner


def test_permit_owner_falls_back_to_random_when_job_id_missing() -> None:
    """job_id 缺失时宁可生成随机段, 也不能让两个并发 job 共用同一个 owner。"""

    owner_a = build_permit_owner(
        worker_id="worker-1", function_name="writing_compose_job", project_id=7
    )
    owner_b = build_permit_owner(
        worker_id="worker-1", function_name="writing_compose_job", project_id=7
    )

    assert owner_a != owner_b
    assert owner_a.split(":")[2].startswith("anon-")
    assert owner_b.split(":")[2].startswith("anon-")


@pytest.mark.asyncio
async def test_owner_count_matches_inflight_through_concurrent_lifecycle() -> None:
    """核心不变量: 并发同函数 job 先后进出时, 每个时间点 owner 数都等于 inflight。"""

    redis = _FakeRedis()
    owners = [
        build_permit_owner(
            worker_id="worker-1",
            function_name="review_project_job",
            job_id=f"job-{index}",
            project_id=515,
        )
        for index in range(3)
    ]

    for index, owner in enumerate(owners, start=1):
        assert await acquire_resource_permit(
            redis, "llm", limit=3, owner=owner, wait_timeout=0.1
        ) is True
        redis.assert_owner_count_matches_inflight("llm", index)

    for index, owner in enumerate(owners):
        await release_resource_owner(redis, "llm", owner)
        await release_resource_permit(redis, "llm")
        redis.assert_owner_count_matches_inflight("llm", len(owners) - index - 1)

    assert redis.inflight_of("llm") == 0
    assert redis.owners_of("llm") == {}


@pytest.mark.asyncio
async def test_early_finished_job_does_not_clear_running_job_inflight() -> None:
    """ 回归: 先结束的同函数 job 释放后, 仍在运行的 job 计数不得被清零。

 刻意复刻修复前的共享 owner 形态, 锁住第二道防线: 即便 owners 哈希被提前删空,
 也不允许据此判定"无人占用"并清掉计数。
 """

    redis = _FakeRedis()
    shared_owner = "worker-1:review_project_job"  # 修复前 worker.py 的 owner 形态

    assert await acquire_resource_permit(
        redis, "llm", limit=3, owner=shared_owner, wait_timeout=0.1
    ) is True
    assert await acquire_resource_permit(
        redis, "llm", limit=3, owner=shared_owner, wait_timeout=0.1
    ) is True
    assert redis.inflight_of("llm") == 2, "两个并发 job 应各占一份计数"

    # 先结束的 job 释放, 共享 owner 被整条删掉, 但另一个 job 仍在运行
    await release_resource_owner(redis, "llm", shared_owner)
    await release_resource_permit(redis, "llm")
    assert redis.inflight_of("llm") == 1, "仍有一个 job 在途, 计数应为 1"
    assert redis.owners_of("llm") == {}, "共享 owner 被删空是本场景的前提"

    # 下一次申请许可会触发残留清理; 在途 job 的计数必须原样保留
    owner_next = build_permit_owner(
        worker_id="worker-1", function_name="writing_compose_job", job_id="job-next", project_id=8
    )
    assert await acquire_resource_permit(
        redis, "llm", limit=3, owner=owner_next, wait_timeout=0.1
    ) is True

    assert redis.inflight_of("llm") == 2, (
        f" 复发: 在途 job 的 inflight 被清零后重新计数, "
        f"实际 {redis.inflight_of('llm')}, 期望 2 (在途 job + 新 job)"
    )


@pytest.mark.asyncio
async def test_worker_permit_owner_is_unique_per_arq_job(monkeypatch) -> None:
    """worker 接线回归: 同 worker 同函数的两个 ARQ job 必须拿到不同 owner。"""

    from contextlib import asynccontextmanager, contextmanager
    from types import SimpleNamespace

    from app import worker as worker_module

    captured_owners: list[str] = []

    @asynccontextmanager
    async def fake_permit_context(redis, resource_type, limit, owner):
        captured_owners.append(owner)
        yield True

    @contextmanager
    def fake_cpu_sticky_scope():
        yield

    async def noop() -> None:
        return None

    monkeypatch.setattr(
        "app.core.tasks.resource_permit.permit_context", fake_permit_context
    )
    monkeypatch.setattr(
        "app.core.tasks.resource_policy.policy_for_function",
        lambda function_name: SimpleNamespace(resource_type="llm", max_jobs=2),
    )
    monkeypatch.setattr(
        "app.core.vectorizer.encode_gate.task_cpu_sticky_scope", fake_cpu_sticky_scope
    )
    monkeypatch.setattr(
        "app.worker.is_project_cancelled_async",
        lambda redis, project_id: _false(),
    )

    for job_id in ("arq-job-1", "arq-job-2"):
        await worker_module._run_with_resource_permit(
            {"redis": object(), "bidmind_worker_id": "worker-1", "job_id": job_id},
            "review_project_job",
            noop,
            project_id=515,
            task_type="review",
        )

    assert captured_owners == [
        "worker-1:review_project_job:arq-job-1:515",
        "worker-1:review_project_job:arq-job-2:515",
    ]
    assert len(set(captured_owners)) == 2


async def _false() -> bool:
    return False


@pytest.mark.asyncio
async def test_concurrent_jobs_never_exceed_resource_limit() -> None:
    """owner 唯一化后并发上限仍必须严格生效, 不得因残留清理被突破。"""

    redis = _FakeRedis()
    granted: list[str] = []

    for index in range(4):
        owner = build_permit_owner(
            worker_id="worker-1",
            function_name="review_project_job",
            job_id=f"job-{index}",
            project_id=515,
        )
        if await acquire_resource_permit(redis, "llm", limit=2, owner=owner, wait_timeout=0.1):
            granted.append(owner)

    assert len(granted) == 2
    redis.assert_owner_count_matches_inflight("llm", 2)


@pytest.mark.asyncio
async def test_stale_owner_beyond_ttl_is_reclaimed_with_its_count() -> None:
    """超过 TTL 的孤儿 owner 应被回收, 且只扣减它自己那一份计数。"""

    redis = _FakeRedis()
    alive_owner = build_permit_owner(
        worker_id="worker-1", function_name="review_project_job", job_id="job-alive", project_id=1
    )
    stale_owner = build_permit_owner(
        worker_id="worker-9", function_name="review_project_job", job_id="job-dead", project_id=2
    )

    assert await acquire_resource_permit(
        redis, "llm", limit=5, owner=alive_owner, wait_timeout=0.1
    ) is True
    assert await acquire_resource_permit(
        redis, "llm", limit=5, owner=stale_owner, wait_timeout=0.1
    ) is True
    # 用可控时间戳把 stale_owner 推到 TTL 之外, 不依赖真实等待
    redis.values[_owners_key("llm")][stale_owner] = str(int(time.time()) - 7200)  # type: ignore[index]

    new_owner = build_permit_owner(
        worker_id="worker-1", function_name="writing_compose_job", job_id="job-new", project_id=3
    )
    assert await acquire_resource_permit(
        redis, "llm", limit=5, owner=new_owner, wait_timeout=0.1
    ) is True

    assert stale_owner not in redis.owners_of("llm")
    assert alive_owner in redis.owners_of("llm")
    redis.assert_owner_count_matches_inflight("llm", 2)


@pytest.mark.asyncio
async def test_empty_owner_hash_no_longer_wipes_inflight_count() -> None:
    """匿名占用 (不传 owner) 时 owners 为空, 不得被当作残留而清掉计数。"""

    redis = _FakeRedis()

    assert await acquire_resource_permit(redis, "process", limit=3, wait_timeout=0.1) is True
    assert redis.inflight_of("process") == 1, "匿名占用同样要计数"
    assert redis.owners_of("process") == {}, "不传 owner 时 owners 应为空, 这是本场景前提"

    owner = build_permit_owner(
        worker_id="worker-1", function_name="process_document_job", job_id="job-x", project_id=9
    )
    assert await acquire_resource_permit(
        redis, "process", limit=3, owner=owner, wait_timeout=0.1
    ) is True

    assert redis.inflight_of("process") == 2, (
        f"owners 为空被误判为无人占用, 匿名在途任务的计数被清掉, "
        f"实际 {redis.inflight_of('process')}, 期望 2"
    )


@pytest.mark.asyncio
async def test_owner_registration_failure_rejects_permit_without_leaking_count() -> None:
    """owner 写入失败必须拒绝占用并回退计数, 不能静默留下无主 inflight。"""

    redis = _OwnerWriteFailingRedis()
    owner = build_permit_owner(
        worker_id="worker-1", function_name="review_project_job", job_id="job-a", project_id=515
    )

    acquired = await acquire_resource_permit(
        redis, "llm", limit=2, owner=owner, wait_timeout=0.1
    )

    assert acquired is False
    assert redis.inflight_of("llm") == 0
    assert redis.owners_of("llm") == {}


@pytest.mark.asyncio
async def test_ownerless_residual_count_is_reclaimed_after_grace(monkeypatch) -> None:
    """无主残留计数必须能自愈: 超过宽限期后被回收, limit 随之恢复可用。

    残留成因是进程在 INCR 之后、owner 登记之前被 kill; 此类计数没有 owner 认领,
    且 inflight key 的 TTL 会被后续每次 acquire 刷新, 不会自然过期。
    """

    redis = _FakeRedis()
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)

    await redis.incr(_inflight_key("llm"))
    await redis.incr(_inflight_key("llm"))
    assert redis.inflight_of("llm") == 2, "前提: 构造出 2 份无主计数"
    assert redis.owners_of("llm") == {}, "前提: 这些计数没有任何 owner 认领"

    owner = build_permit_owner(
        worker_id="worker-1", function_name="review_project_job", job_id="job-a", project_id=515
    )

    # 首次观察只打标记, 不回收
    assert await acquire_resource_permit(
        redis, "llm", limit=2, owner=owner, wait_timeout=0.0
    ) is False
    assert redis.inflight_of("llm") == 2, "首次观察就回收等于没有时间判据"

    grace = _orphan_grace_seconds()
    # 宽限期未满仍不回收
    clock.advance(grace - 1)
    assert await acquire_resource_permit(
        redis, "llm", limit=2, owner=owner, wait_timeout=0.0
    ) is False
    assert redis.inflight_of("llm") == 2, "宽限期未满不得回收"

    # 越过宽限期后回收, 并且 limit 立刻恢复可用
    clock.advance(2)
    assert await acquire_resource_permit(
        redis, "llm", limit=2, owner=owner, wait_timeout=0.0
    ) is True, "宽限期已过, 残留应被回收并放行本次申请"
    redis.assert_owner_count_matches_inflight("llm", 1)


@pytest.mark.asyncio
async def test_shrinking_ownerless_excess_restarts_grace_window(monkeypatch) -> None:
    """无主量下降说明此前差额含瞬时窗口, 必须重新计时, 不能顺势回收。"""

    redis = _FakeRedis()
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)

    await redis.incr(_inflight_key("llm"))
    await redis.incr(_inflight_key("llm"))
    await _reconcile_stale_owners(redis, "llm")  # 打标记 (t0, 2)

    grace = _orphan_grace_seconds()
    half = max(grace // 2, 1)
    clock.advance(half)
    await redis.decr(_inflight_key("llm"))  # 无主量降到 1
    await _reconcile_stale_owners(redis, "llm")  # 重新计时

    clock.advance(half + 1)  # 距重新计时点仍未满宽限
    await _reconcile_stale_owners(redis, "llm")

    assert redis.inflight_of("llm") == 1, (
        f"无主量下降后应重新计时, 实际在新窗口未满时就被回收: "
        f"inflight={redis.inflight_of('llm')}, 期望 1"
    )

    clock.advance(grace)  # 新窗口满
    await _reconcile_stale_owners(redis, "llm")
    assert redis.inflight_of("llm") == 0, "新宽限期满后应完成回收"


@pytest.mark.asyncio
async def test_long_running_owner_is_never_reclaimed_while_heartbeat_fresh(monkeypatch) -> None:
    """持续续期心跳的长任务, 运行远超 TTL 也不得被当作残留清掉。"""

    redis = _FakeRedis()
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)

    owner = build_permit_owner(
        worker_id="worker-1",
        function_name="process_document_job",
        job_id="job-long",
        project_id=1,
    )
    assert await acquire_resource_permit(
        redis, "llm", limit=2, owner=owner, wait_timeout=0.0
    ) is True

    # 长任务跑满 3 个 TTL 周期 (10800s), 期间心跳按间隔续期
    for step in range(9):
        clock.advance(1200)
        assert await _register_owner(redis, "llm", owner) is True
        await _reconcile_stale_owners(redis, "llm")
        assert redis.inflight_of("llm") == 1, (
            f"第 {step + 1} 轮: 心跳新鲜的长任务计数被误清, "
            f"inflight={redis.inflight_of('llm')}, 期望 1"
        )
        assert owner in redis.owners_of("llm"), f"第 {step + 1} 轮: 长任务 owner 被误删"

    redis.assert_owner_count_matches_inflight("llm", 1)


@pytest.mark.asyncio
async def test_long_running_owner_is_reclaimed_once_heartbeat_stops(monkeypatch) -> None:
    """心跳停止 (持有进程已死) 才允许回收, 这是与长任务的唯一区分依据。"""

    redis = _FakeRedis()
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)

    owner = build_permit_owner(
        worker_id="worker-1", function_name="process_document_job", job_id="job-dead", project_id=1
    )
    assert await acquire_resource_permit(
        redis, "llm", limit=2, owner=owner, wait_timeout=0.0
    ) is True

    clock.advance(3601)  # 心跳不再续期, 超过判定阈值
    await _reconcile_stale_owners(redis, "llm")

    assert redis.owners_of("llm") == {}, "心跳过期的 owner 应被回收"
    assert redis.inflight_of("llm") == 0, "回收 owner 时必须同步扣减它那一份计数"


@pytest.mark.asyncio
async def test_permit_context_refreshes_then_stops_owner_heartbeat(monkeypatch) -> None:
    """持有期间必须续期心跳; 退出后必须停止续期, 不能把已释放的 owner 写回。"""

    redis = _FakeRedis()
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)
    monkeypatch.setattr(
        "app.core.tasks.resource_permit._owner_heartbeat_interval",
        lambda _resource=None: 0.01,
    )

    owner = build_permit_owner(
        worker_id="worker-1", function_name="review_project_job", job_id="job-hb", project_id=515
    )

    async with permit_context(redis, "llm", limit=1, owner=owner) as acquired:
        assert acquired is True
        first_heartbeat = redis.owners_of("llm")[owner]
        clock.advance(600)
        await asyncio.sleep(0.05)
        assert redis.owners_of("llm")[owner] != first_heartbeat, "持有期间心跳未续期"

    assert redis.owners_of("llm") == {}, "退出后 owner 必须被删除"
    clock.advance(600)
    await asyncio.sleep(0.05)
    assert redis.owners_of("llm") == {}, "心跳未停止, 把已释放的 owner 又写了回来"
    assert redis.inflight_of("llm") == 0


def test_inflight_probe_never_mutates_redis(monkeypatch) -> None:
    """探针必须是只读的: 在途任务存在但 owners 为空时也不得删除计数。"""

    client = _FakeSyncRedis(inflight=3, owners={})
    monkeypatch.setattr("app.core.redis.get_sync_redis", lambda: client)

    assert get_resource_inflight_sync("llm") == 3
    assert client.deleted_keys == []


@pytest.mark.asyncio
async def test_permit_context_reuses_single_owner_token() -> None:
    """acquire 与 release 必须共用同一个 owner token, 否则 owner 永远无法被清掉。"""

    redis = _FakeRedis()
    owner = build_permit_owner(
        worker_id="worker-1", function_name="review_project_job", job_id="job-ctx", project_id=515
    )

    async with permit_context(redis, "llm", limit=1, owner=owner) as acquired:
        assert acquired is True
        assert list(redis.owners_of("llm")) == [owner]
        redis.assert_owner_count_matches_inflight("llm", 1)

    assert redis.owners_of("llm") == {}
    assert redis.inflight_of("llm") == 0


@pytest.mark.asyncio
async def test_writing_material_guard_accepts_real_project_statuses() -> None:
    """写标素材 guard 只能使用真实存在的 ProjectStatus 成员。"""

    for status in (
        ProjectStatus.OUTLINED,
        ProjectStatus.MATCHING,
        ProjectStatus.MATCHED,
        ProjectStatus.WRITING,
        ProjectStatus.COMPOSED,
        ProjectStatus.FORMATTING,
        ProjectStatus.COMPLETED,
    ):
        result = await guard_writing_material_process(_FakeDb(status), project_id=1)
        assert result.decision == "ready"


@pytest.mark.asyncio
async def test_writing_material_guard_waits_for_outline() -> None:
    result = await guard_writing_material_process(
        _FakeDb(ProjectStatus.OUTLINED, outline_id=None),
        project_id=1,
    )
    assert result.decision == "waiting_dependency"
    assert result.reason == "outline_not_ready"


def test_parse_orphan_marker_and_owner_age_accept_bytes() -> None:
    """ARQ Redis 返回 bytes 时必须按正文解析，不能走 str(bytes) 的 repr。"""

    observed, excess = _parse_orphan_marker(b"1700000000:5")
    assert observed == 1700000000
    assert excess == 5
    assert _owner_age_seconds(b"1700000000", 1700000100) == 100
    assert _parse_orphan_marker("1700000000:3") == (1700000000, 3)


@pytest.mark.asyncio
async def test_bytes_orphan_marker_does_not_reset_grace(monkeypatch) -> None:
    """3655 夹具: bytes 观察标记不得每轮 acquire 都把无主宽限清零。"""

    redis = _BytesReplyRedis()
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)

    await redis.incr(_inflight_key("llm"))
    await redis.incr(_inflight_key("llm"))
    owner = build_permit_owner(
        worker_id="worker-1",
        function_name="extract_requirements_job",
        job_id="job-extract-3655",
        project_id=3655,
    )
    assert await acquire_resource_permit(
        redis, "llm", limit=2, owner=owner, wait_timeout=0.0
    ) is False

    clock.advance(_orphan_grace_seconds() - 1)
    assert await acquire_resource_permit(
        redis, "llm", limit=2, owner=owner, wait_timeout=0.0
    ) is False
    assert redis.inflight_of("llm") == 2, "bytes 标记被当成无法解析会导致宽限永远重新开始"

    clock.advance(2)
    assert await acquire_resource_permit(
        redis, "llm", limit=2, owner=owner, wait_timeout=0.0
    ) is True
    redis.assert_owner_count_matches_inflight("llm", 1)


@pytest.mark.asyncio
async def test_bytes_orphan_marker_process_resource_same_morphology(monkeypatch) -> None:
    """同形态第二例: process 资源的 bytes 无主标记同样按宽限回收。"""

    redis = _BytesReplyRedis()
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)

    await redis.incr(_inflight_key("process"))
    owner = build_permit_owner(
        worker_id="worker-2",
        function_name="process_document_job",
        job_id="job-process-88",
        project_id=88,
    )
    assert await acquire_resource_permit(
        redis, "process", limit=1, owner=owner, wait_timeout=0.0
    ) is False

    clock.advance(_orphan_grace_seconds() + 1)
    assert await acquire_resource_permit(
        redis, "process", limit=1, owner=owner, wait_timeout=0.0
    ) is True
    redis.assert_owner_count_matches_inflight("process", 1)


@pytest.mark.asyncio
async def test_over_limit_acquire_does_not_refresh_inflight_ttl() -> None:
    """满额后的失败探测不得续 inflight TTL。"""

    redis = _FakeRedis()
    owner = build_permit_owner(
        worker_id="worker-1",
        function_name="extract_requirements_job",
        job_id="job-hold",
        project_id=1,
    )
    assert await acquire_resource_permit(
        redis, "llm", limit=1, owner=owner, wait_timeout=0.0
    ) is True
    redis.expire_calls.clear()

    waiter = build_permit_owner(
        worker_id="worker-1",
        function_name="extract_requirements_job",
        job_id="job-wait",
        project_id=2,
    )
    assert await acquire_resource_permit(
        redis, "llm", limit=1, owner=waiter, wait_timeout=0.0
    ) is False
    inflight_expires = [
        key for key, _ttl in redis.expire_calls if key == _inflight_key("llm")
    ]
    assert inflight_expires == [], f"失败探测刷新了 inflight TTL: {redis.expire_calls}"


@pytest.mark.asyncio
async def test_leaked_gpu_permit_expires_within_gpu_ttl_while_llm_keeps_job_ttl(
    monkeypatch,
) -> None:
    """: 秒级 CUDA 临界区的残留必须按分钟自愈, job 级资源仍按小时 TTL。"""
    from app.core.config import settings

    redis = _FakeRedis()
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)
    monkeypatch.setattr(settings, "GPU_ADMISSION_PERMIT_TTL_SEC", 300, raising=False)
    monkeypatch.setattr(settings, "WORKER_RESOURCE_PERMIT_TTL", 3600, raising=False)

    assert await acquire_resource_permit(
        redis, "gpu", limit=1, owner="embedding:batch=5/15:bs=12", wait_timeout=0.0
    ) is True
    assert await acquire_resource_permit(
        redis, "llm", limit=3, owner="worker-1:review_project_job:job-1:1", wait_timeout=0.0
    ) is True
    assert (_inflight_key("gpu"), 300) in redis.expire_calls
    assert (_inflight_key("llm"), 3600) in redis.expire_calls

    # 持有者被杀, 无人续期; 过了 GPU TTL 但远未到 job TTL
    clock.advance(301)
    await _reconcile_stale_owners(redis, "gpu")
    await _reconcile_stale_owners(redis, "llm")

    assert redis.inflight_of("gpu") == 0
    assert redis.owners_of("gpu") == {}
    assert redis.inflight_of("llm") == 1
    assert "worker-1:review_project_job:job-1:1" in redis.owners_of("llm")

    # 新的 GPU 申请者无需等到小时级过期即可进入
    assert await acquire_resource_permit(
        redis, "gpu", limit=1, owner="vision:frame=2/9:bs=4", wait_timeout=0.0
    ) is True

