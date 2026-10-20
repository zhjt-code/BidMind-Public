"""Gate 抽象基类、GateContext 与 GateVerdict。

签名严格按 docs/evidence_gate_design.md §3.1 定义。
子类实现须为纯函数：给定相同 Context 必须产出相同 Verdict，
严禁修改 Context 或调用 DB / 网络。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any, Literal

Severity = Literal["advisory", "enforce", "reject", "off"]
Stage = Literal["pre_llm", "post_llm", "post_pipeline"]
Flow = Literal["investigate", "collusion", "writer", "all"]


@dataclass(frozen=True)
class GateContext:
    """Gate 的只读输入上下文。"""

    flow: Flow
    stage: Stage
    target: Any
    llm_output: Any | None = None
    evidence: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


@dataclass(frozen=True)
class GateVerdict:
    """Gate 的不可变结果。"""

    gate_name: str
    verdict: Literal["pass", "warn", "reject", "enforce_patch"]
    severity: Severity
    reason: str
    suggested_patch: dict[str, Any] | None = None
    confidence: float = 1.0
    trace: dict[str, Any] | None = None


class Gate(ABC):
    """单道 Gate 的抽象基类。

    子类须为纯函数：给定相同 Context 产出相同 Verdict。
    """

    gate_name: str = ""
    flow: Flow = "all"
    stage: Stage = "post_llm"
    order: int = 100
    default_severity: Severity = "advisory"
    description: str = ""

    @abstractmethod
    def applies(self, ctx: GateContext) -> bool:
        """是否对本 Context 适用。返回 False 则跳过执行。"""

    @abstractmethod
    def evaluate(self, ctx: GateContext) -> GateVerdict:
        """执行判定。必须是纯函数。"""


# Re-export replace for pipeline use
gate_verdict_replace = replace
