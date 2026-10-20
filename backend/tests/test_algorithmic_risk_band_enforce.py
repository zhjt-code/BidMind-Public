"""S.5.1 单元测试：ReviewOrchestrator._maybe_enforce_algorithmic_risk_band。

核心断言：
- 一致（verdict=pass）→ 不改 extracted
- 低置信度分歧 → 不改 extracted（advisory 口径）
- 高置信度分歧 → risk_level / failure_state 被覆写（条件升 enforce）
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.ai.gates.base import GateVerdict
from app.core.ai.reviewer import ReviewOrchestrator


def _make_verdict(
    gate_name: str = "AlgorithmicRiskBandGate",
    verdict: str = "warn",
    severity: str = "advisory",
    confidence: float = 0.85,
    risk_level: str | None = "high",
    failure_state: str | None = "needs_review",
) -> GateVerdict:
    patch: dict = {}
    if risk_level is not None:
        patch["risk_level"] = risk_level
    if failure_state is not None:
        patch["failure_state"] = failure_state
    return GateVerdict(
        gate_name=gate_name, verdict=verdict,
        severity=severity, reason="test",
        suggested_patch=patch or None, confidence=confidence,
    )


def _make_extracted(risk_level: str = "low") -> SimpleNamespace:
    """模拟 ExtractedReviewResult：有 risk_level / failure_state 属性。"""
    return SimpleNamespace(risk_level=risk_level, failure_state=None)


class TestMaybeEnforceAlgorithmicRiskBand:
    def test_no_verdicts_noop(self):
        extracted = _make_extracted("low")
        ReviewOrchestrator._maybe_enforce_algorithmic_risk_band(extracted, [])
        assert extracted.risk_level == "low"

    def test_pass_verdict_noop(self):
        """Gate verdict=pass（一致）→ 不改。"""
        extracted = _make_extracted("low")
        v = _make_verdict(verdict="pass", confidence=0.9, risk_level=None)
        ReviewOrchestrator._maybe_enforce_algorithmic_risk_band(extracted, [v])
        assert extracted.risk_level == "low"
        assert extracted.failure_state is None

    def test_low_confidence_disagreement_noop(self, monkeypatch):
        """低置信度分歧 → 保持 advisory，不改 extracted。"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE", 0.8)
        extracted = _make_extracted("low")
        v = _make_verdict(verdict="warn", confidence=0.5, risk_level="high")
        ReviewOrchestrator._maybe_enforce_algorithmic_risk_band(extracted, [v])
        assert extracted.risk_level == "low"  # 未改
        assert extracted.failure_state is None

    def test_exact_threshold_confidence_enforces(self, monkeypatch):
        """confidence 恰好等于阈值 → 应用 patch（>=，含边界）。"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE", 0.8)
        extracted = _make_extracted("low")
        v = _make_verdict(verdict="warn", confidence=0.8, risk_level="high")
        ReviewOrchestrator._maybe_enforce_algorithmic_risk_band(extracted, [v])
        assert extracted.risk_level == "high"
        assert extracted.failure_state == "needs_review"

    def test_high_confidence_disagreement_enforces(self, monkeypatch):
        """高置信度分歧 → risk_level + failure_state 都被覆写。"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE", 0.8)
        extracted = _make_extracted("low")
        v = _make_verdict(verdict="warn", confidence=0.95,
                          risk_level="high", failure_state="needs_review")
        ReviewOrchestrator._maybe_enforce_algorithmic_risk_band(extracted, [v])
        assert extracted.risk_level == "high"
        assert extracted.failure_state == "needs_review"

    def test_non_algorithmic_risk_band_verdict_ignored(self, monkeypatch):
        """其他 Gate 的 warn verdict 不被本函数处理。"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE", 0.8)
        extracted = _make_extracted("low")
        v = _make_verdict(
            gate_name="PageAnchorVerifyGate",  # 不是 AlgorithmicRiskBand
            verdict="warn", confidence=0.95, risk_level="high",
        )
        ReviewOrchestrator._maybe_enforce_algorithmic_risk_band(extracted, [v])
        assert extracted.risk_level == "low"

    def test_threshold_raised_to_1_disables(self, monkeypatch):
        """阈值设为 1.1（永远不满足）→ 退回 advisory。"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE", 1.1)
        extracted = _make_extracted("low")
        v = _make_verdict(verdict="warn", confidence=0.99, risk_level="high")
        ReviewOrchestrator._maybe_enforce_algorithmic_risk_band(extracted, [v])
        assert extracted.risk_level == "low"  # 未改

    def test_patch_without_risk_level_preserved(self, monkeypatch):
        """patch 中只有 failure_state 无 risk_level 时，risk_level 不动，但 failure_state 升级。"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE", 0.8)
        extracted = _make_extracted("low")
        v = _make_verdict(
            verdict="warn", confidence=0.9,
            risk_level=None, failure_state="needs_review",
        )
        ReviewOrchestrator._maybe_enforce_algorithmic_risk_band(extracted, [v])
        assert extracted.risk_level == "low"  # 未改
        assert extracted.failure_state == "needs_review"  # 升级
