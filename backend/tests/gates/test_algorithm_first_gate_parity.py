"""B4 双路一致性测试。

验证 INVESTIGATE_ALGORITHM_FIRST_ENABLED=False 时：
1. pre_llm 阶段的 PhysicalFormSkipGate 不产出 enforce_patch（advisory skip）
2. post_llm 阶段的 AlgorithmicRiskBandGate 为 advisory，不修改 target
3. D6/D7 为 advisory，不修改 collusion target
4. Pipeline 排序中新 Gate 位置正确
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.ai.gates import (
    build_pipeline_for,
    should_use_algorithm_first,
    _get_all_gates,
)
from app.core.ai.gates.base import GateContext


# ---------------------------------------------------------------------------
# should_use_algorithm_first 分流函数
# ---------------------------------------------------------------------------

class TestShouldUseAlgorithmFirst:
    """验证分流函数的开关/比率/hash 行为。"""

    @patch("app.core.config.settings")
    def test_disabled_always_returns_false(self, mock_settings):
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_ENABLED = False
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_RATIO = 1.0
        assert should_use_algorithm_first(123) is False

    @patch("app.core.config.settings")
    def test_enabled_ratio_zero_returns_false(self, mock_settings):
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_ENABLED = True
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_RATIO = 0.0
        assert should_use_algorithm_first(123) is False

    @patch("app.core.config.settings")
    def test_enabled_ratio_one_returns_true(self, mock_settings):
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_ENABLED = True
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_RATIO = 1.0
        assert should_use_algorithm_first(123) is True

    @patch("app.core.config.settings")
    def test_none_project_returns_false(self, mock_settings):
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_ENABLED = True
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_RATIO = 0.5
        assert should_use_algorithm_first(None) is False

    @patch("app.core.config.settings")
    def test_deterministic_for_same_project(self, mock_settings):
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_ENABLED = True
        mock_settings.INVESTIGATE_ALGORITHM_FIRST_RATIO = 0.5
        r1 = should_use_algorithm_first(42)
        r2 = should_use_algorithm_first(42)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Gate 注册完整性
# ---------------------------------------------------------------------------

class TestGateRegistration:
    """验证 B4 新 Gate 已正确注册到 _get_all_gates。"""

    def test_all_gates_include_b4_investigate_gates(self):
        names = {g.gate_name for g in _get_all_gates()}
        assert "PhysicalFormRequirementSkipGate" in names
        assert "AlgorithmicRiskBandGate" in names
        assert "ScoringQuotePresenceGate" in names
        assert "ProhibitionLeakGate" in names
        assert "ScoringPresenceMaxGate" in names

    def test_all_gates_include_b4_collusion_gates(self):
        names = {g.gate_name for g in _get_all_gates()}
        assert "D6FormatAlgorithmicGate" in names
        assert "D7AbnormalConsistencyGate" in names
        assert "PairedQuoteIdentityGate" in names

    def test_physical_form_skip_is_pre_llm(self):
        gate = next(
            g for g in _get_all_gates()
            if g.gate_name == "PhysicalFormRequirementSkipGate"
        )
        assert gate.stage == "pre_llm"
        assert gate.order == 10

    def test_algorithmic_risk_band_order_after_claim(self):
        gate = next(
            g for g in _get_all_gates()
            if g.gate_name == "AlgorithmicRiskBandGate"
        )
        assert gate.stage == "post_llm"
        assert gate.order == 50

    def test_paired_quote_identity_order_after_page_verify(self):
        gates = {g.gate_name: g for g in _get_all_gates()}
        assert gates["FindingPageQuoteVerifyGate"].order == 50
        assert gates["PairedQuoteIdentityGate"].order == 51
        assert gates["D7ConcreteEvidenceGate"].order == 52


# ---------------------------------------------------------------------------
# Pipeline advisory parity：开关关闭时新 Gate 不修改 target
# ---------------------------------------------------------------------------

class TestAdvisoryParity:
    """当新 Gate 为 advisory 时，pipeline._apply_patches 不修改 target。"""

    def test_post_llm_advisory_does_not_patch(self):
        pipeline = build_pipeline_for("investigate")
        target = {"risk_level": "low", "compliance_level": "fully", "score": 90}
        ctx = GateContext(
            flow="investigate",
            stage="post_llm",
            target=target,
            evidence={"is_mandatory": False, "has_response": True, "response_content": "test"},
        )
        pipeline.run(ctx, apply_patch_to=target)
        assert target["risk_level"] == "low"

    def test_collusion_post_llm_d6_d7_advisory_does_not_patch(self):
        pipeline = build_pipeline_for("collusion")
        target = {"d6_format": None, "d7_abnormal": None, "overall_risk": "none"}
        ctx = GateContext(
            flow="collusion",
            stage="post_llm",
            target=target,
            evidence={},
        )
        pipeline.run(ctx, apply_patch_to=target)
        assert target["d6_format"] is None
        assert target["d7_abnormal"] is None
