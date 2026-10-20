"""框架表与权重汇总表解析（自 scoring_table_parser 拆出, 复杂度护栏）。

职责: 解析"卷级框架表(权重列+总分N分按M%折算)"与"评分汇总权重表(加权得分)"
      为卷定义; 框架表价格行携带完整公式时合成 price 事实(缺口B)。
不做: 不解析明细评分项(scoring_table_parser 职责)、不做嵌入式/列统计解析、
      不调用 LLM/DB。
"""

from __future__ import annotations

import re

from app.core.extractor.scoring.framework_price_synthesis import (
    synthesize_framework_price_item,
)
from app.core.extractor.scoring.score_column_semantics import parse_weight_value
from app.core.extractor.scoring.volume_labeling import volume_label_from_summary_row
from app.core.extractor.scoring.volume_parser import VolumeDefinition

# "总分100分" / "总分 100 分"
_VOLUME_MAX_RE = re.compile(r"总分\s*(\d+(?:\.\d+)?)\s*分")
# 卷定义行特征: 含"总分N分"且含"按M%...折算"
_VOLUME_DEF_RE = re.compile(r"总分\s*\d+(?:\.\d+)?\s*分.*?按\s*\d+(?:\.\d+)?\s*[%％].*?折算")

_ZH_VOLUME_MAP = (
    ("技术", "technical"),
    ("价格", "price"),
    ("商务", "commercial"),
    ("服务", "service"),
    ("资信", "qualification"),
)


def has_volume_definition_row(data: list[list[str]]) -> bool:
    """框架表判定: 任一行的拼接文本含"总分N分按M%折算"。"""
    for row in data:
        if _VOLUME_DEF_RE.search("".join(row)):
            return True
    return False


def parse_framework_table(
    header: list[str],
    data: list[list[str]],
    roles: dict[str, int | None],
):
    """框架表: 权重列 + "X总分N分按M%折算" → 卷定义(不产评分子项)。

    缺口B(样本1): 价格行"评分标准"列携带完整价格公式(技术明细另册)时, 以卷定义
    满分为权威合成一条 price 事实(framework_price_synthesis), 公式证据不再丢弃。
    返回 (volume_defs, items): items 至多含一条合成 price 事实。
    """
    weight_i = roles["weight"]
    name_i = _find_col(header, ("评审内容",))
    factor_i = _find_col(header, ("评审因素",))
    std_i = _find_col(header, ("评分标准",))
    volume_defs: dict[str, VolumeDefinition] = {}
    price_rule_parts: list[str] = []
    collecting_price_rule = False
    for row in data:
        weight = _parse_number(_cell(row, weight_i))
        if not weight or weight <= 0:
            # 价格行的公式续行(无权重值)并入规则文本
            if collecting_price_rule and _cell(row, std_i):
                price_rule_parts.append(_cell(row, std_i))
            continue
        collecting_price_rule = False
        zh_name = _cell(row, name_i) or _cell(row, factor_i)
        vol = _zh_to_volume(zh_name)
        if not vol or vol in volume_defs:
            continue
        std_text = _cell(row, std_i)
        if vol == "price" and std_text:
            price_rule_parts.append(std_text)
            collecting_price_rule = True
        vmax = _parse_volume_max(std_text)
        factor_name = (_cell(row, factor_i) or "").replace(" ", "") or None
        volume_defs[vol] = VolumeDefinition(
            volume=vol,
            weight=round(weight / 100.0, 4),
            volume_max=vmax or 100.0,
            label=zh_name.replace(" ", "") or None,
            source="framework",
            evidence_text=(std_text or None),
            row_title=factor_name or (zh_name.replace(" ", "") or None),
        )
    price_item = synthesize_framework_price_item(volume_defs, " ".join(price_rule_parts))
    return volume_defs, ([price_item] if price_item is not None else [])


def parse_weight_summary_defs(grid: list[list[str]]) -> dict[str, VolumeDefinition]:
    """评分汇总表: 优先尝试转置最终分值轴，命中返回；否则旧逻辑（加权得分行）。

    C1: 转置轴路径（最终分值制）优先于旧路径（加权得分汇总表）。
    """
    # C1: 转置轴优先——最终分值制（20/40/40）
    transposed = try_parse_transposed_axis_volume_defs(grid)
    if transposed is not None:
        return transposed

    # 旧逻辑: 加权得分汇总表（0.xx 权重行）
    table_text = "".join("".join(row) for row in grid).replace(" ", "")
    if "加权得分" not in table_text:
        return {}
    volume_defs: dict[str, VolumeDefinition] = {}
    for row in grid:
        row_joined = "".join(row).replace(" ", "")
        volume = _volume_from_summary_row(row_joined)
        if volume is None:
            continue
        weight = _weight_from_row(row)
        if weight is None:
            continue
        volume_defs.setdefault(
            volume,
            VolumeDefinition(
                volume=volume,
                weight=weight,
                volume_max=100.0,
                label=volume_label_from_summary_row(row_joined),
            ),
        )
    return volume_defs


def _volume_from_summary_row(text: str) -> str | None:
    if "报价" in text or "价格" in text:
        return "price"
    if "综合" in text or "商务" in text:
        return "commercial"
    if "技术" in text:
        return "technical"
    return None


def _weight_from_row(row: list[str]) -> float | None:
    for cell in row:
        weight = parse_weight_value(cell)
        if weight is not None:
            return weight
    return None


def _zh_to_volume(zh_name: str) -> str | None:
    name = (zh_name or "").replace(" ", "")
    for kw, vol in _ZH_VOLUME_MAP:
        if kw in name:
            return vol
    return None


def _parse_volume_max(text: str) -> float | None:
    m = _VOLUME_MAX_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _parse_number(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _find_col(header: list[str], keywords: tuple[str, ...]) -> int | None:
    for i, cell in enumerate(header):
        h = (cell or "").replace(" ", "")
        if any(k in h for k in keywords):
            return i
    return None


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def try_parse_transposed_axis_volume_defs(
    grid: list[list[str]], *, source_page: int | None = None
) -> dict[str, VolumeDefinition] | None:
    """转置最终分值轴入口薄调用（委托 transposed_score_axis 模块）。

    解析成功返回 {volume: VolumeDefinition}（最终分值制）；
    网格不符合转置轴特征则返回 None（由调用方尝试其他路径）。
    """
    from app.core.extractor.scoring.transposed_score_axis import (
        final_score_axis_to_volume_defs,
        parse_transposed_final_score_axis,
    )

    axis = parse_transposed_final_score_axis(grid, source_page=source_page)
    if axis is None:
        return None
    return final_score_axis_to_volume_defs(axis)


__all__ = [
    "has_volume_definition_row",
    "parse_framework_table",
    "parse_weight_summary_defs",
    "try_parse_transposed_axis_volume_defs",
]
