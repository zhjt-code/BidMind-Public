"""RelevanceAnomalyBackfillGate — 关联性异常后置检测（B3.6, post_pipeline）。

迁移自 reviewer.py ``_relevance_anomaly_detect``。
"""

from __future__ import annotations

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

# Fix-03: category 口径统一为全称，ExtractedReviewResult.validator 负责缩写归一。
# Requirement.category 本就用 Enum 全称，这里对齐内部唯一口径。
_HIGH_SCORE_CATEGORIES = {"performance", "qualification", "staffing"}


class RelevanceAnomalyBackfillGate(Gate):
    """LLM 未输出 relevance_score 但 score 偏高时，用关键词重叠度估算并标记。"""

    gate_name = "RelevanceAnomalyBackfillGate"
    flow = "investigate"
    stage = "post_pipeline"
    order = 20
    default_severity = "enforce"
    description = "关联性异常后置回填检测"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "post_pipeline":
            return False
        return isinstance(ctx.target, dict) and len(ctx.target) > 0

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        """Run anomaly detection on grouped review results.

        ctx.target: dict[fact_key, list[(ReviewResult, Requirement, BidResponse|None)]]
        ctx.evidence: {"estimate_keyword_relevance": callable}
        """
        grouped = ctx.target
        evidence = ctx.evidence or {}
        estimator = evidence.get("estimate_keyword_relevance")

        if not estimator:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason="estimator function not provided",
            )

        anomalies = 0

        for _key, items in grouped.items():
            for rr, req, br in items:
                if rr.relevance_score is not None:
                    continue
                cat = req.category.value if req.category else ""
                if cat not in _HIGH_SCORE_CATEGORIES:
                    continue
                if rr.score < 80:
                    continue
                if not br or not br.content:
                    continue

                estimated = estimator(req.description or "", br.content or "")
                if estimated < 30:
                    rr.relevance_score = estimated
                    rr.relevance_note = (
                        f"系统后置检测: 关键词重叠度极低({estimated}), "
                        f"响应领域可能与要求不匹配, 建议人工复核"
                    )
                    rr.comment = (
                        (rr.comment or "")
                        + f"｜系统提示：响应内容与要求领域可能不匹配"
                          f"（相关性评估{estimated}分/满分100），建议人工复核"
                    )
                    rr.consistency_flag = "needs_review"
                    rr.reconcile_note = (
                        (rr.reconcile_note or "")
                        + f"｜相关性疑点（评估分{estimated}）"
                    ).strip("｜")
                    anomalies += 1

        verdict = "enforce_patch" if anomalies > 0 else "pass"
        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=f"relevance_anomaly: {anomalies} anomaly(ies) found",
            confidence=1.0,
        )
