"""Judge 批处理生命周期。

职责: 收集不可变 Judge 快照，在 compose 返回前有界 drain，异常时取消。
不做: 不把 Judge 派发到 Session 生命周期外，不改草稿 gate_flags，不把 CoVe 移出关键路径。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class JudgeJobSnapshot:
    project_id: int
    node_id: int
    node_title: str
    node_type: str
    content: str
    source_material_ids: tuple[int, ...]
    source_clause_ids: tuple[int, ...]
    is_high_risk: bool


def snapshot_from_compose(
    *,
    project_id: int,
    node: Any,
    payload: Any,
    is_high_risk: bool,
) -> JudgeJobSnapshot:
    """
    职责: 把章节生成结果压成不可变 Judge 快照。
    不做: 不调用 LLM、不入队、不判定高风险。
    """
    node_type = getattr(node, "node_type", None)
    node_type_label = getattr(node_type, "value", None) if node_type is not None else None
    if not isinstance(node_type_label, str) or not node_type_label:
        node_type_label = "unknown" if node_type is None else str(node_type)
    material_ids = getattr(payload, "source_material_ids", None) or []
    clause_ids = getattr(payload, "source_clause_ids", None) or []
    return JudgeJobSnapshot(
        project_id=int(project_id),
        node_id=int(getattr(node, "id")),
        node_title=str(getattr(node, "title", "") or ""),
        node_type=node_type_label,
        content=str(getattr(payload, "content", "") or ""),
        source_material_ids=tuple(int(item) for item in material_ids),
        source_clause_ids=tuple(int(item) for item in clause_ids),
        is_high_risk=bool(is_high_risk),
    )


@dataclass
class WriterJudgeBatch:
    """
    职责: 入队、有界 drain 与取消 Judge 任务。
    不做: 不执行 LLM 细节、不写入草稿、不替代 CoVe。
    """

    drain_timeout_seconds: float = 30.0
    _queue: list[JudgeJobSnapshot] = field(default_factory=list)
    _tasks: list[asyncio.Task] = field(default_factory=list)

    def enqueue_judge(self, snapshot: JudgeJobSnapshot) -> None:
        self._queue.append(snapshot)

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def inflight_count(self) -> int:
        return sum(1 for task in self._tasks if not task.done())

    async def drain_judges(
        self,
        runner: Callable[[JudgeJobSnapshot], Awaitable[None]],
    ) -> None:
        snapshots = list(self._queue)
        self._queue.clear()
        self._tasks = [asyncio.create_task(runner(item)) for item in snapshots]
        if not self._tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=self.drain_timeout_seconds,
            )
        except asyncio.TimeoutError:
            await self.cancel_judges()
        finally:
            self._tasks = [task for task in self._tasks if not task.done()]

    async def cancel_judges(self) -> None:
        self._queue.clear()
        pending = [task for task in self._tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks = []
