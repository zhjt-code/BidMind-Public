"""Worker lifecycle health summary tests."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.worker_health_service import build_worker_health_summary


@dataclass(frozen=True)
class _PermitSnapshot:
    resource_type: str
    inflight: int
    limit: int


def _worker(
    *,
    functions: list[str],
    state: str = "idle",
    event_loop_lag_ms: int = 10,
    gpu_free_mb: int = 4096,
) -> dict:
    return {
        "worker_id": "worker-1",
        "host": "host-a",
        "pid": 123,
        "functions": functions,
        "state": state,
        "event_loop_lag_ms": event_loop_lag_ms,
        "gpu_free_mb": gpu_free_mb,
        "gpu_inflight": 0,
        "cuda_model_loaded": True,
        "cpu_fallback_model_loaded": False,
    }


def test_worker_health_offline_when_no_fresh_workers() -> None:
    """无新鲜 worker 心跳时应明确返回 offline, 供守护层拉起."""

    summary = build_worker_health_summary(
        workers=[],
        expected_functions=["process_document_job"],
        resource_snapshots=[],
    )

    assert summary["health"] == "offline"
    assert summary["worker_count"] == 0
    assert "start_or_restart_worker_processes" in summary["recommended_actions"]
    assert summary["action_items"][0]["code"] == "start_or_restart_worker_processes"
    assert summary["action_items"][0]["requires_supervisor"] is True


def test_worker_health_healthy_when_expected_functions_covered() -> None:
    """关键 function 均被覆盖且无资源异常时应为 healthy."""

    summary = build_worker_health_summary(
        workers=[_worker(functions=["process_document_job", "review_project_job"])],
        expected_functions=["process_document_job", "review_project_job"],
        resource_snapshots=[],
    )

    assert summary["health"] == "healthy"
    assert summary["missing_functions"] == []
    assert summary["recommended_actions"] == ["none"]
    assert summary["action_items"][0]["title"] == "无需处置"


def test_worker_health_unhealthy_when_critical_function_missing() -> None:
    """关键 function 缺失是生产不可服务风险, 应判定为 unhealthy."""

    summary = build_worker_health_summary(
        workers=[_worker(functions=["process_document_job"])],
        expected_functions=["process_document_job", "review_project_job"],
        resource_snapshots=[],
    )

    assert summary["health"] == "unhealthy"
    assert summary["missing_functions"] == ["review_project_job"]
    assert "critical_function_missing" in summary["reasons"]["unhealthy"]
    assert "start_missing_worker_group" in summary["recommended_actions"]
    assert any(item["code"] == "start_missing_worker_group" for item in summary["action_items"])


def test_worker_health_unhealthy_when_event_loop_lag_high(monkeypatch) -> None:
    """event loop lag 超阈值说明 worker 可能卡死, 应判定为 unhealthy."""

    monkeypatch.setattr(
        "app.services.worker_health_service.settings.WORKER_EVENT_LOOP_LAG_WARN_MS",
        5000,
    )

    summary = build_worker_health_summary(
        workers=[_worker(functions=["process_document_job"], event_loop_lag_ms=6000)],
        expected_functions=["process_document_job"],
        resource_snapshots=[],
    )

    assert summary["health"] == "unhealthy"
    assert "event_loop_lag_high" in summary["reasons"]["unhealthy"]
    assert "drain_then_restart_unresponsive_worker" in summary["recommended_actions"]
    assert any(item["requires_supervisor"] is True for item in summary["action_items"])


def test_worker_health_degraded_when_gpu_memory_low(monkeypatch) -> None:
    """GPU 低显存是降级信号, 不应被误判为需要重启的 unhealthy."""

    monkeypatch.setattr(
        "app.services.worker_health_service.settings.GPU_ADMISSION_PROBE_MIN_FREE_MB",
        2048,
    )

    summary = build_worker_health_summary(
        workers=[_worker(functions=["process_document_job"], gpu_free_mb=1024)],
        expected_functions=["process_document_job"],
        resource_snapshots=[],
    )

    assert summary["health"] == "degraded"
    assert summary["reasons"]["unhealthy"] == []
    assert "gpu_free_memory_low" in summary["reasons"]["degraded"]
    assert "observe_gpu_pressure_or_reduce_embedding_concurrency" in summary["recommended_actions"]
    assert summary["action_items"][0]["severity"] == "warn"
    worker = summary["workers"][0]
    assert worker["gpu_required_free_mb"] == 2048
    assert worker["gpu_admission_reason"] == "gpu_free_memory_low"
    assert worker["recommended_action"] == "observe_gpu_pressure_or_reduce_embedding_concurrency"


def test_worker_health_degraded_when_fallback_permit_saturated() -> None:
    """fallback 许可满载说明 API 兜底压力过高, 应作为 degraded 信号."""

    summary = build_worker_health_summary(
        workers=[_worker(functions=["process_document_job"])],
        expected_functions=["process_document_job"],
        resource_snapshots=[_PermitSnapshot("fallback", inflight=1, limit=1)],
    )

    assert summary["health"] == "degraded"
    assert "fallback_permit_saturated" in summary["reasons"]["degraded"]
    assert "scale_workers_before_increasing_api_fallback" in summary["recommended_actions"]
