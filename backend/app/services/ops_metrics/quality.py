"""ops_metrics.quality: 结果质量 layer (R2-8 拆分产物).

由 admin_ops.py _layer_quality 迁出,通过 thin forwarder 委托。
prom_counter_sum / critic_flow_breakdown / critic_flow_verdict 通过 kwargs
依赖注入透传 (兼容 monkey patch)。

本模块同时承载 critic_flow_breakdown / critic_flow_verdict 两个原本散在 admin_ops
的 helper (仅服务 _layer_quality),admin_ops 仍保留同名 thin forwarder 作为
测试 monkeypatch 锚点。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from app.core.ai.critic.flows import (
    CRITIC_BUSINESS_FLOWS,
    FLOW_COLLUSION,
    FLOW_INVESTIGATE,
    FLOW_WRITER,
)


CRITIC_FLOW_LABELS: dict[str, str] = {
    FLOW_INVESTIGATE: "投标审查",
    FLOW_COLLUSION: "串标审查",
    FLOW_WRITER: "标书编写",
}


def critic_flow_breakdown(
    *,
    prom_counter_sum: Callable[..., float],
    safe_rate: Callable[[float, float], float],
) -> dict[str, dict[str, Any]]:
    """按业务 flow 拆分 CoVe / Judge Counter (R2-8 由 admin_ops._critic_flow_breakdown 迁入).

    只读取 Prometheus 内存 registry,不访问 DB;无样本时返回 0 与 neutral
    所需数据,避免"无流量 = 异常"的误判。
    """
    breakdown: dict[str, dict[str, Any]] = {}
    for flow in CRITIC_BUSINESS_FLOWS:
        judge_agree = prom_counter_sum(
            "judge_outcome_total", {"flow": flow, "verdict": "agree"},
        )
        judge_disagree = prom_counter_sum(
            "judge_outcome_total", {"flow": flow, "verdict": "disagree"},
        )
        judge_need_review = prom_counter_sum(
            "judge_outcome_total", {"flow": flow, "verdict": "need_review"},
        )
        judge_total = judge_agree + judge_disagree + judge_need_review
        judge_inconclusive = prom_counter_sum(
            "judge_inconclusive_total", {"flow": flow},
        )

        cove_agree = prom_counter_sum(
            "cove_outcome_total", {"flow": flow, "outcome": "agree"},
        )
        cove_disagree = prom_counter_sum(
            "cove_outcome_total", {"flow": flow, "outcome": "disagree"},
        )
        cove_inconclusive = prom_counter_sum(
            "cove_outcome_total", {"flow": flow, "outcome": "inconclusive"},
        )
        cove_skipped = prom_counter_sum(
            "cove_outcome_total", {"flow": flow, "outcome": "skipped"},
        )
        cove_total = cove_agree + cove_disagree + cove_inconclusive + cove_skipped

        judge_disagree_rate = safe_rate(judge_disagree, judge_total)
        judge_inconclusive_rate = safe_rate(judge_inconclusive, judge_total + judge_inconclusive)
        cove_disagree_rate = safe_rate(cove_disagree, cove_total)
        cove_inconclusive_rate = safe_rate(cove_inconclusive, cove_total)

        breakdown[flow] = {
            "flow": flow,
            "label": CRITIC_FLOW_LABELS.get(flow, flow),
            "judge": {
                "total": int(judge_total),
                "agree": int(judge_agree),
                "disagree": int(judge_disagree),
                "need_review": int(judge_need_review),
                "inconclusive": int(judge_inconclusive),
                "disagree_rate": round(judge_disagree_rate, 4),
                "inconclusive_rate": round(judge_inconclusive_rate, 4),
            },
            "cove": {
                "total": int(cove_total),
                "agree": int(cove_agree),
                "disagree": int(cove_disagree),
                "inconclusive": int(cove_inconclusive),
                "skipped": int(cove_skipped),
                "disagree_rate": round(cove_disagree_rate, 4),
                "inconclusive_rate": round(cove_inconclusive_rate, 4),
            },
        }
    return breakdown


def critic_flow_verdict(item: dict[str, Any]) -> tuple[str, str]:
    """根据 critic_flow_breakdown 单条结果推断 verdict + reason (R2-8 由 admin_ops 迁入)."""
    judge = item.get("judge") or {}
    cove = item.get("cove") or {}
    judge_total = int(judge.get("total") or 0)
    judge_inconclusive = int(judge.get("inconclusive") or 0)
    cove_total = int(cove.get("total") or 0)
    total = judge_total + judge_inconclusive + cove_total
    if total == 0:
        return "neutral", "当前时间范围内暂无 Critic 样本"

    judge_disagree_rate = float(judge.get("disagree_rate") or 0.0)
    judge_inconclusive_rate = float(judge.get("inconclusive_rate") or 0.0)
    cove_disagree_rate = float(cove.get("disagree_rate") or 0.0)

    reasons: list[str] = []
    verdict = "stable"
    if judge_inconclusive_rate > 0.2 or cove_disagree_rate > 0.4:
        verdict = "critical"
    elif judge_inconclusive_rate > 0.1 or cove_disagree_rate > 0.2:
        verdict = "warn"

    if judge_total > 0 and not (0.05 <= judge_disagree_rate <= 0.15):
        reasons.append(f"judge_disagree_rate={judge_disagree_rate:.1%} 不在 [5%,15%]")
        if verdict == "stable":
            verdict = "warn"
    if judge_inconclusive_rate > 0.1:
        reasons.append(f"judge_inconclusive_rate={judge_inconclusive_rate:.1%}")
    if cove_disagree_rate > 0.2:
        reasons.append(f"cove_disagree_rate={cove_disagree_rate:.1%}")

    if not reasons:
        reasons.append(
            f"judge={judge_total}, cove={cove_total}, "
            f"judge_disagree={judge_disagree_rate:.1%}, cove_disagree={cove_disagree_rate:.1%}",
        )
    return verdict, "; ".join(reasons)


def layer_quality(
    *,
    prom_counter_sum: Callable[..., float],
    critic_flow_breakdown: Callable[[], dict[str, Any]],
    critic_flow_verdict: Callable[[dict[str, Any]], tuple[str, str]],
) -> list[dict[str, Any]]:
    """结果质量: Gate verdict / Judge / CoVe / FailureState."""
    gate_total = prom_counter_sum("gate_verdict_total")
    gate_reject = prom_counter_sum("writer_gate_reject_total")
    judge_total = prom_counter_sum("judge_outcome_total")
    cove_total = prom_counter_sum("cove_outcome_total")
    critic_breakdown = critic_flow_breakdown()

    fs_completed = prom_counter_sum("failure_state_distribution", {"state": "completed"})
    fs_degraded = prom_counter_sum("failure_state_distribution", {"state": "degraded"})
    fs_needs_review = prom_counter_sum("failure_state_distribution", {"state": "needs_review"})
    fs_inconclusive = prom_counter_sum("failure_state_distribution", {"state": "inconclusive"})
    fs_failed = prom_counter_sum("failure_state_distribution", {"state": "failed"})
    fs_total = fs_completed + fs_degraded + fs_needs_review + fs_inconclusive + fs_failed

    completed_ratio = fs_completed / fs_total if fs_total > 0 else 0.0
    failure_state_reason = (
        "当前时间范围内暂无失败状态样本"
        if fs_total == 0
        else f"completed={int(fs_completed)}/total={int(fs_total)}"
    )
    failure_state_verdict = (
        "neutral"
        if fs_total == 0
        else ("stable" if completed_ratio >= 0.8 else ("warn" if completed_ratio >= 0.5 else "critical"))
    )

    metrics: list[dict[str, Any]] = [
        {
            "name": "gate_verdict_total",
            "layer": "quality",
            "pillar": "quality",
            "direction": "neutral",
            "kind": "counter",
            "description": "Gate 裁决累计次数 (所有 verdict 合计)",
            "value": gate_total,
            "baseline": gate_total,
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "累计指标, 趋势看 timeseries",
        },
        {
            "name": "writer_gate_reject_total",
            "layer": "quality",
            "pillar": "quality",
            "direction": "lower_better",
            "kind": "counter",
            "description": "写标 Gate reject 累计数",
            "value": gate_reject,
            "baseline": gate_reject,
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "累计指标",
        },
        {
            "name": "failure_state_perfect_ratio",
            "layer": "quality",
            "pillar": "quality",
            "direction": "higher_better",
            "kind": "gauge",
            "description": "直接完成占比",
            "value": round(completed_ratio, 4),
            "baseline": round(completed_ratio, 4),
            "verdict": failure_state_verdict,
            "delta_pct": 0.0,
            "reason": failure_state_reason,
            "breakdown": {
                "completed": int(fs_completed),
                "degraded": int(fs_degraded),
                "needs_review": int(fs_needs_review),
                "inconclusive": int(fs_inconclusive),
                "failed": int(fs_failed),
            },
        },
        {
            "name": "judge_outcome_total",
            "layer": "quality",
            "pillar": "quality",
            "direction": "neutral",
            "kind": "counter",
            "description": "LLM-as-Judge 累计裁决次数",
            "value": judge_total,
            "baseline": judge_total,
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "累计指标",
        },
        {
            "name": "cove_outcome_total",
            "layer": "quality",
            "pillar": "quality",
            "direction": "neutral",
            "kind": "counter",
            "description": "CoVe 复核累计次数",
            "value": cove_total,
            "baseline": cove_total,
            "verdict": "neutral",
            "delta_pct": 0.0,
            "reason": "累计指标",
        },
    ]

    for flow in CRITIC_BUSINESS_FLOWS:
        item = critic_breakdown[flow]
        verdict, reason = critic_flow_verdict(item)
        judge = item["judge"]
        cove = item["cove"]
        total = int(judge["total"]) + int(judge["inconclusive"]) + int(cove["total"])
        metrics.append({
            "name": f"critic_flow_health_{flow}",
            "layer": "quality",
            "pillar": "quality",
            "direction": "neutral",
            "kind": "counter",
            "description": f"{item['label']} Critic 复核健康度",
            "value": total,
            "baseline": total,
            "verdict": verdict,
            "delta_pct": 0.0,
            "reason": reason,
            "breakdown": item,
        })

    return metrics
