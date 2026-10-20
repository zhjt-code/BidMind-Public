"""ops_metrics.overview: /overview endpoint 业务装配 (R2-8 拆分产物).

由 admin_ops.py get_ops_overview 内 layers dict 装配 + pillar_summary 聚合迁出。
dispatcher 探测保留在 endpoint 内 (涉及 ai_factory 上下文,以及 try/except 兜底)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession


async def build_overview(
    *,
    db: AsyncSession,
    window: str,
    cs: datetime,
    bs: datetime,
    now: datetime,
    dispatcher: dict[str, Any],
    layer_platform: Callable[..., Awaitable[list[dict[str, Any]]]],
    layer_throughput: Callable[..., Awaitable[list[dict[str, Any]]]],
    layer_stage: Callable[[], list[dict[str, Any]]],
    layer_quality: Callable[[], list[dict[str, Any]]],
    layer_llm: Callable[..., Awaitable[list[dict[str, Any]]]],
    layer_vector: Callable[..., Awaitable[list[dict[str, Any]]]],
    layer_stability: Callable[[], list[dict[str, Any]]],
    layer_ops: Callable[..., Awaitable[list[dict[str, Any]]]],
    business_health: Callable[..., Awaitable[dict[str, Any]]],
    cost_snapshot: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """运维监控总览 (八层 x 四维 正交) 业务装配.

    返回 ApiResponse.data 字典,9 个顶层 key 与原 endpoint byte-equal。
    """
    layers: dict[str, list[dict[str, Any]]] = {
        "platform": await layer_platform(db, cs, bs),
        "throughput": await layer_throughput(db, cs, bs),
        "stage": layer_stage(),
        "quality": layer_quality(),
        "llm": await layer_llm(db, cs, bs),
        "vector": await layer_vector(db, cs, bs),
        "stability": layer_stability(),
        "ops": await layer_ops(db, cs, bs),
    }

    pillar_summary: dict[str, dict[str, int]] = {
        "quality": {"improving": 0, "stable": 0, "warn": 0, "critical": 0},
        "reliability": {"improving": 0, "stable": 0, "warn": 0, "critical": 0},
        "efficiency": {"improving": 0, "stable": 0, "warn": 0, "critical": 0},
        "cost": {"improving": 0, "stable": 0, "warn": 0, "critical": 0},
    }
    for _layer_name, metrics in layers.items():
        for m in metrics:
            pillar = m.get("pillar")
            verdict = m.get("verdict")
            if pillar in pillar_summary and verdict in pillar_summary[pillar]:
                pillar_summary[pillar][verdict] += 1

    return {
        "window": window,
        "window_start": cs.isoformat(),
        "baseline_start": bs.isoformat(),
        "generated_at": now.isoformat(),
        "layers": layers,
        "pillar_summary": pillar_summary,
        "business_health": await business_health(db, cs, bs),
        "cost": await cost_snapshot(db, cs, bs),
        "dispatcher": dispatcher,
    }
