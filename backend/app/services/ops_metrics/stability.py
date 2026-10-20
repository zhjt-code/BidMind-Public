"""ops_metrics.stability: 稳定性 layer (R2-8 拆分产物).

由 admin_ops.py _layer_stability 迁出,通过 thin forwarder 委托。
prom_registry 通过 kwargs 依赖注入透传 (兼容 monkey patch admin_ops.PROM_REGISTRY)。
"""
from __future__ import annotations

from typing import Any


def layer_stability(
    *,
    prom_registry: Any,
) -> list[dict[str, Any]]:
    """稳定性: 回归 stability_* 指标 (如果 regression 有运行)."""
    conclusion_samples: list[float] = []
    failure_samples: list[float] = []
    evidence_samples: list[float] = []
    drift_samples: list[float] = []
    try:
        for metric in prom_registry.collect():
            if metric.name == "stability_conclusion_rate":
                for s in metric.samples:
                    if s.name == "stability_conclusion_rate":
                        conclusion_samples.append(float(s.value))
            elif metric.name == "stability_failure_state_rate":
                for s in metric.samples:
                    if s.name == "stability_failure_state_rate":
                        failure_samples.append(float(s.value))
            elif metric.name == "stability_evidence_jaccard":
                for s in metric.samples:
                    if s.name == "stability_evidence_jaccard":
                        evidence_samples.append(float(s.value))
            elif metric.name == "stability_explanation_drift_rate":
                for s in metric.samples:
                    if s.name == "stability_explanation_drift_rate":
                        drift_samples.append(float(s.value))
    except Exception:
        pass

    def _avg(vs: list[float]) -> float:
        return sum(vs) / len(vs) if vs else 0.0

    conclusion_avg = _avg(conclusion_samples)
    failure_avg = _avg(failure_samples)
    evidence_avg = _avg(evidence_samples)
    drift_avg = _avg(drift_samples)

    return [
        {
            "name": "stability_conclusion_rate_avg",
            "layer": "stability",
            "pillar": "quality",
            "direction": "higher_better",
            "kind": "gauge",
            "description": "结论稳定率平均 (跨 flow x project)",
            "value": round(conclusion_avg, 4),
            "baseline": round(conclusion_avg, 4),
            "verdict": (
                "neutral"
                if not conclusion_samples
                else ("stable" if conclusion_avg >= 0.9 else ("warn" if conclusion_avg >= 0.7 else "critical"))
            ),
            "delta_pct": 0.0,
            "reason": "当前时间范围内暂无稳定性样本" if not conclusion_samples else f"samples={len(conclusion_samples)}",
        },
        {
            "name": "stability_failure_state_rate_avg",
            "layer": "stability",
            "pillar": "quality",
            "direction": "higher_better",
            "kind": "gauge",
            "description": "失败契约稳定率平均",
            "value": round(failure_avg, 4),
            "baseline": round(failure_avg, 4),
            "verdict": (
                "neutral"
                if not failure_samples
                else ("stable" if failure_avg >= 0.9 else ("warn" if failure_avg >= 0.7 else "critical"))
            ),
            "delta_pct": 0.0,
            "reason": "当前时间范围内暂无稳定性样本" if not failure_samples else f"samples={len(failure_samples)}",
        },
        {
            "name": "stability_evidence_jaccard_avg",
            "layer": "stability",
            "pillar": "quality",
            "direction": "higher_better",
            "kind": "gauge",
            "description": "关键证据稳定率 (Jaccard) 平均",
            "value": round(evidence_avg, 4),
            "baseline": round(evidence_avg, 4),
            "verdict": (
                "neutral"
                if not evidence_samples
                else ("stable" if evidence_avg >= 0.8 else ("warn" if evidence_avg >= 0.5 else "critical"))
            ),
            "delta_pct": 0.0,
            "reason": "当前时间范围内暂无稳定性样本" if not evidence_samples else f"samples={len(evidence_samples)}",
        },
        {
            "name": "stability_explanation_drift_avg",
            "layer": "stability",
            "pillar": "quality",
            "direction": "lower_better",
            "kind": "gauge",
            "description": "解释漂移率平均 (越低越好)",
            "value": round(drift_avg, 4),
            "baseline": round(drift_avg, 4),
            "verdict": (
                "neutral"
                if not drift_samples
                else ("stable" if drift_avg <= 0.1 else ("warn" if drift_avg <= 0.3 else "critical"))
            ),
            "delta_pct": 0.0,
            "reason": "当前时间范围内暂无稳定性样本" if not drift_samples else f"samples={len(drift_samples)}",
        },
    ]
