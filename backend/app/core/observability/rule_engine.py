"""规则引擎 (Phase 4): 根据 metric_rule 表驱动趋势判断.

统一入口::

    async def evaluate_metric(db, name, series, direction) -> Verdict

内部流程:
    1. 查 metric_rule 获取启用规则 (无规则则退化为 ratio 兜底)
    2. 调用对应 algorithm 算子 evaluate(series, params)
    3. 根据 AlgoResult + direction + verdict_map 映射出最终 verdict
    4. 返回统一格式 Verdict (含 reason / change_points / algorithm)

内置算子: ratio / abs / mk / ewma / cusum
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability.algorithms import AlgoResult
from app.core.observability.algorithms import abs_threshold as abs_algo
from app.core.observability.algorithms import cusum as cusum_algo
from app.core.observability.algorithms import ewma as ewma_algo
from app.core.observability.algorithms import mann_kendall as mk_algo
from app.core.observability.algorithms import ratio as ratio_algo


ALGO_MAP = {
    "ratio": ratio_algo.evaluate,
    "abs": abs_algo.evaluate,
    "mk": mk_algo.evaluate,
    "ewma": ewma_algo.evaluate,
    "cusum": cusum_algo.evaluate,
}


@dataclass
class Verdict:
    verdict: str = "stable"  # improving / stable / warn / critical / degrading / neutral
    algorithm: str = "ratio"
    reason: str = ""
    score: float = 0.0
    change_points: list[Any] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _direction_aware_mapping(hint: str, direction: str) -> str:
    """算法给出"趋势暗示", 结合 direction 映射为 verdict.

    hint: improving / degrading / warn / critical / stable
    direction: higher_better / lower_better / neutral

    规则:
      - higher_better + improving -> improving
      - higher_better + degrading -> warn (不视为 critical, 由 rule 可配置)
      - lower_better + improving -> warn  (lower_better 的 "up" 其实是坏方向)
      - lower_better + degrading -> improving
      - warn / critical / stable: 保持原样
      - neutral: 不翻转
    """
    if hint in {"warn", "critical", "stable"}:
        return hint

    if direction == "neutral":
        return "stable" if hint not in {"improving", "degrading"} else "neutral"

    if direction == "higher_better":
        return "improving" if hint == "improving" else "warn"

    if direction == "lower_better":
        return "improving" if hint == "degrading" else "warn"

    return hint


async def _load_rule(db: AsyncSession, metric_name: str) -> Optional[dict[str, Any]]:
    from app.models.metric_rule import MetricRule
    try:
        row = (
            await db.execute(
                select(MetricRule)
                .where(MetricRule.metric_name == metric_name)
                .where(MetricRule.enabled == True)  # noqa: E712
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "algorithm": row.algorithm or "ratio",
            "params": _safe_json(row.params_json) or {},
            "verdict_map": _safe_json(row.verdict_map_json) or {},
        }
    except Exception as e:
        logger.debug(f"_load_rule[{metric_name}] 失败: {e}")
        return None


def _safe_json(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


async def evaluate_metric(
    db: AsyncSession,
    *,
    metric_name: str,
    series: list[float],
    direction: str = "neutral",
) -> Verdict:
    """根据规则表评估一个指标的当前趋势. DB 不可用时降级为 ratio."""
    rule = await _load_rule(db, metric_name)
    algorithm = (rule or {}).get("algorithm", "ratio")
    params = (rule or {}).get("params", {})
    verdict_map = (rule or {}).get("verdict_map", {})

    algo_fn = ALGO_MAP.get(algorithm)
    if algo_fn is None:
        algo_fn = ALGO_MAP["ratio"]
        algorithm = "ratio"

    try:
        algo_result: AlgoResult = algo_fn(series, params)
    except Exception as e:
        logger.debug(f"evaluate_metric[{metric_name}] algo error: {e}")
        algo_result = AlgoResult(verdict_hint="stable", score=0.0, detail={"error": str(e)})

    hint = algo_result.verdict_hint
    mapped = verdict_map.get(hint, _direction_aware_mapping(hint, direction))

    return Verdict(
        verdict=mapped,
        algorithm=algorithm,
        reason=_format_reason(algorithm, algo_result, direction),
        score=round(algo_result.score, 4),
        change_points=algo_result.change_points,
        detail=algo_result.detail,
    )


def _format_reason(algo: str, result: AlgoResult, direction: str) -> str:
    det = result.detail
    if algo == "ratio":
        return f"delta={det.get('delta_pct', 0)}% (ratio vs baseline)"
    if algo == "mk":
        return f"Mann-Kendall tau={det.get('tau', 0)} p={det.get('p_value', 0)}"
    if algo == "ewma":
        return f"EWMA z={det.get('z', 0)} (UCL/LCL {det.get('ucl', 0)}/{det.get('lcl', 0)})"
    if algo == "cusum":
        return f"CUSUM max_pos={det.get('cusum_pos_max', 0)} max_neg={det.get('cusum_neg_max', 0)}"
    if algo == "abs":
        return f"current_avg={det.get('current_avg', 0)} mode={det.get('mode', '')}"
    return ""
