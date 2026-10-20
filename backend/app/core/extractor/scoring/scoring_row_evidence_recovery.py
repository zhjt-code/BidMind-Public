"""评分行噪声恢复：当分值格因OCR污染导致所有标准解析均失败时，
通过规则列中的独立整项证据推断真实分值，并物化为 ExtractedScoringItem。

职责: 验证并恢复单行的被污染分值；将恢复结果物化为 ExtractedScoringItem。
不做: 不接触正常可解析的分值格；不参与扣分步长/档次分判断；
      不依赖总分残差或取规则最大数字；不修改任何持久化结构。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

from app.core.extractor.scoring.category_inference import infer_scoring_category
from app.core.extractor.scoring.score_column_semantics import (
    is_structural_score_item_name,
    parse_score_range_max,
    parse_short_score,
    parse_short_score_tolerant,
)
from app.core.extractor.scoring.scoring_item_naming import normalize_scoring_item_name
from app.core.extractor.scoring.weighted_volume_table import volume_from_scoring_label
from app.schemas.requirement import ExtractedScoringItem


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveredRowScore:
    """Evidence-based recovery result for a garbled score cell.

    Attributes:
        score: The recovered score value (positive float).
        evidence_types: Tuple of evidence category names that agreed on this value.
    """

    score: float
    evidence_types: tuple[str, ...]


# ---------------------------------------------------------------------------
# Context carrier for item materialization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RowRecoveryContext:
    """Frozen carrier of all parser-local variables needed for one row recovery.

    Keeps the parser call-site thin: pass this dataclass instead of a long
    parameter list, and let attempt_row_recovery own all item construction.
    """

    fallback_name: str    # row name after space-stripping (from parser)
    score_cell: str       # raw score column cell (may contain OCR noise)
    rule_text: str        # rule column cell value (adj_rule in parser)
    content_text: str     # content/description column value for desc merge
    last_cat: str         # last seen category cell (for category inference)
    current_volume: str   # volume in scope at this row; may be "" if undetected
    volume_label: Optional[str]  # volume label string (may be None)
    row_page: Optional[int]      # source page index for this row (may be None)


# ---------------------------------------------------------------------------
# Evidence pattern constants
# ---------------------------------------------------------------------------

# Pattern to detect deduction verbs immediately before "共" in compact text.
# Used with a sliding window check, not a single-char lookbehind.
# Matches: 扣分, 扣减, 扣除, 累计扣除, 累扣, 减(去), 每减, 每扣, ...
_DEDUCTION_BEFORE_CUMULATIVE = re.compile(
    r"(?:"
    r"扣分|扣减|扣除|累计?扣除?|累扣"   # explicit compound deduction forms
    r"|每?[扣减]去?"                     # 扣, 扣去, 减, 减去, 每扣, 每减 …
    r")\s*$"
)

# Window size (chars) to inspect before each "共" occurrence.
_DEDUCTION_WINDOW = 12

# Type C: explicit full-score patterns. Each entry: (type_name, compiled_regex).
# Group 1 captures the numeric score value.
_FULL_SCORE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "有针对性且完全满足项目需求得15分"
    (
        "full_satisfy",
        re.compile(
            r"(?:有针对性且完全满足|完全满足|全部满足|全部达标).*?得(\d+(?:\.\d+)?)分"
        ),
    ),
    # "项目需求得15分" / "招标要求得15分"
    (
        "full_requirement",
        re.compile(r"(?:项目需求|招标要求)得(\d+(?:\.\d+)?)分"),
    ),
    # "整项得15分" / "整项最高15分" / "整项可得15分"
    (
        "full_item",
        re.compile(r"整项(?:最高|可?得)?(\d+(?:\.\d+)?)分"),
    ),
    # "满分15分"
    (
        "full_max_score",
        re.compile(r"满分(\d+(?:\.\d+)?)分"),
    ),
    # "最高可得15分" / "最高得15分" / "最高15分"
    # NOTE: "最高加N分" does NOT match because "加" ≠ "可?得"
    (
        "full_highest",
        re.compile(r"最高(?:可?得)?(\d+(?:\.\d+)?)分"),
    ),
    # "最多得15分" / "最多15分"
    (
        "full_most",
        re.compile(r"最多(?:可?得)?(\d+(?:\.\d+)?)分"),
    ),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_structural(name: str) -> bool:
    """Gate 1 helper: True if name is a structural/subtotal row, not a real item."""
    return is_structural_score_item_name(name.replace(" ", ""))


def _any_standard_parser_succeeds(score_cell: str) -> bool:
    """Gate 2 helper: True if any standard parser can parse the score cell.

    Covers strict (parse_short_score), tolerant (parse_short_score_tolerant),
    and range (parse_score_range_max).
    """
    return (
        parse_short_score(score_cell) is not None
        or parse_short_score_tolerant(score_cell) is not None
        or parse_score_range_max(score_cell) is not None
    )


def _is_deduction_context(compact_text: str, cumulative_match_start: int) -> bool:
    """Check whether "共" at cumulative_match_start appears in a deduction context.

    Inspects a window of up to _DEDUCTION_WINDOW chars immediately before the
    match position. Returns True if a deduction verb ends right at the boundary.
    """
    window_start = max(0, cumulative_match_start - _DEDUCTION_WINDOW)
    window = compact_text[window_start:cumulative_match_start]
    return bool(_DEDUCTION_BEFORE_CUMULATIVE.search(window))


def _extract_evidence_totals(rule_text: str) -> list[tuple[str, float]]:
    """Extract (evidence_type, total_score) pairs from rule_text.

    Deduction-context cumulative totals are excluded via window inspection.
    Each evidence category contributes at most one entry.
    Positive "总共N分" is treated as cumulative_total (window has no deduction verb).
    """
    results: list[tuple[str, float]] = []
    compact = rule_text.replace(" ", "")

    # Type B: cumulative total — "共N分" (with window-based deduction exclusion)
    for m in re.finditer(r"共(\d+(?:\.\d+)?)分", compact):
        val = float(m.group(1))
        if val <= 0:
            continue
        if _is_deduction_context(compact, m.start()):
            continue  # skip "扣分共N分", "减共N分", "累计扣除共N分" etc.
        results.append(("cumulative_total", val))
        break  # one cumulative-total evidence per rule is sufficient

    # Type C: explicit full-score statement (first matching pattern wins)
    for type_name, pat in _FULL_SCORE_PATTERNS:
        m = pat.search(compact)
        if m:
            val = float(m.group(1))
            if val > 0:
                results.append((type_name, val))
                break  # one full-score evidence per rule is sufficient

    return results


# ---------------------------------------------------------------------------
# Core recovery function
# ---------------------------------------------------------------------------


def recover_explicit_row_score(
    *,
    name: str,
    score_cell: str,
    rule_text: str,
) -> Optional[RecoveredRowScore]:
    """Attempt evidence-based score recovery for a single garbled row.

    All gates must pass; otherwise returns None.

    Gate 1 – name completeness:
        name is non-empty, non-structural, ≥ 2 chars after stripping spaces.

    Gate 2 – score_cell qualification:
        non-empty, contains at least one digit or "分", AND every standard
        parser (strict / tolerant / range) fails on it.

    Gate 3 – rule evidence agreement:
        rule_text yields ≥ 2 independent evidence categories that all agree
        on the same numeric total.  Deduction-context "共N分" is excluded.
        "最高加N分" is excluded (does not match "最高可?得?" patterns).

    Gate 4 – positive total: recovered total > 0.

    Not covered: deduction step values (扣N分), grade-band scores,
                 price-formula numbers, max number in rule_text.

    Args:
        name: Item name as seen by the parser (spaces may be stripped).
        score_cell: Raw score column cell (may contain spaces and noise).
        rule_text: Rule column cell for the same row.

    Returns:
        RecoveredRowScore if all gates pass, else None.
    """
    # Gate 1
    stripped = name.replace(" ", "")
    if len(stripped) < 2:
        return None
    if _is_structural(name):
        return None

    # Gate 2
    if not score_cell:
        return None
    if not (re.search(r"\d", score_cell) or "分" in score_cell):
        return None
    if _any_standard_parser_succeeds(score_cell):
        return None

    # Gate 3
    if not rule_text:
        return None
    evidence = _extract_evidence_totals(rule_text)
    if len(evidence) < 2:
        return None
    values = {v for _, v in evidence}
    if len(values) != 1:
        return None  # conflicting evidence

    total = next(iter(values))
    # Gate 4
    if total <= 0:
        return None

    return RecoveredRowScore(
        score=total,
        evidence_types=tuple(t for t, _ in evidence),
    )


# ---------------------------------------------------------------------------
# Item materialization (keeps parser call-site thin)
# ---------------------------------------------------------------------------


def attempt_row_recovery(ctx: RowRecoveryContext) -> Optional[ExtractedScoringItem]:
    """Attempt evidence-based recovery and materialize an ExtractedScoringItem.

    职责: Orchestrate score recovery + item construction from a frozen context.
    不做: Never access DB, LLM, or parser internals; never mutate shared state.

    Returns ExtractedScoringItem on success, None if recovery fails or item
    validation raises.
    """
    recovered = recover_explicit_row_score(
        name=ctx.fallback_name,
        score_cell=ctx.score_cell,
        rule_text=ctx.rule_text,
    )
    if recovered is None:
        return None

    # Normalize name (strips OCR noise / layout decoration)
    name = normalize_scoring_item_name(ctx.fallback_name, ctx.rule_text)

    # Merge content + rule for description (preserves content column value)
    desc_parts = [p for p in (ctx.content_text, ctx.rule_text) if p]
    desc = " ".join(dict.fromkeys(desc_parts)).strip()
    if len(desc) < 2:
        desc = f"{name}评分"

    category, basis = infer_scoring_category(f"{ctx.last_cat}{name}{desc}")

    # Resolve volume: prefer explicit current_volume, fall back to the project's
    # canonical mapping for tables where volume_group_marker cannot detect the
    # category column (e.g. content_i == 0).  default="" avoids false positives.
    volume = ctx.current_volume or volume_from_scoring_label(ctx.last_cat, default="")

    try:
        item = ExtractedScoringItem(
            category=category,
            category_basis=basis,
            score_item_name=(name or "未知项目")[:80],
            description=desc,
            score_weight=recovered.score,
            volume=volume,
            volume_label=ctx.volume_label,
            score_rules=[ctx.rule_text] if ctx.rule_text else [],
        )
    except ValidationError:
        return None

    if ctx.row_page is not None:
        item.source_pages = [ctx.row_page]

    return item
