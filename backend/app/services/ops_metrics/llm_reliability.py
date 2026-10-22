"""LLM 可靠性聚合服务 (P0-A)

职责: 从 llm_attempt_events 事实表按窗口/维度聚合可靠性指标
不做: 不写入事实、不做展示格式化、不承担 Prometheus 内存指标

口径定义(与计划一致):
- 逻辑调用量 = distinct logical_call_id
- 物理请求量 = 行数
- 重试率 = 发生过 attempt_no > 1 的逻辑调用 / 逻辑调用量
- 首次有效率 = 首个 attempt outcome=valid 的逻辑调用 / 逻辑调用量
- 最终有效率 = 最后一个 attempt outcome=valid 的逻辑调用 / 逻辑调用量
- 空响应率 / HTTP错误率 / 超时率 = 对应 outcome 的物理请求 / 物理请求量
- 结构化失败率 = (invalid_json + schema_invalid) / 结构化方法的物理请求量
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import String, case, func, select, type_coerce
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_attempt_event import LlmAttemptEvent as E

WINDOW_SECONDS: dict[str, int] = {
    "1h": 3600,
    "24h": 86400,
    "7d": 86400 * 7,
    "30d": 86400 * 30,
}

# 各窗口默认时间桶宽(秒)
DEFAULT_BUCKET_SECONDS: dict[str, int] = {
    "1h": 300,
    "24h": 3600,
    "7d": 21600,
    "30d": 86400,
}

GROUP_BY_COLUMNS = {
    "purpose": E.purpose,
    "provider": E.provider,
    "model": E.model_name,
    "config": E.config_id,
    "node": E.node_key,
    "flow": E.flow,
}

_STRUCTURED_METHODS = ("chat_json", "chat_with_validation", "chat_cd")
_STRUCTURED_FAIL_OUTCOMES = ("invalid_json", "schema_invalid")


def _rate(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


async def get_llm_reliability(
    db: AsyncSession,
    *,
    window: str = "24h",
    group_by: Optional[str] = None,
    traffic_class: str = "production",
    bucket_seconds: Optional[int] = None,
) -> dict[str, Any]:
    """聚合窗口内的 LLM 可靠性指标"""
    seconds = WINDOW_SECONDS.get(window, WINDOW_SECONDS["24h"])
    now = datetime.now(timezone.utc)
    start = now - timedelta(seconds=seconds)
    bucket = bucket_seconds or DEFAULT_BUCKET_SECONDS.get(window, 3600)

    base_filters = [E.occurred_at >= start, E.traffic_class == traffic_class]

    totals = await _totals(db, base_filters)
    logical = await _logical_call_stats(db, base_filters)
    series = await _timeseries(db, base_filters, bucket_seconds=bucket)
    breakdown = (
        await _breakdown(db, base_filters, group_by=group_by)
        if group_by in GROUP_BY_COLUMNS else []
    )
    if breakdown and group_by in GROUP_BY_COLUMNS:
        from app.services.ops_metrics.llm_dimension_labels import enrich_breakdown_rows

        breakdown = await enrich_breakdown_rows(
            db, group_by=group_by, rows=breakdown,
        )
    label_coverage = await _label_coverage(db, base_filters)

    from app.core.ai.attempt_recorder import get_attempt_recorder

    return {
        "window": window,
        "traffic_class": traffic_class,
        "bucket_seconds": bucket,
        "generated_at": now.isoformat(),
        "totals": {**totals, **logical},
        "timeseries": series,
        "group_by": group_by if group_by in GROUP_BY_COLUMNS else None,
        "breakdown": breakdown,
        "label_coverage": label_coverage,
        "recorder": get_attempt_recorder().stats(),
    }


async def _totals(db: AsyncSession, filters: list) -> dict[str, Any]:
    """物理 attempt 级统计: outcome 分布、429分类、延迟/排队分位数"""
    outcome_rows = (await db.execute(
        select(E.outcome, func.count())
        .where(*filters)
        .group_by(E.outcome)
    )).all()
    outcome_counts = {row[0]: int(row[1]) for row in outcome_rows}
    attempts = sum(outcome_counts.values())

    rl_rows = (await db.execute(
        select(E.rate_limit_kind, func.count())
        .where(*filters, E.rate_limit_kind.isnot(None))
        .group_by(E.rate_limit_kind)
    )).all()
    rate_limit_kinds = {row[0]: int(row[1]) for row in rl_rows}

    pct = (await db.execute(
        select(
            func.percentile_cont(0.5).within_group(E.latency_ms),
            func.percentile_cont(0.95).within_group(E.latency_ms),
        ).where(*filters)
    )).one_or_none()
    queue_pct = (await db.execute(
        select(
            func.percentile_cont(0.5).within_group(E.queue_wait_ms),
            func.percentile_cont(0.95).within_group(E.queue_wait_ms),
        ).where(*filters, E.queue_wait_ms.isnot(None))
    )).one_or_none()

    structured_attempts = (await db.execute(
        select(func.count()).where(*filters, E.method.in_(_STRUCTURED_METHODS))
    )).scalar() or 0
    structured_failures = sum(
        outcome_counts.get(o, 0) for o in _STRUCTURED_FAIL_OUTCOMES
    )

    http_errors = (
        outcome_counts.get("http_error", 0)
        + outcome_counts.get("auth_error", 0)
        + outcome_counts.get("rate_limited", 0)
        + outcome_counts.get("quota_exhausted", 0)
    )

    return {
        "physical_attempts": attempts,
        "outcome_counts": outcome_counts,
        "empty_rate": _rate(outcome_counts.get("empty", 0), attempts),
        "http_error_rate": _rate(http_errors, attempts),
        "timeout_rate": _rate(outcome_counts.get("timeout", 0), attempts),
        "structured_attempts": structured_attempts,
        "structured_fail_rate": _rate(structured_failures, structured_attempts),
        "degraded_count": outcome_counts.get("degraded_plain_chat", 0),
        "rate_limit_kinds": rate_limit_kinds,
        "latency_p50_ms": float(pct[0]) if pct and pct[0] is not None else 0.0,
        "latency_p95_ms": float(pct[1]) if pct and pct[1] is not None else 0.0,
        "queue_p50_ms": (
            float(queue_pct[0]) if queue_pct and queue_pct[0] is not None else 0.0
        ),
        "queue_p95_ms": (
            float(queue_pct[1]) if queue_pct and queue_pct[1] is not None else 0.0
        ),
    }


async def _logical_call_stats(db: AsyncSession, filters: list) -> dict[str, Any]:
    """逻辑调用级统计: 调用量、重试率、首次/最终有效率"""
    first_outcome = type_coerce(
        func.array_agg(aggregate_order_by(E.outcome, E.attempt_no.asc())),
        ARRAY(String),
    )[1]
    final_outcome = type_coerce(
        func.array_agg(aggregate_order_by(E.outcome, E.attempt_no.desc())),
        ARRAY(String),
    )[1]

    sub = (
        select(
            E.logical_call_id,
            func.count().label("attempts"),
            first_outcome.label("first_outcome"),
            final_outcome.label("final_outcome"),
        )
        .where(*filters)
        .group_by(E.logical_call_id)
        .subquery()
    )

    row = (await db.execute(
        select(
            func.count(),
            func.sum(case((sub.c.attempts > 1, 1), else_=0)),
            func.sum(case((sub.c.first_outcome == "valid", 1), else_=0)),
            func.sum(case((sub.c.final_outcome == "valid", 1), else_=0)),
        )
    )).one_or_none()

    logical_calls = int(row[0] or 0) if row else 0
    retried = int(row[1] or 0) if row else 0
    first_valid = int(row[2] or 0) if row else 0
    final_valid = int(row[3] or 0) if row else 0

    return {
        "logical_calls": logical_calls,
        "retried_calls": retried,
        "retry_rate": _rate(retried, logical_calls),
        "first_valid_rate": _rate(first_valid, logical_calls),
        "final_valid_rate": _rate(final_valid, logical_calls),
    }


async def _timeseries(
    db: AsyncSession, filters: list, *, bucket_seconds: int,
) -> list[dict[str, Any]]:
    """按时间桶输出 attempts / valid / failed 序列"""
    bucket_expr = func.to_timestamp(
        func.floor(func.extract("epoch", E.occurred_at) / bucket_seconds)
        * bucket_seconds
    ).label("bucket")

    rows = (await db.execute(
        select(
            bucket_expr,
            func.count(),
            func.sum(case((E.outcome == "valid", 1), else_=0)),
        )
        .where(*filters)
        .group_by(bucket_expr)
        .order_by(bucket_expr)
    )).all()

    return [
        {
            "bucket": row[0].isoformat(),
            "attempts": int(row[1]),
            "valid": int(row[2] or 0),
            "failed": int(row[1]) - int(row[2] or 0),
        }
        for row in rows
    ]


async def _breakdown(
    db: AsyncSession, filters: list, *, group_by: str,
) -> list[dict[str, Any]]:
    """按单一维度拆分 attempts / valid_rate / p95"""
    col = GROUP_BY_COLUMNS[group_by]
    rows = (await db.execute(
        select(
            col,
            func.count(),
            func.sum(case((E.outcome == "valid", 1), else_=0)),
            func.sum(case((E.outcome == "empty", 1), else_=0)),
            func.sum(
                case((E.outcome.in_(_STRUCTURED_FAIL_OUTCOMES), 1), else_=0)
            ),
            func.percentile_cont(0.95).within_group(E.latency_ms),
        )
        .where(*filters)
        .group_by(col)
        .order_by(func.count().desc())
        .limit(50)
    )).all()

    result = []
    for row in rows:
        attempts = int(row[1])
        raw_key = row[0]
        result.append({
            "key": raw_key or "(未标注)",
            "raw_key": raw_key,
            "attempts": attempts,
            "valid_rate": _rate(int(row[2] or 0), attempts),
            "empty_count": int(row[3] or 0),
            "structured_fail_count": int(row[4] or 0),
            "latency_p95_ms": float(row[5]) if row[5] is not None else 0.0,
        })
    return result


async def _label_coverage(db: AsyncSession, filters: list) -> dict[str, Any]:
    """计算 node/purpose/config/provider 四维标注覆盖率。

    not_collected = NULL/空串; unrecognized = 字面量 unknown。
    """
    total = (await db.execute(select(func.count()).where(*filters))).scalar() or 0
    if total <= 0:
        return {
            "total": 0,
            "dimensions": {
                dim: {
                    "labeled": 0,
                    "not_collected": 0,
                    "unrecognized": 0,
                    "coverage_rate": 0.0,
                }
                for dim in ("node", "purpose", "config", "provider")
            },
        }

    dim_cols = {
        "node": E.node_key,
        "purpose": E.purpose,
        "config": E.config_id,
        "provider": E.provider,
    }
    dimensions: dict[str, Any] = {}
    for name, col in dim_cols.items():
        not_collected = (await db.execute(
            select(func.count()).where(
                *filters,
                (col.is_(None)) | (col == ""),
            )
        )).scalar() or 0
        unrecognized = (await db.execute(
            select(func.count()).where(*filters, col == "unknown")
        )).scalar() or 0
        labeled = max(0, int(total) - int(not_collected) - int(unrecognized))
        dimensions[name] = {
            "labeled": labeled,
            "not_collected": int(not_collected),
            "unrecognized": int(unrecognized),
            "coverage_rate": _rate(labeled, total),
        }
    return {"total": int(total), "dimensions": dimensions}
