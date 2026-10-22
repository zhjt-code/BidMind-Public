"""Ratio 算子: 最简单的环比 / 同比.

params:
  - baseline_split: 0.5 (把 series 分成"近"和"远"两半)
  - warn: 0.1 (相对偏差 > 10%)
  - critical: 0.25 (相对偏差 > 25%)
"""

from typing import Any

from app.core.observability.algorithms import AlgoResult


def evaluate(series: list[float], params: dict[str, Any] | None = None) -> AlgoResult:
    params = params or {}
    if len(series) < 4:
        return AlgoResult(verdict_hint="stable", score=0.0, detail={"reason": "series too short"})

    split = params.get("baseline_split", 0.5)
    cut = max(1, int(len(series) * split))
    baseline = series[:-cut]
    current = series[-cut:]
    if not baseline:
        return AlgoResult(verdict_hint="stable", score=0.0, detail={"reason": "no baseline"})

    b_avg = sum(baseline) / len(baseline)
    c_avg = sum(current) / len(current)

    if b_avg == 0:
        delta = 1.0 if c_avg > 0 else 0.0
    else:
        delta = (c_avg - b_avg) / abs(b_avg)

    warn = params.get("warn", 0.10)
    critical = params.get("critical", 0.25)
    abs_d = abs(delta)

    if abs_d < warn:
        hint = "stable"
    elif abs_d < critical:
        hint = "warn"
    else:
        hint = "critical"

    score = max(-1.0, min(1.0, delta))
    return AlgoResult(
        verdict_hint=hint,
        score=score,
        detail={
            "baseline_avg": round(b_avg, 4),
            "current_avg": round(c_avg, 4),
            "delta_pct": round(delta * 100, 2),
        },
    )
