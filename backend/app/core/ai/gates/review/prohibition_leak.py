"""
职责: 禁止项若在禁区检出违禁物，则禁止完全符合。
不做: 不计算评分、不调用 LLM、不改 must_have 项。
"""

from __future__ import annotations

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.ai.gates.review.prohibition_policy import (
    format_prohibition_comment,
    hits_from_dicts,
    parse_prohibition,
)


class ProhibitionLeakGate(Gate):
    """禁区违禁物命中时覆盖满分结论。"""

    gate_name = "ProhibitionLeakGate"
    flow = "investigate"
    stage = "post_llm"
    order = 43
    default_severity = "enforce"
    description = "禁止项核验禁区违禁物，禁止只凭合规声明放行"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "post_llm":
            return False
        evidence = ctx.evidence or {}
        if parse_prohibition(evidence.get("requirement_text")) is None:
            return False
        return bool(self._hits_of(evidence))

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        hits = self._hits_of(evidence)
        spec = parse_prohibition(evidence.get("requirement_text"))
        comment = format_prohibition_comment(hits, spec)
        pages = [hit.page for hit in hits if hit.page is not None]
        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason="禁止项禁区检出违禁物",
            suggested_patch={
                "compliance_level": "not",
                "is_compliant": False,
                "score": 0,
                "risk_level": "high",
                "comment": comment,
            },
            confidence=1.0,
            trace={"pages": pages, "hit_count": len(hits)},
        )

    @staticmethod
    def _hits_of(evidence: dict) -> tuple:
        return hits_from_dicts(
            evidence.get("prohibition_leak_hits")
            or evidence.get("price_leak_hits")
            or ()
        )
