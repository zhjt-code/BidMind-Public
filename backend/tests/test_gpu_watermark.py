"""GPU AIMD watermark guard tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def restore_gpu_watermark_settings():
    from app.core.config import settings

    old_enabled = settings.VECTOR_GPU_AIMD_WATERMARK_ENABLED
    old_ratio = settings.VECTOR_GPU_AIMD_MAX_USED_RATIO
    old_missing = settings.VECTOR_GPU_AIMD_WATERMARK_ON_SNAPSHOT_MISSING
    old_base = settings.GPU_ADMISSION_ESTIMATE_BASE_MB
    old_per_1k = settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB
    settings.VECTOR_GPU_AIMD_WATERMARK_ENABLED = True
    settings.VECTOR_GPU_AIMD_MAX_USED_RATIO = 0.90
    settings.VECTOR_GPU_AIMD_WATERMARK_ON_SNAPSHOT_MISSING = "allow"
    settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 256
    settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 128
    yield
    settings.VECTOR_GPU_AIMD_WATERMARK_ENABLED = old_enabled
    settings.VECTOR_GPU_AIMD_MAX_USED_RATIO = old_ratio
    settings.VECTOR_GPU_AIMD_WATERMARK_ON_SNAPSHOT_MISSING = old_missing
    settings.GPU_ADMISSION_ESTIMATE_BASE_MB = old_base
    settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = old_per_1k


def test_watermark_blocks_increase_when_projected_usage_crosses_limit(
    restore_gpu_watermark_settings,
    monkeypatch,
):
    """升档预测超过水位线时只应阻止升档，不代表 GPU 故障。"""
    from app.core.gpu.admission import GpuSnapshot
    from app.core.vectorizer import gpu_watermark

    monkeypatch.setattr(
        gpu_watermark,
        "get_gpu_snapshot",
        lambda: GpuSnapshot(
            total_mb=10000,
            used_mb=8800,
            free_mb=1200,
            utilization=70,
            source="test",
        ),
    )

    decision = gpu_watermark.should_allow_aimd_increase(
        texts=["a" * 1000] * 14,
        candidate_batch_size=14,
    )

    assert decision.allow_increase is False
    assert decision.reason == "watermark_exceeded"
    assert decision.projected_used_ratio is not None
    assert decision.projected_used_ratio > 0.90
    assert decision.snapshot_used_ratio == pytest.approx(0.88)


def test_watermark_allows_increase_when_projected_usage_stays_under_limit(
    restore_gpu_watermark_settings,
    monkeypatch,
):
    """升档预测低于水位线时不应牺牲吞吐。"""
    from app.core.gpu.admission import GpuSnapshot
    from app.core.vectorizer import gpu_watermark

    monkeypatch.setattr(
        gpu_watermark,
        "get_gpu_snapshot",
        lambda: GpuSnapshot(
            total_mb=10000,
            used_mb=5000,
            free_mb=5000,
            utilization=40,
            source="test",
        ),
    )

    decision = gpu_watermark.should_allow_aimd_increase(
        texts=["a" * 1000] * 14,
        candidate_batch_size=14,
    )

    assert decision.allow_increase is True
    assert decision.reason == "under_watermark"
    assert decision.projected_used_ratio is not None
    assert decision.projected_used_ratio <= 0.90


def test_watermark_allows_by_default_when_snapshot_missing(
    restore_gpu_watermark_settings,
    monkeypatch,
):
    """监控快照缺失默认放行，避免因 NVML 不可用导致吞吐骤降。"""
    from app.core.vectorizer import gpu_watermark

    monkeypatch.setattr(gpu_watermark, "get_gpu_snapshot", lambda: None)

    decision = gpu_watermark.should_allow_aimd_increase(
        texts=["a" * 1000] * 14,
        candidate_batch_size=14,
    )

    assert decision.allow_increase is True
    assert decision.reason == "snapshot_missing_allow"
    assert decision.projected_used_ratio is None


def test_watermark_blocks_when_snapshot_missing_policy_is_block(
    restore_gpu_watermark_settings,
    monkeypatch,
):
    """显式要求 snapshot 缺失阻止升档时, helper 应返回暂停语义。"""
    from app.core.config import settings
    from app.core.vectorizer import gpu_watermark

    settings.VECTOR_GPU_AIMD_WATERMARK_ON_SNAPSHOT_MISSING = "block"
    monkeypatch.setattr(gpu_watermark, "get_gpu_snapshot", lambda: None)

    decision = gpu_watermark.should_allow_aimd_increase(
        texts=["a" * 1000] * 14,
        candidate_batch_size=14,
    )

    assert decision.allow_increase is False
    assert decision.reason == "snapshot_missing_block"


def test_watermark_disabled_always_allows_increase(
    restore_gpu_watermark_settings,
    monkeypatch,
):
    """关闭水位门控时, 不读取 GPU snapshot 也不阻止升档。"""
    from app.core.config import settings
    from app.core.vectorizer import gpu_watermark

    settings.VECTOR_GPU_AIMD_WATERMARK_ENABLED = False
    monkeypatch.setattr(
        gpu_watermark,
        "get_gpu_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("snapshot must not be read")),
    )

    decision = gpu_watermark.should_allow_aimd_increase(
        texts=["a" * 1000] * 14,
        candidate_batch_size=14,
    )

    assert decision.allow_increase is True
    assert decision.reason == "disabled"


def test_watermark_invalid_snapshot_allows_increase(
    restore_gpu_watermark_settings,
    monkeypatch,
):
    """无效 total_mb 不应误阻塞升档, 交由后续 admission 兜底。"""
    from app.core.gpu.admission import GpuSnapshot
    from app.core.vectorizer import gpu_watermark

    monkeypatch.setattr(
        gpu_watermark,
        "get_gpu_snapshot",
        lambda: GpuSnapshot(
            total_mb=0,
            used_mb=0,
            free_mb=0,
            utilization=0,
            source="test",
        ),
    )

    decision = gpu_watermark.should_allow_aimd_increase(
        texts=["a" * 1000] * 14,
        candidate_batch_size=14,
    )

    assert decision.allow_increase is True
    assert decision.reason == "invalid_snapshot_allow"


def test_watermark_ratio_config_is_clamped(
    restore_gpu_watermark_settings,
    monkeypatch,
):
    """误填阈值时必须 clamp，防止配置漂移导致过度保守或无限放大。"""
    from app.core.config import settings
    from app.core.gpu.admission import GpuSnapshot
    from app.core.vectorizer import gpu_watermark

    settings.VECTOR_GPU_AIMD_MAX_USED_RATIO = 1.5
    monkeypatch.setattr(
        gpu_watermark,
        "get_gpu_snapshot",
        lambda: GpuSnapshot(
            total_mb=10000,
            used_mb=9600,
            free_mb=400,
            utilization=90,
            source="test",
        ),
    )

    decision = gpu_watermark.should_allow_aimd_increase(
        texts=["a" * 100] * 4,
        candidate_batch_size=4,
    )

    assert decision.max_used_ratio == pytest.approx(0.98)
    assert decision.allow_increase is False


def test_watermark_uses_candidate_size_when_current_batch_is_short(
    restore_gpu_watermark_settings,
    monkeypatch,
):
    """尾批文本少于候选 bs 时, 水位预测仍应按候选规模保守外推。"""
    from app.core.config import settings
    from app.core.gpu.admission import GpuSnapshot
    from app.core.vectorizer import gpu_watermark

    settings.GPU_ADMISSION_ESTIMATE_BASE_MB = 0
    settings.GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB = 100
    monkeypatch.setattr(
        gpu_watermark,
        "get_gpu_snapshot",
        lambda: GpuSnapshot(
            total_mb=10000,
            used_mb=8400,
            free_mb=1600,
            utilization=70,
            source="test",
        ),
    )

    decision = gpu_watermark.should_allow_aimd_increase(
        texts=["a" * 1000] * 2,
        candidate_batch_size=14,
    )

    assert decision.allow_increase is False
    assert decision.reason == "watermark_exceeded"
    assert decision.estimated_required_mb is not None
    assert decision.estimated_required_mb >= 700
