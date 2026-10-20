"""
职责: 按 outcome 对 llm_attempt_events 做二次聚合与最近失败样本抽取, 支撑失败定位
不做: 不做总体可靠性口径聚合(归 llm_reliability)、不记录 prompt/响应正文
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_attempt_event import LlmAttemptEvent as E
from app.services.ops_metrics.llm_dimension_labels import resolve_dimension_labels
from app.services.ops_metrics.llm_reliability import WINDOW_SECONDS

# 允许下钻的失败 outcome（排除 valid）
FAILURE_OUTCOMES = frozenset({
    "empty",
    "invalid_json",
    "schema_invalid",
    "http_error",
    "transport_error",
    "timeout",
    "rate_limited",
    "quota_exhausted",
    "auth_error",
    "degraded_plain_chat",
    "cancelled",
})

# 样本字段白名单：严禁 prompt / 响应正文
_SAMPLE_FIELDS = (
    "id",
    "occurred_at",
    "logical_call_id",
    "attempt_no",
    "outcome",
    "provider",
    "model_name",
    "config_id",
    "node_key",
    "flow",
    "purpose",
    "method",
    "http_status",
    "latency_ms",
    "rate_limit_kind",
)


def _window_start(window: str) -> datetime:
    seconds = WINDOW_SECONDS.get(window, WINDOW_SECONDS["24h"])
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


async def aggregate_failures_by_dimensions(
    db: AsyncSession,
    *,
    window: str = "24h",
    outcome: str,
    traffic_class: str = "production",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """按 outcome 二次聚合：默认按 model × node 定位热点。"""
    if outcome not in FAILURE_OUTCOMES:
        return []

    start = _window_start(window)
    filters = [
        E.occurred_at >= start,
        E.traffic_class == traffic_class,
        E.outcome == outcome,
    ]

    rows = (
        await db.execute(
            select(
                E.model_name,
                E.node_key,
                E.config_id,
                E.provider,
                func.count().label("attempts"),
                func.percentile_cont(0.95).within_group(E.latency_ms),
            )
            .where(*filters)
            .group_by(E.model_name, E.node_key, E.config_id, E.provider)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()

    model_keys = [r[0] for r in rows]
    node_keys = [r[1] for r in rows]
    model_labels = await resolve_dimension_labels(
        db, group_by="model", raw_keys=model_keys,
    )
    node_labels = await resolve_dimension_labels(
        db, group_by="node", raw_keys=node_keys,
    )

    config_keys = [r[2] for r in rows]
    provider_keys = [r[3] for r in rows]
    config_labels = await resolve_dimension_labels(
        db, group_by="config", raw_keys=config_keys,
    )
    provider_labels = await resolve_dimension_labels(
        db, group_by="provider", raw_keys=provider_keys,
    )

    result: list[dict[str, Any]] = []
    for row in rows:
        model_raw, node_raw, config_id, provider, attempts, p95 = row
        model_label = model_labels.get(model_raw) or {}
        node_label = node_labels.get(node_raw) or {}
        config_label = config_labels.get(config_id) or {}
        provider_label = provider_labels.get(provider) or {}
        result.append({
            "model": model_raw,
            "model_display": model_label.get("display_name") or model_raw or "未采集",
            "node_key": node_raw,
            "node_display": node_label.get("display_name") or node_raw or "未采集",
            "config_id": config_id,
            "config_display": config_label.get("display_name") or config_id or "未采集",
            "provider": provider,
            "provider_display": provider_label.get("display_name") or provider or "未采集",
            "attempts": int(attempts),
            "latency_p95_ms": float(p95) if p95 is not None else 0.0,
        })
    return result


async def list_recent_failed_attempts(
    db: AsyncSession,
    *,
    window: str = "24h",
    outcome: Optional[str] = None,
    traffic_class: str = "production",
    limit: int = 30,
) -> list[dict[str, Any]]:
    """抽取最近失败样本摘要，不包含任何 prompt/响应正文。"""
    start = _window_start(window)
    filters = [
        E.occurred_at >= start,
        E.traffic_class == traffic_class,
        E.outcome != "valid",
    ]
    if outcome:
        if outcome not in FAILURE_OUTCOMES:
            return []
        filters.append(E.outcome == outcome)

    rows = (
        await db.execute(
            select(
                E.id,
                E.occurred_at,
                E.logical_call_id,
                E.attempt_no,
                E.outcome,
                E.provider,
                E.model_name,
                E.config_id,
                E.node_key,
                E.flow,
                E.purpose,
                E.method,
                E.http_status,
                E.latency_ms,
                E.rate_limit_kind,
            )
            .where(*filters)
            .order_by(E.occurred_at.desc())
            .limit(min(max(limit, 1), 100))
        )
    ).all()

    samples: list[dict[str, Any]] = []
    for row in rows:
        sample = {
            "id": int(row[0]),
            "occurred_at": row[1].isoformat() if row[1] else None,
            "logical_call_id": row[2],
            "attempt_no": int(row[3]),
            "outcome": row[4],
            "provider": row[5],
            "model_name": row[6],
            "config_id": row[7],
            "node_key": row[8],
            "flow": row[9],
            "purpose": row[10],
            "method": row[11],
            "http_status": row[12],
            "latency_ms": int(row[13] or 0),
            "rate_limit_kind": row[14],
        }
        # 防御：确保没有越权字段
        samples.append({k: sample[k] for k in _SAMPLE_FIELDS if k in sample})
    return await _enrich_failure_samples(db, samples)


async def _enrich_failure_samples(
    db: AsyncSession,
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """为失败样本附加中文展示字段；技术键保留供 title，不作主文案。"""
    if not samples:
        return samples

    provider_labels = await resolve_dimension_labels(
        db, group_by="provider", raw_keys=[s.get("provider") for s in samples],
    )
    config_labels = await resolve_dimension_labels(
        db, group_by="config", raw_keys=[s.get("config_id") for s in samples],
    )
    node_labels = await resolve_dimension_labels(
        db, group_by="node", raw_keys=[s.get("node_key") for s in samples],
    )
    flow_labels = await resolve_dimension_labels(
        db, group_by="flow", raw_keys=[s.get("flow") for s in samples],
    )
    purpose_labels = await resolve_dimension_labels(
        db, group_by="purpose", raw_keys=[s.get("purpose") for s in samples],
    )

    enriched: list[dict[str, Any]] = []
    for sample in samples:
        row = dict(sample)
        row["provider_display"] = (
            (provider_labels.get(sample.get("provider")) or {}).get("display_name")
            or sample.get("provider")
            or "未采集"
        )
        row["config_display"] = (
            (config_labels.get(sample.get("config_id")) or {}).get("display_name")
            or sample.get("config_id")
            or "未采集"
        )
        row["node_display"] = (
            (node_labels.get(sample.get("node_key")) or {}).get("display_name")
            or sample.get("node_key")
            or "未采集"
        )
        row["flow_display"] = (
            (flow_labels.get(sample.get("flow")) or {}).get("display_name")
            or sample.get("flow")
            or "未采集"
        )
        row["purpose_display"] = (
            (purpose_labels.get(sample.get("purpose")) or {}).get("display_name")
            or sample.get("purpose")
            or "未采集"
        )
        enriched.append(row)
    return enriched


async def get_llm_failure_drilldown(
    db: AsyncSession,
    *,
    window: str = "24h",
    outcome: str,
    traffic_class: str = "production",
    limit: int = 20,
    sample_limit: int = 30,
) -> dict[str, Any]:
    """组装失败下钻响应。"""
    aggregates = await aggregate_failures_by_dimensions(
        db,
        window=window,
        outcome=outcome,
        traffic_class=traffic_class,
        limit=limit,
    )
    samples = await list_recent_failed_attempts(
        db,
        window=window,
        outcome=outcome,
        traffic_class=traffic_class,
        limit=sample_limit,
    )
    return {
        "window": window,
        "traffic_class": traffic_class,
        "outcome": outcome,
        "aggregates": aggregates,
        "samples": samples,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
