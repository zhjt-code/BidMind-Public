"""ops_metrics.stage: 阶段耗时 layer (R2-8 拆分产物).

由 admin_ops.py _layer_stage 迁出,通过 thin forwarder 委托。
prom_histogram_stats 通过 kwargs 依赖注入透传 (兼容 monkey patch)。
"""
from __future__ import annotations

from typing import Any, Callable


def layer_stage(
    *,
    prom_histogram_stats: Callable[[str], dict[str, float]],
) -> list[dict[str, Any]]:
    """阶段耗时: 从 Prometheus gate_pipeline / vector_encode / llm_latency 读."""
    gate_stats = prom_histogram_stats("gate_pipeline_duration_seconds")
    encode_stats = prom_histogram_stats("vector_encode_latency_seconds")
    llm_stats = prom_histogram_stats("llm_latency_seconds")

    return [
        {
            "name": "gate_pipeline_duration_p95",
            "layer": "stage",
            "pillar": "efficiency",
            "direction": "lower_better",
            "kind": "histogram",
            "description": "Gate Pipeline 执行 P95 延迟 (秒)",
            "value": round(gate_stats["p95"], 3),
            "baseline": round(gate_stats["p95"], 3),
            "verdict": "neutral" if gate_stats["count"] == 0 else "stable",
            "delta_pct": 0.0,
            "reason": (
                "当前时间范围内暂无质量门控耗时样本"
                if gate_stats["count"] == 0
                else f"count={int(gate_stats['count'])} avg={round(gate_stats['avg'], 3)}s"
            ),
        },
        {
            "name": "vector_encode_latency_avg",
            "layer": "stage",
            "pillar": "efficiency",
            "direction": "lower_better",
            "kind": "histogram",
            "description": "向量化单批平均耗时 (秒)",
            "value": round(encode_stats["avg"], 3),
            "baseline": round(encode_stats["avg"], 3),
            "verdict": "neutral" if encode_stats["count"] == 0 else "stable",
            "delta_pct": 0.0,
            "reason": (
                "当前时间范围内暂无向量处理耗时样本"
                if encode_stats["count"] == 0
                else f"count={int(encode_stats['count'])}"
            ),
        },
        {
            "name": "llm_latency_p95",
            "layer": "stage",
            "pillar": "efficiency",
            "direction": "lower_better",
            "kind": "histogram",
            "description": "LLM 单次调用 P95 延迟 (秒)",
            "value": round(llm_stats["p95"], 3),
            "baseline": round(llm_stats["p95"], 3),
            "verdict": "neutral" if llm_stats["count"] == 0 else "stable",
            "delta_pct": 0.0,
            "reason": (
                "当前时间范围内暂无模型调用耗时样本"
                if llm_stats["count"] == 0
                else f"count={int(llm_stats['count'])} avg={round(llm_stats['avg'], 3)}s"
            ),
        },
    ]
