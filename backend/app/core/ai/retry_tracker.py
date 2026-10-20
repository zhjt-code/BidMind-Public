"""LLM 批量调用的失败追踪与批次后重试

三个核心业务流程（投标审查、串标审查、标书编写）共用此组件，
在 LLM 调用失败并降级后，记录失败项，待批次结束后统一重试，
利用 dispatcher 可能已轮转到的健康模型组恢复质量。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from loguru import logger


@dataclass
class RetryStats:
    """重试结果统计"""
    retried: int = 0
    recovered: int = 0
    details: list[dict] = field(default_factory=list)


@dataclass
class _FailedItem:
    key: str
    retry_coro_factory: Callable[[], Awaitable[Any]]
    fallback_applied: str = ""


class LLMRetryTracker:
    """LLM 批量调用的失败追踪与批次后重试

    使用方式:
        tracker = LLMRetryTracker("material_tagger")

        # 在 LLM 调用失败的 except 分支中记录
        tracker.record(
            key="material_123",
            retry_coro_factory=lambda: do_llm_call(args),
            fallback_applied="fallback_tags",
        )

        # 批次结束后统一重试
        stats = await tracker.retry_all()
    """

    def __init__(
        self,
        name: str,
        *,
        is_recovered: Optional[Callable[[Any], bool]] = None,
    ) -> None:
        """
 Args:
 name: 日志前缀（如 reviewer / material_tagger）
 is_recovered: 恢复判定谓词; 缺省为"结果非 None"。: 缺省谓词对"失败时返回兜底对象"的调用方是错的——审查侧
 失败返回 failure_result(非 None), 使 100% 失败的批次被报成
 recovered=11/11, 把全量失败伪装成完全自愈。凡调用方存在兜底对象,
 必须显式注入能识别兜底的谓词。
 """
        self._name = name
        self._failed: list[_FailedItem] = []
        self._is_recovered: Callable[[Any], bool] = (
            is_recovered if is_recovered is not None else (lambda result: result is not None)
        )

    def _judge_recovered(self, result: Any) -> bool:
        """安全执行恢复判定; 谓词自身异常按"未恢复"处理(不得掩盖失败)。"""
        try:
            return bool(self._is_recovered(result))
        except Exception as exc:  # noqa: BLE001 判定边界防御
            logger.warning(f"[{self._name}] 恢复判定异常, 按未恢复处理: {exc}")
            return False

    def record(
        self,
        key: str,
        retry_coro_factory: Callable[[], Awaitable[Any]],
        fallback_applied: str = "",
    ) -> None:
        """记录一个 LLM 调用失败的项

        Args:
            key: 标识符（用于日志），如 material_id / requirement_id
            retry_coro_factory: 无参协程工厂，调用时返回一个可 await 的协程。
                调用方通过闭包捕获所有重试所需的上下文。
            fallback_applied: 已应用的降级策略描述（用于日志）
        """
        self._failed.append(_FailedItem(
            key=key,
            retry_coro_factory=retry_coro_factory,
            fallback_applied=fallback_applied,
        ))

    @property
    def failure_count(self) -> int:
        return len(self._failed)

    @property
    def has_failures(self) -> bool:
        return bool(self._failed)

    def clear(self) -> None:
        """清空失败列表（用于 per-pair 等需要重置的场景）"""
        self._failed.clear()

    async def retry_all(self) -> RetryStats:
        """串行重试所有失败项

        适用于顺序处理场景（如素材打标）。
        重试中再次失败仅打日志，不再入队。
        """
        if not self._failed:
            return RetryStats()

        pending = list(self._failed)
        self._failed.clear()
        stats = RetryStats(retried=len(pending))

        logger.info(f"[{self._name}] 重试启动: {len(pending)}个失败项")

        for item in pending:
            detail: dict[str, Any] = {"key": item.key, "success": False, "result": None}
            try:
                result = await item.retry_coro_factory()
                if self._judge_recovered(result):
                    stats.recovered += 1
                    detail["success"] = True
                    detail["result"] = result
                    logger.info(f"[{self._name}] 重试成功: {item.key}")
                else:
                    logger.warning(f"[{self._name}] 重试未恢复: {item.key}")
            except Exception as e:
                logger.warning(
                    f"[{self._name}] 重试仍失败: {item.key}, err={e}"
                )
            stats.details.append(detail)

        self._failed.clear()

        logger.info(
            f"[{self._name}] 重试完成: "
            f"retried={stats.retried}, recovered={stats.recovered}"
        )
        return stats

    async def retry_all_concurrent(
        self,
        max_concurrency: int = 5,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> RetryStats:
        """并发重试所有失败项

        适用于 gather 场景（如审查项并发、串标维度分析并发）。
        使用 Semaphore 控制并发数。

        Args:
            max_concurrency: 最大并发数
            on_progress: 可选进度回调 callback(done, total)；每完成一项后调用
        """
        if not self._failed:
            return RetryStats()

        pending = list(self._failed)
        self._failed.clear()
        stats = RetryStats(retried=len(pending))
        total = len(pending)
        done_count = 0
        done_lock = asyncio.Lock()

        logger.info(
            f"[{self._name}] 并发重试启动: {len(pending)}个失败项, "
            f"max_concurrency={max_concurrency}"
        )

        sem = asyncio.Semaphore(max_concurrency)
        details: list[Optional[dict[str, Any]]] = [None] * len(pending)

        async def _retry_one(idx: int, item: _FailedItem) -> None:
            nonlocal done_count
            detail: dict[str, Any] = {"key": item.key, "success": False, "result": None}
            async with sem:
                try:
                    result = await item.retry_coro_factory()
                    if self._judge_recovered(result):
                        detail["success"] = True
                        detail["result"] = result
                        logger.info(f"[{self._name}] 重试成功: {item.key}")
                    else:
                        logger.warning(f"[{self._name}] 重试未恢复: {item.key}")
                except Exception as e:
                    logger.warning(
                        f"[{self._name}] 重试仍失败: {item.key}, err={e}"
                    )
            details[idx] = detail
            if on_progress is not None:
                async with done_lock:
                    done_count += 1
                    current = done_count
                try:
                    on_progress(current, total)
                except Exception as progress_err:  # noqa: BLE001 进度回调不得阻断重试
                    logger.debug(
                        f"[{self._name}] on_progress 回调异常(忽略): {progress_err}"
                    )

        await asyncio.gather(
            *[_retry_one(i, item) for i, item in enumerate(pending)],
            return_exceptions=True,
        )

        self._failed.clear()

        for d in details:
            if d is not None:
                stats.details.append(d)
                if d["success"]:
                    stats.recovered += 1

        logger.info(
            f"[{self._name}] 并发重试完成: "
            f"retried={stats.retried}, recovered={stats.recovered}"
        )
        return stats
