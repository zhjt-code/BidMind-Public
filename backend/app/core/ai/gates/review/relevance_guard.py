"""RelevanceGuardGate — 关联性守卫（B3.1 迁移样板）。

逐行等价于 reviewer.py ``_apply_relevance_guard``，
拆为三档独立 policy 函数以便单元测试与后续阈值配置化。

签名按 docs/evidence_gate_design.md §3.1 / §4.1 定义。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict


class RelevanceGuardGate(Gate):
    """关联性不足时强制降分（与 Prompt Golden Rule6 评分上限表对齐）。"""

    gate_name = "RelevanceGuardGate"
    flow = "investigate"
    stage = "post_llm"
    order = 30
    default_severity = "enforce"
    description = "LLM relevance_score 低于阈值时强制降分"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "post_llm":
            return False
        target = ctx.target
        relevance = getattr(target, "relevance_score", None)
        if relevance is None:
            return False
        return relevance < 80

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        evidence = ctx.evidence or {}

        relevance: int = target.relevance_score
        is_mandatory: bool = evidence.get("is_mandatory", False)

        score: int = getattr(target, "score", 0)
        compliance_level: str = getattr(target, "compliance_level", "not")
        risk_level: str = getattr(target, "risk_level", "none")
        comment: str = getattr(target, "comment", "") or ""
        relevance_note: str = getattr(target, "relevance_note", "") or ""

        patch = _grade_critical(relevance, is_mandatory, score, compliance_level,
                                risk_level, comment, relevance_note)
        if patch is None:
            patch = _grade_low(relevance, is_mandatory, score, compliance_level,
                               risk_level, comment, relevance_note)
        if patch is None:
            patch = _grade_medium(relevance, score, compliance_level,
                                  risk_level, comment, relevance_note)

        if patch is None:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason=f"relevance_score={relevance}, no downgrade needed",
                confidence=1.0,
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason=f"relevance_score={relevance}, forced downgrade applied",
            suggested_patch=patch,
            confidence=1.0,
        )


_SUFFIX = " [系统降分: 相关性不足]"


def _comment_suffix(relevance: int) -> str:
    return (
        f"｜系统提示：响应内容与要求领域匹配度低"
        f"（相关性评估{relevance}分/满分100），已按规则降分"
    )


def _grade_critical(
    relevance: int,
    is_mandatory: bool,
    score: int,
    compliance_level: str,
    risk_level: str,
    comment: str,
    relevance_note: str,
) -> dict[str, Any] | None:
    """relevance < 30 且为否决项：最严格降分。"""
    if not (relevance < 30 and is_mandatory):
        return None
    return {
        "score": 0,
        "compliance_level": "not",
        "risk_level": "high",
        "is_compliant": False,
        "relevance_note": relevance_note + _SUFFIX,
        "comment": comment + _comment_suffix(relevance),
    }


def _grade_low(
    relevance: int,
    is_mandatory: bool,
    score: int,
    compliance_level: str,
    risk_level: str,
    comment: str,
    relevance_note: str,
) -> dict[str, Any] | None:
    """relevance < 50：中度降分。"""
    if not relevance < 50:
        return None
    patch: dict[str, Any] = {}

    if score > 30:
        patch["score"] = min(score, max(relevance, 20))
    if compliance_level == "fully":
        patch["compliance_level"] = "not"
    if risk_level in ("none", "low"):
        patch["risk_level"] = "medium"
    if is_mandatory:
        patch["risk_level"] = "high"
        patch["is_compliant"] = False

    patch["relevance_note"] = relevance_note + _SUFFIX
    patch["comment"] = comment + _comment_suffix(relevance)
    return patch


def _grade_medium(
    relevance: int,
    score: int,
    compliance_level: str,
    risk_level: str,
    comment: str,
    relevance_note: str,
) -> dict[str, Any] | None:
    """50 <= relevance < 80：轻度降分。"""
    if not relevance < 80:
        return None
    patch: dict[str, Any] = {}
    old_score = score

    if score > 75:
        patch["score"] = 75
    if compliance_level == "fully":
        patch["compliance_level"] = "partially"
    if risk_level == "none":
        patch["risk_level"] = "low"
    if old_score != patch.get("score", old_score):
        patch["comment"] = comment + _comment_suffix(relevance)

    if not patch:
        return None
    return patch
