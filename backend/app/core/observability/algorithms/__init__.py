"""趋势判断算子集合 (Phase 4).

统一接口::

    def evaluate(series: list[float], params: dict) -> AlgoResult

每个算子独立实现, 返回 AlgoResult 包含:
  - verdict_hint: 算法给出的"趋势方向暗示" (improving/stable/warn/critical/degrading)
  - score: 标准化打分 [-1, 1], 负数表示坏方向
  - change_points: 变点时刻列表 (可选, 仅 CUSUM 有)
  - detail: 结构化细节 (trend_strength / p_value / ewma / ...)
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AlgoResult:
    verdict_hint: str = "stable"
    score: float = 0.0
    change_points: list[Any] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


__all__ = ["AlgoResult"]
