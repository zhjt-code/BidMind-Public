"""FailureStateDeterminer -- 纯函数决策树。

严格按 docs/failure_contract.md §2.3 的六步首匹配规则决定单条结果的 failure_state。
Pipeline post_pipeline 阶段末尾调用此函数。

约束：
- 纯函数：无 I/O、无 DB、无网络
- 入参仅为 verdict 列表 + 降级信号 + 异常快照 + 无 LLM 输出标志
- 产出 (FailureState, failure_reason) 二元组
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .failure_state import FailureState

if TYPE_CHECKING:
    from app.core.ai.gates.base import GateVerdict


class FailureStateDeterminer:
    """Stateless determiner; all methods are pure functions."""

    @staticmethod
    def determine(
        verdicts: list[GateVerdict],
        degraded_signals: set[str] | None = None,
        task_exception: Exception | None = None,
        no_llm_output: bool = False,
    ) -> tuple[FailureState, str]:
        """Apply the six-step first-match decision tree.

        Steps (failure_contract.md §2.3):
            1. Task-level exception -> failed
            2. No LLM output (timeout + fallback failure) -> inconclusive
            3. Any Gate verdict=reject -> needs_review
            4. Cross-group conflict / gate_rejected=true -> needs_review
            5. Used degraded path -> degraded
            6. All Gate pass or advisory warn -> completed
        """
        if degraded_signals is None:
            degraded_signals = set()

        # Step 1
        if task_exception is not None:
            return (
                FailureState.FAILED,
                f"Task-level exception: {type(task_exception).__name__}: {task_exception}",
            )

        # Step 2
        if no_llm_output:
            return (
                FailureState.INCONCLUSIVE,
                "No LLM output available (timeout + fallback failure or empty extraction)",
            )

        # Step 3
        reject_verdicts = [v for v in verdicts if v.verdict == "reject"]
        if reject_verdicts:
            names = ", ".join(v.gate_name for v in reject_verdicts)
            return (
                FailureState.NEEDS_REVIEW,
                f"Gate reject triggered by: {names}",
            )

        # Step 4
        conflict_verdicts = [
            v for v in verdicts
            if v.verdict == "enforce_patch"
            and v.suggested_patch
            and v.suggested_patch.get("gate_rejected") is True
        ]
        if conflict_verdicts:
            names = ", ".join(v.gate_name for v in conflict_verdicts)
            return (
                FailureState.NEEDS_REVIEW,
                f"Cross-group conflict or gate_rejected by: {names}",
            )

        # Step 5
        if degraded_signals:
            reasons = ", ".join(sorted(degraded_signals))
            return (
                FailureState.DEGRADED,
                f"Degraded path used: {reasons}",
            )

        # Step 6
        return (
            FailureState.COMPLETED,
            "All gates passed",
        )
