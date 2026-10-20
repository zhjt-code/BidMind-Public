"""
职责: 主观技术方案禁止仅因「有方案」给满分；硬指标与逐档满分不压档。
不做: 不审查报价、不调用 LLM、不改非满分预估。
"""

from __future__ import annotations

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.ai.gates.review.scoring_object_detect import (
    assessments_confirm_full_marks,
    classify_scoring_standard_kind,
    is_presence_only_full_marks,
    is_technical_scoring_item,
    replace_estimated_score_claim,
    resolve_max_score,
)

_CAP_RATIO = 0.6
_CAP_NOTE = "有技术方案不等于满分，须对照评分标准核实质内容后按档给分。"


class ScoringPresenceMaxGate(Gate):
    """技术评分项：意见只有存在性、预估却满分时压到中档。"""

    gate_name = "ScoringPresenceMaxGate"
    flow = "investigate"
    stage = "post_llm"
    order = 43
    default_severity = "enforce"
    description = "技术评分项禁止有方案即满分"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "post_llm":
            return False
        return is_technical_scoring_item(ctx.evidence)

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        evidence = ctx.evidence or {}
        max_score = resolve_max_score(evidence)
        estimated = getattr(target, "estimated_score", None)
        comment = getattr(target, "comment", None) or ""
        if classify_scoring_standard_kind(evidence) == "binary":
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="硬指标满足即满分，不适用技术方案存在性压档",
                confidence=1.0,
            )
        if assessments_confirm_full_marks(
            getattr(target, "rule_assessments", None),
            max_score,
        ):
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="逐档判定已达标且合计满分",
                confidence=1.0,
            )
        if not is_presence_only_full_marks(comment, estimated, max_score):
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="预估分有质量分析支撑或未给满分",
                confidence=1.0,
            )

        capped = round(float(max_score) * _CAP_RATIO, 2)
        new_comment = replace_estimated_score_claim(comment, capped)
        if _CAP_NOTE not in new_comment:
            new_comment = new_comment.rstrip() + _CAP_NOTE
        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason="仅有技术方案存在性，不得给满分",
            suggested_patch={
                "estimated_score": capped,
                "compliance_level": "partially",
                "comment": new_comment,
            },
            confidence=1.0,
            trace={"max_score": max_score, "capped": capped},
        )
