"""B4.4 PhysicalFormRequirementSkipGate — 纯文件形态要求的 pre_llm skip。

职责: 仅对格式类、非评分、非否决、无实质条款混写的装订/签章等要求跳过 LLM，
     并按投标响应有无给出符合或不符合意见。
不做: 不审查评分项/重点评审/否决项；不对澄清答疑/开标时间等程序词放行；
     不把机器标记写入用户可见审查意见。
"""
from __future__ import annotations

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.extractor.tools.physical_form_classifier import PhysicalFormVerdict

SKIP_COMMENT_MATCHED = (
    "本项属于投标文件装订、签章等程序性要求。"
    "投标文件中已找到对应说明，初步判定为符合。"
    "装订份数、现场密封等仍以开标现场核验为准。"
)
SKIP_COMMENT_MISSING = (
    "本项属于投标文件装订、签章等程序性要求。"
    "投标文件中未找到对应响应，初步判定为不符合。"
)

_FORMAT_CATEGORIES = frozenset({"format"})


def _category_token(raw: object) -> str:
    if raw is None:
        return ""
    value = getattr(raw, "value", raw)
    return str(value).strip().lower()


def _is_format_category(raw: object) -> bool:
    return _category_token(raw) in _FORMAT_CATEGORIES


def _is_scoring_or_key(evidence: dict) -> bool:
    kind = str(evidence.get("requirement_kind") or "normal")
    if kind == "scoring_item":
        return True
    return bool(evidence.get("is_key_review"))


def _result_trace(result: PhysicalFormVerdict, **extra: object) -> dict:
    trace: dict = {
        "matched": result.matched_keywords,
        "negations": result.negations,
        "is_physical_form": result.is_physical_form,
        "is_procedural": result.is_procedural,
    }
    trace.update(extra)
    return trace


def _skip_patch(has_response: bool) -> dict:
    if has_response:
        return {
            "compliance_level": "fully",
            "risk_level": "low",
            "score": 95,
            "comment": SKIP_COMMENT_MATCHED,
            "_skip_llm_call": True,
        }
    return {
        "compliance_level": "not",
        "risk_level": "medium",
        "score": 0,
        "comment": SKIP_COMMENT_MISSING,
        "_skip_llm_call": True,
    }


class PhysicalFormRequirementSkipGate(Gate):
    """纯文件形态要求 skip LLM，并按响应有无给出符合/不符合。"""

    gate_name = "PhysicalFormRequirementSkipGate"
    flow = "investigate"
    stage = "pre_llm"
    order = 10
    default_severity = "enforce"
    description = "纯文件形态要求按投标响应给出符合或不符合，跳过 LLM"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "pre_llm":
            return False
        evidence = ctx.evidence or {}
        return bool(evidence.get("requirement_text"))

    def _warn(
        self,
        result: PhysicalFormVerdict,
        reason: str,
        blocked_by: str,
        extra_trace: dict | None = None,
    ) -> GateVerdict:
        trace = _result_trace(result, blocked_by=blocked_by)
        if extra_trace:
            trace.update(extra_trace)
        return GateVerdict(
            gate_name=self.gate_name,
            verdict="warn",
            severity=self.default_severity,
            reason=reason,
            confidence=0.5,
            trace=trace,
        )

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        req_text: str = evidence.get("requirement_text", "")
        from app.core.extractor.tools.physical_form_classifier import (
            classify_requirement,
            substance_keyword_hits,
        )
        result = classify_requirement(req_text)
        hit = result.is_physical_form or result.is_procedural
        _mandatory_raw = evidence.get("is_mandatory")
        must_review = _mandatory_raw is None or bool(_mandatory_raw)

        if not hit:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="非物理形态/程序履约要求",
                confidence=1.0,
            )

        if _is_scoring_or_key(evidence):
            return self._warn(
                result,
                reason="评分项或重点评审项禁止算法放行",
                blocked_by="scoring_or_key_review",
            )

        if must_review:
            return self._warn(
                result,
                reason=f"必审要求命中物理形态词，禁止算法放行: {result.matched_keywords}",
                blocked_by="mandatory_requirement",
                extra_trace={"is_mandatory": _mandatory_raw},
            )

        if "category" not in evidence or not _is_format_category(evidence.get("category")):
            return self._warn(
                result,
                reason="非格式类要求禁止算法放行",
                blocked_by="not_format_category",
            )

        if not result.is_physical_form:
            return self._warn(
                result,
                reason="仅程序词命中，范围不够聚焦，交 LLM 审查",
                blocked_by="procedural_not_focused",
            )

        substance_hits = substance_keyword_hits(req_text)
        if substance_hits:
            return self._warn(
                result,
                reason=f"条款混有实质审查内容，禁止算法放行: {substance_hits}",
                blocked_by="mixed_substance",
                extra_trace={"substance_hits": substance_hits},
            )

        if result.confidence != "high":
            return self._warn(
                result,
                reason=f"物理形态命中但存在否定修饰: {result.negations}",
                blocked_by="negation",
            )

        if "has_response" not in evidence:
            return self._warn(
                result,
                reason="投标响应是否存在未知，禁止算法放行",
                blocked_by="response_unknown",
            )

        has_response = bool(evidence.get("has_response"))
        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason="纯文件形态要求，已按投标响应给出符合或不符合",
            suggested_patch=_skip_patch(has_response),
            confidence=1.0,
            trace=_result_trace(result, has_response=has_response),
        )
