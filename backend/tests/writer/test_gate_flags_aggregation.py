"""P2.1b 防御测试：SectionComposer._aggregate_gate_flags 聚合 verdict 为 gate_flags。

仅测纯函数逻辑, 不依赖 DB/dispatcher。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.section_composer import SectionComposer


@dataclass
class _FakeVerdict:
    gate_name: str
    verdict: str
    severity: str = "advisory"
    reason: str = ""
    suggested_patch: dict | None = None
    trace: dict | None = None


class TestAggregateGateFlags:

    def test_all_pass_returns_none(self):
        verdicts = [
            _FakeVerdict(gate_name="G1", verdict="pass"),
            _FakeVerdict(gate_name="G2", verdict="pass"),
        ]
        assert SectionComposer._aggregate_gate_flags(verdicts) is None

    def test_empty_list_returns_none(self):
        assert SectionComposer._aggregate_gate_flags([]) is None

    def test_mixed_collects_only_non_pass(self):
        verdicts = [
            _FakeVerdict(gate_name="PassGate", verdict="pass"),
            _FakeVerdict(
                gate_name="MaterialSourceTraceGate",
                verdict="reject",
                severity="advisory",
                reason="no_used_id",
                suggested_patch={"material_trace_missing": True},
                trace={"outcome": "no_used_id"},
            ),
            _FakeVerdict(
                gate_name="TableFieldFillRateGate",
                verdict="warn",
                severity="advisory",
                reason="low fill rate",
                suggested_patch={"table_fill_rate": 0.42},
                trace={"fill_rate": 0.42, "non_empty_td": 5, "total_td": 12},
            ),
        ]
        flags = SectionComposer._aggregate_gate_flags(verdicts)
        assert flags is not None
        assert set(flags.keys()) == {"MaterialSourceTraceGate", "TableFieldFillRateGate"}
        mat = flags["MaterialSourceTraceGate"]
        assert mat["verdict"] == "reject"
        assert mat["patch"] == {"material_trace_missing": True}
        assert mat["trace"]["outcome"] == "no_used_id"
        tab = flags["TableFieldFillRateGate"]
        assert tab["patch"] == {"table_fill_rate": 0.42}
        assert tab["trace"]["fill_rate"] == 0.42

    def test_trace_only_known_fields_kept(self):
        verdicts = [
            _FakeVerdict(
                gate_name="DeAIFilterGate",
                verdict="warn",
                trace={
                    "ai_phrase_count": 15,
                    "internal_debug": "should_not_leak",
                    "density_per_1k": 7.5,
                },
            ),
        ]
        flags = SectionComposer._aggregate_gate_flags(verdicts)
        trace = flags["DeAIFilterGate"]["trace"]
        assert "ai_phrase_count" in trace
        assert "density_per_1k" in trace
        assert "internal_debug" not in trace

    def test_reason_truncated(self):
        long_reason = "x" * 500
        verdicts = [
            _FakeVerdict(
                gate_name="X", verdict="warn", reason=long_reason,
            ),
        ]
        flags = SectionComposer._aggregate_gate_flags(verdicts)
        assert len(flags["X"]["reason"]) == 200
