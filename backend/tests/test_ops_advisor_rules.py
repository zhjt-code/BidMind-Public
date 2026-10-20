"""周度 Advisor 规则引擎与指纹防御测试。"""

from __future__ import annotations

import pytest

from app.core.ai_settings import (
    PRIORITY_BACKUP,
    PRIORITY_NORMAL,
    PRIORITY_PREFERRED,
    PRIORITY_SECOND,
)
from app.services.ops_advisor.priority_advice_rules import (
    ConfigReliabilityStats,
    MIN_SAMPLE_ATTEMPTS,
    build_purpose_advice,
)
from app.services.ops_advisor.priority_apply_service import fingerprint_priority


def _stats(
    config_id: str,
    *,
    attempts: int,
    valid_rate: float,
    empty_rate: float = 0.0,
    structured_fail_rate: float = 0.0,
    latency_p95_ms: float = 1000.0,
    current_priority: int = PRIORITY_NORMAL,
) -> ConfigReliabilityStats:
    return ConfigReliabilityStats(
        config_id=config_id,
        model_name=config_id,
        attempts=attempts,
        valid_rate=valid_rate,
        empty_rate=empty_rate,
        structured_fail_rate=structured_fail_rate,
        latency_p95_ms=latency_p95_ms,
        current_priority=current_priority,
    )


def test_insufficient_sample() -> None:
    decision = build_purpose_advice(
        purpose="review",
        candidates=[_stats("a", attempts=5, valid_rate=1.0)],
        current_priority={"a": PRIORITY_NORMAL},
    )
    assert decision.action == "insufficient_data"
    assert decision.rule_id == "all_insufficient_sample"


def test_promote_best_valid_rate() -> None:
    decision = build_purpose_advice(
        purpose="review",
        candidates=[
            _stats("best", attempts=MIN_SAMPLE_ATTEMPTS, valid_rate=0.98),
            _stats("other", attempts=MIN_SAMPLE_ATTEMPTS, valid_rate=0.90),
        ],
        current_priority={"best": PRIORITY_NORMAL, "other": PRIORITY_PREFERRED},
    )
    assert decision.action == "promote"
    assert decision.recommended_config_id == "best"
    assert decision.proposed_priority is not None
    assert decision.proposed_priority["best"] == PRIORITY_PREFERRED
    assert decision.proposed_priority["other"] == PRIORITY_SECOND


def test_keep_when_best_already_preferred() -> None:
    decision = build_purpose_advice(
        purpose="review",
        candidates=[
            _stats(
                "best",
                attempts=MIN_SAMPLE_ATTEMPTS,
                valid_rate=0.99,
                current_priority=PRIORITY_PREFERRED,
            ),
        ],
        current_priority={"best": PRIORITY_PREFERRED},
    )
    assert decision.action == "keep"
    assert decision.rule_id == "best_already_preferred"


def test_demote_worst_degraded() -> None:
    decision = build_purpose_advice(
        purpose="review",
        candidates=[
            _stats("good", attempts=MIN_SAMPLE_ATTEMPTS, valid_rate=0.92),
            _stats("bad", attempts=MIN_SAMPLE_ATTEMPTS, valid_rate=0.70, empty_rate=0.1),
        ],
        current_priority={"good": PRIORITY_PREFERRED, "bad": PRIORITY_NORMAL},
    )
    assert decision.action == "demote"
    assert decision.demote_config_id == "bad"
    assert decision.proposed_priority is not None
    assert decision.proposed_priority["bad"] in (PRIORITY_BACKUP, 999)


def test_fingerprint_stable_and_order_independent() -> None:
    a = fingerprint_priority({"x": 0, "y": 100})
    b = fingerprint_priority({"y": 100, "x": 0})
    c = fingerprint_priority({"x": 0, "y": 50})
    assert a == b
    assert a != c
    assert len(a) == 16


def test_no_candidates() -> None:
    decision = build_purpose_advice(
        purpose="vision",
        candidates=[],
        current_priority={},
    )
    assert decision.action == "insufficient_data"
    assert decision.rule_id == "no_candidates"
