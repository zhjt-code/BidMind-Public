"""超宽稀疏表投影在装配层的接入闸门（归档 /Task4 移植配套抽取）。

职责: 把 scoring_sparse_grid_projection 的决策转换为装配器可直接消费的行/槽/宽度，
 并输出降级告警。
不做: 不做几何投影本身（scoring_sparse_grid_projection 职责）、
 不改段状态、不决定表认领之外的流程分支。

为什么独立成模块:
 scoring_table_assembler.py 逼近复杂度护栏 Hard 阈值（800 行），
 按护栏「新增能力必须新建子模块」，本次移植的接线不得堆入 assembler。
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from app.core.parser.scoring_sparse_grid_projection import (
    SparseGridProjectionSchema,
    decide_sparse_grid_action,
)

__all__ = ["SparseFeedOutcome", "apply_sparse_projection"]


@dataclass(frozen=True)
class SparseFeedOutcome:
    """投影决策在装配侧的落地形态。

    claim_without_rows=True 时调用方应认领该物理表并直接返回（无可用行）。
    """

    rows: list[list[str]]
    none_slots: list[frozenset[int]]
    width: int
    schema: SparseGridProjectionSchema | None
    claim_without_rows: bool


def apply_sparse_projection(
    data: list[list],
    cell_grid: list | None,
    rows: list[list[str]],
    none_slots: list[frozenset[int]],
    width: int,
    schema: SparseGridProjectionSchema | None,
) -> SparseFeedOutcome:
    """按稀疏投影决策改写行集；未命中投影时原样返回。

 投影后 cell_grid 的 merge 槽不再可用（规则文本已行内合并），
 故 none_slots 一律清空（的槽证据仅对未投影路径有效）。
 """
    decision = decide_sparse_grid_action(data, cell_grid, schema)
    if decision.warning:
        logger.warning("[稀疏投影降级] {}", decision.warning)

    if decision.action == "claim_without_rows":
        return SparseFeedOutcome(
            rows=rows, none_slots=none_slots, width=width,
            schema=decision.new_schema or schema, claim_without_rows=True,
        )

    if decision.action == "project" and decision.projected_rows:
        projected = decision.projected_rows
        return SparseFeedOutcome(
            rows=projected,
            none_slots=[frozenset() for _ in projected],
            width=len(projected[0]),
            schema=decision.new_schema or schema,
            claim_without_rows=False,
        )

    return SparseFeedOutcome(
        rows=rows, none_slots=none_slots, width=width,
        schema=decision.new_schema or schema, claim_without_rows=False,
    )
