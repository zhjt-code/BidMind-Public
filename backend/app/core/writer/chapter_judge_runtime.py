"""
职责: 章节裁决候选封顶、分批、只合并绑定项
不做: 向量召回、LLM 调用、绑定落库、超时调度
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")


def cap_judge_candidates(candidates: Sequence[T], max_total: int) -> list[T]:
    """按召回优先级原序截断；max_total<=0 表示不封顶。"""
    items = list(candidates)
    if max_total <= 0:
        return items
    return items[:max_total]


def iter_judge_batches(candidates: Sequence[T], batch_size: int) -> Iterator[list[T]]:
    """把候选切成固定大小批次；batch_size<=0 时整表一批。"""
    items = list(candidates)
    if not items:
        return
    size = len(items) if batch_size <= 0 else max(int(batch_size), 1)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def merge_bind_true(batch_results: Iterable[Any]) -> list[dict]:
    """只保留 should_bind=true，按首次出现的 clause_id 去重。"""
    merged: list[dict] = []
    seen: set[Any] = set()
    for batch in batch_results:
        rows = batch if isinstance(batch, list) else [batch]
        for item in rows:
            if not isinstance(item, dict):
                continue
            if not item.get("should_bind", False):
                continue
            cid = item.get("clause_id")
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            merged.append(item)
    return merged


def try_unique_rule_bind(
    candidates: Sequence[Any],
    *,
    exclusive_unique: bool,
    volume_gated_unique: bool,
) -> Any | None:
    """唯一候选且可由规则锁定时跳过 LLM。"""
    if len(candidates) != 1:
        return None
    if exclusive_unique or volume_gated_unique:
        return candidates[0]
    return None
