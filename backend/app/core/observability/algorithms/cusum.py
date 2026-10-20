"""CUSUM (累积和) 变点检测.

params:
  - k: 0.5  (敏感度参数, 单位 sigma)
  - h: 5    (告警阈值, 单位 sigma, cusum 超过 h*sigma 判定变点)

返回 AlgoResult:
  - verdict_hint: warn / critical / stable / improving
  - score: 最大累积和 / h  (标准化到 [-1, 1] 外)
  - change_points: 变点位置索引列表
  - detail: { k, h, cusum_pos_max, cusum_neg_max }
"""

from typing import Any

from app.core.observability.algorithms import AlgoResult


def evaluate(series: list[float], params: dict[str, Any] | None = None) -> AlgoResult:
    params = params or {}
    k = params.get("k", 0.5)
    h = params.get("h", 5)
    n = len(series)
    if n < 8:
        return AlgoResult(verdict_hint="stable", score=0.0, detail={"reason": "series too short"})

    # 基线 (前半段均值 / std)
    half = n // 2
    baseline = series[:half]
    mean_b = sum(baseline) / len(baseline)
    var_b = sum((v - mean_b) ** 2 for v in baseline) / len(baseline)
    sigma = var_b ** 0.5 if var_b > 0 else 1e-6

    K = k * sigma
    H = h * sigma

    s_pos = 0.0
    s_neg = 0.0
    cusum_pos_max = 0.0
    cusum_neg_max = 0.0
    pos_points: list[int] = []
    neg_points: list[int] = []
    for i, v in enumerate(series):
        s_pos = max(0.0, s_pos + (v - mean_b) - K)
        s_neg = min(0.0, s_neg + (v - mean_b) + K)
        if s_pos > cusum_pos_max:
            cusum_pos_max = s_pos
        if s_neg < cusum_neg_max:
            cusum_neg_max = s_neg
        if s_pos > H:
            pos_points.append(i)
            s_pos = 0.0
        if s_neg < -H:
            neg_points.append(i)
            s_neg = 0.0

    if pos_points and not neg_points:
        hint = "improving"
    elif neg_points and not pos_points:
        hint = "degrading"
    elif pos_points and neg_points:
        hint = "warn"
    else:
        # 虽没触发 H 但若 cusum_max 较大可告警
        ratio = max(cusum_pos_max, abs(cusum_neg_max)) / H if H > 0 else 0
        if ratio > 0.7:
            hint = "warn"
        else:
            hint = "stable"

    score = (cusum_pos_max + cusum_neg_max) / H if H > 0 else 0.0
    return AlgoResult(
        verdict_hint=hint,
        score=max(-1.0, min(1.0, score)),
        change_points=[{"index": idx, "direction": "up"} for idx in pos_points]
        + [{"index": idx, "direction": "down"} for idx in neg_points],
        detail={
            "k": k,
            "h": h,
            "sigma": round(sigma, 4),
            "baseline_mean": round(mean_b, 4),
            "cusum_pos_max": round(cusum_pos_max, 4),
            "cusum_neg_max": round(cusum_neg_max, 4),
        },
    )
