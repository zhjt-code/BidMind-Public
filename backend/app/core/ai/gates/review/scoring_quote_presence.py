"""
职责: 价格评分项按招标要求核验报价形态；无对应形态则预估分为 0。
不做: 不计算比较分、不审查技术方案、不调用 LLM。
"""

from __future__ import annotations

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.ai.gates.review.scoring_object_detect import (
    has_bid_quote,
    is_comparative_price_formula,
    is_price_scoring_item,
    strip_estimated_score_claim,
)

COMMENT_NO_QUOTE = (
    "未查询到报价。投标文件中未找到开标一览表、投标报价、投标总价等金额，"
    "也未找到招标要求的下浮/降点/折扣率，仅复述评标价格分计算规则不视为已报价。"
    "本项预估得分：0分。"
)
COMMENT_COMPARATIVE = (
    "已查询到投标报价，但本项价格分依赖与其他投标人比较，单标审查无法精确计算。"
    "比较分待评标。预估评标分仅供参考，不代表实际评标得分。"
)


class ScoringQuotePresenceGate(Gate):
    """价格评分项：无报价打 0；比较分公式有报价则不编造预估分。"""

    gate_name = "ScoringQuotePresenceGate"
    flow = "investigate"
    stage = "post_llm"
    order = 42
    default_severity = "enforce"
    description = "价格评分项核验报价对象，禁止把评标办法复述当成已报价满分"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "post_llm":
            return False
        return is_price_scoring_item(ctx.evidence)

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        has_response = bool(evidence.get("has_response"))
        response_text = evidence.get("response_content") or ""
        req_text = evidence.get("requirement_text") or ""
        quoted = has_response and has_bid_quote(response_text, req_text)

        if not quoted:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="enforce_patch",
                severity=self.default_severity,
                reason="价格评分项未查询到报价",
                suggested_patch={
                    "estimated_score": 0,
                    "score": 0,
                    "compliance_level": "not",
                    "is_compliant": False,
                    "comment": COMMENT_NO_QUOTE,
                },
                confidence=1.0,
                trace={"quoted": False, "has_response": has_response},
            )

        if is_comparative_price_formula(req_text):
            comment = strip_estimated_score_claim(
                getattr(ctx.target, "comment", None), ""
            )
            if "比较分待评标" not in comment:
                comment = (comment.rstrip() + COMMENT_COMPARATIVE).strip()
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="enforce_patch",
                severity=self.default_severity,
                reason="比较分公式单标无法精确计算",
                suggested_patch={
                    "estimated_score": None,
                    "comment": comment,
                },
                confidence=1.0,
                trace={"quoted": True, "comparative": True},
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="pass",
            severity=self.default_severity,
            reason="已查询到报价且公式可在本标内核验",
            confidence=1.0,
            trace={"quoted": True, "comparative": False},
        )
