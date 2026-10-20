"""评分规则列证据回填。

职责: 用装配层已验证的名称/分值/规则列，在唯一同名同分行上补齐候选的规则文本。
不做: 不重跑评分表解析、不改名称/分值/描述/卷别、不模糊匹配、不访问 LLM/DB。
"""
from __future__ import annotations

import re
import unicodedata

from app.core.extractor.scoring.score_column_semantics import (
    parse_short_score_tolerant,
)
from app.core.extractor.scoring.scoring_grid_payload import override_column_roles
from app.core.extractor.scoring.scoring_item_naming import (
    strip_scoring_name_decoration,
)
from app.schemas.requirement import ExtractedScoringItem

def backfill_rules_from_payload_roles(
    items: list[ExtractedScoringItem],
    rows: list[list[str]],
    lexical_roles: dict[str, int | None],
    column_roles: dict | None,
) -> list[ExtractedScoringItem]:
    """仅当载荷实际覆盖了词面角色时，按载荷角色执行规则回填。"""
    payload_roles = override_column_roles(lexical_roles, column_roles)
    if payload_roles is lexical_roles:
        return list(items)
    return backfill_rules_from_role_grid(items, rows, payload_roles)


def backfill_rules_from_role_grid(
    items: list[ExtractedScoringItem],
    rows: list[list[str]],
    roles: dict[str, int | None],
) -> list[ExtractedScoringItem]:
    """对无规则候选做精确行回填；匹配不唯一或列角色不完整时保持原样。"""
    name_col = roles.get("name")
    score_col = roles.get("score")
    rule_col = roles.get("rule")
    if (
        name_col is None
        or score_col is None
        or rule_col is None
        or rule_col in {name_col, score_col}
    ):
        return list(items)

    row_evidence: dict[tuple[str, float], list[str]] = {}
    for row in rows:
        score = parse_short_score_tolerant(_cell(row, score_col))
        name = _normalize_name(_cell(row, name_col))
        rule = _cell(row, rule_col).strip()
        if score is None or not name or not rule:
            continue
        row_evidence.setdefault((name, round(float(score), 4)), []).append(rule)

    enriched: list[ExtractedScoringItem] = []
    for item in items:
        if item.score_rules or item.deduction_rules:
            enriched.append(item)
            continue
        key = (
            _normalize_name(item.score_item_name),
            round(float(item.score_weight or 0.0), 4),
        )
        matches = row_evidence.get(key, [])
        if len(matches) != 1:
            enriched.append(item)
            continue
        enriched.append(item.model_copy(update={"score_rules": matches}))
    return enriched


def _normalize_name(value: str) -> str:
    """名称精确键：NFKC、剥分值装饰、去空白与常规边界标点。"""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = strip_scoring_name_decoration(text)
    return re.sub(r"\s+", "", text).strip(":：;；,，。")


def _cell(row: list[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return str(row[index] or "")


__all__ = [
    "backfill_rules_from_payload_roles",
    "backfill_rules_from_role_grid",
]
