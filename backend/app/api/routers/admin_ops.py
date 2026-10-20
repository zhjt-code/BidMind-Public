"""运维监控总览聚合端点 (router 层).

承载 11 个 endpoint 装饰、参数校验与共享 helper (作为测试 monkeypatch 锚点);
8 个 layer 函数体、2 个领域聚合、6 个 SQL/装配密集 endpoint 业务块全部迁出到
`app.services.ops_metrics` 子包,本文件以 thin forwarder 方式委托 (R2-8)。

数据源: Prometheus 内存 registry (llm_/gate_/failure_state_/vector_/arq_) +
system_error_logs + audit_logs + vector_job_states + dispatcher-status + 计费快照。
端点结构参考 docs/failure_contract.md。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query
from prometheus_client import REGISTRY as PROM_REGISTRY
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_optional, require_admin
from app.core.database import get_db
from app.models.task_observation import TaskFlow
from app.models.user import User
from app.schemas.common import ApiResponse


router = APIRouter(prefix="/admin/ops", tags=["运维监控"])


# ==================== 辅助: 时间窗口解析 ====================

WINDOW_SECONDS: dict[str, int] = {
    "1h": 3600,
    "24h": 86400,
    "7d": 86400 * 7,
    "30d": 86400 * 30,
}


def _window_range(window: str) -> tuple[datetime, datetime, datetime]:
    """返回 (current_start, baseline_start, now), baseline 为上一等宽窗口."""
    seconds = WINDOW_SECONDS.get(window, WINDOW_SECONDS["24h"])
    now = datetime.now(timezone.utc)
    current_start = now - timedelta(seconds=seconds)
    baseline_start = current_start - timedelta(seconds=seconds)
    return current_start, baseline_start, now


# ==================== 辅助: 环比 verdict ====================

def _verdict_from_ratio(
    current: float,
    baseline: float,
    direction: Literal["higher_better", "lower_better", "neutral"],
    warn_threshold: float = 0.1,
    critical_threshold: float = 0.25,
) -> dict[str, Any]:
    """基于"当前值 vs 基线值"粗判趋势。

    - higher_better: 上升为好, 下降为坏
    - lower_better: 下降为好, 上升为坏
    - neutral: 不判断
    """
    if direction == "neutral":
        return {"verdict": "neutral", "delta_pct": 0.0, "reason": ""}

    if baseline == 0:
        if current == 0:
            return {"verdict": "stable", "delta_pct": 0.0, "reason": "baseline=0, current=0"}
        return {
            "verdict": "warn" if direction == "lower_better" else "improving",
            "delta_pct": 100.0,
            "reason": "baseline=0, current>0",
        }

    delta_pct = (current - baseline) / abs(baseline)
    abs_delta = abs(delta_pct)

    if direction == "higher_better":
        good = current > baseline
    else:  # lower_better
        good = current < baseline

    if abs_delta < warn_threshold:
        verdict = "stable"
    elif abs_delta < critical_threshold:
        verdict = "improving" if good else "warn"
    else:
        verdict = "improving" if good else "critical"

    return {
        "verdict": verdict,
        "delta_pct": round(delta_pct * 100, 2),
        "reason": f"current={round(current, 3)} vs baseline={round(baseline, 3)}, delta={round(delta_pct * 100, 2)}%",
    }


# ==================== 辅助: 从 Prometheus registry 读数 ====================

def _prom_counter_sum(metric_name: str, label_filter: Optional[dict[str, str]] = None) -> float:
    """遍历 Prometheus registry 累加某 Counter 总值 (所有 label 组合, 或按给定 filter 筛选)."""
    total = 0.0
    try:
        for metric in PROM_REGISTRY.collect():
            if metric.name != metric_name:
                continue
            for sample in metric.samples:
                # Counter 暴露为 metric_name_total 和 metric_name_created, 只累加前者
                if not sample.name.endswith("_total") and sample.name != metric_name:
                    continue
                if label_filter:
                    if not all(sample.labels.get(k) == v for k, v in label_filter.items()):
                        continue
                total += float(sample.value)
    except Exception:
        return 0.0
    return total


def _safe_rate(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _critic_flow_breakdown() -> dict[str, dict[str, Any]]:
    """Thin forwarder → services.ops_metrics.quality.critic_flow_breakdown (R2-8)."""
    from app.services.ops_metrics.quality import critic_flow_breakdown
    return critic_flow_breakdown(
        prom_counter_sum=_prom_counter_sum,
        safe_rate=_safe_rate,
    )


def _critic_flow_verdict(item: dict[str, Any]) -> tuple[str, str]:
    """Thin forwarder → services.ops_metrics.quality.critic_flow_verdict (R2-8)."""
    from app.services.ops_metrics.quality import critic_flow_verdict
    return critic_flow_verdict(item)


def _prom_histogram_stats(metric_name: str) -> dict[str, float]:
    """读取 Histogram 的 count / sum / 近似 P95 (基于 bucket). 简化实现, 后续 Phase 3 接入本地存储后替换."""
    count = 0.0
    total_sum = 0.0
    buckets: list[tuple[float, float]] = []
    try:
        for metric in PROM_REGISTRY.collect():
            if metric.name != metric_name:
                continue
            for sample in metric.samples:
                if sample.name.endswith("_count"):
                    count += float(sample.value)
                elif sample.name.endswith("_sum"):
                    total_sum += float(sample.value)
                elif sample.name.endswith("_bucket"):
                    le = sample.labels.get("le")
                    if le and le != "+Inf":
                        try:
                            buckets.append((float(le), float(sample.value)))
                        except ValueError:
                            pass
    except Exception:
        pass

    avg = total_sum / count if count > 0 else 0.0

    # 近似 P95: 找到累计频次 >= 95% count 的第一个 bucket
    p95 = 0.0
    if count > 0 and buckets:
        buckets.sort(key=lambda x: x[0])
        # buckets 里的 value 是 "<= le 的累计次数" (对同 label 组), 但我们聚合了所有 label, 退化为粗估
        target = 0.95 * count
        for le, cum in buckets:
            if cum >= target:
                p95 = le
                break
        if p95 == 0.0 and buckets:
            p95 = buckets[-1][0]

    return {"count": count, "sum": total_sum, "avg": avg, "p95": p95}


# ==================== 层 1: 平台健康 ====================

async def _layer_platform(db: AsyncSession, cs: datetime, bs: datetime) -> list[dict[str, Any]]:
    """Thin forwarder → services.ops_metrics.platform.layer_platform (R2-8)."""
    from app.services.ops_metrics.platform import layer_platform
    return await layer_platform(
        db=db, cs=cs, bs=bs,
        verdict_from_ratio=_verdict_from_ratio,
    )


async def _layer_throughput(db: AsyncSession, cs: datetime, bs: datetime) -> list[dict[str, Any]]:
    """Thin forwarder → services.ops_metrics.throughput.layer_throughput (R2-8)."""
    from app.services.ops_metrics.throughput import layer_throughput
    return await layer_throughput(
        db=db, cs=cs, bs=bs,
        verdict_from_ratio=_verdict_from_ratio,
    )


def _layer_stage() -> list[dict[str, Any]]:
    """Thin forwarder → services.ops_metrics.stage.layer_stage (R2-8)."""
    from app.services.ops_metrics.stage import layer_stage
    return layer_stage(
        prom_histogram_stats=_prom_histogram_stats,
    )


def _layer_quality() -> list[dict[str, Any]]:
    """Thin forwarder → services.ops_metrics.quality.layer_quality (R2-8)."""
    from app.services.ops_metrics.quality import layer_quality
    return layer_quality(
        prom_counter_sum=_prom_counter_sum,
        critic_flow_breakdown=_critic_flow_breakdown,
        critic_flow_verdict=_critic_flow_verdict,
    )


async def _layer_llm(db: AsyncSession, cs: datetime, bs: datetime) -> list[dict[str, Any]]:
    """Thin forwarder → services.ops_metrics.llm.layer_llm (R2-8)."""
    from app.services.ops_metrics.llm import layer_llm
    return await layer_llm(
        db=db, cs=cs, bs=bs,
        prom_counter_sum=_prom_counter_sum,
        verdict_from_ratio=_verdict_from_ratio,
    )


async def _layer_vector(db: AsyncSession, cs: datetime, bs: datetime) -> list[dict[str, Any]]:
    """Thin forwarder → services.ops_metrics.vector.layer_vector (R2-8)."""
    from app.services.ops_metrics.vector import layer_vector
    return await layer_vector(
        db=db, cs=cs, bs=bs,
        prom_counter_sum=_prom_counter_sum,
    )


def _layer_stability() -> list[dict[str, Any]]:
    """Thin forwarder → services.ops_metrics.stability.layer_stability (R2-8)."""
    from app.services.ops_metrics.stability import layer_stability
    return layer_stability(
        prom_registry=PROM_REGISTRY,
    )


async def _layer_ops(db: AsyncSession, cs: datetime, bs: datetime) -> list[dict[str, Any]]:
    """Thin forwarder → services.ops_metrics.ops.layer_ops (R2-8)."""
    from app.services.ops_metrics.ops import layer_ops
    return await layer_ops(
        db=db, cs=cs, bs=bs,
        verdict_from_ratio=_verdict_from_ratio,
    )


# ==================== 成本与三业务健康 ====================

async def _cost_snapshot(db: AsyncSession, cs: datetime, bs: datetime) -> dict[str, Any]:
    """Thin forwarder → services.ops_metrics.cost_snapshot.cost_snapshot (R2-8)."""
    from app.services.ops_metrics.cost_snapshot import cost_snapshot
    return await cost_snapshot(db=db, cs=cs, bs=bs)


async def _business_health(db: AsyncSession, cs: datetime, bs: datetime) -> dict[str, Any]:
    """Thin forwarder → services.ops_metrics.business_health.business_health (R2-8)."""
    from app.services.ops_metrics.business_health import business_health
    return await business_health(db=db, cs=cs, bs=bs)


# ==================== 主端点 ====================

@router.get("/overview", response_model=ApiResponse)
async def get_ops_overview(
    window: str = Query(default="24h", description="时间窗口: 1h/24h/7d/30d"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """运维监控总览 (八层 x 四维 正交).

    返回结构:
      {
        "window": "24h",
        "generated_at": "...",
        "layers": {
          "platform": [metric, ...],
          "throughput": [...],
          "stage": [...],
          "quality": [...],
          "llm": [...],
          "vector": [...],
          "stability": [...],
          "ops": [...]
        },
        "business_health": {tender, collusion, writer},
        "cost": {...}
      }

    每条 metric 统一携带 {name, layer, pillar, direction, kind, description, value, baseline, verdict, delta_pct, reason, [breakdown]}
    """
    if window not in WINDOW_SECONDS:
        window = "24h"
    cs, bs, now = _window_range(window)

    dispatcher = {}
    try:
        from app.core.ai.ai_factory import (
            get_judge_dispatcher,
            get_review_dispatcher,
            get_vision_dispatcher,
            get_extract_dispatcher,
        )
        review = get_review_dispatcher()
        vision = get_vision_dispatcher()
        judge = get_judge_dispatcher()
        extract = get_extract_dispatcher()
        dispatcher = {
            "review": review.get_status() if review else {"channel_count": 0, "channels": []},
            "vision": vision.get_status() if vision else {"channel_count": 0, "channels": []},
            "judge": judge.get_status() if judge else {"channel_count": 0, "channels": []},
            "extract": extract.get_status() if extract else {"channel_count": 0, "channels": []},
        }
    except Exception:
        dispatcher = {}

    from app.services.ops_metrics.overview import build_overview
    data = await build_overview(
        db=db, window=window, cs=cs, bs=bs, now=now, dispatcher=dispatcher,
        layer_platform=_layer_platform,
        layer_throughput=_layer_throughput,
        layer_stage=_layer_stage,
        layer_quality=_layer_quality,
        layer_llm=_layer_llm,
        layer_vector=_layer_vector,
        layer_stability=_layer_stability,
        layer_ops=_layer_ops,
        business_health=_business_health,
        cost_snapshot=_cost_snapshot,
    )
    return ApiResponse(code=200, message="success", data=data)


@router.get("/flow-funnel/{flow}", response_model=ApiResponse)
async def get_flow_funnel(
    flow: str,
    window: str = Query(default="24h", description="时间窗口: 1h/24h/7d/30d"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """三业务子页阶段漏斗数据.

    flow ∈ {tender, collusion, writer, process}
    基于 task_observations 表聚合:
      - 各 task_type 的 start/end 数 (漏斗)
      - 各 task_type 的 P50/P95 耗时
      - 近窗口 outcome 分布
    """
    if window not in WINDOW_SECONDS:
        window = "24h"
    cs, _bs, now = _window_range(window)

    if flow not in [f.value for f in TaskFlow]:
        return ApiResponse(code=400, message=f"unknown flow: {flow}", data=None)

    from app.services.ops_metrics.flow_funnel import compute_flow_funnel
    data = await compute_flow_funnel(db=db, flow=flow, cs=cs, now=now, window=window)
    return ApiResponse(code=200, message="success", data=data)


# ==================== Phase 3: Catalog / Summary / Timeseries ====================

@router.get("/metrics/catalog", response_model=ApiResponse)
async def get_metrics_catalog(
    layer: Optional[str] = Query(default=None, description="按 layer 过滤"),
    pillar: Optional[str] = Query(default=None, description="按 pillar 过滤"),
    business: Optional[str] = Query(default=None, description="按 business 过滤"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """返回 metric_catalog 表 (启动期扫描的指标目录). 前端按此元数据渲染卡片."""
    from app.services.ops_metrics.metrics_catalog import list_metrics_catalog
    data = await list_metrics_catalog(
        db=db, layer=layer, pillar=pillar, business=business,
        safe_json_list=_safe_json_list,
        safe_json_dict=_safe_json_dict,
    )
    return ApiResponse(code=200, message="success", data=data)


@router.get("/metrics/summary", response_model=ApiResponse)
async def get_metrics_summary(
    window: str = Query(default="24h"),
    layer: Optional[str] = Query(default=None),
    baseline: Optional[str] = Query(default=None, description="基线对比: none / week_initial"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """返回每条指标当前值 + Phase 4 MetricRule 驱动的 verdict."""
    from app.services.ops_metrics.metrics_summary import list_metrics_summary
    if window not in WINDOW_SECONDS:
        window = "24h"
    cs, _bs, now = _window_range(window)

    baseline_data: dict[str, Any] = {}
    if baseline == "week_initial":
        baseline_data = _load_baseline_json("week_initial.json")

    data = await list_metrics_summary(
        db=db, cs=cs, now=now, window=window,
        layer=layer, baseline=baseline, baseline_data=baseline_data,
    )
    return ApiResponse(code=200, message="success", data=data)


def _load_baseline_json(filename: str) -> dict[str, Any]:
    """从 docs/baselines/ 读基线 JSON (基于 admin_ops.py 的 __file__ 上溯 4 级到项目根)."""
    import json
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    p = os.path.join(root, "docs", "baselines", filename)
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


@router.get("/llm/reliability", response_model=ApiResponse)
async def get_llm_reliability_metrics(
    window: str = Query(default="24h"),
    group_by: Optional[str] = Query(
        default=None,
        description="单维度拆分: purpose / provider / model / config / node / flow",
    ),
    traffic_class: str = Query(
        default="production",
        description="流量类别: production / admin_test / regression",
    ),
    bucket_seconds: Optional[int] = Query(default=None, ge=60, le=86400),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """P0-A: LLM 可靠性聚合(基于 llm_attempt_events 事实表, 跨进程可信口径)."""
    from app.services.ops_metrics.llm_reliability import (
        WINDOW_SECONDS as RELIABILITY_WINDOWS,
        get_llm_reliability,
    )
    if window not in RELIABILITY_WINDOWS:
        window = "24h"
    if traffic_class not in ("production", "admin_test", "regression"):
        traffic_class = "production"
    data = await get_llm_reliability(
        db, window=window, group_by=group_by,
        traffic_class=traffic_class, bucket_seconds=bucket_seconds,
    )
    return ApiResponse(code=200, message="success", data=data)


@router.get("/llm/failures", response_model=ApiResponse)
async def get_llm_failure_drilldown_metrics(
    window: str = Query(default="24h"),
    outcome: str = Query(..., description="失败 outcome, 如 auth_error / timeout / empty"),
    traffic_class: str = Query(
        default="production",
        description="流量类别: production / admin_test / regression",
    ),
    limit: int = Query(default=20, ge=1, le=50),
    sample_limit: int = Query(default=30, ge=1, le=100),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """失败 outcome 下钻: 按 model×node 聚合 + 最近失败样本(不含正文)。"""
    from app.services.ops_metrics.llm_failure_drilldown import (
        FAILURE_OUTCOMES,
        get_llm_failure_drilldown,
    )
    from app.services.ops_metrics.llm_reliability import (
        WINDOW_SECONDS as RELIABILITY_WINDOWS,
    )
    if window not in RELIABILITY_WINDOWS:
        window = "24h"
    if traffic_class not in ("production", "admin_test", "regression"):
        traffic_class = "production"
    if outcome not in FAILURE_OUTCOMES:
        return ApiResponse(
            code=400,
            message=f"不支持的 outcome: {outcome}",
            data=None,
        )
    data = await get_llm_failure_drilldown(
        db,
        window=window,
        outcome=outcome,
        traffic_class=traffic_class,
        limit=limit,
        sample_limit=sample_limit,
    )
    return ApiResponse(code=200, message="success", data=data)


@router.get("/metrics/hotspots", response_model=ApiResponse)
async def get_metrics_hotspots(
    window: str = Query(default="24h"),
    limit: int = Query(default=10, ge=1, le=50),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Phase 5: 优化热点定位端点."""
    from app.services.ops_metrics.metrics_hotspots import list_metrics_hotspots
    if window not in WINDOW_SECONDS:
        window = "24h"
    cs, _bs, now = _window_range(window)
    data = await list_metrics_hotspots(
        db=db, cs=cs, now=now, window=window, limit=limit,
        collect_related_events=_collect_related_events,
    )
    return ApiResponse(code=200, message="success", data=data)


async def _collect_related_events(
    db: AsyncSession,
    *,
    cs: datetime,
    now: datetime,
    name: str,
) -> dict[str, Any]:
    """Thin forwarder → services.ops_metrics.related_events.collect_related_events (R2-8)."""
    from app.services.ops_metrics.related_events import collect_related_events
    return await collect_related_events(db, cs=cs, now=now, name=name)


class FrontendMetricEvent(BaseModel):
    """前端指标事件体 (Phase 6)."""
    name: str = Field(..., max_length=128)
    value: float = 0.0
    labels: Optional[dict[str, str]] = None
    ts: Optional[float] = None  # Unix seconds


class FrontendMetricsPayload(BaseModel):
    events: list[FrontendMetricEvent] = Field(default_factory=list, max_length=200)


@router.post("/metrics/frontend", response_model=ApiResponse)
async def ingest_frontend_metrics(
    payload: FrontendMetricsPayload,
    _user: Optional[User] = Depends(get_current_user_optional),
) -> ApiResponse:
    """Phase 6: 前端埋点上报端点 (批量)."""
    from app.services.ops_metrics.frontend_metrics import ingest_frontend_metric_events
    data = await ingest_frontend_metric_events(payload.events)
    return ApiResponse(code=200, message="ok", data=data)


@router.post("/regression/trigger", response_model=ApiResponse)
async def trigger_regression(
    flow: Optional[str] = Query(default=None, description="flow 过滤 (tender/collusion/writer)"),
    _admin: User = Depends(require_admin),
) -> ApiResponse:
    """Phase 5: 一键触发回归 runner, 通过 ARQ 异步执行.

    runner 完成后会把稳定率指标写回 metric_snapshots (需配合 runner 末尾上报实现).
    本端点只负责入队, 不阻塞.
    """
    try:
        from app.core.redis import get_redis_pool
        pool = await get_redis_pool()
        if pool is None:
            return ApiResponse(code=503, message="Redis 不可用, 无法触发", data=None)
        job = await pool.enqueue_job("regression_runner_job", flow or "all")
        return ApiResponse(
            code=200,
            message="已触发回归",
            data={"job_id": getattr(job, "job_id", None), "flow": flow or "all"},
        )
    except Exception as e:
        return ApiResponse(code=500, message=f"触发失败: {e}", data=None)


@router.get("/workers/health", response_model=ApiResponse)
async def get_worker_health(
    _admin: User = Depends(require_admin),
) -> ApiResponse:
    """只读返回 ARQ Worker 生命周期健康摘要."""

    from app.services.worker_health_service import build_worker_health_summary

    return ApiResponse(
        code=200,
        message="success",
        data=build_worker_health_summary(),
    )


@router.get("/metrics/rules", response_model=ApiResponse)
async def list_metric_rules(
    enabled_only: bool = Query(default=False),
    metric_name: Optional[str] = Query(default=None),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """只读列出 metric_rule 表 (Phase 4). 编辑留到后续 Phase."""
    from app.services.ops_metrics.metrics_rules import list_metric_rules
    data = await list_metric_rules(
        db=db, enabled_only=enabled_only, metric_name=metric_name,
        safe_json_dict=_safe_json_dict,
    )
    return ApiResponse(code=200, message="success", data=data)


@router.get("/metrics/timeseries", response_model=ApiResponse)
async def get_metrics_timeseries(
    name: str = Query(..., description="指标名"),
    window: str = Query(default="24h"),
    labels_hash: Optional[str] = Query(default=None, description="labels 组合哈希 (可选)"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """单指标时间序列 (从 metric_snapshots 温层读)."""
    from app.services.ops_metrics.metrics_timeseries import get_metrics_timeseries
    if window not in WINDOW_SECONDS:
        window = "24h"
    cs, _bs, now = _window_range(window)
    data = await get_metrics_timeseries(
        db=db, cs=cs, now=now, window=window, name=name, labels_hash=labels_hash,
    )
    return ApiResponse(code=200, message="success", data=data)


def _safe_json_list(s: Optional[str]) -> list:
    if not s:
        return []
    try:
        import json
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _safe_json_dict(s: Optional[str]) -> dict:
    if not s:
        return {}
    try:
        import json
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


@router.get("/vector-jobs/distribution", response_model=ApiResponse)
async def vector_job_distribution(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """独立只读端点: 向量任务状态分布 (用于 /admin/ops 子图)."""
    from app.services.ops_metrics.vector_jobs import get_vector_job_distribution
    data = await get_vector_job_distribution(db)
    return ApiResponse(code=200, message="success", data=data)
