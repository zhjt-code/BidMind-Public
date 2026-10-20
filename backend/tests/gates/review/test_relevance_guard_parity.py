"""RelevanceGuardGate 行为等价性测试（B3.1；Fix-01 后 legacy 已下线）。

Fix-01 起 reviewer.py ``_apply_relevance_guard`` 已删除，Gate Pipeline 为唯一实现。
本文件保留一份**逐字 legacy 副本**作为"行为基线"与 Gate 输出做 50+ 场景对照，
确保未来任何 Gate 修改都能显式发现语义漂移。
"""

import copy
import pytest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.pipeline import GatePipeline
from app.core.ai.gates.review.relevance_guard import RelevanceGuardGate


# ---------------------------------------------------------------------------
# Minimal stubs that replicate the fields the guard touches
# ---------------------------------------------------------------------------

@dataclass
class FakeRequirement:
    id: int = 1
    is_mandatory: bool = False
    category: Optional[object] = None
    content: str = ""
    description: str = ""
    canonical_key: Optional[str] = None
    main_requirement_id: Optional[int] = None


@dataclass
class FakeBidResponse:
    id: int = 1
    content: str = ""


@dataclass
class FakeMatchedPair:
    requirement: FakeRequirement
    response: Optional[FakeBidResponse] = None
    has_response: bool = True

    @property
    def requirement_id(self) -> int:
        return self.requirement.id

    @property
    def response_id(self) -> Optional[int]:
        return self.response.id if self.response else None

    @property
    def category(self) -> str:
        return "unknown"

    @property
    def is_mandatory(self) -> bool:
        return bool(self.requirement.is_mandatory)


class FakeExtracted:
    """Mutable namespace mirroring ExtractedReviewResult fields the guard touches."""

    def __init__(self, **kwargs):
        defaults = dict(
            relevance_score=None,
            score=80,
            compliance_level="fully",
            risk_level="none",
            is_compliant=True,
            comment="initial comment",
            relevance_note="",
        )
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(self, k, v)

    def snapshot(self) -> dict:
        return {k: getattr(self, k) for k in (
            "relevance_score", "score", "compliance_level", "risk_level",
            "is_compliant", "comment", "relevance_note",
        )}


# ---------------------------------------------------------------------------
# Legacy 行为基线副本（Fix-01 前 reviewer.py 内联实现的逐字拷贝）
# 线上实现已由 RelevanceGuardGate 取代，这里仅作等价性参考基准
# ---------------------------------------------------------------------------

def _apply_relevance_guard_legacy(extracted: FakeExtracted, pair: FakeMatchedPair) -> None:
    relevance = extracted.relevance_score
    if relevance is None:
        return

    suffix = " [系统降分: 相关性不足]"
    comment_suffix = (
        f"｜系统提示：响应内容与要求领域匹配度低"
        f"（相关性评估{relevance}分/满分100），已按规则降分"
    )

    if relevance < 30 and pair.is_mandatory:
        extracted.score = 0
        extracted.compliance_level = "not"
        extracted.risk_level = "high"
        extracted.is_compliant = False
        extracted.relevance_note = (extracted.relevance_note or "") + suffix
        extracted.comment = (extracted.comment or "") + comment_suffix
    elif relevance < 50:
        if extracted.score > 30:
            extracted.score = min(extracted.score, max(relevance, 20))
        if extracted.compliance_level == "fully":
            extracted.compliance_level = "not"
        if extracted.risk_level in ("none", "low"):
            extracted.risk_level = "medium"
        if pair.is_mandatory:
            extracted.risk_level = "high"
            extracted.is_compliant = False
        extracted.relevance_note = (extracted.relevance_note or "") + suffix
        extracted.comment = (extracted.comment or "") + comment_suffix
    elif relevance < 80:
        old_score = extracted.score
        if extracted.score > 75:
            extracted.score = 75
        if extracted.compliance_level == "fully":
            extracted.compliance_level = "partially"
        if extracted.risk_level == "none":
            extracted.risk_level = "low"
        if old_score != extracted.score:
            extracted.comment = (extracted.comment or "") + comment_suffix


# ---------------------------------------------------------------------------
# Gate path helper
# ---------------------------------------------------------------------------

def _apply_via_gate(extracted: FakeExtracted, pair: FakeMatchedPair) -> None:
    gate = RelevanceGuardGate()
    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=extracted,
        evidence={"is_mandatory": pair.is_mandatory},
    )
    if not gate.applies(ctx):
        return
    verdict = gate.evaluate(ctx)
    if verdict.suggested_patch:
        for k, v in verdict.suggested_patch.items():
            setattr(extracted, k, v)


# ---------------------------------------------------------------------------
# Sample matrix (50+ scenarios)
# ---------------------------------------------------------------------------

_SCORE_VALUES = [0, 20, 25, 30, 50, 75, 80, 90, 100]
_RELEVANCE_VALUES = [None, 0, 10, 20, 29, 30, 40, 49, 50, 60, 70, 79, 80, 95]
_COMPLIANCE_VALUES = ["fully", "partially", "not"]
_RISK_VALUES = ["none", "low", "medium", "high"]
_MANDATORY_VALUES = [True, False]


def _generate_samples():
    """Generate a deterministic sample matrix covering all code paths."""
    samples = []

    for rel in _RELEVANCE_VALUES:
        for mand in _MANDATORY_VALUES:
            for score in [0, 30, 50, 80, 100]:
                for cl in ["fully", "partially", "not"]:
                    for rl in ["none", "low", "medium", "high"]:
                        samples.append((rel, mand, score, cl, rl))

    return samples


_SAMPLES = _generate_samples()


@pytest.mark.parametrize(
    "relevance,is_mandatory,score,compliance_level,risk_level",
    _SAMPLES,
    ids=[
        f"rel={s[0]}_mand={s[1]}_sc={s[2]}_cl={s[3]}_rl={s[4]}"
        for s in _SAMPLES
    ],
)
def test_parity(relevance, is_mandatory, score, compliance_level, risk_level):
    """Gate path must produce identical result to legacy path."""
    pair = FakeMatchedPair(
        requirement=FakeRequirement(is_mandatory=is_mandatory),
    )

    legacy = FakeExtracted(
        relevance_score=relevance,
        score=score,
        compliance_level=compliance_level,
        risk_level=risk_level,
        is_compliant=True,
        comment="initial comment",
        relevance_note="",
    )
    gate_copy = FakeExtracted(
        relevance_score=relevance,
        score=score,
        compliance_level=compliance_level,
        risk_level=risk_level,
        is_compliant=True,
        comment="initial comment",
        relevance_note="",
    )

    _apply_relevance_guard_legacy(legacy, pair)
    _apply_via_gate(gate_copy, pair)

    legacy_snap = legacy.snapshot()
    gate_snap = gate_copy.snapshot()

    assert legacy_snap == gate_snap, (
        f"Parity mismatch:\n"
        f"  legacy = {legacy_snap}\n"
        f"  gate   = {gate_snap}\n"
        f"  diff   = { {k: (legacy_snap[k], gate_snap[k]) for k in legacy_snap if legacy_snap[k] != gate_snap[k]} }"
    )
