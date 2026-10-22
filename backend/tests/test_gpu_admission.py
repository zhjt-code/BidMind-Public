"""GPU Admission Control contract tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.test_worker_resource_scheduling import _FakeRedis  # noqa: E402


@pytest.fixture
def restore_gpu_admission_settings():
    from app.core.gpu import admission

    old_enabled = admission.settings.GPU_ADMISSION_ENABLED
    old_min_free = admission.settings.GPU_ADMISSION_MIN_FREE_MB
    old_base = admission.settings.GPU_ADMISSION_ESTIMATE_BASE_MB
    old_per_1k = admission.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB
    old_threshold = admission.settings.GPU_ADMISSION_REPEATED_FAIL_THRESHOLD
    old_wait = admission.settings.GPU_ADMISSION_WAIT_TIMEOUT_SEC
    admission._heartbeats.clear()
    admission.settings.GPU_ADMISSION_ENABLED = True
    admission.settings.GPU_ADMISSION_MIN_FREE_MB = 1024
    admission.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 512
    admission.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 128
    admission.settings.GPU_ADMISSION_REPEATED_FAIL_THRESHOLD = 3
    yield
    admission.settings.GPU_ADMISSION_ENABLED = old_enabled
    admission.settings.GPU_ADMISSION_MIN_FREE_MB = old_min_free
    admission.settings.GPU_ADMISSION_ESTIMATE_BASE_MB = old_base
    admission.settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = old_per_1k
    admission.settings.GPU_ADMISSION_REPEATED_FAIL_THRESHOLD = old_threshold
    admission.settings.GPU_ADMISSION_WAIT_TIMEOUT_SEC = old_wait


def test_estimate_embedding_required_mb_scales_with_text_size(
    restore_gpu_admission_settings,
):
    from app.core.gpu.admission import estimate_embedding_required_mb

    small = estimate_embedding_required_mb(["a" * 500], batch_size=4)
    large = estimate_embedding_required_mb(["a" * 3000] * 4, batch_size=16)

    assert small >= 512
    assert large > small


def test_estimate_embedding_required_mb_uses_largest_micro_batch(
    restore_gpu_admission_settings,
):
    from app.core.gpu.admission import estimate_embedding_required_mb

    texts = ["a" * 1000] * 12

    batch_12 = estimate_embedding_required_mb(texts, batch_size=12)
    batch_4 = estimate_embedding_required_mb(texts, batch_size=4)

    assert batch_12 == 1280
    assert batch_4 == 768
    assert batch_4 < batch_12


@pytest.mark.asyncio
async def test_gpu_admission_falls_back_when_snapshot_missing(
    restore_gpu_admission_settings,
    monkeypatch,
):
    from app.core.gpu import admission

    monkeypatch.setattr(admission, "get_gpu_snapshot", lambda: None)

    async with admission.gpu_admission_context(
        kind="embedding",
        estimated_required_mb=512,
        owner="test",
    ) as decision:
        assert decision.action == "cpu_fallback"
        assert decision.reason == "snapshot_unavailable"


@pytest.mark.asyncio
async def test_gpu_admission_falls_back_on_low_vram(
    restore_gpu_admission_settings,
    monkeypatch,
):
    from app.core.gpu import admission

    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=8192,
            used_mb=7600,
            free_mb=592,
            utilization=90,
            source="test",
        ),
    )

    async with admission.gpu_admission_context(
        kind="embedding",
        estimated_required_mb=1024,
        owner="test",
    ) as decision:
        assert decision.action == "cpu_fallback"
        assert decision.reason == "insufficient_vram"
        assert decision.snapshot is not None
        assert decision.snapshot.free_mb == 592


@pytest.mark.asyncio
async def test_gpu_admission_falls_back_when_inflight_full(
    restore_gpu_admission_settings,
    monkeypatch,
):
    from app.core.gpu import admission

    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=24576,
            used_mb=2048,
            free_mb=22528,
            utilization=5,
            source="test",
        ),
    )

    async def fake_acquire(kind: str, owner: str, **_kwargs):
        return None, "", False

    monkeypatch.setattr(admission, "_acquire_gpu_permit", fake_acquire)

    async with admission.gpu_admission_context(
        kind="embedding",
        estimated_required_mb=1024,
        owner="test",
    ) as decision:
        assert decision.action == "cpu_fallback"
        assert decision.reason == "inflight_full"


@pytest.mark.asyncio
async def test_gpu_admission_releases_permit_after_gpu_use(
    restore_gpu_admission_settings,
    monkeypatch,
):
    from app.core.gpu import admission

    released: list[str] = []
    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: admission.GpuSnapshot(
            total_mb=24576,
            used_mb=2048,
            free_mb=22528,
            utilization=5,
            source="test",
        ),
    )

    async def fake_acquire(kind: str, owner: str, **_kwargs):
        return object(), f"{kind}:{owner}", True

    async def fake_release(redis, owner: str):  # noqa: ANN001
        released.append(owner)

    monkeypatch.setattr(admission, "_acquire_gpu_permit", fake_acquire)
    monkeypatch.setattr(admission, "_release_gpu_permit", fake_release)

    async with admission.gpu_admission_context(
        kind="embedding",
        estimated_required_mb=1024,
        owner="test",
    ) as decision:
        assert decision.action == "use_gpu"
        assert decision.reason == "admitted"

    assert released == ["embedding:test"]


def test_gpu_admission_wait_timeout_covers_cross_process_encode_slot(
    restore_gpu_admission_settings,
):
    """跨进程 CUDA 临界区是秒级单批 encode, 等待窗不得再回到亚秒级立即 CPU 回退。"""
    from app.core.config import settings
    from app.core.gpu import admission

    assert settings.GPU_ADMISSION_WAIT_TIMEOUT_SEC >= 10.0
    assert admission.settings.GPU_ADMISSION_WAIT_TIMEOUT_SEC >= 10.0


class _CountingRedis(_FakeRedis):
    """记录 incr 次数, 用于证明等待窗内真的轮询复试过, 而非一次失败即回退。"""

    def __init__(self) -> None:
        super().__init__()
        self.incr_calls = 0

    async def incr(self, key: str) -> int:
        self.incr_calls += 1
        return await super().incr(key)


def _patch_permit_backend(monkeypatch, redis: _CountingRedis) -> None:
    """让 _acquire_gpu_permit 走真实 permit 实现, 只把 Redis 换成替身。"""

    async def _pool():
        return redis

    monkeypatch.setattr("app.core.redis.get_redis_pool", _pool)
    monkeypatch.setattr(
        "app.core.config.settings.WORKER_RESOURCE_PERMIT_POLL_INTERVAL",
        0.05,
        raising=False,
    )


def _snapshot(admission, *, free_mb: int):
    return admission.GpuSnapshot(
        total_mb=24576,
        used_mb=24576 - free_mb,
        free_mb=free_mb,
        utilization=50,
        source="test",
    )


async def _hold_gpu_permit(redis: _CountingRedis, owner: str) -> None:
    from app.core.tasks.resource_permit import acquire_resource_permit

    held = await acquire_resource_permit(
        redis, "gpu", limit=1, wait_timeout=0.0, owner=owner
    )
    assert held is True, "前置条件: holder 必须先占住唯一 GPU permit"


def _release_gpu_permit_after(redis: _CountingRedis, owner: str, delay: float):
    async def _release() -> None:
        from app.core.tasks.resource_permit import (
            release_resource_owner,
            release_resource_permit,
        )

        await asyncio.sleep(delay)
        await release_resource_owner(redis, "gpu", owner)
        await release_resource_permit(redis, "gpu")

    return asyncio.create_task(_release())


@pytest.mark.asyncio
async def test_gpu_admission_waits_for_busy_permit_then_uses_gpu(
    restore_gpu_admission_settings,
    monkeypatch,
):
    """本案例: permit 被别的进程持有时应排队让路, 释放后拿到 GPU, 而不是 0.5s 后打到 CPU。"""
    from app.core.gpu import admission

    redis = _CountingRedis()
    _patch_permit_backend(monkeypatch, redis)
    monkeypatch.setattr(admission, "get_gpu_snapshot", lambda: _snapshot(admission, free_mb=22528))
    admission.settings.GPU_ADMISSION_WAIT_TIMEOUT_SEC = 2.0

    await _hold_gpu_permit(redis, "embedding:holder")
    # 0.6s > 旧默认 0.5s: 若等待窗被改回亚秒级或写死 0.5, 本例必红
    releaser = _release_gpu_permit_after(redis, "embedding:holder", 0.6)
    baseline = redis.incr_calls

    async with admission.gpu_admission_context(
        kind="embedding",
        estimated_required_mb=1024,
        owner="batch=2/8:bs=16",
    ) as decision:
        assert decision.action == "use_gpu"
        assert decision.reason == "admitted"
        assert decision.acquired_permit is True

    await releaser
    assert redis.incr_calls - baseline >= 2, "应在等待窗内轮询复试"


@pytest.mark.asyncio
async def test_gpu_admission_waits_for_busy_permit_on_resident_low_vram_branch(
    restore_gpu_admission_settings,
    monkeypatch,
):
    """同形态第二实体: 常驻模型低显存分支同样经 permit, 释放后应 use_gpu/resident_model_loaded。"""
    from app.core.gpu import admission

    redis = _CountingRedis()
    _patch_permit_backend(monkeypatch, redis)
    # free 592 < estimated 1024 + min_free 1024, 强制走 resident_model_loaded 分支
    monkeypatch.setattr(admission, "get_gpu_snapshot", lambda: _snapshot(admission, free_mb=592))
    admission.settings.GPU_ADMISSION_WAIT_TIMEOUT_SEC = 2.0

    await _hold_gpu_permit(redis, "embedding:holder")
    releaser = _release_gpu_permit_after(redis, "embedding:holder", 0.6)
    baseline = redis.incr_calls

    async with admission.gpu_admission_context(
        kind="embedding",
        estimated_required_mb=1024,
        owner="batch=3/8:bs=8",
        resident_model_loaded=True,
    ) as decision:
        assert decision.action == "use_gpu"
        assert decision.reason == "resident_model_loaded"
        assert decision.acquired_permit is True

    await releaser
    assert redis.incr_calls - baseline >= 2, "低显存常驻分支也必须等 permit, 不能立即回退"


@pytest.mark.asyncio
async def test_gpu_admission_gives_up_to_cpu_after_wait_window(
    restore_gpu_admission_settings,
    monkeypatch,
):
    """边界: permit 一直不释放时等待窗必须收口到 cpu_fallback/inflight_full, 不得无限等。"""
    from app.core.gpu import admission

    redis = _CountingRedis()
    _patch_permit_backend(monkeypatch, redis)
    monkeypatch.setattr(admission, "get_gpu_snapshot", lambda: _snapshot(admission, free_mb=22528))
    admission.settings.GPU_ADMISSION_WAIT_TIMEOUT_SEC = 0.2

    await _hold_gpu_permit(redis, "embedding:holder")
    baseline = redis.incr_calls

    async with admission.gpu_admission_context(
        kind="embedding",
        estimated_required_mb=1024,
        owner="batch=4/8:bs=16",
    ) as decision:
        assert decision.action == "cpu_fallback"
        assert decision.reason == "inflight_full"
        assert decision.acquired_permit is False

    assert redis.incr_calls - baseline >= 2, "应轮询复试后才回退, 而非一次失败即回退"
    assert redis.inflight_of("gpu") == 1, "holder 的占用不得被等待者误清"
    assert "embedding:holder" in redis.owners_of("gpu")


@pytest.mark.asyncio
async def test_gpu_permit_holder_heartbeats_with_gpu_ttl_and_stops_on_release(
    restore_gpu_admission_settings,
    monkeypatch,
):
    """持有 GPU 许可期间必须有心跳并按 GPU 短 TTL 续期; 释放后心跳停止, owner 不被写回。"""
    from app.core.gpu import admission
    from app.core.tasks import resource_permit

    redis = _CountingRedis()
    _patch_permit_backend(monkeypatch, redis)
    monkeypatch.setattr(admission, "get_gpu_snapshot", lambda: _snapshot(admission, free_mb=22528))
    monkeypatch.setattr(admission.settings, "GPU_ADMISSION_PERMIT_TTL_SEC", 300, raising=False)
    gpu_inflight_key = resource_permit._resource_key("gpu")

    async with admission.gpu_admission_context(
        kind="embedding",
        estimated_required_mb=1024,
        owner="batch=1/3:bs=16",
    ) as decision:
        assert decision.acquired_permit is True
        heartbeat = admission._heartbeats.get("embedding:batch=1/3:bs=16")
        assert heartbeat is not None and not heartbeat.done()
        gpu_ttls = {ttl for key, ttl in redis.expire_calls if key == gpu_inflight_key}
        assert gpu_ttls == {300}, f"GPU 许可必须用独立短 TTL, 实际: {gpu_ttls}"

    assert admission._heartbeats == {}
    assert heartbeat.done()
    assert redis.inflight_of("gpu") == 0
    assert redis.owners_of("gpu") == {}


def _idle_snapshot(admission, *, free_mb: int, utilization: int):
    return admission.GpuSnapshot(
        total_mb=24576,
        used_mb=24576 - free_mb,
        free_mb=free_mb,
        utilization=utilization,
        source="test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "owner", "free_mb", "estimated_mb", "utilization"),
    [
        ("embedding", "batch=5/15:bs=12", 14192, 384, 0),
        ("vision", "frame=2/9:bs=4", 18432, 512, 8),
    ],
)
async def test_gpu_admission_reclaims_idle_permit_and_uses_gpu(
    restore_gpu_admission_settings,
    monkeypatch,
    kind,
    owner,
    free_mb,
    estimated_mb,
    utilization,
):
    """设备空闲且空闲显存够用时, 占满的许可不得继续否决 GPU。"""
    from app.core.gpu import admission

    redis = _CountingRedis()
    _patch_permit_backend(monkeypatch, redis)
    monkeypatch.setattr(
        admission,
        "get_gpu_snapshot",
        lambda: _idle_snapshot(
            admission, free_mb=free_mb, utilization=utilization,
        ),
    )
    admission.settings.GPU_ADMISSION_WAIT_TIMEOUT_SEC = 0.2
    admission.settings.GPU_ADMISSION_MIN_FREE_MB = 512

    await _hold_gpu_permit(redis, f"{kind}:stale-holder")

    async with admission.gpu_admission_context(
        kind=kind,
        estimated_required_mb=estimated_mb,
        owner=owner,
    ) as decision:
        assert decision.action == "use_gpu"
        assert decision.reason == "admitted"
        assert decision.acquired_permit is True
        assert f"{kind}:stale-holder" not in redis.owners_of("gpu")
        assert f"{kind}:{owner}" in redis.owners_of("gpu")

    assert redis.inflight_of("gpu") == 0
