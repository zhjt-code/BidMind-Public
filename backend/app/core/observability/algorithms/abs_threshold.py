"""绝对阈值算子.

params:
  - warn: 0.05  (current 均值超出 [min, max] 则告警)
  - critical: 0.2
  - min / max: 可选, 若指定则按区间判断
  - mode: "below" / "above"  (决定是小于某值算坏, 还是大于某值算坏)
"""

from typing import Any

from app.core.observability.algorithms import AlgoResult


def evaluate(series: list[float], params: dict[str, Any] | None = None) -> AlgoResult:
    params = params or {}
    if not series:
        return AlgoResult(verdict_hint="stable", score=0.0, detail={"reason": "empty series"})

    current = sum(series[-min(len(series), 5):]) / min(len(series), 5)
    warn = params.get("warn")
    critical = params.get("critical")
    mode = params.get("mode", "above")

    hint = "stable"
    if critical is not None:
        if (mode == "above" and current >= critical) or (mode == "below" and current <= critical):
            hint = "critical"
    if hint != "critical" and warn is not None:
        if (mode == "above" and current >= warn) or (mode == "below" and current <= warn):
            hint = "warn"

    return AlgoResult(
        verdict_hint=hint,
        score=current,
        detail={"current_avg": round(current, 4), "mode": mode, "warn": warn, "critical": critical},
    )
