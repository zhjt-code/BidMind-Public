"""指数加权移动平均 + 控制带 (EWMA control chart).

params:
  - lam: 0.2 (加权因子)
  - sigma: 3 (控制带宽度, 默认 3 sigma)

返回 AlgoResult:
  - verdict_hint: warn / critical / stable
  - score: 最后点偏离中心的 z 值
  - detail: { center, ucl, lcl, last_value, last_ewma }
"""

import math
from typing import Any

from app.core.observability.algorithms import AlgoResult


def evaluate(series: list[float], params: dict[str, Any] | None = None) -> AlgoResult:
    params = params or {}
    lam = params.get("lam", 0.2)
    sigma_k = params.get("sigma", 3)
    n = len(series)
    if n < 5:
        return AlgoResult(verdict_hint="stable", score=0.0, detail={"reason": "series too short"})

    center = sum(series) / n
    variance = sum((v - center) ** 2 for v in series) / n
    std = math.sqrt(variance) if variance > 0 else 0.0

    # EWMA 标准差放大因子 (对于大 n 简化为 std * sqrt(lam/(2-lam)))
    factor = math.sqrt(lam / (2 - lam))
    ewma_std = std * factor if std > 0 else 0.0

    # 计算 EWMA
    ewma = series[0]
    for v in series[1:]:
        ewma = lam * v + (1 - lam) * ewma

    ucl = center + sigma_k * ewma_std
    lcl = center - sigma_k * ewma_std

    hint = "stable"
    if ewma > ucl or ewma < lcl:
        hint = "critical"
    elif abs(ewma - center) > (sigma_k / 2) * ewma_std:
        hint = "warn"

    z = (ewma - center) / ewma_std if ewma_std > 0 else 0.0
    return AlgoResult(
        verdict_hint=hint,
        score=max(-5.0, min(5.0, z)),
        detail={
            "center": round(center, 4),
            "std": round(std, 4),
            "ewma": round(ewma, 4),
            "ucl": round(ucl, 4),
            "lcl": round(lcl, 4),
            "z": round(z, 3),
            "lam": lam,
            "sigma_k": sigma_k,
        },
    )
