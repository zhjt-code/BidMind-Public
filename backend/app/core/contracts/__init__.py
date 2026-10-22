"""失败合同层（Layer 6）。

提供统一的 FailureState 枚举与 FailureStateDeterminer 决策树。
字符串值与 tests/regression/schemas/common.py 的 FailureStateEnum 严格对齐。
"""

from .failure_state import FailureState, max_severity, severity_rank
from .failure_state_determiner import FailureStateDeterminer

__all__ = [
    "FailureState",
    "FailureStateDeterminer",
    "max_severity",
    "severity_rank",
]
