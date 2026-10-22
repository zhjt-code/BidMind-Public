"""章节画像叶子分批与覆盖率纯函数。

职责: 把叶子节点列表切为固定大小批次，并计算覆盖率 / 缺漏 ID。
不做: 不调用 LLM、不写库、不做质量判定。
"""

from __future__ import annotations

from typing import Any, Sequence, TypeVar

T = TypeVar("T")


def split_leaves_into_batches(
    leaves: Sequence[T],
    batch_size: int,
) -> list[list[T]]:
    """按 batch_size 切分叶子列表；batch_size<=0 时整表作为一批。"""
    if not leaves:
        return []
    size = len(leaves) if batch_size <= 0 else max(1, int(batch_size))
    return [list(leaves[i : i + size]) for i in range(0, len(leaves), size)]


def compute_coverage(*, expected: int, accepted: int) -> float:
    """计算覆盖率；expected<=0 时返回 0.0。"""
    if expected <= 0:
        return 0.0
    return max(0.0, min(1.0, accepted / expected))


def find_missing_ids(
    expected_ids: Sequence[Any],
    accepted_ids: Sequence[Any] | set[Any],
) -> list[Any]:
    """返回 expected 中尚未被 accepted 覆盖的 ID，保持原序。"""
    accepted_set = set(accepted_ids)
    return [nid for nid in expected_ids if nid not in accepted_set]


__all__ = [
    "split_leaves_into_batches",
    "compute_coverage",
    "find_missing_ids",
]
