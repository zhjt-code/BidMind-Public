"""B2.4 — JSON 修复 / 空返回等降级信号的协程安全传递。

使用 contextvars.ContextVar 保证 asyncio per-task 隔离，
与 prompt_registry.current_node_key 采用相同机制。

本批次(B2)仅负责"信号产生"，B3.9 由接棒人在 Pipeline 末尾
读取 ContextVar 快照并喂给 FailureStateDeterminer。
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DegradationSignal:
    """单次 LLM 调用中检测到的降级事件。"""

    source: str
    """触发来源: json_repair_truncated | json_repair_before_error | empty_return"""

    flow: str
    """业务流程: review | collusion | writer | extractor | unknown"""

    repair_type: str
    """修复策略: truncated_array | before_error | control_chars | trailing_comma"""

    original_length: int
    """原始 JSON 字符串长度"""

    recovered_elements: int
    """抢救出的元素数（list 场景）"""


current_degradation_signals: ContextVar[Optional[list[DegradationSignal]]] = ContextVar(
    "current_degradation_signals",
    default=None,
)


def push_signal(signal: DegradationSignal) -> None:
    """追加一条降级信号到当前协程的 ContextVar 缓冲区。"""
    buf = current_degradation_signals.get()
    if buf is None:
        buf = []
        current_degradation_signals.set(buf)
    buf.append(signal)


def drain_signals() -> list[DegradationSignal]:
    """取出并清空当前协程的全部降级信号（B3.9 消费用）。"""
    buf = current_degradation_signals.get()
    current_degradation_signals.set(None)
    return buf or []
