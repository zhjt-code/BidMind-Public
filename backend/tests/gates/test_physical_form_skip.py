"""Unit tests for PhysicalFormRequirementSkipGate.

All tests mock classify_requirement so they never touch the YAML dictionary
or file system, keeping the suite fast and deterministic.

 补充：TestMandatoryRequirementBlock 组覆盖必审安全防线，
验证 is_mandatory=True/None/缺失时禁止算法放行。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.scoring

from unittest.mock import patch

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.review.physical_form_skip import PhysicalFormRequirementSkipGate
from app.core.extractor.tools.physical_form_classifier import PhysicalFormVerdict

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GATE = PhysicalFormRequirementSkipGate()

_CLASSIFY_PATH = (
    "app.core.extractor.tools.physical_form_classifier.classify_requirement"
)


def _ctx(
    flow: str = "investigate",
    stage: str = "pre_llm",
    requirement_text: str = "需提供原件",
    is_mandatory: bool | None = False,
    include_is_mandatory: bool = True,
    category: str | None = None,
    include_category: bool = False,
    has_response: bool | None = None,
    include_has_response: bool = False,
    requirement_kind: str = "normal",
    is_key_review: bool | int = False,
) -> GateContext:
    """构造 GateContext 测试上下文。

    is_mandatory 默认 False，表示非必审场景（enforce_patch 路径）。
    include_is_mandatory=False 时 evidence 中完全不含 is_mandatory 键，
    用于测试"键缺失"的保守判定路径（应按必审处理）。

    注意: 生产契约中 is_mandatory 缺失等价于必审、拒绝算法放行，与本函数
    默认值相反。该默认值仅为免除既有非必审用例逐个传参，新增用例必须显式
    声明 is_mandatory 语义，不得依赖此默认值，否则测试与生产判定会脱节。
    """
    evidence: dict = {
        "requirement_text": requirement_text,
        "requirement_kind": requirement_kind,
        "is_key_review": is_key_review,
    }
    if include_is_mandatory:
        evidence["is_mandatory"] = is_mandatory
    if include_category:
        evidence["category"] = category
    if include_has_response:
        evidence["has_response"] = has_response
    return GateContext(
        flow=flow,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        target=None,
        evidence=evidence,
    )


def _skip_ctx(**overrides: object) -> GateContext:
    """可算法 skip 的最小完备上下文：格式类、非必审、响应有无已知。"""
    kwargs: dict = {
        "requirement_text": "投标文件须加盖公章",
        "is_mandatory": False,
        "category": "format",
        "include_category": True,
        "has_response": True,
        "include_has_response": True,
        "requirement_kind": "normal",
        "is_key_review": False,
    }
    kwargs.update(overrides)
    return _ctx(**kwargs)  # type: ignore[arg-type]


def _pf_verdict(
    is_physical_form: bool = False,
    is_procedural: bool = False,
    matched: list[str] | None = None,
    negations: list[str] | None = None,
    confidence: str = "high",
) -> PhysicalFormVerdict:
    return PhysicalFormVerdict(
        is_physical_form=is_physical_form,
        is_procedural=is_procedural,
        matched_keywords=matched or [],
        negations=negations or [],
        confidence=confidence,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# applies() tests
# ---------------------------------------------------------------------------

class TestApplies:
    def test_applies_true_when_correct_flow_stage_and_text(self) -> None:
        ctx = _ctx(flow="investigate", stage="pre_llm", requirement_text="需提供原件")
        assert GATE.applies(ctx) is True

    def test_applies_false_when_flow_is_collusion(self) -> None:
        ctx = _ctx(flow="collusion", stage="pre_llm")
        assert GATE.applies(ctx) is False

    def test_applies_false_when_flow_is_writer(self) -> None:
        ctx = _ctx(flow="writer", stage="pre_llm")
        assert GATE.applies(ctx) is False

    def test_applies_false_when_stage_is_post_llm(self) -> None:
        ctx = GateContext(
            flow="investigate",
            stage="post_llm",
            target=None,
            evidence={"requirement_text": "原件"},
        )
        assert GATE.applies(ctx) is False

    def test_applies_false_when_requirement_text_empty(self) -> None:
        ctx = _ctx(flow="investigate", stage="pre_llm", requirement_text="")
        assert GATE.applies(ctx) is False

    def test_applies_false_when_evidence_is_none(self) -> None:
        ctx = GateContext(flow="investigate", stage="pre_llm", target=None, evidence=None)
        assert GATE.applies(ctx) is False


# ---------------------------------------------------------------------------
# evaluate() tests
# ---------------------------------------------------------------------------

class TestEvaluate:
    def test_physical_form_high_confidence_returns_enforce_patch(self) -> None:
        mock_result = _pf_verdict(
            is_physical_form=True,
            matched=["加盖公章"],
            confidence="high",
        )
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(_skip_ctx())

        assert verdict.verdict == "enforce_patch"
        assert verdict.severity == "enforce"
        assert verdict.confidence == 1.0
        assert verdict.suggested_patch is not None
        assert verdict.suggested_patch["_skip_llm_call"] is True
        assert verdict.suggested_patch["compliance_level"] == "fully"
        assert verdict.suggested_patch["risk_level"] == "low"
        assert verdict.suggested_patch["score"] == 95

    def test_procedural_only_does_not_skip(self) -> None:
        mock_result = _pf_verdict(
            is_procedural=True,
            matched=["现场踏勘"],
            confidence="high",
        )
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(_skip_ctx(requirement_text="现场踏勘"))

        assert verdict.verdict == "warn"
        assert verdict.suggested_patch is None
        assert verdict.trace is not None
        assert verdict.trace.get("blocked_by") == "procedural_not_focused"

    def test_no_match_returns_pass(self) -> None:
        mock_result = _pf_verdict(
            is_physical_form=False,
            is_procedural=False,
            matched=[],
            confidence="high",
        )
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(_ctx(requirement_text="具有三年以上施工经验"))

        assert verdict.verdict == "pass"
        assert verdict.suggested_patch is None

    def test_physical_form_with_negation_returns_warn(self) -> None:
        # is_mandatory=False（默认）：非必审低置信（否定修饰），走低置信 warn 分支
        mock_result = _pf_verdict(
            is_physical_form=True,
            matched=["原件"],
            negations=["不需要"],
            confidence="low",
        )
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(_ctx())

        assert verdict.verdict == "warn"
        assert verdict.confidence == 0.5
        assert verdict.suggested_patch is None

    def test_procedural_with_negation_returns_warn(self) -> None:
        mock_result = _pf_verdict(
            is_procedural=True,
            matched=["现场踏勘"],
            negations=["无需"],
            confidence="low",
        )
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(_ctx())

        assert verdict.verdict == "warn"

    def test_enforce_patch_trace_contains_matched_and_negations(self) -> None:
        mock_result = _pf_verdict(
            is_physical_form=True,
            matched=["加盖公章"],
            confidence="high",
        )
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(_skip_ctx())

        assert verdict.trace is not None
        assert "matched" in verdict.trace
        assert "negations" in verdict.trace
        assert verdict.trace["matched"] == ["加盖公章"]

    def test_suggested_patch_comment_is_user_facing_match(self) -> None:
        mock_result = _pf_verdict(
            is_physical_form=True, matched=["加盖公章"], confidence="high",
        )
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(_skip_ctx())

        comment = verdict.suggested_patch["comment"]  # type: ignore[index]
        assert "初步判定为符合" in comment
        assert "开标现场核验" in comment
        assert "[算法skip]" not in comment
        assert "LLM" not in comment

    def test_gate_metadata(self) -> None:
        assert GATE.gate_name == "PhysicalFormRequirementSkipGate"
        assert GATE.flow == "investigate"
        assert GATE.stage == "pre_llm"
        assert GATE.order == 10
        assert GATE.default_severity == "enforce"

    def test_evaluate_calls_classify_with_requirement_text(self) -> None:
        mock_result = _pf_verdict()
        with patch(_CLASSIFY_PATH, return_value=mock_result) as mock_fn:
            GATE.evaluate(_ctx(requirement_text="提供营业执照原件"))
        mock_fn.assert_called_once_with("提供营业执照原件")


# ---------------------------------------------------------------------------
# 必审安全防线用例
# ---------------------------------------------------------------------------

class TestMandatoryRequirementBlock:
    """验证 修复后的核心安全约束。

 必审要求（is_mandatory=True、None 或键缺失）在物理形态词命中时
 必须返回 warn 而非 enforce_patch，禁止算法放行。
 """

    def test_mandatory_true_blocks_skip_even_with_high_confidence(self) -> None:
        """is_mandatory=True + 高置信命中 → warn；verified: suggested_patch 为空，不含 _skip_llm_call（核心防线）"""
        mock_result = _pf_verdict(
            is_physical_form=True,
            matched=["投标保证金"],
            confidence="high",
        )
        ctx = _ctx(is_mandatory=True)
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(ctx)

        assert verdict.verdict == "warn", (
            f"必审要求必须阻断算法放行，实际 verdict={verdict.verdict!r}, trace={verdict.trace!r}"
        )
        assert verdict.suggested_patch is None, (
            f"必审要求 suggested_patch 必须为 None，实际={verdict.suggested_patch!r}"
        )
        # 核心防线：显式确认不含 _skip_llm_call
        if verdict.suggested_patch is not None:
            assert "_skip_llm_call" not in verdict.suggested_patch, (
                "检测到 _skip_llm_call，必审要求不得触发算法放行"
            )

    def test_missing_is_mandatory_key_treated_as_mandatory(self) -> None:
        """is_mandatory 键完全缺失 → 输入不完备，保守按必审处理，返回 warn"""
        mock_result = _pf_verdict(
            is_physical_form=True,
            matched=["原件"],
            confidence="high",
        )
        ctx = _ctx(include_is_mandatory=False)  # evidence 中完全不含 is_mandatory 键
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(ctx)

        assert verdict.verdict == "warn", (
            f"is_mandatory 键缺失应保守按必审处理，实际 verdict={verdict.verdict!r}, trace={verdict.trace!r}"
        )
        assert verdict.suggested_patch is None, (
            f"缺失 is_mandatory 不得算法放行，suggested_patch={verdict.suggested_patch!r}"
        )

    def test_is_mandatory_none_treated_as_mandatory(self) -> None:
        """is_mandatory=None → 输入不完备，按必审保守处理，返回 warn"""
        mock_result = _pf_verdict(
            is_physical_form=True,
            matched=["原件"],
            confidence="high",
        )
        ctx = _ctx(is_mandatory=None)
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(ctx)

        assert verdict.verdict == "warn", (
            f"is_mandatory=None 应按必审保守处理，实际 verdict={verdict.verdict!r}, trace={verdict.trace!r}"
        )
        assert verdict.suggested_patch is None, (
            f"is_mandatory=None 不得算法放行，suggested_patch={verdict.suggested_patch!r}"
        )

    def test_mandatory_false_high_confidence_returns_enforce_patch(self) -> None:
        """is_mandatory=False + 高置信命中 → 允许算法放行，enforce_patch"""
        mock_result = _pf_verdict(
            is_physical_form=True,
            matched=["加盖公章"],
            confidence="high",
        )
        ctx = _skip_ctx(is_mandatory=False)
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(ctx)

        assert verdict.verdict == "enforce_patch", (
            f"非必审高置信命中应为 enforce_patch，实际 verdict={verdict.verdict!r}, trace={verdict.trace!r}"
        )
        assert verdict.suggested_patch is not None
        assert verdict.suggested_patch["_skip_llm_call"] is True, (
            f"非必审命中 _skip_llm_call 必须为 True，实际={verdict.suggested_patch!r}"
        )
        assert verdict.suggested_patch["compliance_level"] == "fully"
        assert verdict.suggested_patch["score"] == 95

    def test_mandatory_true_low_confidence_blocked_by_mandatory_not_negation(self) -> None:
        """is_mandatory=True + 低置信命中（有否定词） → warn，
        trace.blocked_by=="mandatory_requirement"（必审分支优先于低置信分支）"""
        mock_result = _pf_verdict(
            is_physical_form=True,
            matched=["原件"],
            negations=["不需要"],
            confidence="low",
        )
        ctx = _ctx(is_mandatory=True)
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(ctx)

        assert verdict.verdict == "warn", (
            f"is_mandatory=True + 低置信应为 warn，实际 verdict={verdict.verdict!r}"
        )
        assert verdict.trace is not None
        assert verdict.trace.get("blocked_by") == "mandatory_requirement", (
            f"必审分支应在低置信分支之前触发，blocked_by={verdict.trace.get('blocked_by')!r}，"
            f"完整 trace={verdict.trace!r}"
        )

    def test_mandatory_true_no_keyword_hit_returns_pass(self) -> None:
        """is_mandatory=True + 完全未命中 → pass（必审标志不得影响未命中路径）"""
        mock_result = _pf_verdict(
            is_physical_form=False,
            is_procedural=False,
            matched=[],
            confidence="high",
        )
        ctx = _ctx(requirement_text="具有五年以上施工经验", is_mandatory=True)
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(ctx)

        assert verdict.verdict == "pass", (
            f"未命中时 is_mandatory=True 不得改变 pass 结果，实际 verdict={verdict.verdict!r}"
        )

    def test_mandatory_branch_trace_contains_all_observable_fields(self) -> None:
        """必审分支 trace 必须保留 matched/is_physical_form/is_procedural/negations，
        且 blocked_by 标记存在、is_mandatory 被回显"""
        mock_result = _pf_verdict(
            is_physical_form=True,
            matched=["原件", "投标函"],
            confidence="high",
        )
        ctx = _ctx(is_mandatory=True)
        with patch(_CLASSIFY_PATH, return_value=mock_result):
            verdict = GATE.evaluate(ctx)

        assert verdict.trace is not None, "必审 warn 分支必须输出 trace"
        trace = verdict.trace
        assert "matched" in trace, f"trace 缺少 matched 字段: {trace!r}"
        assert "is_physical_form" in trace, f"trace 缺少 is_physical_form 字段: {trace!r}"
        assert "is_procedural" in trace, f"trace 缺少 is_procedural 字段: {trace!r}"
        assert "negations" in trace, f"trace 缺少 negations 字段: {trace!r}"
        assert "blocked_by" in trace, f"trace 缺少 blocked_by 字段: {trace!r}"
        assert "is_mandatory" in trace, f"trace 缺少 is_mandatory 回显字段: {trace!r}"
        assert trace["blocked_by"] == "mandatory_requirement", (
            f"blocked_by 值错误: {trace['blocked_by']!r}"
        )
        assert trace["is_mandatory"] is True, (
            f"is_mandatory 回显值错误: {trace['is_mandatory']!r}"
        )
        assert trace["matched"] == ["原件", "投标函"], (
            f"matched 回显值错误: {trace['matched']!r}"
        )


# ---------------------------------------------------------------------------
# 聚焦 skip + 按响应给出符合/不符合意见
# ---------------------------------------------------------------------------

class TestSkipFocusAndOpinion:
    """评分项/非格式类/复合实质/响应未知一律交 LLM；纯格式项按有无响应给真意见。"""

    def _physical(self) -> PhysicalFormVerdict:
        return _pf_verdict(
            is_physical_form=True, matched=["加盖公章"], confidence="high",
        )

    def test_scoring_item_never_skips(self) -> None:
        with patch(_CLASSIFY_PATH, return_value=self._physical()):
            verdict = GATE.evaluate(_skip_ctx(requirement_kind="scoring_item"))
        assert verdict.verdict == "warn"
        assert verdict.suggested_patch is None
        assert verdict.trace is not None
        assert verdict.trace.get("blocked_by") == "scoring_or_key_review"

    def test_key_review_never_skips(self) -> None:
        with patch(_CLASSIFY_PATH, return_value=self._physical()):
            verdict = GATE.evaluate(_skip_ctx(is_key_review=1))
        assert verdict.verdict == "warn"
        assert verdict.suggested_patch is None
        assert verdict.trace is not None
        assert verdict.trace.get("blocked_by") == "scoring_or_key_review"

    def test_non_format_category_never_skips(self) -> None:
        with patch(_CLASSIFY_PATH, return_value=self._physical()):
            verdict = GATE.evaluate(_skip_ctx(category="performance"))
        assert verdict.verdict == "warn"
        assert verdict.suggested_patch is None
        assert verdict.trace is not None
        assert verdict.trace.get("blocked_by") == "not_format_category"

    def test_missing_category_never_skips(self) -> None:
        with patch(_CLASSIFY_PATH, return_value=self._physical()):
            verdict = GATE.evaluate(_skip_ctx(include_category=False, category=None))
        assert verdict.verdict == "warn"
        assert verdict.trace is not None
        assert verdict.trace.get("blocked_by") == "not_format_category"

    def test_mixed_substance_never_skips(self) -> None:
        with patch(_CLASSIFY_PATH, return_value=self._physical()):
            verdict = GATE.evaluate(
                _skip_ctx(requirement_text="技术方案须加盖公章")
            )
        assert verdict.verdict == "warn"
        assert verdict.suggested_patch is None
        assert verdict.trace is not None
        assert verdict.trace.get("blocked_by") == "mixed_substance"
        assert "技术方案" in (verdict.trace.get("substance_hits") or [])

    def test_missing_has_response_never_skips(self) -> None:
        with patch(_CLASSIFY_PATH, return_value=self._physical()):
            verdict = GATE.evaluate(
                _skip_ctx(include_has_response=False, has_response=None)
            )
        assert verdict.verdict == "warn"
        assert verdict.trace is not None
        assert verdict.trace.get("blocked_by") == "response_unknown"

    def test_no_response_is_not_compliant_with_user_comment(self) -> None:
        with patch(_CLASSIFY_PATH, return_value=self._physical()):
            verdict = GATE.evaluate(_skip_ctx(has_response=False))
        assert verdict.verdict == "enforce_patch"
        suggested = verdict.suggested_patch
        assert suggested is not None
        assert suggested["compliance_level"] == "not"
        assert suggested["score"] == 0
        assert suggested["risk_level"] == "medium"
        assert "初步判定为不符合" in suggested["comment"]
        assert "[算法skip]" not in suggested["comment"]
        assert "LLM" not in suggested["comment"]
