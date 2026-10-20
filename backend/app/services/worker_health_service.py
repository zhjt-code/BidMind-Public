"""Worker lifecycle health summary service.

This module only reads existing heartbeat and permit state. It does not start,
stop, drain, or restart workers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from app.core.config import settings

WorkerHealthLevel = Literal["healthy", "degraded", "unhealthy", "offline"]

DEFAULT_CRITICAL_FUNCTIONS: tuple[str, ...] = (
    "process_document_job",
    "extract_requirements_job",
    "review_project_job",
    "collusion_analyze_job",
    "document_image_vl_job",
    "writing_extract_job",
    "writing_outline_job",
    "writing_material_process_job",
    "writing_match_job",
    "writing_compose_job",
    "writing_clause_vectorize_job",
    "flush_metrics_job",
)

ACTION_RUNBOOKS: dict[str, dict[str, Any]] = {
    "start_or_restart_worker_processes": {
        "title": "拉起缺失的 Worker 进程",
        "severity": "critical",
        "owner": "ops",
        "description": "新鲜窗口内没有 Worker 心跳，先拉起标准 Worker 组，再复查健康摘要。",
        "command": r".\scripts\production\start_workers.ps1 -SkipApi",
        "requires_supervisor": True,
    },
    "check_redis_connectivity": {
        "title": "检查 Redis 与心跳写入",
        "severity": "critical",
        "owner": "ops",
        "description": "Worker 心跳依赖 Redis，若进程已启动但仍 offline，优先检查 Redis 连接和心跳 key。",
        "command": "redis-cli ping",
        "requires_supervisor": False,
    },
    "start_missing_worker_group": {
        "title": "补齐缺失的 Worker 组",
        "severity": "critical",
        "owner": "ops",
        "description": "存在关键 job 没有新鲜 Worker 覆盖，按缺失 function 所属资源组拉起对应 Worker。",
        "command": r".\scripts\production\start_workers.ps1 -SkipApi",
        "requires_supervisor": True,
    },
    "drain_then_restart_unresponsive_worker": {
        "title": "先 drain 再重启卡顿 Worker",
        "severity": "critical",
        "owner": "ops",
        "description": "event loop lag 已超过阈值，先确认当前任务是否可自然结束，再由外部 supervisor 重启进程。",
        "command": "GET /api/admin/ops/workers/health",
        "requires_supervisor": True,
    },
    "observe_gpu_pressure_or_reduce_embedding_concurrency": {
        "title": "观察 GPU 压力或降低向量化并发",
        "severity": "warn",
        "owner": "ops",
        "description": "GPU 空闲显存偏低属于降级信号，默认不重启；先观察 admission/fallback，再考虑降低批量或扩容。",
        "command": "nvidia-smi",
        "requires_supervisor": False,
    },
    "wait_for_gpu_permit_or_scale_gpu_capacity": {
        "title": "等待 GPU permit 释放或扩容",
        "severity": "warn",
        "owner": "ops",
        "description": "GPU 临界区许可已满，避免强行并发；持续饱和时再扩容 GPU Worker 或降低入口并发。",
        "command": "GET /api/admin/ops/workers/health",
        "requires_supervisor": False,
    },
    "scale_workers_before_increasing_api_fallback": {
        "title": "优先扩 Worker，避免放大 API fallback",
        "severity": "warn",
        "owner": "ops",
        "description": "本地 fallback 已满载，说明 API 进程承压；优先恢复/扩容 Worker，不要先提高 API fallback 上限。",
        "command": r".\scripts\production\start_workers.ps1 -SkipApi",
        "requires_supervisor": True,
    },
    "verify_draining_worker_exits_cleanly": {
        "title": "确认 draining Worker 正常退出",
        "severity": "warn",
        "owner": "ops",
        "description": "Worker 正在关闭或清理。若长期保持 draining，检查任务卡死并由 supervisor 重启。",
        "command": "GET /api/admin/ops/workers/health",
        "requires_supervisor": True,
    },
    "none": {
        "title": "无需处置",
        "severity": "info",
        "owner": "ops",
        "description": "Worker 健康摘要未发现需要人工介入的信号。",
        "command": "",
        "requires_supervisor": False,
    },
}


def _to_int(value: Any) -> int | None:
    """Best-effort integer coercion for Redis heartbeat fields."""

    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool | None:
    """Best-effort boolean coercion for Redis heartbeat fields."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _normalize_functions(value: Any) -> list[str]:
    """Normalize worker function declarations from heartbeat payloads."""

    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, tuple | set):
        return [str(item) for item in value if str(item)]
    return []


def _normalize_worker(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one raw Redis worker heartbeat into API-safe fields."""

    return {
        "worker_id": str(raw.get("worker_id") or ""),
        "host": raw.get("host"),
        "pid": _to_int(raw.get("pid")),
        "functions": _normalize_functions(raw.get("functions")),
        "state": str(raw.get("state") or "idle"),
        "current_function": raw.get("current_function"),
        "current_resource": raw.get("current_resource"),
        "event_loop_lag_ms": _to_int(raw.get("event_loop_lag_ms")),
        "gpu_admission_enabled": _to_bool(raw.get("gpu_admission_enabled")),
        "gpu_inflight": _to_int(raw.get("gpu_inflight")),
        "gpu_free_mb": _to_int(raw.get("gpu_free_mb")),
        "gpu_snapshot_source": raw.get("gpu_snapshot_source"),
        "cuda_model_loaded": _to_bool(raw.get("cuda_model_loaded")),
        "cpu_fallback_model_loaded": _to_bool(raw.get("cpu_fallback_model_loaded")),
    }


def _resource_snapshot_dict(snapshot: Any) -> dict[str, Any]:
    """Convert PermitSnapshot-like objects into plain dictionaries."""

    resource_type = getattr(snapshot, "resource_type", None)
    inflight = getattr(snapshot, "inflight", None)
    limit = getattr(snapshot, "limit", None)
    return {
        "resource_type": str(resource_type or ""),
        "inflight": int(inflight or 0),
        "limit": int(limit or 0),
    }


def _select_health_level(
    *,
    has_workers: bool,
    unhealthy_reasons: list[str],
    degraded_reasons: list[str],
) -> WorkerHealthLevel:
    """Resolve the aggregate health level with explicit precedence."""

    if not has_workers:
        return "offline"
    if unhealthy_reasons:
        return "unhealthy"
    if degraded_reasons:
        return "degraded"
    return "healthy"


def build_worker_health_summary(
    *,
    workers: Sequence[Mapping[str, Any]] | None = None,
    expected_functions: Sequence[str] | None = None,
    resource_snapshots: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Build a read-only Worker health summary from heartbeats and permits.

    Args:
        workers: Optional raw heartbeat payloads. When omitted, fresh workers
            are loaded from Redis via ``scan_alive_workers_sync``.
        expected_functions: Functions that must be covered by at least one
            fresh worker for production readiness.
        resource_snapshots: Optional permit snapshots. When omitted, snapshots
            are loaded from Redis via ``get_resource_snapshots_sync``.
    """

    if workers is None:
        from app.core.arq_runtime import scan_alive_workers_sync

        workers = scan_alive_workers_sync()
    if resource_snapshots is None:
        from app.core.tasks.resource_permit import get_resource_snapshots_sync

        resource_snapshots = get_resource_snapshots_sync()

    normalized_workers = [_normalize_worker(worker) for worker in workers]
    expected = list(expected_functions or DEFAULT_CRITICAL_FUNCTIONS)
    covered_functions = {
        function
        for worker in normalized_workers
        for function in worker.get("functions", [])
    }
    missing_functions = sorted(set(expected) - covered_functions)

    event_loop_warn_ms = int(getattr(settings, "WORKER_EVENT_LOOP_LAG_WARN_MS", 5000))
    gpu_probe_min_free_mb = int(getattr(settings, "GPU_ADMISSION_PROBE_MIN_FREE_MB", 2048))

    unhealthy_reasons: list[str] = []
    degraded_reasons: list[str] = []

    if missing_functions:
        unhealthy_reasons.append("critical_function_missing")

    for worker in normalized_workers:
        lag_ms = worker.get("event_loop_lag_ms")
        if lag_ms is not None and lag_ms > event_loop_warn_ms:
            unhealthy_reasons.append("event_loop_lag_high")
        if str(worker.get("state") or "").lower() == "draining":
            degraded_reasons.append("worker_draining")
        gpu_free_mb = worker.get("gpu_free_mb")
        worker["gpu_required_free_mb"] = gpu_probe_min_free_mb
        if gpu_free_mb is not None and gpu_free_mb < gpu_probe_min_free_mb:
            degraded_reasons.append("gpu_free_memory_low")
            worker["gpu_admission_reason"] = "gpu_free_memory_low"
            worker["recommended_action"] = "observe_gpu_pressure_or_reduce_embedding_concurrency"
        else:
            worker["gpu_admission_reason"] = None
            worker["recommended_action"] = None

    resources = [_resource_snapshot_dict(snapshot) for snapshot in resource_snapshots]
    for snapshot in resources:
        limit = snapshot["limit"]
        inflight = snapshot["inflight"]
        if limit > 0 and inflight >= limit and snapshot["resource_type"] in {"fallback", "gpu"}:
            degraded_reasons.append(f"{snapshot['resource_type']}_permit_saturated")

    unhealthy_reasons = sorted(set(unhealthy_reasons))
    degraded_reasons = sorted(set(degraded_reasons))
    health = _select_health_level(
        has_workers=bool(normalized_workers),
        unhealthy_reasons=unhealthy_reasons,
        degraded_reasons=degraded_reasons,
    )

    recommended_actions = _recommended_actions(
        health=health,
        unhealthy_reasons=unhealthy_reasons,
        degraded_reasons=degraded_reasons,
    )
    action_items = [_action_item(action) for action in recommended_actions]

    return {
        "health": health,
        "worker_count": len(normalized_workers),
        "workers": normalized_workers,
        "expected_functions": expected,
        "missing_functions": missing_functions,
        "covered_functions": sorted(covered_functions),
        "resources": resources,
        "reasons": {
            "unhealthy": unhealthy_reasons,
            "degraded": degraded_reasons,
        },
        "thresholds": {
            "event_loop_lag_warn_ms": event_loop_warn_ms,
            "gpu_probe_min_free_mb": gpu_probe_min_free_mb,
        },
        "recommended_actions": recommended_actions,
        "action_items": action_items,
    }


def _recommended_actions(
    *,
    health: WorkerHealthLevel,
    unhealthy_reasons: Sequence[str],
    degraded_reasons: Sequence[str],
) -> list[str]:
    """Return operator-facing actions for the resolved health state."""

    if health == "offline":
        return ["start_or_restart_worker_processes", "check_redis_connectivity"]
    actions: list[str] = []
    if "critical_function_missing" in unhealthy_reasons:
        actions.append("start_missing_worker_group")
    if "event_loop_lag_high" in unhealthy_reasons:
        actions.append("drain_then_restart_unresponsive_worker")
    if "gpu_free_memory_low" in degraded_reasons:
        actions.append("observe_gpu_pressure_or_reduce_embedding_concurrency")
    if "gpu_permit_saturated" in degraded_reasons:
        actions.append("wait_for_gpu_permit_or_scale_gpu_capacity")
    if "fallback_permit_saturated" in degraded_reasons:
        actions.append("scale_workers_before_increasing_api_fallback")
    if "worker_draining" in degraded_reasons:
        actions.append("verify_draining_worker_exits_cleanly")
    return actions or ["none"]


def _action_item(action: str) -> dict[str, Any]:
    """Return a structured runbook item for one recommended action."""

    runbook = ACTION_RUNBOOKS.get(action, ACTION_RUNBOOKS["none"])
    return {
        "code": action,
        "title": runbook["title"],
        "severity": runbook["severity"],
        "owner": runbook["owner"],
        "description": runbook["description"],
        "command": runbook["command"],
        "requires_supervisor": runbook["requires_supervisor"],
    }
