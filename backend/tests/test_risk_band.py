"""Unit tests for derive_risk_band() decision tree.

Tests are organised by the rule they exercise.  All edge cases mentioned in
the specification (relevance exactly 80, 50, 30) are included.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.ai.risk_band import RiskBandVerdict, derive_risk_band
from app.core.extractor.tools.amount_parser import AmountVerdict
from app.core.extractor.tools.date_range_checker import DateRangeVerdict
from app.core.matcher.relevance_scorer import RelevanceScore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_in_range() -> DateRangeVerdict:
    return DateRangeVerdict(
        verdict="in_range",
        parsed_range=(date(2022, 1, 1), date(2025, 1, 1)),
    )


def _date_out_of_range() -> DateRangeVerdict:
    return DateRangeVerdict(
        verdict="out_of_range",
        parsed_range=(date(2022, 1, 1), date(2024, 1, 1)),
        parsed_anchor=date(2020, 6, 1),
    )


def _date_ambiguous() -> DateRangeVerdict:
    return DateRangeVerdict(verdict="ambiguous", ambiguity_reasons=["anchor_not_found"])


def _date_unparseable() -> DateRangeVerdict:
    return DateRangeVerdict(verdict="unparseable", ambiguity_reasons=["no_time_pattern_found"])


def _amount_high() -> AmountVerdict:
    return AmountVerdict(canonical_value=Decimal("5000000"), unit="wan_yuan", confidence="high")


def _amount_medium() -> AmountVerdict:
    return AmountVerdict(canonical_value=Decimal("3000000"), unit="wan_yuan", confidence="medium")


def _amount_low_conf_parseable() -> AmountVerdict:
    """Low confidence but has a canonical_value (e.g. CN numeral without unit)."""
    return AmountVerdict(canonical_value=Decimal("50000"), unit="yuan", confidence="low")


def _amount_unparseable() -> AmountVerdict:
    """Canonical value is None — equivalent to 'amount below/unparseable'."""
    return AmountVerdict()


def _rel(score: int) -> RelevanceScore:
    return RelevanceScore(
        score=score,
        type_match=score / 100.0,
        semantic_sim=score / 100.0,
        domain_keyword_hit=score / 100.0,
    )


# ---------------------------------------------------------------------------
# Parametrised test matrix
# ---------------------------------------------------------------------------

_CASES = [
    # id, is_mandatory, date_v, amount_v, relevance, expected_band, expected_conf
    # ── Rule 1: mandatory + critical failure ────────────────────────────────
    pytest.param(
        True, _date_out_of_range(), None, None,
        "high", 0.95,
        id="r1_mandatory_date_out_of_range",
    ),
    pytest.param(
        True, None, _amount_unparseable(), None,
        "high", 0.95,
        id="r1_mandatory_amount_below",
    ),
    pytest.param(
        True, None, None, _rel(20),
        "high", 0.95,
        id="r1_mandatory_relevance_below_30",
    ),
    pytest.param(
        True, _date_out_of_range(), _amount_unparseable(), _rel(10),
        "high", 0.95,
        id="r1_mandatory_all_critical_triggers",
    ),
    # ── Rule 2: mandatory + uncertain dimension ──────────────────────────────
    pytest.param(
        True, _date_ambiguous(), None, None,
        "medium", 0.6,
        id="r2_mandatory_date_ambiguous",
    ),
    pytest.param(
        True, _date_unparseable(), None, None,
        "medium", 0.6,
        id="r2_mandatory_date_unparseable",
    ),
    pytest.param(
        True, None, _amount_low_conf_parseable(), None,
        "medium", 0.6,
        id="r2_mandatory_amount_low_confidence",
    ),
    pytest.param(
        True, _date_ambiguous(), _amount_low_conf_parseable(), _rel(90),
        "medium", 0.6,
        id="r2_mandatory_date_and_amount_uncertain",
    ),
    # ── Rule 3: high relevance + all pass ───────────────────────────────────
    pytest.param(
        False, None, None, _rel(80),
        "none", 0.9,
        id="r3_relevance_exactly_80_all_none",
    ),
    pytest.param(
        False, _date_in_range(), _amount_high(), _rel(90),
        "none", 0.9,
        id="r3_relevance_90_date_in_range_amount_high",
    ),
    pytest.param(
        True, _date_in_range(), _amount_high(), _rel(90),
        "none", 0.9,
        id="r3_mandatory_all_pass_relevance_90",
    ),
    pytest.param(
        False, None, None, _rel(85),
        "none", 0.9,
        id="r3_relevance_85_no_other_inputs",
    ),
    # ── Rule 4: medium relevance ─────────────────────────────────────────────
    pytest.param(
        False, None, None, _rel(79),
        "low", 0.8,
        id="r4_relevance_79",
    ),
    pytest.param(
        False, None, None, _rel(65),
        "low", 0.8,
        id="r4_relevance_65",
    ),
    pytest.param(
        False, None, None, _rel(50),
        "low", 0.8,
        id="r4_relevance_exactly_50",
    ),
    pytest.param(
        False, _date_out_of_range(), None, _rel(70),
        "low", 0.8,
        id="r4_non_mandatory_date_out_of_range_relevance_70",
    ),
    # ── Rule 5: fallback ─────────────────────────────────────────────────────
    pytest.param(
        False, None, None, None,
        "medium", 0.3,
        id="r5_all_none_inputs",
    ),
    pytest.param(
        False, None, None, _rel(40),
        "medium", 0.3,
        id="r5_non_mandatory_relevance_40",
    ),
    pytest.param(
        False, None, None, _rel(30),
        "medium", 0.3,
        id="r5_edge_relevance_exactly_30_non_mandatory",
    ),
    # Edge: mandatory + relevance=30 (30 is NOT < 30, so rule 1 doesn't fire)
    # No date/amount issues → rule 2 also skips → rule 3 skips (30 < 80)
    # → rule 4 skips (30 < 50) → fallback
    pytest.param(
        True, None, None, _rel(30),
        "medium", 0.3,
        id="r5_edge_mandatory_relevance_exactly_30_fallback",
    ),
]


@pytest.mark.parametrize(
    "is_mandatory,date_v,amount_v,relevance,expected_band,expected_conf",
    _CASES,
)
def test_derive_risk_band(
    is_mandatory: bool,
    date_v: DateRangeVerdict | None,
    amount_v: AmountVerdict | None,
    relevance: RelevanceScore | None,
    expected_band: str,
    expected_conf: float,
) -> None:
    result = derive_risk_band(
        is_mandatory=is_mandatory,
        date_verdict=date_v,
        amount_verdict=amount_v,
        relevance=relevance,
    )

    assert isinstance(result, RiskBandVerdict)
    assert result.risk_band == expected_band, (
        f"Expected band={expected_band!r}, got {result.risk_band!r}. "
        f"factors={result.contributing_factors}"
    )
    assert result.confidence == pytest.approx(expected_conf), (
        f"Expected confidence={expected_conf}, got {result.confidence}"
    )


# ---------------------------------------------------------------------------
# Structural / snapshot tests
# ---------------------------------------------------------------------------

def test_contributing_factors_keys_always_present() -> None:
    """contributing_factors always has date_check, amount_check, relevance_check, triggers."""
    result = derive_risk_band(
        is_mandatory=False,
        date_verdict=_date_in_range(),
        amount_verdict=_amount_high(),
        relevance=_rel(60),
    )
    for key in ("date_check", "amount_check", "relevance_check", "triggers"):
        assert key in result.contributing_factors, f"Missing key: {key}"


def test_inputs_snapshot_captures_all_fields() -> None:
    """inputs_snapshot includes is_mandatory, date, amount, and relevance data."""
    result = derive_risk_band(
        is_mandatory=True,
        date_verdict=_date_in_range(),
        amount_verdict=_amount_high(),
        relevance=_rel(75),
    )
    snapshot = result.inputs_snapshot
    assert snapshot["is_mandatory"] is True
    assert snapshot["date_verdict"] == "in_range"
    assert snapshot["amount_confidence"] == "high"
    assert snapshot["relevance_score"] == 75


def test_rule1_triggers_list_includes_correct_trigger() -> None:
    """Rule 1 trigger list accurately names which dimension fired."""
    result = derive_risk_band(
        is_mandatory=True,
        date_verdict=_date_out_of_range(),
        amount_verdict=_amount_high(),
        relevance=_rel(50),
    )
    assert result.risk_band == "high"
    assert "date_out_of_range" in result.contributing_factors["triggers"]


def test_rule2_amount_low_conf_does_not_override_rule1_date_out_of_range() -> None:
    """Rule 1 (date out_of_range) takes priority over Rule 2 (amount low_conf)."""
    result = derive_risk_band(
        is_mandatory=True,
        date_verdict=_date_out_of_range(),
        amount_verdict=_amount_low_conf_parseable(),
        relevance=_rel(70),
    )
    assert result.risk_band == "high"
    assert result.confidence == pytest.approx(0.95)


def test_rule3_does_not_fire_when_date_fails() -> None:
    """Rule 3 requires date_passes; out_of_range blocks it.

    With relevance=80, Rule 3 skips (date fails) and Rule 4 also skips
    (80 is not < 80), so it falls all the way to the fallback → medium/0.3.
    Using relevance=75 to verify the path through Rule 4 instead.
    """
    # Case A: relevance=75 (50 <= 75 < 80) → Rule 4 fires despite bad date (non-mandatory)
    result_a = derive_risk_band(
        is_mandatory=False,
        date_verdict=_date_out_of_range(),
        amount_verdict=None,
        relevance=_rel(75),
    )
    assert result_a.risk_band == "low"

    # Case B: relevance=80 + date fails → Rule 3 & Rule 4 both skip → fallback
    result_b = derive_risk_band(
        is_mandatory=False,
        date_verdict=_date_out_of_range(),
        amount_verdict=None,
        relevance=_rel(80),
    )
    assert result_b.risk_band == "medium"
    assert result_b.confidence == pytest.approx(0.3)


def test_amount_medium_confidence_does_not_trigger_rule2() -> None:
    """AmountVerdict with confidence='medium' is NOT low-confidence for Rule 2."""
    result = derive_risk_band(
        is_mandatory=True,
        date_verdict=None,
        amount_verdict=_amount_medium(),
        relevance=_rel(90),
    )
    # Medium confidence amount passes Rules 1 & 2; Rule 3 fires
    assert result.risk_band == "none"


def test_none_inputs_snapshot_keys() -> None:
    """When all tool inputs are None, inputs_snapshot still has keys."""
    result = derive_risk_band(False, None, None, None)
    assert "is_mandatory" in result.inputs_snapshot
    assert "date_verdict" in result.inputs_snapshot
    assert "relevance_score" in result.inputs_snapshot


def test_result_is_frozen_dataclass() -> None:
    """RiskBandVerdict is immutable."""
    result = derive_risk_band(False, None, None, _rel(60))
    with pytest.raises((AttributeError, TypeError)):
        result.risk_band = "high"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Defensive guard: non-AmountVerdict / non-DateRangeVerdict inputs are degraded
# ---------------------------------------------------------------------------
# Regression for BUG: reviewer._compute_algorithm_evidence() once stored the
# string return of compare_amount() into amount_verdict, which crashed the
# entire review flow via AttributeError on `.confidence`. derive_risk_band()
# must now coerce bad types to None and continue, not raise.


def test_guard_amount_verdict_string_is_degraded_not_crash() -> None:
    """amount_verdict 传入字符串(历史 bug 场景) 时应降级为 None, 不抛异常."""
    result = derive_risk_band(
        is_mandatory=True,
        date_verdict=None,
        amount_verdict="meets",  # type: ignore[arg-type]
        relevance=_rel(90),
    )
    assert isinstance(result, RiskBandVerdict)
    # 降级后等同 amount_verdict=None, relevance=90 → Rule 3 → none/0.9
    assert result.risk_band == "none"
    assert "degraded_inputs" in result.inputs_snapshot
    assert any("amount_verdict" in d for d in result.inputs_snapshot["degraded_inputs"])


def test_guard_date_verdict_wrong_type_is_degraded() -> None:
    """date_verdict 传入非 DateRangeVerdict 类型时降级为 None."""
    result = derive_risk_band(
        is_mandatory=False,
        date_verdict="in_range",  # type: ignore[arg-type]
        amount_verdict=None,
        relevance=_rel(85),
    )
    assert result.risk_band == "none"
    assert "degraded_inputs" in result.inputs_snapshot
    assert any("date_verdict" in d for d in result.inputs_snapshot["degraded_inputs"])


def test_guard_relevance_wrong_type_is_degraded() -> None:
    """relevance 传入数字(非 RelevanceScore) 时降级为 None, 落到 fallback."""
    result = derive_risk_band(
        is_mandatory=False,
        date_verdict=None,
        amount_verdict=None,
        relevance=70,  # type: ignore[arg-type]
    )
    # 降级后 relevance=None → Rule 3/4 都跳过 → Rule 5 fallback
    assert result.risk_band == "medium"
    assert result.confidence == pytest.approx(0.3)
    assert "degraded_inputs" in result.inputs_snapshot


def test_guard_correct_types_no_degradation_marker() -> None:
    """正常类型输入时 inputs_snapshot 不含 degraded_inputs 键."""
    result = derive_risk_band(
        is_mandatory=False,
        date_verdict=_date_in_range(),
        amount_verdict=_amount_high(),
        relevance=_rel(90),
    )
    assert "degraded_inputs" not in result.inputs_snapshot
