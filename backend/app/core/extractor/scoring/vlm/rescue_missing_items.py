"""救援缺项反馈（点名重询; 起委托共享叶子清单）。

职责: 基于 leaf_inventory 网格叶子清单, 对照已提取候选点名缺失行,
 供 rescue_rounds 重询反馈使用; 并保留 (名,分值) 元组级续行缝合工具。
不做: 不修改 scoring_table_parser / 网格组装, 不落库, 不调用 LLM,
 不自建清单口径(单源归 leaf_inventory, 防两套缝合/信号判定漂移)。
"""
from __future__ import annotations

from typing import Any

from app.core.extractor.scoring.leaf_inventory import (
    leaf_inventory_from_chunks,
    missing_leaf_names,
)

_CONTINUE_SUFFIXES = ("与", "及", "的")
_MAX_CONTINUATION_CHARS = 6


def stitch_continuation_name_rows(
    rows: list[tuple[str, float | None]],
) -> list[tuple[str, float | None]]:
    """(名, 分值) 元组级缝合: 空分值短名且上行以连接符收尾则拼接。

    网格行级的结构性缝合已单源在 leaf_inventory.stitch_grid_continuation_rows;
    本工具服务已脱离网格形态的 (名,分值) 序列消费方。
    """
    stitched: list[tuple[str, float | None]] = []
    for name, score in rows:
        name = str(name or "").strip()
        if (
            stitched
            and score is None
            and name
            and len(name) <= _MAX_CONTINUATION_CHARS
            and stitched[-1][0].endswith(_CONTINUE_SUFFIXES)
        ):
            prev_name, prev_score = stitched[-1]
            stitched[-1] = (prev_name + name, prev_score)
            continue
        if name:
            stitched.append((name, score))
    return stitched


def missing_scored_item_names(chunks: list[Any], kept: list[Any]) -> list[str]:
    """网格带分值叶子相对已提取候选的差集(保序去重, 委托共享清单)。"""
    kept_names = [
        str(getattr(item, "score_item_name", "") or "") for item in kept or []
    ]
    return missing_leaf_names(leaf_inventory_from_chunks(chunks or []), kept_names)


_HINT_RULE_MAX_CHARS = 80
_HINT_MAX_COUNT = 3
_HINT_NAME_MIN_LEN = 4


def group_rule_row_hints(chunks: list[Any], kept: list[Any]) -> list[str]:
    """无清单叶子的组行原文提示: 该组尚无提取项覆盖时点出计分行原文。

 覆盖判定: 任一 kept 项名(compact>=4字)是该组规则原文的子串即视为已覆盖
 (如「投标总报价」⊂ 报价规则句), 已覆盖的组不再提示, 防误导模型。
 """
    import re
    import unicodedata

    from app.core.extractor.scoring.leaf_inventory import unresolved_group_rule_rows
    from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk

    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).lower()

    kept_compacts = [
        _compact(getattr(item, "score_item_name", "") or "") for item in kept or []
    ]
    hints: list[str] = []
    for chunk in chunks or []:
        grid = (payload_from_chunk(chunk) or {}).get("grid")
        if not grid:
            continue
        for group_cell, rule_cell in unresolved_group_rule_rows(grid):
            rule_compact = _compact(rule_cell)
            if any(
                len(name) >= _HINT_NAME_MIN_LEN and name in rule_compact
                for name in kept_compacts
            ):
                continue
            hints.append(f"{group_cell}: {rule_cell[:_HINT_RULE_MAX_CHARS]}")
            if len(hints) >= _HINT_MAX_COUNT:
                return hints
    return hints


def format_missing_item_feedback(missing: list[str]) -> str:
    if not missing:
        return ""
    preview = "；".join(missing[:8])
    return f"请补提: {preview}"


__all__ = [
    "format_missing_item_feedback",
    "group_rule_row_hints",
    "missing_scored_item_names",
    "stitch_continuation_name_rows",
]
