"""Deterministic time-range checker for Chinese government bid review.

Pure functions only — no DB or network calls.
All dates must be injected via the ``today: date`` parameter;
``datetime.now()`` / ``date.today()`` are never used here.

Usage example::

    from datetime import date
    from app.core.extractor.tools.date_range_checker import (
        DateRangeAssumption,
        check_performance_anchor,
    )

    verdict = check_performance_anchor(
        requirement_text="近三年完成的类似项目",
        response_text="合同签订日期：2023年5月",
        today=date(2025, 6, 15),
    )
    assert verdict.verdict == "in_range"
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import dateparser
from dateutil.relativedelta import relativedelta

# ──────────────────────────────────────────────────────────────────────────────
# Public data models
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DateRangeAssumption:
    """Configurable assumptions for date range parsing.

    Attributes:
        base_date_source: Which business date ``today`` represents.  The
            caller is responsible for passing the correct date; this field
            is recorded for auditability only and does not change the
            calculation (the function always uses the value of ``today``).
        boundary_inclusive: When ``True`` (default), range boundaries are
            included in the in-range check (``start <= anchor <= end``).
        anchor_priority: Ordered tuple of anchor-type names.  The first
            type that is found in the response text is used.
    """

    base_date_source: Literal[
        "announcement", "submission_deadline", "review_now"
    ] = "announcement"
    boundary_inclusive: bool = True
    anchor_priority: tuple[str, ...] = (
        "contract_sign",
        "acceptance",
        "completion",
        "invoice",
    )


@dataclass(frozen=True)
class DateRangeVerdict:
    """Result of a date range check.

    Attributes:
        verdict: One of ``"in_range"``, ``"out_of_range"``, ``"ambiguous"``,
            ``"unparseable"``.  When returned by :func:`parse_time_requirement`
            alone, ``"in_range"`` means *the range was successfully parsed*
            (not that an anchor is within it).
        parsed_range: The resolved ``(start, end)`` date pair, or ``None``
            when parsing failed.
        parsed_anchor: The anchor date extracted from the response text, or
            ``None`` when not applicable / not found.
        ambiguity_reasons: Human-readable reason codes explaining why the
            verdict is ``"ambiguous"`` or ``"unparseable"``.
    """

    verdict: Literal["in_range", "out_of_range", "ambiguous", "unparseable"]
    parsed_range: tuple[date, date] | None = None
    parsed_anchor: date | None = None
    ambiguity_reasons: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ──────────────────────────────────────────────────────────────────────────────

_CN_DIGITS: dict[str, int] = {
    "十五": 15,
    "十四": 14,
    "十三": 13,
    "十二": 12,
    "十一": 11,
    "两": 2,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

# Maps anchor type name → list of Chinese keywords that signal that anchor
# in response text.  Ordered from most specific to least specific within
# each list so that the first keyword hit wins.
_ANCHOR_KEYWORDS: dict[str, list[str]] = {
    "contract_sign": [
        "合同签订日期",
        "合同签订时间",
        "签订日期",
        "签订于",
        "合同签于",
        "合同日期",
    ],
    "acceptance": [
        "验收日期",
        "验收时间",
        "验收完成",
        "最终验收",
    ],
    "completion": [
        "竣工时间",
        "竣工日期",
        "完工时间",
        "完成时间",
        "完成日期",
    ],
    "invoice": [
        "开票日期",
        "发票日期",
        "开具发票",
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# Compiled regex patterns  (built once at import time)
# ──────────────────────────────────────────────────────────────────────────────

# Priority 1a — full date range: YYYY年MM月DD日至YYYY年MM月DD日
#                               or YYYY.MM.DD至YYYY.MM.DD
#                               or YYYY/MM/DD至YYYY/MM/DD
_RE_FULL_DATE_RANGE = re.compile(
    r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?"
    r"\s*(?:至|—)\s*"
    r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?"
)

# Priority 1b — year-month range: 2021年3月至2024年6月
_RE_YM_RANGE = re.compile(
    r"(\d{4})年(\d{1,2})月\s*(?:至|—|-)\s*(\d{4})年(\d{1,2})月"
)

# Priority 1c — year-only range: 2021年至2024年  /  2021年-2024年  /  2021-2024年
_RE_YEAR_RANGE = re.compile(r"(\d{4})\s*年?\s*(?:至|—|-)\s*(\d{4})\s*年")

# Priority 2 — since-year: 2023年以来 / 自2023年起 / 自2023年起至今 / 2023年至今
_RE_SINCE_YEAR = re.compile(
    r"(?:自\s*)?(\d{4})\s*年\s*(?:以来|起(?:至今)?|至今)"
)

# Priority 3 — recent-N-years: 近三年 / 最近3年 / 近十五年
_CN_NUM_ALTS = "|".join(sorted(_CN_DIGITS.keys(), key=len, reverse=True))
_RE_RECENT_N_YEARS = re.compile(
    rf"(?:近|最近)\s*(\d+|{_CN_NUM_ALTS})\s*年"
)

# Inline date expression for anchor extraction — ordered most-specific first
_RE_DATE_EXPR = re.compile(
    r"\d{4}年\d{1,2}月\d{1,2}日"        # 2023年5月15日
    r"|\d{4}[./-]\d{1,2}[./-]\d{1,2}"  # 2024.03.15 / 2024-03-15 / 2024/03/15
    r"|\d{4}年\d{1,2}月"                # 2023年5月
    r"|\d{4}年"                          # 2023年  (least specific)
)


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────


def _cn_num_to_int(token: str) -> int | None:
    """Convert a Chinese numeral string or ASCII digit string to ``int``.

    Returns ``None`` for unrecognised tokens.
    """
    if token.isdigit():
        return int(token)
    return _CN_DIGITS.get(token)


def _safe_date(year: int, month: int, day: int = 1) -> date | None:
    """Construct a :class:`~datetime.date`, returning ``None`` on invalid values."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_end(year: int, month: int) -> date | None:
    """Return the last calendar day of *month* in *year*, or ``None`` on error."""
    try:
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, last_day)
    except ValueError:
        return None


def _make_dp_settings(today: date) -> dict:
    """Build a ``dateparser`` settings dict anchored to *today*.

    Note: locale/language is passed separately as ``languages=['zh']`` to
    ``dateparser.parse``; it is not a valid key inside the settings dict.
    """
    return {
        "TIMEZONE": "Asia/Shanghai",
        "RELATIVE_BASE": datetime.combine(today, datetime.min.time()),
        "RETURN_AS_TIMEZONE_AWARE": False,
        "PREFER_DAY_OF_MONTH": "first",
        "DATE_ORDER": "YMD",
    }


def _dp_parse(text: str, today: date) -> date | None:
    """Parse *text* as a date using ``dateparser`` with Chinese locale settings.

    Uses *today* as the relative base and never touches the system clock.
    Returns ``None`` when parsing fails for any reason.
    """
    try:
        result = dateparser.parse(
            text,
            languages=["zh"],
            settings=_make_dp_settings(today),
        )
        return result.date() if result else None
    except Exception:
        return None


def _extract_anchor_dates(text: str, today: date) -> dict[str, date]:
    """Find named anchor dates in *text* by keyword proximity.

    For each anchor type (``contract_sign``, ``acceptance``, ``completion``,
    ``invoice``), searches for associated Chinese keywords and extracts the
    nearest date expression that follows the keyword (within 50 characters).

    Args:
        text: Response text from the bid document.
        today: Reference date used by ``dateparser``; never the system clock.

    Returns:
        A mapping of ``anchor_type → date`` for every anchor type found.
        Types with no match are absent from the dict.
    """
    found: dict[str, date] = {}
    for anchor_type, keywords in _ANCHOR_KEYWORDS.items():
        for kw in keywords:
            pos = text.find(kw)
            if pos == -1:
                continue
            after = text[pos + len(kw) : pos + len(kw) + 50]
            m = _RE_DATE_EXPR.search(after)
            if m:
                parsed = _dp_parse(m.group(0), today)
                if parsed is not None:
                    found[anchor_type] = parsed
                    break  # one date per anchor type is sufficient
    return found


def _select_anchor(
    anchors: dict[str, date],
    priority: tuple[str, ...],
) -> date | None:
    """Return the highest-priority anchor date from *anchors*.

    Iterates *priority* in order and returns the first type found in
    *anchors*.  Falls back to the first available anchor when no priority
    type is present.
    """
    for ptype in priority:
        if ptype in anchors:
            return anchors[ptype]
    if anchors:
        return next(iter(anchors.values()))
    return None


def _in_range(anchor: date, start: date, end: date, inclusive: bool) -> bool:
    """Return ``True`` when *anchor* lies within the range."""
    if inclusive:
        return start <= anchor <= end
    return start < anchor < end


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def parse_time_requirement(
    text: str,
    today: date,
    assumption: DateRangeAssumption | None = None,
) -> DateRangeVerdict:
    """Parse a time requirement expression into a concrete date range.

    Recognises the following Chinese time patterns (checked in priority order):

    1. **Explicit full-date range** — ``YYYY年MM月DD日至YYYY年MM月DD日``,
       ``YYYY.MM.DD至YYYY.MM.DD``
    2. **Year-month range** — ``2021年3月至2024年6月``
    3. **Year-only range** — ``2021年至2024年``, ``2021-2024年``
    4. **Since-year** — ``2023年以来``, ``自2023年起``, ``2023年至今``
    5. **Recent-N-years** — ``近三年``, ``最近5年`` (supports Arabic and
       Chinese numerals 1–15 plus ``两``)

    Degradation rules (R1 safety):
    - If no pattern is recognised → ``"unparseable"``; **never** ``"in_range"``.
    - If the resolved start date is in the future (since-year pattern) →
      ``"ambiguous"`` with reason ``"since_year_in_future"``.

    Args:
        text: Requirement text containing the time expression.
        today: Reference date; never call ``datetime.now()`` inside this
            function — use *today* exclusively.
        assumption: Parsing assumptions (defaults to
            :class:`DateRangeAssumption`).

    Returns:
        A :class:`DateRangeVerdict` where ``verdict="in_range"`` indicates
        that the range was parsed successfully (``parsed_range`` is set),
        and ``verdict`` is ``"unparseable"`` or ``"ambiguous"`` otherwise.
    """
    if assumption is None:
        assumption = DateRangeAssumption()

    txt = text.strip()

    # ── Priority 1a: explicit full-date range ─────────────────────────────────
    m = _RE_FULL_DATE_RANGE.search(txt)
    if m:
        start = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        end = _safe_date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        if start is None or end is None:
            return DateRangeVerdict(
                verdict="unparseable",
                ambiguity_reasons=["invalid_date_components"],
            )
        if start > end:
            return DateRangeVerdict(
                verdict="unparseable",
                ambiguity_reasons=["range_start_after_end"],
            )
        return DateRangeVerdict(verdict="in_range", parsed_range=(start, end))

    # ── Priority 1b: year-month range ─────────────────────────────────────────
    m = _RE_YM_RANGE.search(txt)
    if m:
        start = _safe_date(int(m.group(1)), int(m.group(2)))
        end = _month_end(int(m.group(3)), int(m.group(4)))
        if start is None or end is None:
            return DateRangeVerdict(
                verdict="unparseable",
                ambiguity_reasons=["invalid_date_components"],
            )
        if start > end:
            return DateRangeVerdict(
                verdict="unparseable",
                ambiguity_reasons=["range_start_after_end"],
            )
        return DateRangeVerdict(verdict="in_range", parsed_range=(start, end))

    # ── Priority 1c: year-only range ──────────────────────────────────────────
    m = _RE_YEAR_RANGE.search(txt)
    if m:
        start_year, end_year = int(m.group(1)), int(m.group(2))
        start = _safe_date(start_year, 1, 1)
        end = _safe_date(end_year, 12, 31)
        if start is None or end is None:
            return DateRangeVerdict(
                verdict="unparseable",
                ambiguity_reasons=["invalid_date_components"],
            )
        if start > end:
            return DateRangeVerdict(
                verdict="unparseable",
                ambiguity_reasons=["range_start_after_end"],
            )
        return DateRangeVerdict(verdict="in_range", parsed_range=(start, end))

    # ── Priority 2: X年以来 / 自X年起 / X年至今 ──────────────────────────────
    m = _RE_SINCE_YEAR.search(txt)
    if m:
        since_year = int(m.group(1))
        start = _safe_date(since_year, 1, 1)
        if start is None:
            return DateRangeVerdict(
                verdict="unparseable",
                ambiguity_reasons=["invalid_since_year"],
            )
        if start > today:
            return DateRangeVerdict(
                verdict="ambiguous",
                ambiguity_reasons=["since_year_in_future"],
            )
        return DateRangeVerdict(verdict="in_range", parsed_range=(start, today))

    # ── Priority 3: 近N年 / 最近N年 ──────────────────────────────────────────
    m = _RE_RECENT_N_YEARS.search(txt)
    if m:
        n_raw = m.group(1)
        n = _cn_num_to_int(n_raw)
        if n is None or n <= 0:
            return DateRangeVerdict(
                verdict="unparseable",
                ambiguity_reasons=[f"unrecognised_n_years: {n_raw!r}"],
            )
        start = today - relativedelta(years=n)
        return DateRangeVerdict(verdict="in_range", parsed_range=(start, today))

    return DateRangeVerdict(
        verdict="unparseable",
        ambiguity_reasons=["no_time_pattern_found"],
    )


def check_performance_anchor(
    requirement_text: str,
    response_text: str,
    today: date,
    *,
    assumption: DateRangeAssumption | None = None,
) -> DateRangeVerdict:
    """Check whether a performance date in *response_text* falls within the
    time range stated in *requirement_text*.

    Processing steps:

    1. Call :func:`parse_time_requirement` to resolve the requirement into a
       ``(start, end)`` date range.
    2. Propagate ``"unparseable"`` or ``"ambiguous"`` verdicts immediately —
       the function never returns ``"in_range"`` when parsing fails.
    3. Extract candidate anchor dates from *response_text* by scanning for
       Chinese keywords (合同签订日期, 验收日期, 竣工时间, …) followed by a
       date expression within 50 characters.
    4. Select the best anchor using ``assumption.anchor_priority``.
    5. If no anchor is found, return ``"ambiguous"`` with reason
       ``"anchor_not_found"``; the ``parsed_range`` is still populated so
       the caller can surface a partial result.
    6. Compare the anchor to the range respecting ``boundary_inclusive`` and
       return ``"in_range"`` or ``"out_of_range"``.

    Args:
        requirement_text: Text from the tender document describing the
            time requirement (招标要求), e.g. ``"近三年完成的类似项目"``.
        response_text: Text from the bid document containing the
            performance claim (投标响应), e.g.
            ``"合同签订日期：2023年5月"``.
        today: Reference date; never call ``datetime.now()`` here.
        assumption: Parsing and comparison assumptions.  If omitted,
            :class:`DateRangeAssumption` defaults are used.

    Returns:
        A :class:`DateRangeVerdict` with all evidence fields populated.
    """
    if assumption is None:
        assumption = DateRangeAssumption()

    range_result = parse_time_requirement(requirement_text, today, assumption)

    # Propagate parse failures — R1 safety: never silently return in_range
    if range_result.parsed_range is None:
        return range_result

    anchors = _extract_anchor_dates(response_text, today)
    anchor_date = _select_anchor(anchors, assumption.anchor_priority)

    if anchor_date is None:
        return DateRangeVerdict(
            verdict="ambiguous",
            parsed_range=range_result.parsed_range,
            ambiguity_reasons=["anchor_not_found"],
        )

    start, end = range_result.parsed_range
    verdict: Literal["in_range", "out_of_range"] = (
        "in_range"
        if _in_range(anchor_date, start, end, assumption.boundary_inclusive)
        else "out_of_range"
    )
    return DateRangeVerdict(
        verdict=verdict,
        parsed_range=range_result.parsed_range,
        parsed_anchor=anchor_date,
    )
