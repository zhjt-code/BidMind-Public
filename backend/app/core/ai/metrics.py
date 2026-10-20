"""LLM 可观测性指标

提供 Prometheus Counter/Histogram/Gauge 用于追踪 LLM 调用量、Token 消耗、
延迟、重试和 Dispatcher 自适应并发状态。
"""

from prometheus_client import Counter, Histogram, Gauge

llm_calls_total = Counter(
    "llm_calls_total",
    "LLM 调用总次数",
    ["model", "purpose", "status"],
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "LLM Token 消耗总量",
    ["model", "direction"],
)

llm_latency_seconds = Histogram(
    "llm_latency_seconds",
    "LLM 单次调用延迟（秒）",
    ["model"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)

llm_retry_total = Counter(
    "llm_retry_total",
    "LLM 调用重试总次数",
    ["model"],
)

llm_rate_limit_total = Counter(
    "llm_rate_limit_total",
    "LLM 429 分类信号总次数",
    ["provider", "model", "kind"],
)

llm_channel_cooldown_gauge = Gauge(
    "llm_channel_cooldown_seconds",
    "LLM 通道最近一次冷却秒数",
    ["purpose", "config_id"],
)


def record_llm_call(
    model: str,
    purpose: str,
    status: str,
    latency: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    """记录一次 LLM 调用的完整指标"""
    llm_calls_total.labels(model=model, purpose=purpose, status=status).inc()
    llm_latency_seconds.labels(model=model).observe(latency)
    if prompt_tokens > 0:
        llm_tokens_total.labels(model=model, direction="prompt").inc(prompt_tokens)
    if completion_tokens > 0:
        llm_tokens_total.labels(model=model, direction="completion").inc(completion_tokens)


def record_llm_retry(model: str) -> None:
    """记录一次 LLM 重试"""
    llm_retry_total.labels(model=model).inc()


def record_rate_limit_signal(provider: str, model: str, kind: str) -> None:
    """记录一次 LLM 429 分类信号"""
    llm_rate_limit_total.labels(
        provider=provider or "unknown",
        model=model or "unknown",
        kind=kind,
    ).inc()


def record_channel_cooldown(
    purpose: str,
    config_id: str,
    cooldown_seconds: int,
) -> None:
    """记录通道冷却秒数。"""
    llm_channel_cooldown_gauge.labels(
        purpose=purpose,
        config_id=config_id,
    ).set(cooldown_seconds)


# ==================== Dispatcher 自适应并发指标 ====================

dispatcher_concurrency_current = Gauge(
    "dispatcher_concurrency_current",
    "Dispatcher AIMD 当前自适应并发数",
    ["purpose"],
)

dispatcher_concurrency_adjustments_total = Counter(
    "dispatcher_concurrency_adjustments_total",
    "Dispatcher AIMD 并发调整次数",
    ["purpose", "direction"],
)


def record_concurrency_adjustment(purpose: str, direction: str, current: int) -> None:
    """记录一次并发调整

    Args:
        purpose: 调度器用途 (review/vision)
        direction: 调整方向 (up/down)
        current: 调整后的并发数
    """
    dispatcher_concurrency_current.labels(purpose=purpose).set(current)
    dispatcher_concurrency_adjustments_total.labels(
        purpose=purpose, direction=direction,
    ).inc()
