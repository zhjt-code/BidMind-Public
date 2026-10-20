"""评分救援候选的逐项分组关系裁判。

职责: 从有序 table_row_with_header 结构证据恢复权威 item→group 关系并纠正候选。
不做: 不提取评分叶子/规则、不调用LLM/DB、不裁决总verdict、不猜测不闭合的分组。
"""

from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.core.extractor.scoring.adjacent_weight_table import parse_table_row_chunk
from app.core.extractor.scoring.group_column import parse_labeled_score
from app.core.extractor.scoring.ledger.label_normalization import normalize_group_label
from app.core.extractor.scoring.row_group_attribution import recover_row_group_labels
from app.core.extractor.scoring.scoring_item_naming import strip_scoring_name_decoration

_ORDINAL_RE = re.compile(r"^(\d+)[.、．]?$")
_NUMERIC_SCORE_RE = re.compile(r"^[（(]?(\d+(?:\.\d+)?)分[）)]?$")
_LABELED_SCORE_RE = re.compile(
    r"^(?P<label>.+?)[（(]?(?P<score>\d+(?:\.\d+)?)分[）)]?$"
)
_TOLERANCE = 0.01


@dataclass(frozen=True)
class GroupRelationOutcome:
    """分组关系裁判结果。"""

    items: tuple[Any, ...]
    authoritative: bool
    group_totals: dict[str, float]
    corrected_count: int = 0
    cleared_count: int = 0
    reason: str = ""


@dataclass(frozen=True)
class _EvidenceRow:
    """线性表格行的稳定顺序与单元格投影。"""

    chunk_index: int
    page_num: int
    cells: tuple[str, ...]


@dataclass(frozen=True)
class _MappedItem:
    """候选评分项到唯一原文行/单元格的映射。"""

    item_index: int
    row_index: int
    cell_index: int
    name: str
    score: float


@dataclass(frozen=True)
class _GroupAnchor:
    """顶层序号行开启的连续分组区间。"""

    ordinal: int
    mapped_position: int
    row_index: int
    label: str
    declared: float | None


def reconcile_item_group_relations(
    items: list[Any],
    row_chunks: list[Any],
    *,
    require_authority: bool,
) -> GroupRelationOutcome:
    """以闭合结构关系纠正候选；要求权威但证据不足时剥除全部未验证归组。"""
    rows = _evidence_rows(row_chunks)
    relation, totals, reason = _recover_authoritative_relations(items, rows)
    if relation:
        updated: list[Any] = []
        corrected = 0
        for index, item in enumerate(items):
            label = relation.get(index)
            if label is None:
                updated.append(item)
                continue
            if str(getattr(item, "volume_label", "") or "") != label:
                corrected += 1
            updated.append(_copy_with_group(item, label))
        return GroupRelationOutcome(
            items=tuple(updated),
            authoritative=True,
            group_totals=totals,
            corrected_count=corrected,
        )
    if not require_authority:
        return GroupRelationOutcome(
            items=tuple(items),
            authoritative=False,
            group_totals={},
            reason=reason,
        )
    cleared: list[Any] = []
    cleared_count = 0
    for item in items:
        if getattr(item, "volume_label", None) or getattr(item, "volume", None):
            cleared_count += 1
        cleared.append(_copy_with_group(item, None))
    return GroupRelationOutcome(
        items=tuple(cleared),
        authoritative=False,
        group_totals={},
        cleared_count=cleared_count,
        reason=reason,
    )


def _recover_authoritative_relations(
    items: list[Any],
    rows: list[_EvidenceRow],
) -> tuple[dict[int, str], dict[str, float], str]:
    """生成经双重闭合验证的条目索引到原文组标签映射。"""
    scored_indices = [
        index
        for index, item in enumerate(items)
        if float(getattr(item, "score_weight", 0) or 0) > 0
    ]
    if len(scored_indices) < 2 or not rows:
        return {}, {}, "group_relation_evidence_missing"
    mapped, reason = _map_items_to_rows(items, scored_indices, rows)
    if not mapped:
        return {}, {}, reason
    anchors, reason = _collect_group_anchors(mapped, rows)
    if not anchors:
        return {}, {}, reason
    relations, totals, pseudo_rows, row_scores, row_names, declared = (
        _build_closed_partition(mapped, anchors, rows)
    )
    if not relations:
        return {}, {}, "group_declared_score_missing"
    recovered = recover_row_group_labels(
        pseudo_rows,
        name_col=2,
        row_scores=row_scores,
        row_names=row_names,
        declared_scores=declared,
    )
    if len(recovered) != len(mapped):
        return {}, {}, "group_partition_not_closed"
    authoritative = {
        mapped[position].item_index: recovered[position]
        for position in range(len(mapped))
    }
    if authoritative != relations:
        return {}, {}, "group_partition_mismatch"
    return authoritative, totals, ""


def _evidence_rows(chunks: list[Any]) -> list[_EvidenceRow]:
    """按全局chunk顺序解析普通表格行证据。"""
    rows: list[_EvidenceRow] = []
    for chunk in sorted(
        chunks or [],
        key=lambda value: (
            int(getattr(value, "chunk_index", 0) or 0),
            int(getattr(value, "page_num", 0) or 0),
        ),
    ):
        cells = parse_table_row_chunk(str(getattr(chunk, "content", "") or ""))
        if cells:
            rows.append(
                _EvidenceRow(
                    chunk_index=int(getattr(chunk, "chunk_index", 0) or 0),
                    page_num=int(getattr(chunk, "page_num", 0) or 0),
                    cells=tuple(cells),
                )
            )
    return rows


def _map_items_to_rows(
    items: list[Any],
    scored_indices: list[int],
    rows: list[_EvidenceRow],
) -> tuple[list[_MappedItem], str]:
    """以名称和分值把每个正分候选唯一映射到一条原文表格行。"""
    mapped: list[_MappedItem] = []
    used_rows: set[int] = set()
    for item_index in scored_indices:
        item = items[item_index]
        name = str(getattr(item, "score_item_name", "") or "").strip()
        score = float(getattr(item, "score_weight", 0) or 0)
        matches: dict[int, list[int]] = {}
        for row_index, row in enumerate(rows):
            for cell_index, cell in enumerate(row.cells):
                if _cell_matches_item(cell, name, score):
                    matches.setdefault(row_index, []).append(cell_index)
        if len(matches) != 1:
            return [], "item_row_mapping_ambiguous"
        row_index, cell_indices = next(iter(matches.items()))
        if row_index in used_rows:
            return [], "multiple_items_share_row"
        used_rows.add(row_index)
        cell_index = min(cell_indices, key=lambda idx: len(rows[row_index].cells[idx]))
        mapped.append(
            _MappedItem(
                item_index=item_index,
                row_index=row_index,
                cell_index=cell_index,
                name=name,
                score=score,
            )
        )
    mapped.sort(key=lambda value: value.row_index)
    return mapped, ""


def _collect_group_anchors(
    mapped: list[_MappedItem],
    rows: list[_EvidenceRow],
) -> tuple[list[_GroupAnchor], str]:
    """从连续顶层序号行收集分组边界与声明分值。"""
    anchors: list[_GroupAnchor] = []
    for position, item in enumerate(mapped):
        cells = rows[item.row_index].cells
        ordinal_match = _ORDINAL_RE.fullmatch(_compact(cells[0])) if cells else None
        if ordinal_match is None:
            continue
        ordinal = int(ordinal_match.group(1))
        if item.cell_index >= 2:
            raw_group = cells[item.cell_index - 1]
            parsed = _parse_group_cell(raw_group)
            label = parsed[0]
            declared = parsed[1]
        elif item.cell_index == 1:
            label = item.name
            declared = item.score
        else:
            return [], "group_anchor_item_position_invalid"
        label = str(label or "").strip()
        if not normalize_group_label(label):
            return [], "group_anchor_label_invalid"
        anchors.append(
            _GroupAnchor(
                ordinal=ordinal,
                mapped_position=position,
                row_index=item.row_index,
                label=label,
                declared=declared,
            )
        )
    ordinals = [anchor.ordinal for anchor in anchors]
    if len(anchors) < 2 or any(
        current != previous + 1
        for previous, current in zip(ordinals, ordinals[1:])
    ):
        return [], "group_anchor_sequence_invalid"
    if anchors[0].mapped_position != 0:
        return [], "group_leading_items_unscoped"
    return anchors, ""


def _parse_group_cell(raw: str) -> tuple[str, float | None]:
    """解析组名单元格；兼容“名称 25分”与“名称（25分）”两种表格投影。"""
    parsed = parse_labeled_score(raw)
    if parsed is not None:
        return parsed
    compact = _compact(raw)
    match = _LABELED_SCORE_RE.fullmatch(compact)
    if match is None:
        return strip_scoring_name_decoration(raw), None
    label = strip_scoring_name_decoration(match.group("label"))
    try:
        score = float(match.group("score"))
    except ValueError:
        return label, None
    return label, score if score > 0 else None


def _build_closed_partition(
    mapped: list[_MappedItem],
    anchors: list[_GroupAnchor],
    rows: list[_EvidenceRow],
) -> tuple[
    dict[int, str],
    dict[str, float],
    list[list[str]],
    dict[int, float],
    dict[int, str],
    dict[str, float],
]:
    """按锚点区间闭合组分，并构造既有逐行归组器可复核的伪网格。"""
    relations: dict[int, str] = {}
    totals: dict[str, float] = {}
    declared_scores: dict[str, float] = {}
    pseudo_rows = [["", "", f"{item.name}（{item.score:g}分）"] for item in mapped]
    row_scores = {position: item.score for position, item in enumerate(mapped)}
    row_names = {position: item.name for position, item in enumerate(mapped)}
    for anchor_index, anchor in enumerate(anchors):
        next_anchor = anchors[anchor_index + 1] if anchor_index + 1 < len(anchors) else None
        end_position = next_anchor.mapped_position if next_anchor else len(mapped)
        end_row = next_anchor.row_index if next_anchor else len(rows)
        members = mapped[anchor.mapped_position:end_position]
        group_sum = round(sum(member.score for member in members), 4)
        declared = anchor.declared
        if declared is None:
            candidates = {
                score
                for row in rows[anchor.row_index:end_row]
                for cell in row.cells
                if (score := _numeric_score(cell)) is not None
                and abs(score - group_sum) <= _TOLERANCE
            }
            if len(candidates) != 1:
                return {}, {}, [], {}, {}, {}
            declared = next(iter(candidates))
        if abs(float(declared) - group_sum) > _TOLERANCE:
            return {}, {}, [], {}, {}, {}
        key = normalize_group_label(anchor.label)
        if not key or key in declared_scores:
            return {}, {}, [], {}, {}, {}
        declared_scores[key] = float(declared)
        totals[anchor.label] = group_sum
        pseudo_rows[anchor.mapped_position][0] = str(anchor.ordinal)
        pseudo_rows[anchor.mapped_position][1] = f"{anchor.label}（{float(declared):g}分）"
        for member in members:
            relations[member.item_index] = anchor.label
    if len(relations) != len(mapped):
        return {}, {}, [], {}, {}, {}
    return relations, totals, pseudo_rows, row_scores, row_names, declared_scores


def _cell_matches_item(cell: str, name: str, score: float) -> bool:
    """名称与满分同时命中同一单元格才视为叶子行证据。"""
    compact_cell = _compact(cell)
    compact_name = _compact(name)
    return bool(
        compact_name
        and compact_name in compact_cell
        and f"{score:g}分" in compact_cell
    )


def _numeric_score(cell: str) -> float | None:
    """解析跨页续行中独立成格的纯分值声明。"""
    match = _NUMERIC_SCORE_RE.fullmatch(_compact(cell))
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _copy_with_group(item: Any, label: str | None) -> Any:
    """复制并重置归组轴；旧volume可能受错误标签污染，须由权威关系后续重归。"""
    update = {"volume_label": label, "volume": None}
    try:
        return item.model_copy(update=update)
    except (AttributeError, TypeError, ValueError):
        cloned = copy.copy(item)
        setattr(cloned, "volume_label", label)
        if hasattr(cloned, "volume"):
            setattr(cloned, "volume", None)
        return cloned


def _compact(text: str) -> str:
    """统一全半角、空白和大小写以作结构匹配。"""
    return re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", str(text or "")),
    ).lower()


def volume_defs_from_group_totals(group_totals: dict[str, float]) -> dict:
    """把闭合分组分值投影为标准卷定义。

 职责: 以结构闭合的组分值（含跨页缝合结果）构造 volume 枚举卷定义，
 取代模型 header 自述的未验证组总分。
 不做: 不猜测未闭合分组、不合并同卷枚举的多个组（歧义即弃该卷）、
 不修改评分项、不回退到模型自述值。
 """
    from app.core.extractor.scoring.volume_parser import VolumeDefinition
    from app.core.extractor.scoring.weighted_volume_table import (
        volume_from_scoring_label,
    )

    candidates: dict[str, list[tuple[str, float]]] = {}
    for label, total in (group_totals or {}).items():
        try:
            score = float(total)
        except (TypeError, ValueError):
            continue
        if score <= 0:
            continue
        volume = volume_from_scoring_label(str(label or ""), default="")
        if not volume:
            continue
        candidates.setdefault(volume, []).append((str(label), score))
    return {
        volume: VolumeDefinition(
            volume=volume,
            # 与 header 兜底同一权重口径(总分/100), 仅换成结构闭合的分值来源
            weight=round(entries[0][1] / 100.0, 4),
            volume_max=entries[0][1],
            label=entries[0][0],
            source="group_relation",
        )
        for volume, entries in candidates.items()
        if len(entries) == 1
    }


__all__ = [
    "GroupRelationOutcome",
    "reconcile_item_group_relations",
    "volume_defs_from_group_totals",
]
