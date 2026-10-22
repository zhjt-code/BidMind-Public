"""LLM 逻辑调用尝试预算 (P0-B)

职责: 定义单次逻辑调用允许的最大物理尝试数与端到端 deadline
不做: 不执行重试、不选择通道、不感知业务语义

背景: SDK 隐式重试已关闭(max_retries=0), Dispatcher 原上限为"全部通道数"
(review/vision 25、judge 16), 叠加业务层校验/批后重试后单次逻辑调用可能
放大为数十次物理请求。本模块提供统一预算, dispatcher 消费:
- max_attempts: 跨通道尝试总数上限
- deadline_seconds: 从进入调度(含排队)起的端到端时限
- timeout_max_attempts: 超时类失败的独立物理尝试上限(含首发)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings


class LlmDeadlineExceededError(TimeoutError):
    """逻辑调用超出端到端 deadline 的显式信号(outcome=timeout)"""


@dataclass(frozen=True)
class AttemptBudget:
    """单次逻辑调用的尝试预算"""

    max_attempts: int
    deadline_seconds: Optional[float]
    timeout_max_attempts: int = 2

    def clamp_attempts(self, channel_count: int) -> int:
        """预算与通道数取小; 预算关闭时保持通道数(现状语义)"""
        if self.max_attempts <= 0:
            return channel_count
        return min(channel_count, self.max_attempts)


@dataclass
class DeadlineClock:
    """从调度入口开始计时的 deadline 时钟"""

    deadline_seconds: Optional[float]
    started_at: float

    @classmethod
    def start(cls, deadline_seconds: Optional[float]) -> "DeadlineClock":
        return cls(deadline_seconds=deadline_seconds, started_at=time.monotonic())

    def remaining(self) -> Optional[float]:
        """剩余秒数; 无 deadline 时返回 None"""
        if self.deadline_seconds is None or self.deadline_seconds <= 0:
            return None
        return self.deadline_seconds - (time.monotonic() - self.started_at)

    def expired(self) -> bool:
        remaining = self.remaining()
        return remaining is not None and remaining <= 0

    def raise_if_expired(self, label: str) -> None:
        if self.expired():
            raise LlmDeadlineExceededError(
                f"LLM 逻辑调用超出端到端时限 {self.deadline_seconds:.0f}s: {label}"
            )


def resolve_budget() -> AttemptBudget:
    """读取全局预算配置; 总闸关闭时退回现状(通道数上限、无 deadline)。

 timeout_max_attempts 独立于总闸, 始终生效: 超时后同组盲试成功率极低,
 必须限制墙钟放大。
 """
    timeout_max = int(getattr(settings, "LLM_TIMEOUT_MAX_ATTEMPTS", 2))
    if not getattr(settings, "LLM_ATTEMPT_BUDGET_ENABLED", True):
        return AttemptBudget(
            max_attempts=0,
            deadline_seconds=None,
            timeout_max_attempts=timeout_max,
        )
    return AttemptBudget(
        max_attempts=int(getattr(settings, "LLM_MAX_ATTEMPTS_DEFAULT", 6)),
        deadline_seconds=float(getattr(settings, "LLM_CALL_DEADLINE_SECONDS", 900)),
        timeout_max_attempts=timeout_max,
    )
