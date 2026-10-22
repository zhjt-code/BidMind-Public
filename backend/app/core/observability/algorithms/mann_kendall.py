"""Mann-Kendall 趋势检验 (非参数).

params:
  - alpha: 0.05 (显著性水平, p-value < alpha 才认定有趋势)

返回 AlgoResult:
  - verdict_hint: improving / degrading / stable
  - score: Kendall's tau [-1, 1]
  - detail: { S, var_s, z, p_value, trend }
"""

import math
from typing import Any

from app.core.observability.algorithms import AlgoResult


def evaluate(series: list[float], params: dict[str, Any] | None = None) -> AlgoResult:
    params = params or {}
    alpha = params.get("alpha", 0.05)
    n = len(series)
    if n < 4:
        return AlgoResult(verdict_hint="stable", score=0.0, detail={"reason": "series too short"})

    # 计算 S 统计量
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff = series[j] - series[i]
            if diff > 0:
                s += 1
            elif diff < 0:
                s -= 1

    # 方差 (未考虑 ties, 对浮点序列已足够)
    var_s = n * (n - 1) * (2 * n + 5) / 18.0
    if var_s <= 0:
        return AlgoResult(verdict_hint="stable", score=0.0, detail={"S": s, "var_s": var_s})

    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0

    # p-value (双侧)
    p_value = 2 * (1 - _phi(abs(z)))

    # Kendall's tau
    tau = s / (n * (n - 1) / 2) if n > 1 else 0.0

    if p_value < alpha:
        hint = "improving" if s > 0 else "degrading"
    else:
        hint = "stable"

    return AlgoResult(
        verdict_hint=hint,
        score=max(-1.0, min(1.0, tau)),
        detail={
            "S": s,
            "var_s": round(var_s, 2),
            "z": round(z, 3),
            "p_value": round(p_value, 4),
            "tau": round(tau, 3),
            "trend": hint,
        },
    )


def _phi(x: float) -> float:
    """标准正态分布 CDF 近似 (Abramowitz & Stegun 公式 26.2.17)."""
    # constants
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return 0.5 * (1.0 + sign * y)
