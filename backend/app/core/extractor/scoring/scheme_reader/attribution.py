"""SchemeMap 归属贴标器。

职责: 把确定性解析出的评分项按 SchemeMap 行区间归属重贴 volume/volume_label/
      scheme(enforce), 或仅记录与现行归属的分歧(shadow)。
不做: 不调用 LLM、不解析表格、不做守恒裁决、不修改分值与名称。

匹配策略(双重证据, 防误贴):
- 行骨架因素文本与评分项名称/描述的归一包含匹配(因素为纯序号时不作文本依据);
- 分值一致性(骨架分值格与 score_weight 相等)作为并联证据;
- 候选归属不唯一且卷/方案不一致时放弃贴标(fail-open), 记录分歧诊断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.core.extractor.scoring.scheme_reader.contracts import (
    SchemeMap,
    SpanAssignment,
)

_SCHEME_PREFIX = "scheme_"
_MIN_FACTOR_TEXT_LEN = 2


@dataclass(frozen=True)
class _RowRecord:
    """可匹配的骨架行记录。"""

    page: int
    factor: str
    score: float | None
    assignment: SpanAssignment


@dataclass
class AttributionOutcome:
    """贴标结果(shadow 模式 stamped 恒为 0, 分歧只进 divergences)。"""

    stamped: int = 0
    unmatched: int = 0
    divergences: list[str] = field(default_factory=list)


def _normalize(text: object) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _parse_score(text: object) -> float | None:
    matched = re.match(r"^(\d+(?:\.\d+)?)", _normalize(text))
    if not matched:
        return None
    try:
        return float(matched.group(1))
    except ValueError:
        return None


def _build_row_records(scheme_map: SchemeMap, signals: dict) -> list[_RowRecord]:
    tables = {
        (int(t.get("page", 0) or 0), int(t.get("table_index", 0) or 0)): t
        for t in (signals.get("tables") or [])
    }
    records: list[_RowRecord] = []
    for assignment in scheme_map.assignments:
        table = tables.get((assignment.page, assignment.table_index))
        if table is None:
            continue
        for row in table.get("rows") or []:
            row_index = int(row.get("row_index", -1))
            if not (assignment.row_start <= row_index <= assignment.row_end):
                continue
            records.append(
                _RowRecord(
                    page=assignment.page,
                    factor=_normalize(row.get("factor")),
                    score=_parse_score(row.get("score")),
                    assignment=assignment,
                )
            )
    return records


def _factor_is_textual(factor: str) -> bool:
    return len(factor) >= _MIN_FACTOR_TEXT_LEN and not factor.isdigit()


def _row_matches_item(record: _RowRecord, item: Any) -> bool:
    item_pages = [int(p) for p in (getattr(item, "source_pages", None) or [])]
    if item_pages and record.page not in item_pages:
        return False
    item_score = getattr(item, "score_weight", None)
    score_agrees = (
        record.score is not None
        and item_score is not None
        and abs(record.score - float(item_score)) < 1e-6
    )
    name = _normalize(getattr(item, "score_item_name", ""))
    desc = _normalize(getattr(item, "description", ""))
    if _factor_is_textual(record.factor):
        text_hit = (
            (name and (record.factor in name or name in record.factor))
            or (desc and record.factor in desc)
        )
        if text_hit:
            return True
        # 因素文本存在但不匹配: 不允许仅凭分值匹配(同分值明细行极常见)
        return False
    # 纯序号/空因素行: 只能依赖 页码∩分值 双证据
    return bool(item_pages) and score_agrees


def _resolve_assignment(
    records: list[_RowRecord], item: Any
) -> tuple[SpanAssignment | None, bool]:
    """返回 (归属, 是否歧义): 候选跨多个不同(方案,卷)时判歧义放弃。"""
    hits = [r.assignment for r in records if _row_matches_item(r, item)]
    if not hits:
        return None, False
    keys = {(a.scheme_index, a.volume) for a in hits}
    if len(keys) > 1:
        return None, True
    return hits[0], False


def stamp_scheme_attribution(
    items: list[Any],
    scheme_map: SchemeMap,
    signals: dict,
    *,
    enforce: bool,
) -> AttributionOutcome:
    """按 SchemeMap 对评分项贴卷/方案标签; shadow(enforce=False) 只记分歧。"""
    outcome = AttributionOutcome()
    records = _build_row_records(scheme_map, signals)
    if not records:
        outcome.divergences.append("SchemeMap 无可匹配行记录, 贴标跳过")
        return outcome

    for item in items:
        assignment, ambiguous = _resolve_assignment(records, item)
        if assignment is None:
            outcome.unmatched += 1
            if ambiguous:
                outcome.divergences.append(
                    f"归属歧义放弃贴标: {getattr(item, 'score_item_name', '')[:20]}"
                )
            continue
        target_scheme = f"{_SCHEME_PREFIX}{assignment.scheme_index}"
        current_volume = str(getattr(item, "volume", "") or "unspecified")
        current_scheme = str(getattr(item, "scheme", "") or "")
        changed = (
            current_volume != assignment.volume or current_scheme != target_scheme
        )
        if changed:
            outcome.divergences.append(
                f"{getattr(item, 'score_item_name', '')[:20]}: "
                f"{current_volume}/{current_scheme or '-'} -> "
                f"{assignment.volume}/{target_scheme}"
            )
        if not enforce or not changed:
            continue
        try:
            item.volume = assignment.volume
            if assignment.volume_label:
                item.volume_label = assignment.volume_label
            item.scheme = target_scheme
            outcome.stamped += 1
        except (AttributeError, ValueError) as exc:
            outcome.divergences.append(
                f"贴标失败({getattr(item, 'score_item_name', '')[:20]}): {exc}"
            )

    if outcome.divergences:
        logger.info(
            "[评分结构贴标] enforce={} 改标={} 未匹配={} 分歧样例: {}",
            enforce,
            outcome.stamped,
            outcome.unmatched,
            "; ".join(outcome.divergences[:5]),
        )
    return outcome


__all__ = ["AttributionOutcome", "stamp_scheme_attribution"]
