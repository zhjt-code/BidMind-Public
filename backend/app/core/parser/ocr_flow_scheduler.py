"""OCR flow-level fair concurrency scheduler."""

from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _FlowState:
    """Internal flow accounting state."""

    order: int
    inflight: int = 0
    class_inflight: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    waiting: int = 0
    class_waiting: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    updated_at: float = field(default_factory=time.monotonic)


class OcrFlowScheduler:
    """
    职责: 按流程为 OCR HTTP 请求分配公平并发槽位。
    不做: 不选择 OCR 实例、不调整 AIMD 容量、不发起 HTTP 调用。
    """

    _PRIMARY_CLASS = "per_image"

    def __init__(self, contended_max_share_ratio: float = 0.6) -> None:
        self._ratio = min(max(float(contended_max_share_ratio), 0.1), 0.9)
        self._flows: dict[str, _FlowState] = {}
        self._order_counter = 0
        self._capacity = 1
        self._condition = asyncio.Condition()

    async def acquire(
        self,
        flow_id: str,
        request_class: str,
        *,
        capacity: int,
    ) -> None:
        """Acquire one OCR flow slot."""

        normalized_flow = self._normalize_flow_id(flow_id)
        normalized_class = self._normalize_request_class(request_class)
        async with self._condition:
            new_capacity = max(int(capacity), 1)
            if new_capacity != self._capacity:
                self._capacity = new_capacity
                self._condition.notify_all()
            flow = self._get_or_create_flow(normalized_flow)
            flow.waiting += 1
            flow.class_waiting[normalized_class] += 1
            flow.updated_at = time.monotonic()
            try:
                while not self._can_acquire(normalized_flow, normalized_class):
                    await self._condition.wait()
                flow.inflight += 1
                flow.class_inflight[normalized_class] += 1
                flow.updated_at = time.monotonic()
            finally:
                flow.waiting = max(0, flow.waiting - 1)
                flow.class_waiting[normalized_class] = max(
                    0,
                    flow.class_waiting[normalized_class] - 1,
                )
                self._cleanup_idle_locked(normalized_flow)
                self._condition.notify_all()

    async def release(self, flow_id: str, request_class: str) -> None:
        """Release one OCR flow slot."""

        normalized_flow = self._normalize_flow_id(flow_id)
        normalized_class = self._normalize_request_class(request_class)
        async with self._condition:
            flow = self._flows.get(normalized_flow)
            if flow is not None:
                flow.inflight = max(0, flow.inflight - 1)
                flow.class_inflight[normalized_class] = max(
                    0,
                    flow.class_inflight[normalized_class] - 1,
                )
                flow.updated_at = time.monotonic()
                self._cleanup_idle_locked(normalized_flow)
            self._condition.notify_all()

    @asynccontextmanager
    async def slot(self, flow_id: str, request_class: str, *, capacity: int):
        """Context manager wrapper around acquire/release."""

        await self.acquire(flow_id, request_class, capacity=capacity)
        try:
            yield
        finally:
            await self.release(flow_id, request_class)

    def get_stats(self) -> dict:
        """Return scheduler state for tests and observability."""

        return {
            "capacity": self._capacity,
            "flows": {
                flow_id: {
                    "inflight": flow.inflight,
                    "waiting": flow.waiting,
                    "order": flow.order,
                    "class_inflight": dict(flow.class_inflight),
                    "class_waiting": dict(flow.class_waiting),
                }
                for flow_id, flow in self._flows.items()
            },
        }

    def _get_or_create_flow(self, flow_id: str) -> _FlowState:
        flow = self._flows.get(flow_id)
        if flow is None:
            flow = _FlowState(order=self._order_counter)
            self._order_counter += 1
            self._flows[flow_id] = flow
        return flow

    def _can_acquire(self, flow_id: str, request_class: str) -> bool:
        if self._global_inflight() >= self._capacity:
            return False
        if not self._class_priority_allows(flow_id, request_class):
            return False

        quotas = self._calculate_quotas()
        flow = self._flows[flow_id]
        quota = quotas.get(flow_id, 1)
        if flow.inflight < quota:
            return True
        return not self._has_waiting_flow_under_quota(flow_id, quotas)

    def _class_priority_allows(self, flow_id: str, request_class: str) -> bool:
        if request_class == self._PRIMARY_CLASS:
            return True
        flow = self._flows[flow_id]
        return flow.class_waiting[self._PRIMARY_CLASS] <= 0

    def _has_waiting_flow_under_quota(
        self,
        current_flow_id: str,
        quotas: dict[str, int],
    ) -> bool:
        for flow_id, flow in self._flows.items():
            if flow_id == current_flow_id:
                continue
            if flow.waiting > 0 and flow.inflight < quotas.get(flow_id, 1):
                return True
        return False

    def _calculate_quotas(self) -> dict[str, int]:
        active = self._active_flow_ids()
        if not active:
            return {}
        if len(active) == 1:
            return {active[0]: self._capacity}
        if len(active) == 2:
            return self._calculate_two_flow_quotas(active)
        return self._calculate_even_quotas(active)

    def _calculate_two_flow_quotas(self, active: list[str]) -> dict[str, int]:
        first, second = active
        if self._capacity <= 1:
            return {first: 1, second: 1}
        first_quota = math.floor(self._capacity * self._ratio)
        first_quota = max(1, min(self._capacity - 1, first_quota))
        return {
            first: first_quota,
            second: max(1, self._capacity - first_quota),
        }

    def _calculate_even_quotas(self, active: list[str]) -> dict[str, int]:
        base = max(1, self._capacity // len(active))
        remainder = max(0, self._capacity - base * len(active))
        quotas: dict[str, int] = {}
        for index, flow_id in enumerate(active):
            quotas[flow_id] = base + (1 if index < remainder else 0)
        return quotas

    def _active_flow_ids(self) -> list[str]:
        return [
            flow_id
            for flow_id, _flow in sorted(
                self._flows.items(),
                key=lambda item: item[1].order,
            )
        ]

    def _global_inflight(self) -> int:
        return sum(flow.inflight for flow in self._flows.values())

    def _cleanup_idle_locked(self, flow_id: str) -> None:
        flow = self._flows.get(flow_id)
        if flow is None:
            return
        if flow.inflight == 0 and flow.waiting == 0:
            del self._flows[flow_id]

    @staticmethod
    def _normalize_flow_id(flow_id: str) -> str:
        normalized = (flow_id or "default").strip()
        return normalized or "default"

    @staticmethod
    def _normalize_request_class(request_class: str) -> str:
        normalized = (request_class or "per_image").strip()
        return normalized or "per_image"
