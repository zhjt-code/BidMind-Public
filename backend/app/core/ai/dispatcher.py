"""AI 请求统一调度器

管理多个 API 通道（同厂商多 Key + 跨厂商），按请求级别动态分配最优通道。
实现与 CloudClient 完全相同的方法签名，上层业务代码零修改。

调度策略:
- 最小并发选择: 优先选择 active_requests 最少的通道
- 熔断恢复: 连续失败时自动熔断，到期后半开探测
- 全局背压: 内置 Semaphore 限制总并发数

通道来源:
- 每个 ModelConfigItem 的每个可用 Key 创建一个独立 CloudClient 作为通道
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlparse

from loguru import logger
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from pydantic import BaseModel

from app.core.ai.cloud_client import (
    CloudClient,
    LlmContentValidationError,
    UnsupportedResponseFormatError,
)
from app.core.ai.llm_errors import EmptyLLMResponseError
from app.core.ai.rate_limit_signal import (
    RateLimitKind,
    classify_rate_limit_error,
)
from app.core.observability.metrics import fallback_trigger_total

_RETRYABLE_BAD_REQUEST_PATTERNS = (
    re.compile(r"invalid temperature", re.IGNORECASE),
    re.compile(r"only\s+1\s+is\s+allowed", re.IGNORECASE),
    re.compile(r"unsupported parameter", re.IGNORECASE),
    re.compile(r"parameter .* not allowed", re.IGNORECASE),
)


if TYPE_CHECKING:
    from app.core.ai.dispatcher_concurrency import DispatcherConcurrencyController


# ==================== 数据结构 ====================

@dataclass
class ChannelState:
    """单个 API 通道的运行时状态"""
    client: CloudClient
    config_id: str
    provider: str
    model_group: str = ""
    key_index: int = 0
    priority: int = 100

    active_requests: int = 0
    total_requests: int = 0
    total_errors: int = 0
    total_rate_limits: int = 0
    total_quota_exhausted: int = 0
    total_latency_ms: float = 0

    circuit_open: bool = False
    circuit_open_until: float = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0
    last_rate_limit_kind: str = ""

    half_open: bool = False
    half_open_budget: int = 1
    _last_circuit_duration: int = 30

    probe_latency_ms: float = 0
    probe_success: bool = True
    # P0-B: 最近一次真实健康探测结果(kind/ok/latency_ms/error/probed_at)
    last_probe: Optional[dict] = None

    @property
    def avg_latency_ms(self) -> float:
        if self.total_requests == 0:
            return 0
        return self.total_latency_ms / self.total_requests

    def is_available(self) -> bool:
        if not self.circuit_open:
            return True
        if time.monotonic() >= self.circuit_open_until:
            return True
        return False

    def is_selectable(self) -> bool:
        """半开通道仅在活跃请求低于探测预算时可被选中"""
        if not self.is_available():
            return False
        if self.cooldown_until > 0 and time.monotonic() < self.cooldown_until:
            return False
        if self.half_open and self.active_requests >= self.half_open_budget:
            return False
        return True

    def record_success(self, latency_ms: float) -> None:
        self.total_requests += 1
        self.total_latency_ms += latency_ms
        self.consecutive_failures = 0
        if self.half_open:
            self.half_open = False
            self._last_circuit_duration = 30
            logger.info(
                f"通道半开探测成功，完全恢复: "
                f"config={self.config_id}#{self.key_index}"
            )

    def record_failure(self, is_rate_limit: bool = False) -> None:
        self.total_requests += 1
        self.total_errors += 1
        self.consecutive_failures += 1
        if is_rate_limit:
            self.total_rate_limits += 1
        if self.half_open:
            self._reopen_circuit()
        else:
            self._maybe_circuit_break()

    def _maybe_circuit_break(self) -> None:
        if self.consecutive_failures >= 10:
            duration = 300
        elif self.consecutive_failures >= 5:
            duration = 60
        elif self.consecutive_failures >= 3:
            duration = 30
        else:
            return

        self.circuit_open = True
        self.circuit_open_until = time.monotonic() + duration
        self._last_circuit_duration = duration
        logger.warning(
            f"通道熔断: config={self.config_id}#{self.key_index} "
            f"provider={self.provider}, 连续失败={self.consecutive_failures}, "
            f"熔断{duration}s"
        )

    def _reopen_circuit(self) -> None:
        """半开探测失败，以翻倍时长重新熔断"""
        duration = min(self._last_circuit_duration * 2, 600)
        self.circuit_open = True
        self.half_open = False
        self.circuit_open_until = time.monotonic() + duration
        self._last_circuit_duration = duration
        logger.warning(
            f"通道半开探测失败，重新熔断: config={self.config_id}#{self.key_index} "
            f"provider={self.provider}, 熔断{duration}s"
        )

    def to_status_dict(self) -> dict:
        return {
            "config_id": self.config_id,
            "provider": self.provider,
            "model_group": self.model_group,
            "key_index": self.key_index,
            "priority": self.priority,
            "active_requests": self.active_requests,
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "total_rate_limits": self.total_rate_limits,
            "total_quota_exhausted": self.total_quota_exhausted,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "circuit_open": self.circuit_open,
            "cooldown_remaining_sec": round(max(self.cooldown_until - time.monotonic(), 0), 1),
            "last_rate_limit_kind": self.last_rate_limit_kind,
            "half_open": self.half_open,
            "consecutive_failures": self.consecutive_failures,
            "probe_latency_ms": round(self.probe_latency_ms, 1),
            "probe_success": self.probe_success,
            "last_probe": self.last_probe,
        }


@dataclass
class DispatcherMetrics:
    """调度器全局指标"""
    total_requests: int = 0
    total_errors: int = 0
    total_rate_limits: int = 0
    current_concurrent: int = 0
    peak_concurrent: int = 0
    avg_latency_ms: float = 0
    _total_latency_ms: float = 0

    def record_request(self, latency_ms: float, success: bool, is_rate_limit: bool = False) -> None:
        self.total_requests += 1
        self._total_latency_ms += latency_ms
        self.avg_latency_ms = self._total_latency_ms / self.total_requests
        if not success:
            self.total_errors += 1
        if is_rate_limit:
            self.total_rate_limits += 1

    def to_dict(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "total_rate_limits": self.total_rate_limits,
            "current_concurrent": self.current_concurrent,
            "peak_concurrent": self.peak_concurrent,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
        }


# ==================== Provider 识别 ====================

_DOMAIN_PROVIDER_MAP = {
    "api.deepseek.com": "deepseek",
    "dashscope.aliyuncs.com": "qwen",
    "open.bigmodel.cn": "zhipu",
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "api.moonshot.cn": "moonshot",
    "api.siliconflow.cn": "siliconflow",
    "ark.cn-beijing.volces.com": "volcengine",
    "api.openrouter.ai": "openrouter",
    "openrouter.ai": "openrouter",
    # P0-A: 补齐当前运行端点, 避免 provider 标签退化为 unknown
    "aihubmix.com": "aihubmix",
    "inferera.com": "aihubmix",
    "tokenhub.tencentmaas.com": "tencent",
    "qianfan.baidubce.com": "baidu",
}


def detect_provider(api_base: str) -> str:
    try:
        parsed = urlparse(api_base)
        hostname = parsed.hostname or ""
        for domain, name in _DOMAIN_PROVIDER_MAP.items():
            if domain in hostname:
                return name
        if hostname in ("localhost", "127.0.0.1"):
            return "local"
    except Exception:
        pass
    return "unknown"


# ==================== 异常 ====================

class NoAvailableChannelError(Exception):
    """所有通道均不可用"""
    pass


# ==================== 核心调度器 ====================

class AIDispatcher:
    """AI 请求统一调度器
    
    实现与 CloudClient 完全相同的方法签名（chat / chat_json / 
    chat_with_images / chat_with_validation / health_check），
    上层业务代码可无缝替换。

    并发控制: 内置 AIMD 自适应并发控制器，根据 429/延迟/错误率
    动态调整全局并发上限，替代原有固定 Semaphore。
    """

    def __init__(self, purpose: str):
        self._purpose = purpose
        self._channels: list[ChannelState] = []
        self._lock = asyncio.Lock()
        self._concurrency_controller: Optional["DispatcherConcurrencyController"] = None
        self.metrics = DispatcherMetrics()
        self._project_bindings: dict[int, tuple[str, float]] = {}
        self._binding_lock = asyncio.Lock()
        self._dispatch_count: int = 0

    @property
    def channel_count(self) -> int:
        return len(self._channels)

    def register_channel(
        self,
        client: CloudClient,
        config_id: str,
        key_index: int = 0,
        model_group: str = "",
        priority: int = 100,
    ) -> None:
        provider = detect_provider(client.base_url)
        ch = ChannelState(
            client=client,
            config_id=config_id,
            provider=provider,
            model_group=model_group or config_id,
            key_index=key_index,
            priority=priority,
        )
        self._channels.append(ch)
        self._concurrency_controller = None
        logger.debug(
            f"Dispatcher[{self._purpose}] 注册通道: "
            f"config={config_id}#{key_index} provider={provider} "
            f"model={client.model_name}"
        )

    def _get_semaphore(self) -> asyncio.Semaphore:
        return self._get_controller().semaphore

    def _get_controller(self) -> "DispatcherConcurrencyController":
        if self._concurrency_controller is None:
            from app.core.ai.dispatcher_concurrency import (
                DispatcherConcurrencyController,
            )
            from app.core.config import settings

            ch_count = self.get_primary_group_channel_count()
            if self._purpose == "vision":
                initial_per_channel = settings.VISION_CONCURRENCY_CLOUD_INITIAL
                min_per_channel = settings.VISION_CONCURRENCY_CLOUD_MIN
                max_per_channel = settings.VISION_CONCURRENCY_CLOUD_MAX
                window_size = settings.VISION_CONCURRENCY_WINDOW_SIZE
                increase_cooldown = settings.VISION_CONCURRENCY_INCREASE_COOLDOWN
                latency_ceiling = settings.VISION_CONCURRENCY_LATENCY_CEILING
            else:
                initial_per_channel = settings.REVIEW_CONCURRENCY_CLOUD_INITIAL
                min_per_channel = settings.REVIEW_CONCURRENCY_CLOUD_MIN
                max_per_channel = settings.REVIEW_CONCURRENCY_CLOUD_MAX
                window_size = settings.REVIEW_CONCURRENCY_WINDOW_SIZE
                increase_cooldown = settings.REVIEW_CONCURRENCY_INCREASE_COOLDOWN
                latency_ceiling = settings.REVIEW_CONCURRENCY_LATENCY_CEILING
            self._concurrency_controller = DispatcherConcurrencyController(
                channel_count=ch_count,
                initial_per_channel=initial_per_channel,
                min_per_channel=min_per_channel,
                max_per_channel=max_per_channel,
                window_size=window_size,
                increase_cooldown=increase_cooldown,
                latency_ceiling=latency_ceiling,
                purpose=self._purpose,
            )
        return self._concurrency_controller

    def _get_model_groups(self) -> dict[str, list[ChannelState]]:
        groups: dict[str, list[ChannelState]] = {}
        for ch in self._channels:
            groups.setdefault(ch.model_group, []).append(ch)
        return groups

    def _group_model_name(self, group_id: str) -> str:
        """从模型组ID解析出人类可读的模型名称"""
        groups = self._get_model_groups()
        channels = groups.get(group_id, [])
        return channels[0].client.model_name if channels else "unknown"

    async def _select_best_group(self) -> str:
        """三阶段选择最优模型组

        阶段1: 所有组都空闲 -> 选 priority 最小的（默认首选）
        阶段2: 部分组已被项目占用 -> 在未占用的可用组中按 priority 选
        阶段3: 所有可用组都已占用 -> 选 load_ratio 最低的
        """
        groups = self._get_model_groups()
        bound_groups = {grp for grp, _ in self._project_bindings.values()}

        candidates: list[tuple[str, int, float]] = []
        for gid, channels in groups.items():
            available = [ch for ch in channels if ch.is_available()]
            if not available:
                continue
            group_priority = min(ch.priority for ch in available)
            load_ratio = (
                sum(ch.active_requests for ch in available)
                / max(len(available), 1)
            )
            candidates.append((gid, group_priority, load_ratio))

        if not candidates:
            return next(iter(groups), "")

        idle = [(gid, p, lr) for gid, p, lr in candidates if gid not in bound_groups]
        if idle:
            idle.sort(key=lambda x: x[1])
            return idle[0][0]

        candidates.sort(key=lambda x: (x[1], x[2]))
        return candidates[0][0]

    async def _resolve_group_for_project(self, project_id: Optional[int]) -> Optional[str]:
        """解析项目的模型组绑定

        绑定组全部熔断时自动解绑并降级到其他健康组,
        确保业务流程不因单模型组故障而中断。
        """
        if project_id is None:
            return None
        async with self._binding_lock:
            if project_id in self._project_bindings:
                group, _ = self._project_bindings[project_id]
                groups = self._get_model_groups()
                if group in groups:
                    channels = groups[group]
                    if any(ch.is_available() for ch in channels):
                        self._project_bindings[project_id] = (group, time.monotonic())
                        return group
                    del self._project_bindings[project_id]
                    model_name = self._group_model_name(group)
                    logger.warning(
                        f"Dispatcher[{self._purpose}] 项目{project_id}"
                        f"绑定组{group}(model={model_name})全部熔断，解绑重选"
                    )
                else:
                    del self._project_bindings[project_id]
            group = await self._select_best_group()
            if group:
                self._project_bindings[project_id] = (group, time.monotonic())
                model_name = self._group_model_name(group)
                logger.info(
                    f"Dispatcher[{self._purpose}] 项目{project_id}"
                    f"绑定模型组: {group} (model={model_name})"
                )
            return group

    async def _select_channel(
        self,
        group: Optional[str] = None,
        exclude: Optional[set[int]] = None,
        avoid_groups: Optional[set[str]] = None,
        avoid_models: Optional[set[str]] = None,
        max_wait_seconds: Optional[float] = None,
    ) -> Optional[ChannelState]:
        """选择最优通道

        Args:
            group: 限制在指定模型组内选择
            exclude: 需要排除的通道 id(channel) 集合（用于重试环）
            avoid_groups: 软偏好：尽量避开这些模型组
                （用于 UnsupportedResponseFormatError 发生后，强制跨组而非再磨同组 key）。
                若避开后仍有健康候选则走其他组；否则退回原候选池，不丢失容灾。
            avoid_models: 软偏好：尽量避开这些 model_name
                （用于超时后优先换不同模型；无替代时 fail-open 回退原池）。
            max_wait_seconds: P0-B 端到端 deadline 剩余时间; 全熔断等待
                半开探测的睡眠不得超过该值。

        Returns:
            最优通道，若所有通道均不可用且排除后无候选则返回 None

        跨组降级: 当指定 group 内所有通道熔断时，自动降级到其他健康组，
        仅当全局所有通道都不可用时才等待半开探测。
        """
        avoid_groups = avoid_groups or set()
        avoid_models = avoid_models or set()

        async with self._lock:
            pool = self._channels
            force_cross_group = False
            if group and group in avoid_groups:
                # 项目绑定组发生内容校验失败/能力不兼容后，不能继续在
                # 绑定组内磨其它 key；只要存在非 avoid 组候选，就立即跨组。
                cross_pool = [
                    ch for ch in self._channels
                    if ch.model_group not in avoid_groups
                ]
                if cross_pool:
                    pool = cross_pool
                    force_cross_group = True
                else:
                    pool = [ch for ch in self._channels if ch.model_group == group]
                    logger.warning(
                        f"Dispatcher[{self._purpose}] 组{group}已标记跳过，"
                        f"但无其它可用模型组，回退同组剩余通道"
                    )
            elif group:
                pool = [ch for ch in self._channels if ch.model_group == group]
                if not pool:
                    pool = self._channels

            if exclude:
                filtered_pool = [ch for ch in pool if id(ch) not in exclude]
                if filtered_pool:
                    pool = filtered_pool
                elif group:
                    pool = []
                else:
                    return None

            selectable = [ch for ch in pool if ch.is_selectable()]

            if not selectable:
                available = [ch for ch in pool if ch.is_available()]
                if available:
                    if all(
                        ch.cooldown_until > 0 and time.monotonic() < ch.cooldown_until
                        for ch in available
                    ):
                        logger.warning(
                            f"Dispatcher[{self._purpose}] 所有可用通道均处于冷却期，"
                            f"为避免死等将旁路冷却尝试最小负载通道"
                        )
                    selectable = available

            # avoid_groups 软偏好：优先在非 avoid 的同池候选里选
            if selectable and avoid_groups:
                preferred = [ch for ch in selectable if ch.model_group not in avoid_groups]
                if preferred:
                    selectable = preferred

            # avoid_models 软偏好：优先避开已超时的 model_name（无替代则 fail-open）
            if selectable and avoid_models:
                preferred = [
                    ch for ch in selectable if ch.client.model_name not in avoid_models
                ]
                if preferred:
                    selectable = preferred

            if not selectable and group:
                all_pool = [ch for ch in self._channels if id(ch) not in (exclude or set())]
                selectable = [ch for ch in all_pool if ch.is_selectable()]
                if not selectable:
                    available = [ch for ch in all_pool if ch.is_available()]
                    if available:
                        selectable = available
                # 跨组降级阶段，优先选不在 avoid_groups 里的组
                if selectable and avoid_groups:
                    preferred = [ch for ch in selectable if ch.model_group not in avoid_groups]
                    if preferred:
                        selectable = preferred
                # 跨组降级后同样应用 avoid_models 软偏好
                if selectable and avoid_models:
                    preferred = [
                        ch for ch in selectable if ch.client.model_name not in avoid_models
                    ]
                    if preferred:
                        selectable = preferred
                if selectable:
                    chosen = min(selectable, key=lambda c: (c.priority, c.active_requests, c.avg_latency_ms))
                    fallback_trigger_total.labels(trigger_type="cross_group_degradation", flow=self._purpose).inc()
                    logger.warning(
                        f"Dispatcher[{self._purpose}] 组{group}本轮可用key已全部尝试，"
                        f"降级到{chosen.model_group}(model={chosen.client.model_name})"
                    )
                    return chosen

            if not selectable:
                fallback_pool = pool if pool else self._channels
                earliest = min(fallback_pool, key=lambda c: c.circuit_open_until)
                wait = max(earliest.circuit_open_until - time.monotonic(), 0.1)
                # P0-B: 全熔断等待受端到端 deadline 约束
                if max_wait_seconds is not None:
                    wait = min(wait, max(max_wait_seconds, 0.1))
                fallback_trigger_total.labels(trigger_type="all_circuit_open_wait", flow=self._purpose).inc()
                logger.warning(
                    f"Dispatcher[{self._purpose}] 所有通道熔断，"
                    f"等待 {wait:.1f}s 后半开探测"
                )
                await asyncio.sleep(wait)
                earliest.circuit_open = False
                earliest.half_open = True
                earliest.half_open_budget = 1
                return earliest

            if force_cross_group:
                chosen = min(
                    selectable,
                    key=lambda c: (c.priority, c.active_requests, c.avg_latency_ms),
                )
                fallback_trigger_total.labels(
                    trigger_type="cross_group_degradation", flow=self._purpose,
                ).inc()
                logger.warning(
                    f"Dispatcher[{self._purpose}] 组{group}已标记跳过，"
                    f"立即降级到{chosen.model_group}(model={chosen.client.model_name})"
                )
                return chosen

            return min(
                selectable,
                key=lambda c: (c.priority, c.active_requests, c.avg_latency_ms),
            )

    async def _maybe_cleanup_bindings(self) -> None:
        """懒清理过期绑定 (每200次调度检查一次,持锁执行)"""
        self._dispatch_count += 1
        if self._dispatch_count % 200 != 0:
            return
        async with self._binding_lock:
            now = time.monotonic()
            expired = [pid for pid, (_, ts) in self._project_bindings.items() if now - ts > 7200]
            for pid in expired:
                del self._project_bindings[pid]
            if expired:
                logger.debug(f"Dispatcher[{self._purpose}] 清理{len(expired)}个过期绑定")

    async def release_project_binding(self, project_id: int) -> None:
        """主动释放项目的模型组绑定（任务完成/失败时调用）"""
        async with self._binding_lock:
            removed = self._project_bindings.pop(project_id, None)
            if removed:
                logger.info(
                    f"Dispatcher[{self._purpose}] 项目{project_id}已解绑模型组: {removed[0]}"
                )

    async def _dispatch(self, method: str, project_id: Optional[int] = None, **kwargs: Any) -> Any:
        from app.core.ai.attempt_budget import (
            DeadlineClock,
            LlmDeadlineExceededError,
            resolve_budget,
        )
        from app.core.ai.attempt_recorder import get_attempt_recorder
        from app.core.ai.dispatcher_concurrency import DispatcherRequestMetric
        from app.core.ai.project_fair_queue import fair_request_slot

        _budget = resolve_budget()
        _clock = DeadlineClock.start(_budget.deadline_seconds)
        _queue_t0 = time.monotonic()
        # 裸 Semaphore 是严格 FIFO, 一个项目的扇出会把其它项目的请求压在队尾饿死;
        # 改由公平队列按项目轮转发放, 容量真源仍是 AIMD 控制器的 Semaphore。
        sem = fair_request_slot(
            self._get_semaphore(), purpose=self._purpose, project_id=project_id
        )
        # P0-B: 排队等待纳入端到端 deadline, 防止全局背压下无限期悬挂
        _sem_wait = _clock.remaining()
        if _sem_wait is not None:
            try:
                await asyncio.wait_for(sem.acquire(), timeout=max(_sem_wait, 0.1))
            except asyncio.TimeoutError:
                raise LlmDeadlineExceededError(
                    f"Dispatcher[{self._purpose}] 排队等待超出端到端时限 "
                    f"{_budget.deadline_seconds:.0f}s"
                ) from None
        else:
            await sem.acquire()
        try:
            self.metrics.current_concurrent += 1
            if self.metrics.current_concurrent > self.metrics.peak_concurrent:
                self.metrics.peak_concurrent = self.metrics.current_concurrent

            _attempt_token = get_attempt_recorder().begin_dispatch(
                purpose=self._purpose, project_id=project_id, method=method,
                queue_wait_ms=int((time.monotonic() - _queue_t0) * 1000),
            )
            try:
                await self._maybe_cleanup_bindings()
                group = await self._resolve_group_for_project(project_id)

                # P0-B: 尝试次数受统一预算约束(默认 6), 不再默认磨穿全部通道
                max_attempts = _budget.clamp_attempts(len(self._channels))
                tried: set[int] = set()
                # 复核降级链路加固：出现 UnsupportedResponseFormatError 时，
                # 同组剩余 key 再试也是同样结果，应立刻跨组。
                avoid_groups: set[str] = set()
                # P1-2: 超时后软避开同 model_name，无替代时 fail-open 回原池
                avoid_models: set[str] = set()
                from app.core.ai.timeout_avoidance import (
                    current_avoids as _sticky_avoids,
                )
                from app.core.ai.timeout_avoidance import (
                    record_timeout as _record_sticky_timeout,
                )
                _sticky_groups, _sticky_models = _sticky_avoids(self._purpose)
                avoid_groups.update(_sticky_groups)
                avoid_models.update(_sticky_models)
                if _sticky_groups or _sticky_models:
                    logger.warning(
                        f"Dispatcher[{self._purpose}] 起步即避开刚超时模型: "
                        f"avoided={sorted(_sticky_groups)}, "
                        f"avoided_models={sorted(_sticky_models)}"
                    )
                # 超时类失败独立计数; 同组盲试成功率极低, 达上限即放弃
                _timeout_attempts = 0
                # P0-B: 同一模型组累计 2 次鉴权失败即跨组(密钥失效不磨剩余 key)
                _auth_group_failures: dict[str, int] = {}
                last_exc: Optional[Exception] = None
                dispatch_kwargs = dict(kwargs)
                # P0-D: 结构化降级策略——False 时禁止最终回退普通 chat(fail-closed),
                # 该参数为 dispatcher 语义, 不下发给 CloudClient 方法。
                _allow_plain_fallback = bool(
                    dispatch_kwargs.pop("allow_plain_fallback", True)
                )
                if (
                    method in ("chat_json", "chat_with_validation")
                    and dispatch_kwargs.get("json_schema") is not None
                ):
                    # 让 response_format 能力不兼容信号先交给 dispatcher 跨组处理；
                    # 所有候选都不支持时，再由最终兜底回到普通 chat。
                    # chat_with_validation 同样是严格 schema 任务，
                    # 不透传会在不支持约束解码的模型上静默退化为自由文本。
                    dispatch_kwargs.setdefault("propagate_unsupported", True)

                # P0-C: 能力路由——排除 effective 不满足本次调用要求的通道
                # (视觉/结构化/输出预算), fail-open 兜底在 capability_router 内。
                from app.core.ai.capability_router import (
                    build_requirements,
                    capability_excluded_ids,
                )
                _cap_excluded = capability_excluded_ids(
                    self._channels,
                    build_requirements(method, dispatch_kwargs),
                    purpose=self._purpose,
                )

                for attempt in range(max_attempts):
                    _clock.raise_if_expired(
                        f"Dispatcher[{self._purpose}] attempt={attempt + 1}"
                    )
                    _exclude = tried | _cap_excluded
                    channel = await self._select_channel(
                        group=group,
                        exclude=_exclude if _exclude else None,
                        avoid_groups=avoid_groups if avoid_groups else None,
                        avoid_models=avoid_models if avoid_models else None,
                        max_wait_seconds=_clock.remaining(),
                    )
                    if channel is None:
                        break
                    # 超时后续试必须显式打出落点, 避免"弃组并切换"后无跨组日志
                    # 造成观测盲区(软偏好成功/同组回退都不走 force_cross_group 文案)。
                    if _timeout_attempts > 0 and avoid_groups:
                        _timeout_cap_log = int(
                            getattr(_budget, "timeout_max_attempts", 2) or 2
                        )
                        if channel.model_group in avoid_groups:
                            logger.warning(
                                f"Dispatcher[{self._purpose}] timeout failover "
                                f"fallback same-group (no other group): "
                                f"group={channel.model_group}, "
                                f"model={channel.client.model_name}, "
                                f"{channel.config_id}#{channel.key_index}, "
                                f"avoided={sorted(avoid_groups)}, "
                                f"avoided_models={sorted(avoid_models)}, "
                                f"timeout {_timeout_attempts}/{_timeout_cap_log}, "
                                f"attempt {attempt + 1}/{max_attempts}"
                            )
                        else:
                            logger.warning(
                                f"Dispatcher[{self._purpose}] timeout failover -> "
                                f"group={channel.model_group}, "
                                f"model={channel.client.model_name}, "
                                f"{channel.config_id}#{channel.key_index}, "
                                f"avoided={sorted(avoid_groups)}, "
                                f"avoided_models={sorted(avoid_models)}, "
                                f"timeout {_timeout_attempts}/{_timeout_cap_log}, "
                                f"attempt {attempt + 1}/{max_attempts}"
                            )
                    tried.add(id(channel))
                    channel.active_requests += 1
                    get_attempt_recorder().set_channel(
                        config_id=channel.config_id, provider=channel.provider,
                    )

                    start = time.monotonic()
                    is_429 = False
                    try:
                        fn = getattr(channel.client, method)
                        # P0-B: 单次通道调用不得越过端到端 deadline
                        _remaining = _clock.remaining()
                        if _remaining is not None:
                            result = await asyncio.wait_for(
                                fn(**dispatch_kwargs), timeout=max(_remaining, 0.1),
                            )
                        else:
                            result = await fn(**dispatch_kwargs)

                        latency_ms = (time.monotonic() - start) * 1000
                        channel.active_requests -= 1
                        channel.record_success(latency_ms)
                        self.metrics.record_request(latency_ms, success=True)
                        await self._get_controller().record(DispatcherRequestMetric(
                            latency_ms=latency_ms, success=True,
                            is_rate_limited=False, is_timeout=False,
                        ))
                        pt = ct = 0
                        _usage = getattr(channel.client, "_last_usage", None)
                        if _usage:
                            pt = _usage.get("prompt_tokens", 0)
                            ct = _usage.get("completion_tokens", 0)
                        self._try_record_trace(
                            latency_ms=latency_ms,
                            model_name=getattr(channel.client, "model_name", ""),
                            project_id=project_id,
                            error=False,
                            prompt_tokens=pt,
                            completion_tokens=ct,
                        )
                        return result

                    except (RateLimitError, APIStatusError) as e:
                        latency_ms = (time.monotonic() - start) * 1000
                        channel.active_requests -= 1

                        # P0-B: 401/403 鉴权/配置类失败——长冷却、不喂 AIMD、
                        # 不计熔断(通道本身健康, 是密钥/权限问题); 同组两次即跨组。
                        if (
                            isinstance(e, APIStatusError)
                            and e.status_code in (401, 403)
                            and not isinstance(e, RateLimitError)
                        ):
                            from app.core.config import settings as _settings
                            channel.total_requests += 1
                            channel.total_errors += 1
                            channel.last_rate_limit_kind = "auth_or_config"
                            channel.cooldown_until = max(
                                channel.cooldown_until,
                                time.monotonic()
                                + int(_settings.LLM_QUOTA_EXHAUSTED_COOLDOWN),
                            )
                            self.metrics.record_request(latency_ms, success=False)
                            await self._get_controller().record(DispatcherRequestMetric(
                                latency_ms=latency_ms, success=False,
                                is_rate_limited=False, is_timeout=False,
                            ))
                            last_exc = e
                            _grp = channel.model_group
                            _auth_group_failures[_grp] = _auth_group_failures.get(_grp, 0) + 1
                            if _auth_group_failures[_grp] >= 2:
                                avoid_groups.add(_grp)
                            if attempt < max_attempts - 1 and len(self._channels) > 1:
                                logger.warning(
                                    f"Dispatcher[{self._purpose}] 鉴权失败({e.status_code}) "
                                    f"通道进入长冷却并切换 (attempt {attempt + 1}/{max_attempts}): "
                                    f"model={channel.client.model_name}, "
                                    f"{channel.config_id}#{channel.key_index}"
                                )
                                continue
                            raise

                        is_429 = isinstance(e, RateLimitError) or (
                            isinstance(e, APIStatusError) and e.status_code == 429
                        )
                        feeds_aimd = is_429
                        if is_429:
                            signal = classify_rate_limit_error(e)
                            feeds_aimd = signal.feeds_aimd
                            channel.last_rate_limit_kind = signal.kind.value
                            if signal.kind is RateLimitKind.QUOTA_EXHAUSTED:
                                channel.total_quota_exhausted += 1
                            if signal.cooldown_seconds > 0:
                                channel.cooldown_until = max(
                                    channel.cooldown_until,
                                    time.monotonic() + signal.cooldown_seconds,
                                )
                            try:
                                from app.core.ai.metrics import (
                                    record_channel_cooldown,
                                    record_rate_limit_signal,
                                )
                                record_rate_limit_signal(
                                    channel.provider,
                                    channel.client.model_name,
                                    signal.kind.value,
                                )
                                record_channel_cooldown(
                                    self._purpose,
                                    channel.config_id,
                                    signal.cooldown_seconds,
                                )
                            except Exception:
                                pass
                            log_fn = logger.warning if feeds_aimd else logger.error
                            log_fn(
                                f"Dispatcher[{self._purpose}] 429分类: "
                                f"kind={signal.kind.value}, feeds_aimd={feeds_aimd}, "
                                f"cooldown={signal.cooldown_seconds}s, "
                                f"model={channel.client.model_name}, "
                                f"{channel.config_id}#{channel.key_index}"
                            )
                        channel.record_failure(is_rate_limit=feeds_aimd)
                        self.metrics.record_request(
                            latency_ms, success=False, is_rate_limit=feeds_aimd,
                        )
                        await self._get_controller().record(DispatcherRequestMetric(
                            latency_ms=latency_ms, success=False,
                            is_rate_limited=feeds_aimd, is_timeout=False,
                        ))
                        last_exc = e

                        is_retryable = is_429
                        # 401/403 已由上方鉴权分支处理, 此处仅保留服务端错误类
                        if isinstance(e, APIStatusError) and e.status_code in (500, 502, 503):
                            is_retryable = True
                        if (
                            isinstance(e, APIStatusError)
                            and e.status_code == 400
                            and self._is_retryable_bad_request(e)
                        ):
                            is_retryable = True

                        if is_retryable and attempt < max_attempts - 1 and len(self._channels) > 1:
                            status_code = getattr(e, 'status_code', 'N/A')
                            if isinstance(e, APIStatusError) and e.status_code == 400:
                                logger.warning(
                                    f"Dispatcher[{self._purpose}] 检测到参数不兼容类400，"
                                    f"允许降级重试: model={channel.client.model_name}, "
                                    f"{channel.config_id}#{channel.key_index}, error={str(e)[:200]}"
                                )
                            logger.info(
                                f"Dispatcher[{self._purpose}] status={status_code} "
                                f"尝试切换通道 (attempt {attempt + 1}/{max_attempts}): "
                                f"model={channel.client.model_name}, "
                                f"{channel.config_id}#{channel.key_index}"
                            )
                            continue
                        raise

                    except UnsupportedResponseFormatError as e:
                        # 能力不兼容（provider/model 不支持 response_format）：
                        # 1) 不计通道故障，不熔断 —— 通道本身是健康的。
                        # 2) 把当前 group 加入 avoid_groups，下一次 _select_channel
                        #    优先跨组，不再磨同组剩余 key（它们也会同样 400）。
                        latency_ms = (time.monotonic() - start) * 1000
                        channel.active_requests -= 1
                        self.metrics.record_request(latency_ms, success=False)
                        await self._get_controller().record(DispatcherRequestMetric(
                            latency_ms=latency_ms, success=False,
                            is_rate_limited=False, is_timeout=False,
                        ))
                        last_exc = e
                        avoid_groups.add(channel.model_group)
                        if attempt < max_attempts - 1 and len(self._channels) > 1:
                            logger.warning(
                                f"Dispatcher[{self._purpose}] UnsupportedResponseFormat: "
                                f"group={channel.model_group}, model={channel.client.model_name}, "
                                f"强制跨组 (attempt {attempt + 1}/{max_attempts}): "
                                f"{channel.config_id}#{channel.key_index}"
                            )
                            continue
                        if method in ("chat_json", "chat_with_validation"):
                            # P0-D: fail-closed——高风险结构化节点拒绝接受降级产物,
                            # 显式上抛由调用方 fail-safe(skipped/需人工复核)处置。
                            if not _allow_plain_fallback:
                                _nk = None
                                try:
                                    from app.core.ai.prompt_registry import current_node_key
                                    _nk = current_node_key.get(None)
                                except Exception:
                                    pass
                                logger.error(
                                    f"Dispatcher[{self._purpose}] 所有候选均不支持 "
                                    f"response_format 且调用方禁止降级(fail-closed): "
                                    f"node={_nk}, method={method}"
                                )
                                raise
                            fallback_kwargs = dict(dispatch_kwargs)
                            fallback_kwargs["propagate_unsupported"] = False
                            fallback_start = time.monotonic()
                            channel.active_requests += 1
                            _nk = None
                            try:
                                from app.core.ai.prompt_registry import current_node_key
                                _nk = current_node_key.get(None)
                            except Exception:
                                pass
                            fallback_trigger_total.labels(
                                trigger_type="plain_chat_degradation", flow=self._purpose,
                            ).inc()
                            logger.warning(
                                f"Dispatcher[{self._purpose}] 所有候选均不支持 response_format, "
                                f"最终回退普通 chat({method}): node={_nk}, "
                                f"model={channel.client.model_name}, "
                                f"{channel.config_id}#{channel.key_index}"
                            )
                            try:
                                fallback_result = await getattr(channel.client, method)(**fallback_kwargs)
                            except asyncio.CancelledError:
                                # fallback await 路径同样需显式捕获 CancelledError，
                                # 确保 active_requests（此处为 fallback 的 +1）恰好递减一次后重抛。
                                channel.active_requests -= 1
                                raise
                            except Exception as fallback_err:
                                fallback_latency_ms = (time.monotonic() - fallback_start) * 1000
                                channel.active_requests -= 1
                                channel.record_failure()
                                self.metrics.record_request(fallback_latency_ms, success=False)
                                await self._get_controller().record(DispatcherRequestMetric(
                                    latency_ms=fallback_latency_ms, success=False,
                                    is_rate_limited=False, is_timeout=False,
                                ))
                                self._try_record_trace(
                                    latency_ms=fallback_latency_ms,
                                    model_name=getattr(channel.client, "model_name", ""),
                                    project_id=project_id,
                                    error=True,
                                )
                                raise fallback_err
                            fallback_latency_ms = (time.monotonic() - fallback_start) * 1000
                            channel.active_requests -= 1
                            channel.record_success(fallback_latency_ms)
                            # P0-D: 降级产物不得计 valid——可靠性统计需看见降级规模
                            from app.core.ai.llm_outcome import LlmOutcome as _LO
                            get_attempt_recorder().amend_last_attempt(_LO.DEGRADED_PLAIN_CHAT)
                            self.metrics.record_request(fallback_latency_ms, success=True)
                            await self._get_controller().record(DispatcherRequestMetric(
                                latency_ms=fallback_latency_ms, success=True,
                                is_rate_limited=False, is_timeout=False,
                            ))
                            _usage = getattr(channel.client, "_last_usage", None)
                            self._try_record_trace(
                                latency_ms=fallback_latency_ms,
                                model_name=getattr(channel.client, "model_name", ""),
                                project_id=project_id,
                                error=False,
                                prompt_tokens=(_usage or {}).get("prompt_tokens", 0),
                                completion_tokens=(_usage or {}).get("completion_tokens", 0),
                            )
                            return fallback_result
                        raise

                    except asyncio.CancelledError:
                        # CancelledError 继承自 BaseException 而非 Exception，
                        # 必须显式捕获，确保 active_requests 恰好递减一次后原样重抛。
                        channel.active_requests -= 1
                        raise

                    except (asyncio.TimeoutError, APITimeoutError) as e:
                        # APITimeoutError 与 deadline wait_for 超时统一口径;
                        # 首次即 avoid_groups, 超时类物理尝试受 timeout_max_attempts 限制。
                        latency_ms = (time.monotonic() - start) * 1000
                        channel.active_requests -= 1
                        _timeout_attempts += 1
                        channel.record_failure()
                        self.metrics.record_request(latency_ms, success=False)
                        await self._get_controller().record(DispatcherRequestMetric(
                            latency_ms=latency_ms, success=False,
                            is_rate_limited=False, is_timeout=True,
                        ))
                        last_exc = e
                        avoid_groups.add(channel.model_group)
                        model_name = getattr(channel.client, "model_name", None) or ""
                        if model_name:
                            avoid_models.add(model_name)
                        _record_sticky_timeout(
                            self._purpose, model_name, channel.model_group,
                        )
                        _timeout_cap = int(getattr(_budget, "timeout_max_attempts", 2) or 2)
                        if (
                            _timeout_attempts < _timeout_cap
                            and attempt < max_attempts - 1
                            and len(self._channels) > 1
                        ):
                            logger.warning(
                                f"Dispatcher[{self._purpose}] {type(e).__name__}: "
                                f"{str(e)[:120]} 超时弃组并切换 "
                                f"(timeout {_timeout_attempts}/{_timeout_cap}, "
                                f"attempt {attempt + 1}/{max_attempts}): "
                                f"group={channel.model_group}, "
                                f"model={channel.client.model_name}, "
                                f"{channel.config_id}#{channel.key_index}"
                            )
                            continue
                        logger.warning(
                            f"Dispatcher[{self._purpose}] {type(e).__name__}: "
                            f"{str(e)[:120]} 超时达上限放弃 "
                            f"(timeout {_timeout_attempts}/{_timeout_cap}, "
                            f"attempt {attempt + 1}/{max_attempts}): "
                            f"group={channel.model_group}, "
                            f"model={channel.client.model_name}, "
                            f"{channel.config_id}#{channel.key_index}"
                        )
                        raise

                    except LlmContentValidationError as e:
                        # 内容校验失败是"模型 x prompt"层面的系统性偏差，
                        # 不是通道故障：不熔断；同模型换 key 在确定性/低温配置下
                        # 必然复现同样输出，因此加 avoid_groups 强制跨组换模型。
                        latency_ms = (time.monotonic() - start) * 1000
                        channel.active_requests -= 1
                        self.metrics.record_request(latency_ms, success=False)
                        await self._get_controller().record(DispatcherRequestMetric(
                            latency_ms=latency_ms, success=False,
                            is_rate_limited=False, is_timeout=False,
                        ))
                        last_exc = e
                        avoid_groups.add(channel.model_group)
                        # 截断扩容知识跨通道续用——通道内已确认初始 max_tokens
                        # 不足时, 后续通道直接从扩容后的预算起步, 避免逐模型重复截断。
                        suggested = getattr(e, "suggested_max_tokens", None)
                        if (
                            suggested
                            and suggested > int(dispatch_kwargs.get("max_tokens", 0) or 0)
                        ):
                            dispatch_kwargs["max_tokens"] = suggested
                            logger.info(
                                f"Dispatcher[{self._purpose}] 跨通道续用截断扩容预算: "
                                f"max_tokens->{suggested}"
                            )
                        if attempt < max_attempts - 1 and len(self._channels) > 1:
                            logger.warning(
                                f"Dispatcher[{self._purpose}] 内容校验失败: "
                                f"group={channel.model_group}, model={channel.client.model_name}, "
                                f"强制跨组换模型 (attempt {attempt + 1}/{max_attempts}): "
                                f"{channel.config_id}#{channel.key_index}, err={str(e)[:120]}"
                            )
                            continue
                        raise

                    except EmptyLLMResponseError as e:
                        # 清洗后空响应是通道/模型可靠性问题，不得当作
                        # JSONDecodeError 语义错误（后者不换通道）。不熔断，但
                        # avoid_groups 强制跨组，避免同模型多 key 重复空返回。
                        latency_ms = (time.monotonic() - start) * 1000
                        channel.active_requests -= 1
                        self.metrics.record_request(latency_ms, success=False)
                        await self._get_controller().record(DispatcherRequestMetric(
                            latency_ms=latency_ms, success=False,
                            is_rate_limited=False, is_timeout=False,
                        ))
                        last_exc = e
                        avoid_groups.add(channel.model_group)
                        if attempt < max_attempts - 1 and len(self._channels) > 1:
                            logger.warning(
                                f"Dispatcher[{self._purpose}] 空响应换通道: "
                                f"group={channel.model_group}, model={channel.client.model_name}, "
                                f"raw_len={getattr(e, 'raw_len', 0)}, "
                                f"think_consumed={getattr(e, 'think_consumed', False)}, "
                                f"finish_reason={getattr(e, 'finish_reason', None)}, "
                                f"(attempt {attempt + 1}/{max_attempts}): "
                                f"{channel.config_id}#{channel.key_index}"
                            )
                            continue
                        raise

                    except Exception as e:
                        latency_ms = (time.monotonic() - start) * 1000
                        channel.active_requests -= 1

                        # JSONDecodeError 是上层语义错误（模型返回非 JSON），
                        # 不代表通道故障；既不 record_failure，也不切换通道，
                        # 直接上抛给调用方（Judge/CoVe 等）的 fail-safe 处理。
                        if isinstance(e, json.JSONDecodeError):
                            self.metrics.record_request(latency_ms, success=False)
                            await self._get_controller().record(DispatcherRequestMetric(
                                latency_ms=latency_ms, success=False,
                                is_rate_limited=False, is_timeout=False,
                            ))
                            doc = getattr(e, "doc", "") or ""
                            pos = getattr(e, "pos", 0) or 0
                            snippet = doc[max(0, pos - 30):pos + 30].replace("\n", "\\n")
                            logger.warning(
                                f"Dispatcher[{self._purpose}] JSONDecodeError "
                                f"(语义错误不熔断): model={channel.client.model_name}, "
                                f"{channel.config_id}#{channel.key_index}, "
                                f"err={str(e)[:120]}, doc_len={len(doc)}, "
                                f"snippet={snippet[:120]}"
                            )
                            raise

                        channel.record_failure()
                        self.metrics.record_request(latency_ms, success=False)
                        await self._get_controller().record(DispatcherRequestMetric(
                            latency_ms=latency_ms, success=False,
                            is_rate_limited=False, is_timeout=False,
                        ))
                        last_exc = e
                        # 传输错误与超时同口径弃组。APITimeoutError 已在
                        # 上方分支处理；此处只收真正的 APIConnectionError，走常规
                        # max_attempts，不受 timeout_max_attempts=2 截断。
                        if isinstance(e, APIConnectionError):
                            avoid_groups.add(channel.model_group)
                            model_name = getattr(channel.client, "model_name", None) or ""
                            if model_name:
                                avoid_models.add(model_name)
                            _record_sticky_timeout(
                                self._purpose, model_name, channel.model_group,
                            )
                        if attempt < max_attempts - 1 and len(self._channels) > 1:
                            _switch_hint = (
                                "传输弃组并切换"
                                if isinstance(e, APIConnectionError)
                                else "尝试切换通道"
                            )
                            logger.warning(
                                f"Dispatcher[{self._purpose}] {type(e).__name__}: {str(e)[:120]} "
                                f"{_switch_hint} (attempt {attempt + 1}/{max_attempts}): "
                                f"group={channel.model_group}, "
                                f"model={channel.client.model_name}, "
                                f"{channel.config_id}#{channel.key_index}, "
                                f"avoided={sorted(avoid_groups)}"
                            )
                            continue
                        raise

                self._try_record_trace(
                    latency_ms=0, model_name="",
                    project_id=project_id, error=True,
                )
                if last_exc is not None:
                    raise last_exc
                raise NoAvailableChannelError(
                    f"Dispatcher[{self._purpose}] 无可用通道"
                )

            finally:
                get_attempt_recorder().end_dispatch(_attempt_token)
                self.metrics.current_concurrent -= 1
        finally:
            sem.release()

    def _try_record_trace(
        self,
        latency_ms: float,
        model_name: str,
        project_id: int | None,
        error: bool,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """从协程上下文读取 node_key，异步记录执行轨迹（fire-and-forget）"""
        try:
            from app.core.ai.prompt_registry import current_node_key, get_registry
            nk = current_node_key.get(None)
            if not nk:
                return
            current_node_key.set(None)
            get_registry().record_call(
                node_key=nk,
                project_id=project_id,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=error,
                model_name=model_name,
            )
        except Exception:
            pass

    @staticmethod
    def _is_retryable_bad_request(error: APIStatusError) -> bool:
        """仅对已知的模型参数不兼容类 400 允许降级重试。"""
        message = str(error)
        return any(pattern.search(message) for pattern in _RETRYABLE_BAD_REQUEST_PATTERNS)

    # ==================== 与 CloudClient 一致的公开接口 ====================

    async def chat(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> str:
        return await self._dispatch(
            "chat",
            user_message=user_message,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def chat_json(
        self,
        user_message: str,
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        *,
        json_schema: Optional[dict] = None,
        allow_plain_fallback: bool = True,
    ) -> dict:
        return await self._dispatch(
            "chat_json",
            user_message=user_message,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            json_schema=json_schema,
            allow_plain_fallback=allow_plain_fallback,
        )

    async def chat_with_images(
        self,
        text_message: str,
        image_data_list: list[str],
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> str:
        return await self._dispatch(
            "chat_with_images",
            text_message=text_message,
            image_data_list=image_data_list,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def chat_with_validation(
        self,
        user_message: str,
        response_model: type[BaseModel],
        system_message: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        max_retries: int = 3,
        *,
        json_schema: Optional[dict] = None,
        allow_plain_fallback: bool = True,
    ) -> BaseModel:
        return await self._dispatch(
            "chat_with_validation",
            user_message=user_message,
            response_model=response_model,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            json_schema=json_schema,
            allow_plain_fallback=allow_plain_fallback,
        )

    async def health_check(self) -> bool:
        for ch in self._channels:
            if ch.is_available():
                try:
                    ok = await ch.client.health_check()
                    if ok:
                        return True
                except Exception:
                    continue
        return False

    # ==================== 热更新 ====================

    def update_channel_priorities(self, priority_map: dict[str, int]) -> None:
        """热更新通道优先级，保留所有运行时状态(熔断/AIMD/延迟统计等)"""
        updated = 0
        for ch in self._channels:
            if ch.config_id in priority_map:
                ch.priority = priority_map[ch.config_id]
                updated += 1
        if updated:
            logger.debug(
                f"Dispatcher[{self._purpose}] 热更新优先级: {updated}个通道"
            )

    # ==================== 通道查询 ====================

    def get_primary_group_channel_count(self) -> int:
        """返回最高优先级可用模型组的通道数（即该模型配置的 key 数量）

        业务任务只聚焦一个模型组，并发基数应基于该组的 key 数，
        而非全局所有厂商通道总数。
        """
        groups = self._get_model_groups()
        best_group_id: Optional[str] = None
        best_priority = float("inf")
        for gid, channels in groups.items():
            available = [ch for ch in channels if ch.is_available()]
            if not available:
                continue
            group_priority = min(ch.priority for ch in available)
            if group_priority < best_priority:
                best_priority = group_priority
                best_group_id = gid
        if best_group_id:
            return len(groups[best_group_id])
        return 1

    # ==================== 状态查询 ====================

    def get_status(self) -> dict:
        groups = self._get_model_groups()
        group_summary = {}
        for gid, channels in groups.items():
            group_summary[gid] = {
                "channel_count": len(channels),
                "active_requests": sum(ch.active_requests for ch in channels),
                "available": sum(1 for ch in channels if ch.is_available()),
            }
        status: dict[str, Any] = {
            "purpose": self._purpose,
            "channel_count": self.channel_count,
            "model_groups": group_summary,
            "project_bindings": len(self._project_bindings),
            "channels": [ch.to_status_dict() for ch in self._channels],
            "metrics": self.metrics.to_dict(),
        }
        if self._concurrency_controller is not None:
            status["adaptive_concurrency"] = self._concurrency_controller.get_stats()
        return status
