"""权重列卷定义解析。

职责: 从含权重列的卷级评分表中解析卷满分与卷权重定义。
不做: 不解析评分子项、不落库、不调用 LLM/DB。
"""

from __future__ import annotations

import re

from app.core.extractor.scoring.score_column_semantics import (
    find_embedded_scores,
    normalize_cell,
    parse_score_range_max,
    parse_short_score,
)
from app.core.extractor.scoring.volume_labeling import service_volume_signal
from app.core.extractor.scoring.volume_parser import VolumeDefinition

_WEIGHT_HEADER_TERMS = ("权重", "占比")
_BARE_WEIGHT_HEADER_TERMS = ("权重", "占比", "比重")
_NAME_HEADER_TERMS = ("评分项", "评审因素", "评审内容", "评分内容", "评分卷")
_SCORE_HEADER_TERMS = ("分值", "满分", "标准分")
_PERCENT_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
_DECIMAL_WEIGHT_RE = re.compile(r"^(0(?:\.\d+)?|1(?:\.0+)?)$")
_BARE_POINTS_RE = re.compile(r"^(\d+(?:\.\d+)?)$")
_VOLUME_TITLE_RE = re.compile(
    r"^(?:投标)?(?:价格|报价|评标价|评审价)$"
    r"|^(?:技术|商务|资信|信用|信誉|服务|综合)(?:部分|标|评审|卷)?$"
    r"|^(?:技术|商务|资信|信用|信誉|服务|综合|价格|报价).{0,4}(?:部分|标|评审|卷)$"
)


def parse_weighted_volume_definitions(
    header: list[str],
    data: list[list[str]],
) -> dict[str, VolumeDefinition]:
    """解析 `权重 | 评审因素 | 分值 | 评审标准` 型卷定义表。

    至少识别两个有效卷才返回，避免普通评分项表中偶然出现权重列时误触发。
    """
    weight_cols = _matching_columns(header, _WEIGHT_HEADER_TERMS)
    if not weight_cols:
        return {}
    score_cols = _matching_columns(header, _SCORE_HEADER_TERMS)

    definitions: dict[str, VolumeDefinition] = {}
    for row in data:
        weight = _row_weight(row, weight_cols)
        if weight is None:
            continue
        volume, volume_max, volume_label = _row_volume_and_max(row, score_cols)
        if volume is None or volume_max is None:
            continue
        definitions.setdefault(
            volume,
            VolumeDefinition(
                volume=volume,
                weight=weight,
                volume_max=volume_max,
                label=volume_label,
            ),
        )

    if len(definitions) < 2:
        return {}
    return definitions


def parse_bare_weight_volume_table(
    header: list[str],
    data: list[list[str]],
) -> dict[str, VolumeDefinition]:
    """解析「权重即最终分值」的外层权重表。

 形态: 表头含权重/占比/比重(含「权重(%)」), 单元格为裸数字 30/54/16,
 行名为卷标题。volume_max=数值, weight=数值/100。
 fail-closed: 有效卷≥2、卷别不重复、满分合计≈100。百分号权重走旧路径。
 """
    weight_cols = _matching_columns(header, _BARE_WEIGHT_HEADER_TERMS)
    if not weight_cols:
        return {}
    name_cols = _matching_columns(header, _NAME_HEADER_TERMS)
    if not name_cols:
        name_cols = [
            idx
            for idx, cell in enumerate(header)
            if idx not in weight_cols
            and "序号" not in normalize_cell(cell)
        ]
    if not name_cols:
        return {}

    definitions: dict[str, VolumeDefinition] = {}
    last_volume: str | None = None
    for row in data:
        points = _row_bare_points(row, weight_cols)
        if points is None:
            if last_volume and last_volume in definitions:
                extra = " ".join(str(cell).strip() for cell in row if str(cell).strip())
                if extra:
                    previous = definitions[last_volume]
                    definitions[last_volume] = VolumeDefinition(
                        volume=previous.volume,
                        weight=previous.weight,
                        volume_max=previous.volume_max,
                        label=previous.label,
                        source=previous.source,
                        evidence_text=f"{previous.evidence_text or ''} {extra}".strip(),
                        row_title=previous.row_title,
                    )
            continue
        label = _row_volume_label(row, name_cols)
        if not _is_volume_title(label):
            continue
        volume = volume_from_scoring_label(label, default="")
        if not volume:
            continue
        if volume in definitions:
            return {}
        evidence = " ".join(str(cell).strip() for cell in row if str(cell).strip())
        definitions[volume] = VolumeDefinition(
            volume=volume,
            weight=round(points / 100.0, 4),
            volume_max=points,
            label=label,
            source="framework",
            evidence_text=evidence or None,
            row_title=label,
        )
        last_volume = volume

    if len(definitions) < 2:
        return {}
    total = round(sum(item.volume_max for item in definitions.values()), 4)
    if abs(total - 100.0) > 1.0:
        return {}
    return definitions


def volume_from_scoring_label(text: str, default: str = "technical") -> str:
    """评分卷标签 → 标准 volume 键。: 补 service 卷(从严信号: 服务部分/服务评审/服务标, 泛"服务"词不判卷);
 技术先于服务, 保证"技术服务部分"仍归 technical。: 综合标/综合部分映射为 composite, 避免未知卷连坐清空 header。
 """
    compact = normalize_cell(text)
    if any(term in compact for term in ("报价", "价格", "评标价", "评审价")):
        return "price"
    if "商务" in compact:
        return "commercial"
    if "技术" in compact:
        return "technical"
    if "综合" in compact:
        return "composite"
    if service_volume_signal(compact):
        return "service"
    if any(term in compact for term in ("资信", "信用", "信誉")):
        return "qualification"
    return default


def _matching_columns(header: list[str], terms: tuple[str, ...]) -> list[int]:
    out: list[int] = []
    for idx, cell in enumerate(header):
        compact = normalize_cell(cell)
        if any(term in compact for term in terms):
            out.append(idx)
    return out


def _row_weight(row: list[str], weight_cols: list[int]) -> float | None:
    for idx in weight_cols:
        value = _parse_weight(_cell(row, idx))
        if value is not None:
            return value
    return None


def _parse_weight(text: str) -> float | None:
    compact = normalize_cell(text)
    percent = _PERCENT_WEIGHT_RE.search(compact)
    if percent:
        try:
            value = float(percent.group(1)) / 100.0
        except (TypeError, ValueError):
            return None
        return round(value, 4) if 0 < value <= 1 else None
    decimal = _DECIMAL_WEIGHT_RE.match(compact)
    if decimal:
        try:
            value = float(decimal.group(1))
        except (TypeError, ValueError):
            return None
        return round(value, 4) if 0 < value <= 1 else None
    return None


def _row_volume_and_max(
    row: list[str],
    score_cols: list[int],
) -> tuple[str | None, float | None, str | None]:
    best_label = ""
    best_score: float | None = None
    for cell in row:
        for name, score in find_embedded_scores(cell):
            if score > (best_score or 0):
                best_label = name
                best_score = score
    if best_score is None:
        best_score = _row_score_max(row, score_cols)
    if best_score is None or not best_label:
        return None, None, None
    volume = volume_from_scoring_label(best_label, default="")
    if not volume:
        return None, None, None
    return volume, best_score, best_label or None


def _row_score_max(row: list[str], score_cols: list[int]) -> float | None:
    for idx in score_cols:
        value = parse_score_range_max(_cell(row, idx))
        if value is not None:
            return value
        value = parse_short_score(_cell(row, idx))
        if value is not None:
            return value
    return None


def _cell(row: list[str], idx: int) -> str:
    return str(row[idx] or "") if 0 <= idx < len(row) else ""


def _parse_bare_points(text: str) -> float | None:
    compact = normalize_cell(text)
    match = _BARE_POINTS_RE.match(compact)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None
    if value <= 1 or value > 100:
        return None
    return value


def _row_bare_points(row: list[str], weight_cols: list[int]) -> float | None:
    for idx in weight_cols:
        value = _parse_bare_points(_cell(row, idx))
        if value is not None:
            return value
    return None


def _row_volume_label(row: list[str], name_cols: list[int]) -> str:
    for idx in name_cols:
        label = normalize_cell(_cell(row, idx))
        if label:
            return label
    return ""


def _is_volume_title(label: str) -> bool:
    compact = normalize_cell(label)
    return bool(compact and _VOLUME_TITLE_RE.match(compact))


__all__ = [
    "parse_bare_weight_volume_table",
    "parse_weighted_volume_definitions",
    "volume_from_scoring_label",
]
