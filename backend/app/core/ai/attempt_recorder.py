"""LLM 物理尝试记录器 (P0-A 可靠性观测)

职责: 以 contextvar 维护调用上下文, 缓冲并批量写入 llm_attempt_events 事实表
不做: 不做聚合、不参与调度决策、不阻塞 LLM 主链路(队列满即丢弃并计数)

用法:
- Dispatcher 在 _dispatch 入口调用 begin_dispatch() 建立逻辑调用上下文,
  每次选中通道后调用 set_channel(), 结束时 end_dispatch()。
- CloudClient._call_with_retry 在每次物理请求完成/失败时调用
  record_success()/record_error(), 自动继承上下文并递增 attempt_no。
- chat_json/chat_with_validation 在内容级失败时调用 amend_last_attempt()
  把同一物理请求的 outcome 精化为 invalid_json/schema_invalid
  (记录与修正之间无 await, 事件循环内无竞态)。
- 无 dispatch 上下文的直连调用(设置页测试等)自动生成单次上下文。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from app.core.ai.llm_outcome import LlmOutcome, classify_exception, classify_response

# 流量类别的唯一权威定义在 observability 层(错误日志与 attempt 事实表共用同一 ContextVar);
# 此处再导出以保持 `from app.core.ai.attempt_recorder import current_traffic_class` 的既有入口。
from app.core.observability.traffic_class import (  # noqa: F401
    current_traffic_class,
    resolve_traffic_class,
)

_FLUSH_INTERVAL_SECONDS = 2.0
_FLUSH_BATCH_SIZE = 200
_QUEUE_MAX = 5000


@dataclass
class AttemptEvent:
    """待落库的单条 attempt 事实(可在 flush 前被 amend 精化)"""

    occurred_at: datetime
    logical_call_id: str
    attempt_no: int
    traffic_class: str
    process_role: Optional[str]
    purpose: Optional[str]
    provider: Optional[str]
    model_name: Optional[str]
    config_id: Optional[str]
    node_key: Optional[str]
    flow: Optional[str]
    project_id: Optional[int]
    method: Optional[str]
    http_status: Optional[int]
    outcome: str
    rate_limit_kind: Optional[str]
    queue_wait_ms: Optional[int]
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class DispatchContext:
    """一次逻辑调用(dispatcher._dispatch 或直连调用)的上下文"""

    logical_call_id: str
    purpose: Optional[str] = None
    project_id: Optional[int] = None
    method: Optional[str] = None
    queue_wait_ms: Optional[int] = None
    config_id: Optional[str] = None
    provider: Optional[str] = None
    attempt_counter: int = 0
    last_event: Optional[AttemptEvent] = None


_dispatch_ctx: ContextVar[Optional[DispatchContext]] = ContextVar(
    "llm_dispatch_ctx", default=None
)


def _resolve_node_key() -> Optional[str]:
    """只读取 prompt_registry 的 current_node_key, 不消费(消费权归 trace)"""
    try:
        from app.core.ai.prompt_registry import current_node_key
        return current_node_key.get(None)
    except Exception:
        return None


def _resolve_flow(node_key: Optional[str]) -> Optional[str]:
    if not node_key or "." not in node_key:
        return None
    return node_key.split(".", 1)[0]


class AttemptRecorder:
    """缓冲 + 后台批量落库"""

    def __init__(self) -> None:
        self._queue: deque[AttemptEvent] = deque(maxlen=_QUEUE_MAX)
        self._flusher: Optional[asyncio.Task] = None
        self._dropped_count: int = 0
        self._inserted_count: int = 0

    # ---------- 上下文管理 ----------

    def begin_dispatch(
        self,
        *,
        purpose: Optional[str],
        project_id: Optional[int],
        method: Optional[str],
        queue_wait_ms: Optional[int] = None,
    ) -> Any:
        """建立逻辑调用上下文, 返回 contextvar token 供 end_dispatch 恢复"""
        ctx = DispatchContext(
            logical_call_id=uuid.uuid4().hex,
            purpose=purpose,
            project_id=project_id,
            method=method,
            queue_wait_ms=queue_wait_ms,
        )
        return _dispatch_ctx.set(ctx)

    def end_dispatch(self, token: Any) -> None:
        try:
            _dispatch_ctx.reset(token)
        except Exception:
            _dispatch_ctx.set(None)

    def set_channel(
        self,
        *,
        config_id: Optional[str],
        provider: Optional[str],
    ) -> None:
        """Dispatcher 每次选中通道后更新通道维度"""
        ctx = _dispatch_ctx.get()
        if ctx is not None:
            ctx.config_id = config_id
            ctx.provider = provider

    def _ensure_context(self) -> DispatchContext:
        ctx = _dispatch_ctx.get()
        if ctx is None:
            # 直连调用(设置页测试/工厂探测): 生成一次性上下文
            ctx = DispatchContext(logical_call_id=uuid.uuid4().hex)
            _dispatch_ctx.set(ctx)
        return ctx

    # ---------- 记录 ----------

    def _record(
        self,
        *,
        outcome: str,
        latency_ms: int,
        model_name: Optional[str],
        method: Optional[str],
        http_status: Optional[int] = None,
        rate_limit_kind: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> Optional[AttemptEvent]:
        from app.core.config import settings
        if not getattr(settings, "LLM_ATTEMPT_EVENTS_ENABLED", True):
            return None
        try:
            ctx = self._ensure_context()
            ctx.attempt_counter += 1
            node_key = _resolve_node_key()
            event = AttemptEvent(
                occurred_at=datetime.now(timezone.utc),
                logical_call_id=ctx.logical_call_id,
                attempt_no=ctx.attempt_counter,
                traffic_class=resolve_traffic_class(),
                process_role=os.getenv("BIDMIND_PROCESS_ROLE", "api"),
                purpose=ctx.purpose,
                provider=ctx.provider,
                model_name=model_name,
                config_id=ctx.config_id,
                node_key=node_key,
                flow=_resolve_flow(node_key),
                project_id=ctx.project_id,
                # 业务方法(chat_json/chat_with_validation)优先于物理标签(chat/chat_cd),
                # 结构化失败率的分母需要业务级方法维度
                method=ctx.method or method,
                http_status=http_status,
                outcome=outcome,
                rate_limit_kind=rate_limit_kind,
                queue_wait_ms=ctx.queue_wait_ms if ctx.attempt_counter == 1 else None,
                latency_ms=max(0, int(latency_ms)),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            if len(self._queue) >= _QUEUE_MAX:
                self._dropped_count += 1
                return None
            self._queue.append(event)
            ctx.last_event = event
            self._ensure_flusher()
            return event
        except Exception:
            # 观测不得影响主链路
            return None

    def record_success(
        self,
        response: Any,
        *,
        latency_ms: int,
        model_name: Optional[str],
        method: Optional[str],
    ) -> None:
        """物理请求返回 HTTP 2xx 后记录; content 空/纯 think 计 empty"""
        outcome = classify_response(response)
        pt = ct = 0
        try:
            usage = getattr(response, "usage", None)
            if usage:
                pt = usage.prompt_tokens or 0
                ct = usage.completion_tokens or 0
        except Exception:
            pass
        self._record(
            outcome=outcome.value,
            latency_ms=latency_ms,
            model_name=model_name,
            method=method,
            http_status=200,
            prompt_tokens=pt,
            completion_tokens=ct,
        )

    def record_error(
        self,
        exc: BaseException,
        *,
        latency_ms: int,
        model_name: Optional[str],
        method: Optional[str],
    ) -> None:
        outcome, status, rl_kind = classify_exception(exc)
        self._record(
            outcome=outcome.value,
            latency_ms=latency_ms,
            model_name=model_name,
            method=method,
            http_status=status,
            rate_limit_kind=rl_kind,
        )

    def amend_last_attempt(self, outcome: LlmOutcome) -> None:
        """把最近一次 attempt 的 outcome 精化(如 valid -> invalid_json)。

        必须在 record 与 amend 之间无 await 时调用; 若事件已被 flush,
        修正静默失效(可接受的 2 秒窗口内极小概率)。
        """
        ctx = _dispatch_ctx.get()
        if ctx is not None and ctx.last_event is not None:
            ctx.last_event.outcome = outcome.value

    # ---------- 落库 ----------

    def _ensure_flusher(self) -> None:
        if self._flusher is not None and not self._flusher.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._flusher = loop.create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL_SECONDS)
            try:
                await self.flush_pending()
            except Exception as e:
                logger.debug(f"attempt 事件落库失败(非阻塞): {e}")
            if not self._queue:
                # 队列空闲即退出, 下次 record 会重建(避免测试/关停悬挂任务)
                break

    async def flush_pending(self) -> int:
        """立即批量写入当前缓冲, 返回写入行数(测试与关停钩子使用)"""
        if not self._queue:
            return 0
        batch: list[AttemptEvent] = []
        while self._queue and len(batch) < _FLUSH_BATCH_SIZE:
            batch.append(self._queue.popleft())
        if not batch:
            return 0
        try:
            from app.core.database import async_session_factory
            from app.models.llm_attempt_event import LlmAttemptEvent
            async with async_session_factory() as db:
                db.add_all([
                    LlmAttemptEvent(
                        occurred_at=e.occurred_at,
                        logical_call_id=e.logical_call_id,
                        attempt_no=e.attempt_no,
                        traffic_class=e.traffic_class,
                        process_role=e.process_role,
                        purpose=e.purpose,
                        provider=e.provider,
                        model_name=e.model_name,
                        config_id=e.config_id,
                        node_key=e.node_key,
                        flow=e.flow,
                        project_id=e.project_id,
                        method=e.method,
                        http_status=e.http_status,
                        outcome=e.outcome,
                        rate_limit_kind=e.rate_limit_kind,
                        queue_wait_ms=e.queue_wait_ms,
                        latency_ms=e.latency_ms,
                        prompt_tokens=e.prompt_tokens,
                        completion_tokens=e.completion_tokens,
                    )
                    for e in batch
                ])
                await db.commit()
            self._inserted_count += len(batch)
            return len(batch)
        except Exception as e:
            self._dropped_count += len(batch)
            logger.debug(f"attempt 事件批量写入失败, 丢弃 {len(batch)} 条: {e}")
            return 0

    def stats(self) -> dict:
        return {
            "queued": len(self._queue),
            "inserted": self._inserted_count,
            "dropped": self._dropped_count,
        }

    def reset_for_tests(self) -> None:
        self._queue.clear()
        self._dropped_count = 0
        self._inserted_count = 0
        if self._flusher is not None:
            flusher = self._flusher
            self._flusher = None
            if not flusher.done() and not flusher.get_loop().is_closed():
                flusher.cancel()


_recorder: Optional[AttemptRecorder] = None


def get_attempt_recorder() -> AttemptRecorder:
    global _recorder
    if _recorder is None:
        _recorder = AttemptRecorder()
    return _recorder
