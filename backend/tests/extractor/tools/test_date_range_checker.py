"""Tests for date_range_checker.py

Test layers
-----------
L0 — 8 typical cases          (5 in_range + 3 out_of_range)
L1 — 10 ambiguity / assumption variation
L2 — 8 boundary conditions
L3 — 8 expression variants
L4 — 6 degradation            (NEVER in_range; must be ambiguous or unparseable)

Every test injects ``today`` explicitly — no system-clock usage.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.extractor.tools.date_range_checker import (
    DateRangeAssumption,
    DateRangeVerdict,
    check_performance_anchor,
    parse_time_requirement,
)


# ──────────────────────────────────────────────────────────────────────────────
# L0 — Typical cases
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "req, resp, today, expected",
    [
        # ── 5 in_range ────────────────────────────────────────────────────────
        pytest.param(
            "具备近三年类似项目业绩",
            "合同签订日期：2023年5月",
            date(2025, 6, 15),
            "in_range",
            id="l0-01-recent3-contract-sign",
        ),
        pytest.param(
            "2020年以来完成的类似项目",
            "合同签订日期：2022年3月",
            date(2025, 6, 15),
            "in_range",
            id="l0-02-since-year-contract-sign",
        ),
        pytest.param(
            "2021年至2024年内完成的工程",
            "竣工时间：2023年6月",
            date(2025, 6, 15),
            "in_range",
            id="l0-03-year-range-completion",
        ),
        pytest.param(
            "具有2019年至2025年期间的业绩",
            "签订于2021年1月",
            date(2025, 6, 15),
            "in_range",
            id="l0-04-year-range-signed-at",
        ),
        pytest.param(
            "近五年完成的类似工程",
            "验收日期：2022.08.15",
            date(2025, 6, 15),
            "in_range",
            id="l0-05-recent5-acceptance-dot-date",
        ),
        # ── 3 out_of_range ────────────────────────────────────────────────────
        pytest.param(
            "2021年至2023年内完成的项目",
            "合同签订日期：2019年12月",
            date(2025, 6, 15),
            "out_of_range",
            id="l0-06-year-range-before-start",
        ),
        pytest.param(
            "近三年业绩要求",
            "竣工时间：2021年5月15日",
            date(2025, 6, 15),
            "out_of_range",
            id="l0-07-recent3-completion-too-old",
        ),
        pytest.param(
            "2023年以来完成的工程",
            "验收日期：2022年6月",
            date(2025, 6, 15),
            "out_of_range",
            id="l0-08-since-year-acceptance-before-start",
        ),
    ],
)
def test_l0_typical(req: str, resp: str, today: date, expected: str) -> None:
    """L0: standard inputs produce the correct in_range / out_of_range verdict."""
    result = check_performance_anchor(req, resp, today)
    assert result.verdict == expected


# ──────────────────────────────────────────────────────────────────────────────
# L1 — Ambiguity / assumption variation
# ──────────────────────────────────────────────────────────────────────────────


class TestL1Ambiguity:
    """Same performance data, different assumptions → different outcomes."""

    def test_l1_01_recent3_standard_in_range(self) -> None:
        """近三年 with anchor well inside the window → in_range."""
        result = check_performance_anchor(
            "近三年", "合同签订日期：2023年5月", date(2025, 6, 15)
        )
        assert result.verdict == "in_range"
        assert result.parsed_anchor == date(2023, 5, 1)

    def test_l1_02_earlier_today_pushes_anchor_out(self) -> None:
        """Moving today back enough makes the same anchor fall after end → out."""
        # today=2023-04-30  →  end=2023-04-30; anchor=2023-05-01 > end
        result = check_performance_anchor(
            "近三年", "合同签订日期：2023年5月", date(2023, 4, 30)
        )
        assert result.verdict == "out_of_range"

    def test_l1_03_boundary_inclusive_start_is_in_range(self) -> None:
        """Anchor exactly on start (inclusive=True) → in_range."""
        today = date(2025, 5, 1)  # start = 2022-05-01
        result = check_performance_anchor(
            "近三年",
            "合同签订日期：2022年5月1日",
            today,
            assumption=DateRangeAssumption(boundary_inclusive=True),
        )
        assert result.verdict == "in_range"

    def test_l1_04_boundary_non_inclusive_start_is_out_of_range(self) -> None:
        """Anchor exactly on start (inclusive=False) → out_of_range."""
        today = date(2025, 5, 1)  # start = 2022-05-01
        result = check_performance_anchor(
            "近三年",
            "合同签订日期：2022年5月1日",
            today,
            assumption=DateRangeAssumption(boundary_inclusive=False),
        )
        assert result.verdict == "out_of_range"

    def test_l1_05_anchor_not_found_returns_ambiguous(self) -> None:
        """Response without any date → ambiguous with anchor_not_found."""
        result = check_performance_anchor(
            "近三年完成的类似项目",
            "本公司具备承接此类工程的能力和资质，参与多个国家级项目。",
            date(2025, 6, 15),
        )
        assert result.verdict == "ambiguous"
        assert "anchor_not_found" in result.ambiguity_reasons

    def test_l1_06_ambiguous_preserves_parsed_range(self) -> None:
        """Parsed range is still returned even when anchor is missing."""
        result = check_performance_anchor(
            "2021年至2024年",
            "公司成立于2010年，具有丰富经验。",
            date(2025, 6, 15),
        )
        assert result.verdict == "ambiguous"
        assert result.parsed_range == (date(2021, 1, 1), date(2024, 12, 31))
        assert result.parsed_anchor is None

    def test_l1_07_anchor_priority_contract_over_completion(self) -> None:
        """contract_sign is in-range; completion is out-of-range.
        Default priority (contract_sign first) → in_range."""
        resp = "竣工时间：2020年3月\n合同签订日期：2023年7月"
        result = check_performance_anchor(
            "近三年",
            resp,
            date(2025, 6, 15),
            assumption=DateRangeAssumption(
                anchor_priority=("contract_sign", "acceptance", "completion", "invoice")
            ),
        )
        assert result.verdict == "in_range"
        assert result.parsed_anchor == date(2023, 7, 1)

    def test_l1_08_anchor_priority_completion_over_contract(self) -> None:
        """Flipped priority: completion is selected → out_of_range."""
        resp = "竣工时间：2020年3月\n合同签订日期：2023年7月"
        result = check_performance_anchor(
            "近三年",
            resp,
            date(2025, 6, 15),
            assumption=DateRangeAssumption(
                anchor_priority=("completion", "contract_sign", "acceptance", "invoice")
            ),
        )
        assert result.verdict == "out_of_range"
        assert result.parsed_anchor == date(2020, 3, 1)

    def test_l1_09_since_year_anchor_exactly_on_start(self) -> None:
        """2023年以来 → start=2023-01-01; anchor=2023-01-01 → in_range (inclusive)."""
        result = check_performance_anchor(
            "2023年以来完成的项目",
            "合同签订日期：2023年1月",
            date(2025, 6, 15),
        )
        assert result.verdict == "in_range"
        assert result.parsed_anchor == date(2023, 1, 1)

    def test_l1_10_different_n_years_different_verdicts_same_anchor(self) -> None:
        """近两年 vs 近三年 with the same anchor → opposite verdicts."""
        anchor_resp = "合同签订日期：2023年1月"
        today = date(2025, 6, 15)

        # 近两年: start=2023-06-15; anchor=2023-01-01 < start → out_of_range
        two_year = check_performance_anchor("近两年", anchor_resp, today)
        # 近三年: start=2022-06-15; anchor=2023-01-01 > start → in_range
        three_year = check_performance_anchor("近三年", anchor_resp, today)

        assert two_year.verdict == "out_of_range"
        assert three_year.verdict == "in_range"


# ──────────────────────────────────────────────────────────────────────────────
# L2 — Boundary conditions
# ──────────────────────────────────────────────────────────────────────────────


class TestL2Boundary:
    """Exact boundary dates and edge cases."""

    def test_l2_01_anchor_on_since_year_start_inclusive(self) -> None:
        """Anchor = Jan 1 of since-year → in_range (boundary inclusive)."""
        result = check_performance_anchor(
            "2020年以来", "合同签订日期：2020年1月", date(2025, 6, 15)
        )
        assert result.verdict == "in_range"
        assert result.parsed_anchor == date(2020, 1, 1)

    def test_l2_02_anchor_one_day_before_start(self) -> None:
        """Anchor one day before start → out_of_range."""
        result = check_performance_anchor(
            "2020年以来",
            "合同签订日期：2019年12月31日",
            date(2025, 6, 15),
        )
        assert result.verdict == "out_of_range"
        assert result.parsed_anchor == date(2019, 12, 31)

    def test_l2_03_anchor_on_year_range_end_inclusive(self) -> None:
        """Anchor = Dec 31 of end-year → in_range."""
        result = check_performance_anchor(
            "2021年至2024年",
            "竣工时间：2024年12月31日",
            date(2025, 6, 15),
        )
        assert result.verdict == "in_range"
        assert result.parsed_anchor == date(2024, 12, 31)

    def test_l2_04_anchor_one_day_after_end(self) -> None:
        """Anchor one day after end → out_of_range."""
        result = check_performance_anchor(
            "2021年至2023年",
            "合同签订日期：2024年1月1日",
            date(2025, 6, 15),
        )
        assert result.verdict == "out_of_range"

    def test_l2_05_non_inclusive_start_boundary(self) -> None:
        """Anchor == start, boundary_inclusive=False → out_of_range."""
        today = date(2025, 6, 15)  # start for 近三年 = 2022-06-15
        result = check_performance_anchor(
            "近三年",
            "合同签订日期：2022年6月15日",
            today,
            assumption=DateRangeAssumption(boundary_inclusive=False),
        )
        assert result.verdict == "out_of_range"

    def test_l2_06_non_inclusive_end_boundary(self) -> None:
        """Anchor == end (today), boundary_inclusive=False → out_of_range."""
        today = date(2025, 6, 15)
        result = check_performance_anchor(
            "近三年",
            "合同签订日期：2025年6月15日",
            today,
            assumption=DateRangeAssumption(boundary_inclusive=False),
        )
        assert result.verdict == "out_of_range"

    def test_l2_07_cross_year_dec31_in_range(self) -> None:
        """Anchor = Dec 31 with year-range ending Dec 31 → in_range."""
        result = check_performance_anchor(
            "2021年至2023年",
            "竣工时间：2023年12月31日",
            date(2025, 6, 15),
        )
        assert result.verdict == "in_range"

    def test_l2_08_leap_year_base_date(self) -> None:
        """近三年 from Feb 29 → start = Feb 28 three years earlier."""
        today = date(2024, 2, 29)
        result = parse_time_requirement("近三年", today)

        assert result.verdict == "in_range"
        assert result.parsed_range is not None
        start, end = result.parsed_range
        assert end == today
        assert start == date(2021, 2, 28)


# ──────────────────────────────────────────────────────────────────────────────
# L3 — Expression variants
# ──────────────────────────────────────────────────────────────────────────────


_TODAY_L3 = date(2025, 6, 15)
_RECENT3_START = date(2022, 6, 15)
_RECENT3_END = _TODAY_L3


@pytest.mark.parametrize(
    "text, exp_start, exp_end",
    [
        pytest.param(
            "近3年",
            _RECENT3_START,
            _RECENT3_END,
            id="l3-01-arabic-digit",
        ),
        pytest.param(
            "近三年",
            _RECENT3_START,
            _RECENT3_END,
            id="l3-02-chinese-san",
        ),
        pytest.param(
            "最近三年",
            _RECENT3_START,
            _RECENT3_END,
            id="l3-03-zuijin-prefix-chinese",
        ),
        pytest.param(
            "最近3年",
            _RECENT3_START,
            _RECENT3_END,
            id="l3-04-zuijin-prefix-arabic",
        ),
        pytest.param(
            "2023年以来",
            date(2023, 1, 1),
            _TODAY_L3,
            id="l3-05-yilai-form",
        ),
        pytest.param(
            "自2023年起",
            date(2023, 1, 1),
            _TODAY_L3,
            id="l3-06-zi-qi-form",
        ),
        pytest.param(
            "2021年至2024年",
            date(2021, 1, 1),
            date(2024, 12, 31),
            id="l3-07-pure-year-range",
        ),
        pytest.param(
            "（近三年）内完成的项目",
            _RECENT3_START,
            _RECENT3_END,
            id="l3-08-parenthetical",
        ),
    ],
)
def test_l3_expression_variants(
    text: str,
    exp_start: date,
    exp_end: date,
) -> None:
    """L3: all supported Chinese time expression forms parse to the correct range."""
    result = parse_time_requirement(text, today=_TODAY_L3)
    assert result.verdict == "in_range", (
        f"Expected 'in_range' for {text!r}, got {result.verdict!r}: "
        f"{result.ambiguity_reasons}"
    )
    assert result.parsed_range is not None
    assert result.parsed_range[0] == exp_start, (
        f"Start mismatch for {text!r}: {result.parsed_range[0]} != {exp_start}"
    )
    assert result.parsed_range[1] == exp_end, (
        f"End mismatch for {text!r}: {result.parsed_range[1]} != {exp_end}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# L4 — Degradation (NEVER in_range)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "req, resp, expected_verdict",
    [
        pytest.param(
            "甲方认为合适的时间段内",
            "合同签订日期：2023年5月",
            "unparseable",
            id="l4-01-vague-requirement-no-pattern",
        ),
        pytest.param(
            "年",
            "合同签订日期：2023年5月",
            "unparseable",
            id="l4-02-single-character-year-only",
        ),
        pytest.param(
            "近几年的业绩",
            "合同签订日期：2023年5月",
            "unparseable",
            id="l4-03-ji-not-in-cn-digits",
        ),
        pytest.param(
            "2024年至2021年的项目",
            "合同签订日期：2023年5月",
            "unparseable",
            id="l4-04-reversed-year-range",
        ),
        pytest.param(
            "近三年完成的类似项目",
            "本公司具备相关资质，技术实力雄厚。",
            "ambiguous",
            id="l4-05-no-date-in-response",
        ),
        pytest.param(
            "2021年至2024年内",
            "项目团队经验丰富，方案满足招标要求。",
            "ambiguous",
            id="l4-06-year-range-no-anchor-in-response",
        ),
    ],
)
def test_l4_degradation_never_in_range(
    req: str,
    resp: str,
    expected_verdict: str,
) -> None:
    """L4: degradation cases must NEVER return 'in_range'."""
    result = check_performance_anchor(req, resp, date(2025, 6, 15))
    assert result.verdict != "in_range", (
        f"Safety violation: got in_range for req={req!r}. "
        f"Range={result.parsed_range}, anchor={result.parsed_anchor}"
    )
    assert result.verdict == expected_verdict, (
        f"Expected {expected_verdict!r}, got {result.verdict!r}. "
        f"Reasons: {result.ambiguity_reasons}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Supplementary unit tests for parse_time_requirement
# ──────────────────────────────────────────────────────────────────────────────


class TestParseTimeRequirement:
    """Unit-level checks on parse_time_requirement in isolation."""

    def test_full_date_range_ymd_dot(self) -> None:
        result = parse_time_requirement("2021.03.15至2024.06.30", date(2025, 1, 1))
        assert result.verdict == "in_range"
        assert result.parsed_range == (date(2021, 3, 15), date(2024, 6, 30))

    def test_full_date_range_chinese(self) -> None:
        result = parse_time_requirement(
            "2021年3月15日至2024年6月30日", date(2025, 1, 1)
        )
        assert result.verdict == "in_range"
        assert result.parsed_range == (date(2021, 3, 15), date(2024, 6, 30))

    def test_ym_range_end_is_last_day_of_month(self) -> None:
        result = parse_time_requirement("2021年3月至2024年6月", date(2025, 1, 1))
        assert result.verdict == "in_range"
        assert result.parsed_range is not None
        assert result.parsed_range[0] == date(2021, 3, 1)
        assert result.parsed_range[1] == date(2024, 6, 30)

    def test_year_range_with_dash(self) -> None:
        result = parse_time_requirement("2021年-2024年", date(2025, 1, 1))
        assert result.verdict == "in_range"
        assert result.parsed_range == (date(2021, 1, 1), date(2024, 12, 31))

    def test_compact_dash_range(self) -> None:
        result = parse_time_requirement("2021-2024年项目", date(2025, 1, 1))
        assert result.verdict == "in_range"
        assert result.parsed_range == (date(2021, 1, 1), date(2024, 12, 31))

    def test_since_year_at_today(self) -> None:
        today = date(2025, 6, 15)
        result = parse_time_requirement("2025年以来", today)
        assert result.verdict == "in_range"
        assert result.parsed_range == (date(2025, 1, 1), today)

    def test_since_year_future_is_ambiguous(self) -> None:
        result = parse_time_requirement("2030年以来", date(2025, 6, 15))
        assert result.verdict == "ambiguous"
        assert "since_year_in_future" in result.ambiguity_reasons

    def test_recent_n_10_arabic(self) -> None:
        today = date(2025, 6, 15)
        result = parse_time_requirement("近10年", today)
        assert result.verdict == "in_range"
        assert result.parsed_range == (date(2015, 6, 15), today)

    def test_recent_n_15_chinese(self) -> None:
        today = date(2025, 6, 15)
        result = parse_time_requirement("近十五年", today)
        assert result.verdict == "in_range"
        assert result.parsed_range == (date(2010, 6, 15), today)

    def test_default_assumption_is_applied_when_none(self) -> None:
        result1 = parse_time_requirement("近三年", date(2025, 6, 15), assumption=None)
        result2 = parse_time_requirement(
            "近三年", date(2025, 6, 15), assumption=DateRangeAssumption()
        )
        assert result1.parsed_range == result2.parsed_range

    def test_reversed_full_date_range_is_unparseable(self) -> None:
        result = parse_time_requirement(
            "2024年6月30日至2021年3月15日", date(2025, 1, 1)
        )
        assert result.verdict == "unparseable"
        assert "range_start_after_end" in result.ambiguity_reasons

    def test_empty_string_is_unparseable(self) -> None:
        result = parse_time_requirement("", date(2025, 6, 15))
        assert result.verdict == "unparseable"

    def test_zi_qi_zhijin_form(self) -> None:
        """自2022年起至今 → start=2022-01-01, end=today."""
        today = date(2025, 6, 15)
        result = parse_time_requirement("自2022年起至今", today)
        assert result.verdict == "in_range"
        assert result.parsed_range == (date(2022, 1, 1), today)

    def test_zhijin_suffix(self) -> None:
        """2022年至今 → start=2022-01-01, end=today."""
        today = date(2025, 6, 15)
        result = parse_time_requirement("2022年至今", today)
        assert result.verdict == "in_range"
        assert result.parsed_range == (date(2022, 1, 1), today)
