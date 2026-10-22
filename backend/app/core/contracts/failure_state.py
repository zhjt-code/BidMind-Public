"""统一失败合同状态枚举。

严格按 docs/failure_contract.md §2.1 定义，字符串值与
tests/regression/schemas/common.py 的 FailureStateEnum 一一对齐。
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable

_SEVERITY_ORDER: dict[str, int] = {
    "completed": 0,
    "degraded": 1,
    "needs_review": 2,
    "inconclusive": 3,
    "failed": 4,
}


class FailureState(str, Enum):
    """统一失败合同状态。

    严格按从轻到重排序：
        completed < degraded < needs_review < inconclusive < failed
    """

    COMPLETED = "completed"
    DEGRADED = "degraded"
    NEEDS_REVIEW = "needs_review"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


def severity_rank(state: FailureState | str) -> int:
    """Return the severity rank (0=lightest, 4=heaviest)."""
    val = state.value if isinstance(state, FailureState) else state
    return _SEVERITY_ORDER[val]


def max_severity(states: Iterable[FailureState | str]) -> FailureState:
    """Return the most severe state from a collection.

    Used by GatePipeline patch aggregation (evidence_gate_design §3.4).
    """
    result = FailureState.COMPLETED
    for s in states:
        candidate = FailureState(s) if isinstance(s, str) else s
        if severity_rank(candidate) > severity_rank(result):
            result = candidate
    return result
