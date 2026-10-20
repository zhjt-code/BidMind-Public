"""ops_metrics.frontend_metrics: /metrics/frontend endpoint 业务装配 (R2-8 拆分产物).

由 admin_ops.py ingest_frontend_metrics 内 registry 自登记 + record_sample 调用迁出。
"""
from __future__ import annotations

from typing import Any


async def ingest_frontend_metric_events(
    events: list[Any],
) -> dict[str, int]:
    """处理前端埋点上报事件 (批量).

    典型指标: web_first_paint_seconds / web_api_failure_rate / web_poll_count_avg.
    只接受已在 MetricsRegistry 登记的指标名 (front-end 层); 未登记的静默丢弃 (web_ 前缀
    会触发首次自动登记)。返回 {accepted, dropped} 统计。
    """
    from app.core.observability.instrument import _build_prom_metric  # type: ignore[attr-defined]
    from app.core.observability.registry import MetricSource, MetricSpec, get_metrics_registry
    from app.core.observability.storage import record_sample

    reg = get_metrics_registry()
    accepted = 0
    dropped = 0
    for ev in events:
        spec = reg.get(ev.name)
        if spec is None:
            if not ev.name.startswith("web_"):
                dropped += 1
                continue
            spec = MetricSpec(
                name=ev.name,
                layer="frontend",
                pillar="quality",
                business="platform",
                direction="neutral",
                kind="counter",
                labels=list((ev.labels or {}).keys()),
                description="auto-registered from frontend probe",
                source=MetricSource(module="frontend", qualname=ev.name, file="", line=0),
            )
            reg.register(spec, _build_prom_metric(
                name=ev.name, kind=spec.kind, description=spec.description, prom_labels=spec.labels,
            ))
        await record_sample(name=ev.name, value=ev.value, labels=ev.labels)
        accepted += 1

    return {"accepted": accepted, "dropped": dropped}
