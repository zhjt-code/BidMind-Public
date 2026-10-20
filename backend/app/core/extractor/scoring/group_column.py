# -*- coding: utf-8 -*-
"""评分表分组列结构识别（根治：结构判据取代词面白名单）。

职责: 从表格网格按"列结构 + 父子分值闭合"确定性识别分组列(如"评审因素"列),
 产出组声明对与逐行组归属映射。
不做: 不判定 volume 枚举、不做词面语义联想(不含任何"技术/报价/商务"类关键词表)、
 不落库、不调用 LLM/DB、不修改调用方既有解析行为。

为什么用结构而非词面(根因):
 `_GROUP_SUFFIX_GATE`(后缀表 部分/标/卷/评审/标准/项/书)与
 `volume_labeling._volume_label_has_signal`(关键词表 报价/价格/商务/综合/技术/
 资信/信用/信誉)都在用有限词表判断开放集合的组归属。doc1933(项目2447)原文 5 个
 大类中"设计方案(26分)"/"施工方案(30分)"不含任何表内词面, 被静默漏失, 致账本
 仅捕 3 组、18 项无标签落 unlabeled。分组列是**表格自带的结构**, 与措辞无关。

采纳门禁(两条必须同时成立, 任一不成立返回 None 让调用方回退旧逻辑):
 判据1 列结构: 自左向右首个满足"非空格均形如 `名称（N分）`、锚点>=2 个、
 首锚点之后无异形非空格"的列;
 判据2 父子闭合: 每个组的分值 == 其右侧明细列在该组行区间内的分值之和(容差
 SCORE_MATCH_TOLERANCE)。任一组不闭合即整体拒收(不做部分采纳, 避免
 错位标签污染下游对账)。

判据2 同时承担 `_GROUP_SUFFIX_GATE` 原有的防护职责: 项级行(如"质量保证措施
（15分）")右侧不存在能求和到自身的明细行, 天然无法闭合, 不会被误升为组。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from app.core.extractor.scoring.ledger.contracts import SCORE_MATCH_TOLERANCE
from app.core.extractor.scoring.ledger.label_normalization import is_total_like_label
from app.core.extractor.scoring.score_column_semantics import TOTAL_ROW_TERMS

# 单元格形态: "设计方案 （26分）" / "环境保护管理体系与措施（4分）" / "施工单位企业业绩1 （1分）"。
# 标签不限字符集与长度(明细项名可含数字/长达13字), 由 _is_usable_label 单独把关——
# 这里刻意不复用 declaration_capture._GROUP_PAREN_RE(其 {2,12} 纯汉字约束会漏明细项)。
_CELL_SCORE_RE = re.compile(
    r"^(?P<label>.+?)\s*[（(]\s*(?P<score>\d+(?:\.\d+)?)\s*分\s*[)）]\s*$"
)
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
_MIN_GROUP_COUNT = 2
_MAX_LABEL_LEN = 40


@dataclass(frozen=True)
class ScoringGroupSpan:
    """单个组: 原文标签 + 声明分值 + 所辖行区间(row_end 不含) + 明细分值明细。"""

    label: str
    score: float
    row_start: int
    row_end: int
    detail_scores: tuple[float, ...]


@dataclass(frozen=True)
class GroupColumnDetection:
    """分组列识别结果: 列位置 + 组序列 + 逐行组归属。"""

    column_index: int
    detail_column_index: int
    groups: tuple[ScoringGroupSpan, ...]
    row_labels: Mapping[int, str]

    @property
    def declared_total(self) -> float:
        return round(sum(group.score for group in self.groups), 4)


def detect_group_column(
    grid: Sequence[Sequence[object]] | None,
) -> GroupColumnDetection | None:
    """识别分组列; 判据1/2 任一不成立返回 None(调用方回退既有词面逻辑)。"""
    rows = _normalize_grid(grid)
    # 至少需要 表头/组行 + 一个明细行, 以及 分组列 + 明细列 两列。
    if len(rows) < 2 or max((len(row) for row in rows), default=0) < 2:
        return None

    column_count = max(len(row) for row in rows)
    for group_col in range(column_count - 1):
        anchors = _collect_group_anchors(rows, group_col)
        if anchors is None or len(anchors) < _MIN_GROUP_COUNT:
            continue
        detection = _resolve_with_detail_column(rows, group_col, anchors, column_count)
        if detection is not None:
            return detection
    return None


def _normalize_grid(grid: Sequence[Sequence[object]] | None) -> list[list[str]]:
    if not grid:
        return []
    return [[str(cell or "").strip() for cell in (row or [])] for row in grid]


def _cell(row: Sequence[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def parse_labeled_score(text: str) -> tuple[str, float] | None:
    """解析 "名称（N分）" 形态单元格; 非该形态或标签不含汉字/超长时返回 None。

    刻意**不**在此判定"总分/合计"类: 组列与明细列的汇总语义粒度不同——组声明须用
    严格的 `is_total_like_label`(含"得分"即拒), 而明细项名"投标报价得分（30分）"
    是合法项名, 只应排除真正的汇总行(`TOTAL_ROW_TERMS`)。两侧各自把关。
    """
    compact = str(text or "").strip()
    if not compact or len(compact) > _MAX_LABEL_LEN + 12:
        return None
    match = _CELL_SCORE_RE.match(compact.replace("\n", " "))
    if not match:
        return None
    label = match.group("label").strip(" 　:：、,，。;；")
    if not label or len(label) > _MAX_LABEL_LEN or not _CHINESE_RE.search(label):
        return None
    try:
        score = float(match.group("score"))
    except ValueError:
        return None
    if score <= 0:
        return None
    return label, score


def _parse_group_anchor(text: str) -> tuple[str, float] | None:
    """组列单元格: 额外排除"总分/合计"类汇总名(组声明不得由汇总行充当)。"""
    parsed = parse_labeled_score(text)
    if parsed is None or is_total_like_label(parsed[0]):
        return None
    return parsed


def _parse_detail_cell(text: str) -> tuple[str, float] | None:
    """明细列单元格: 仅排除真正的汇总行, 复用既有单源 `TOTAL_ROW_TERMS`。

    不套用 `is_total_like_label`——其 SKIP_TERMS 含裸"得分", 会误杀 doc1933 的
    合法明细项"投标报价得分（30分）", 进而使该组无明细可闭合、整体识别被拒。
    """
    parsed = parse_labeled_score(text)
    if parsed is None or any(term in parsed[0] for term in TOTAL_ROW_TERMS):
        return None
    return parsed


def _collect_group_anchors(
    rows: list[list[str]], column: int
) -> list[tuple[int, str, float]] | None:
    """判据1: 收集该列锚点; 首锚点之后出现异形非空格即判该列不是分组列(返回 None)。

    首锚点之前允许异形非空格(表头, 如"评审因素"); 汇总名(总分/合计)全程跳过,
    既不作锚点也不算异形。
    """
    anchors: list[tuple[int, str, float]] = []
    for row_index, row in enumerate(rows):
        text = _cell(row, column)
        if not text:
            continue
        parsed = _parse_group_anchor(text)
        if parsed is not None:
            anchors.append((row_index, parsed[0], parsed[1]))
            continue
        if _is_summary_cell(text):
            continue
        if anchors:
            # 组区间内混入非"名称（N分）"文本, 该列不具备分组列的结构同质性。
            return None
    return anchors


def _is_summary_cell(text: str) -> bool:
    """汇总格(如"合计 100分"/"总分")不参与锚点也不算结构异形。"""
    parsed = _CELL_SCORE_RE.match(str(text or "").replace("\n", " "))
    label = parsed.group("label").strip() if parsed else str(text or "")
    return is_total_like_label(label)


def _resolve_with_detail_column(
    rows: list[list[str]],
    group_col: int,
    anchors: list[tuple[int, str, float]],
    column_count: int,
) -> GroupColumnDetection | None:
    """判据2: 自 group_col 右侧逐列试作明细列, 要求全部组父子闭合。"""
    spans = _build_spans(anchors, len(rows))
    for detail_col in range(group_col + 1, column_count):
        groups = _close_groups(rows, spans, detail_col)
        if groups is None:
            continue
        row_labels = _build_row_labels(rows, groups, detail_col)
        if not row_labels:
            continue
        return GroupColumnDetection(
            column_index=group_col,
            detail_column_index=detail_col,
            groups=groups,
            row_labels=row_labels,
        )
    return None


def _build_spans(
    anchors: list[tuple[int, str, float]], row_total: int
) -> list[tuple[int, str, float, int]]:
    """组行区间: 本锚点行 → 下一锚点行(不含); 末组延伸到表尾。"""
    spans: list[tuple[int, str, float, int]] = []
    for position, (row_index, label, score) in enumerate(anchors):
        row_end = anchors[position + 1][0] if position + 1 < len(anchors) else row_total
        spans.append((row_index, label, score, row_end))
    return spans


def _close_groups(
    rows: list[list[str]],
    spans: list[tuple[int, str, float, int]],
    detail_col: int,
) -> tuple[ScoringGroupSpan, ...] | None:
    """逐组求明细和并比对声明分值; 任一组不闭合返回 None(整体拒收)。"""
    groups: list[ScoringGroupSpan] = []
    for row_start, label, score, row_end in spans:
        detail_scores: list[float] = []
        for row_index in range(row_start, min(row_end, len(rows))):
            parsed = _parse_detail_cell(_cell(rows[row_index], detail_col))
            if parsed is not None:
                detail_scores.append(parsed[1])
        if not detail_scores:
            return None
        if abs(sum(detail_scores) - score) > SCORE_MATCH_TOLERANCE:
            return None
        groups.append(
            ScoringGroupSpan(
                label=label,
                score=round(score, 4),
                row_start=row_start,
                row_end=row_end,
                detail_scores=tuple(detail_scores),
            )
        )
    return tuple(groups)


def _build_row_labels(
    rows: list[list[str]],
    groups: tuple[ScoringGroupSpan, ...],
    detail_col: int,
) -> dict[int, str]:
    """逐行组归属: 仅标注确有明细项的行(空行/续行不虚标)。"""
    row_labels: dict[int, str] = {}
    for group in groups:
        for row_index in range(group.row_start, min(group.row_end, len(rows))):
            if _parse_detail_cell(_cell(rows[row_index], detail_col)) is not None:
                row_labels[row_index] = group.label
    return row_labels


__all__ = [
    "GroupColumnDetection",
    "ScoringGroupSpan",
    "detect_group_column",
    "parse_labeled_score",
]
