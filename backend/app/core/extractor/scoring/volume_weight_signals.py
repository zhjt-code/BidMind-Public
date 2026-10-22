"""评分卷小数权重信号解析。

职责: 从评分表grid中识别`技术部分|0.5`、`商务部分 0.3`等小数权重声明并转为卷定义。
不做: 不提取评分项、不解析百分号折算句、不做合计校验以外的业务修正。
"""

from __future__ import annotations

import re

from app.core.extractor.scoring.column_role_inference import volume_from_group_text
from app.core.extractor.scoring.score_column_semantics import parse_weight_value
from app.core.extractor.scoring.volume_parser import VolumeDefinition

_INLINE_DECIMAL_WEIGHT_RE = re.compile(
    r"(?P<label>[\u4e00-\u9fff]{1,12}(?:标|部分|卷)?)\s*[:：]?\s*(?P<weight>0?\.\d+|1\.0+)"
)
_LABEL_TERMS = ("技术", "商务", "价格", "报价", "资信", "资格", "综合", "服务")


def parse_decimal_volume_weight_defs(grid: list[list[str]]) -> dict[str, VolumeDefinition]:
    """解析小数权重形态为卷定义。

    仅当解析出至少两个卷且权重和约等于1时采纳；`volume_max`统一取100，表示各卷卷内
    百分制再按权重折算。
    """
    candidates: list[tuple[str, float, str]] = []
    for row in grid:
        candidates.extend(_row_adjacent_candidates(row))
        candidates.extend(_row_inline_candidates(row))
    if len(candidates) < 2:
        return {}

    defs: dict[str, VolumeDefinition] = {}
    for label, weight, volume in candidates:
        if volume == "other" or volume in defs:
            continue
        defs[volume] = VolumeDefinition(
            volume=volume,
            weight=weight,
            volume_max=100.0,
            label=label,
        )
    if len(defs) < 2:
        return {}
    weight_total = round(sum(v.weight for v in defs.values()), 4)
    if abs(weight_total - 1.0) > 0.02:
        return {}
    return defs


def _row_adjacent_candidates(row: list[str]) -> list[tuple[str, float, str]]:
    out: list[tuple[str, float, str]] = []
    for idx, cell in enumerate(row[:-1]):
        label = _clean_label(cell)
        if not label:
            continue
        weight = parse_weight_value(row[idx + 1])
        if weight is None:
            continue
        volume = volume_from_group_text(label, "other")
        out.append((label, weight, volume))
    if out:
        return out

    # 合并单元格常把标签放首列、权重放末列，中间列均为空；按非空单元格相邻
    # 识别，且只接受恰好两个非空格，避免把评分明细行误判为卷权重声明。
    nonempty = [cell for cell in row if str(cell or "").strip()]
    if len(nonempty) == 2:
        label = _clean_label(nonempty[0])
        weight = parse_weight_value(nonempty[1])
        if label and weight is not None:
            volume = volume_from_group_text(label, "other")
            out.append((label, weight, volume))
    return out


def _row_inline_candidates(row: list[str]) -> list[tuple[str, float, str]]:
    out: list[tuple[str, float, str]] = []
    for cell in row:
        text = str(cell or "").replace(" ", "")
        if not text or not any(term in text for term in _LABEL_TERMS):
            continue
        for match in _INLINE_DECIMAL_WEIGHT_RE.finditer(text):
            label = _clean_label(match.group("label"))
            if not label:
                continue
            weight = parse_weight_value(match.group("weight"))
            if weight is None:
                continue
            volume = volume_from_group_text(label, "other")
            out.append((label, weight, volume))
    return out


def _clean_label(text: object) -> str:
    label = str(text or "").replace(" ", "").strip("：:;；,，。")
    if not label or len(label) > 20:
        return ""
    if not any(term in label for term in _LABEL_TERMS):
        return ""
    if re.search(r"\d+(?:\.\d+)?\s*分", label):
        return ""
    return label


__all__ = ["parse_decimal_volume_weight_defs"]
