"""ops_metrics.llm: LLM 效率 layer (R2-8 拆分产物).

由 admin_ops.py _layer_llm 迁出,通过 thin forwarder 委托。
prom_counter_sum / verdict_from_ratio 通过 kwargs 依赖注入透传 (兼容 monkey patch)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_execution_trace import LlmExecutionTrace


async def layer_llm(
    *,
    db: AsyncSession,
    cs: datetime,
    bs: datetime,
    prom_counter_sum: Callable[..., float],
    verdict_from_ratio: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    """LLM 效率: 调用次数 / Token / retry / dispatcher."""
    llm_calls = prom_counter_sum("llm_calls_total")
    llm_calls_err = prom_counter_sum("llm_calls_total", {"status": "error"})
    llm_retry = prom_counter_sum("llm_retry_total")
    empty_return = prom_counter_sum("empty_return_total")
    json_repair = prom_counter_sum("json_repair_total")

    err_rate = llm_calls_err / llm_calls if llm_calls > 0 else 0.0

    trace_prompt_cur = (await db.execute(
        select(func.coalesce(func.sum(LlmExecutionTrace.total_prompt_tokens), 0))
        .where(LlmExecutionTrace.created_at >= cs)
    )).scalar() or 0

    trace_prompt_base = (await db.execute(
        select(func.coalesce(func.sum(LlmExecutionTrace.total_prompt_tokens), 0))
        .where(LlmExecutionTrace.created_at >= bs)
        .where(LlmExecutionTrace.created_at < cs)
    )).scalar() or 0

    trace_completion_cur = (await db.execute(
        select(func.coalesce(func.sum(LlmExecutionTrace.total_completion_tokens), 0))
        .where(LlmExecutionTrace.created_at >= cs)
    )).scalar() or 0

    return [
        {
            "name": "llm_calls_total",
            "layer": "llm",
            "pillar": "efficiency",
            "direction": "neutral",
            "kind": "counter",
            "description": "LLM 调用累计次数",
            "value": llm_calls,
            "baseline": llm_calls,
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "累计指标, 趋势看 timeseries",
        },
        {
            "name": "llm_error_rate",
            "layer": "llm",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "gauge",
            "description": "LLM 调用错误率 (status=error / total)",
            "value": round(err_rate, 4),
            "baseline": round(err_rate, 4),
            "verdict": (
                "neutral"
                if llm_calls == 0
                else ("critical" if err_rate > 0.05 else ("warn" if err_rate > 0.02 else "stable"))
            ),
            "delta_pct": 0.0,
            "reason": (
                "当前时间范围内暂无模型调用样本"
                if llm_calls == 0
                else f"error={int(llm_calls_err)}/total={int(llm_calls)}"
            ),
        },
        {
            "name": "llm_retry_total",
            "layer": "llm",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "counter",
            "description": "LLM 重试累计次数",
            "value": llm_retry,
            "baseline": llm_retry,
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "累计指标",
        },
        {
            "name": "llm_tokens_prompt_window",
            "layer": "llm",
            "pillar": "cost",
            "direction": "neutral",
            "kind": "counter",
            "description": "近窗口 prompt token 消耗 (from LlmExecutionTrace)",
            "value": float(trace_prompt_cur),
            "baseline": float(trace_prompt_base),
            **verdict_from_ratio(float(trace_prompt_cur), float(trace_prompt_base), "neutral"),
        },
        {
            "name": "llm_tokens_completion_window",
            "layer": "llm",
            "pillar": "cost",
            "direction": "neutral",
            "kind": "counter",
            "description": "近窗口 completion token 消耗",
            "value": float(trace_completion_cur),
            "baseline": float(trace_completion_cur),
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "窗口累计, 详情见 timeseries",
        },
        {
            "name": "empty_return_total",
            "layer": "llm",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "counter",
            "description": "LLM 空返回累计次数",
            "value": empty_return,
            "baseline": empty_return,
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "累计指标",
        },
        {
            "name": "json_repair_total",
            "layer": "llm",
            "pillar": "reliability",
            "direction": "lower_better",
            "kind": "counter",
            "description": "JSON 截断修复累计触发",
            "value": json_repair,
            "baseline": json_repair,
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "累计指标",
        },
    ]
