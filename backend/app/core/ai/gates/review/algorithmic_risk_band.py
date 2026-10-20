"""B4.7 AlgorithmicRiskBandGate — 算法 risk_band 与 LLM 对比（本批次 advisory only）。

所有投标 post_llm 流量都执行（与 INVESTIGATE_ALGORITHM_FIRST_RATIO 解耦），
保证度量样本充足。

Gate 行为说明:
  - 算法 band == LLM band → pass
  - 算法 band != LLM band → warn，suggested_patch 含算法推荐值（advisory，不强制覆盖）
  - 失败计数通过 prometheus 指标上报，异常静默处理
"""
from __future__ import annotations

from app.core.ai.critic.flows import FLOW_INVESTIGATE
from app.core.ai.gates.base import Gate, GateContext, GateVerdict


class AlgorithmicRiskBandGate(Gate):
    """算法 risk_band 与 LLM 判定对比。

    本批次（B4.7）仅为观测模式（severity=advisory），不强制覆盖 LLM 结论。
    若一致则 pass；若分歧则 warn 并通过 suggested_patch 记录算法推荐，
    供后续批次升级为 enforce 时直接启用。
    """

    gate_name = "AlgorithmicRiskBandGate"
    flow = FLOW_INVESTIGATE
    stage = "post_llm"
    order = 50
    default_severity = "advisory"
    description = "算法 risk_band 与 LLM 判定对比"

    def applies(self, ctx: GateContext) -> bool:
        return ctx.flow == FLOW_INVESTIGATE and ctx.stage == "post_llm"

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        is_mandatory: bool = evidence.get("is_mandatory", False)
        category: str = evidence.get("category", "unknown")

        # Pre-computed tool verdicts populated by reviewer before gate invocation
        date_verdict = evidence.get("date_verdict")
        amount_verdict = evidence.get("amount_verdict")
        relevance_result = evidence.get("relevance_result")

        from app.core.ai.risk_band import derive_risk_band
        rb = derive_risk_band(
            is_mandatory=is_mandatory,
            date_verdict=date_verdict,
            amount_verdict=amount_verdict,
            relevance=relevance_result,
        )

        llm_band: str = getattr(ctx.target, "risk_level", "none") or "none"
        alg_band: str = rb.risk_band

        # ------------------------------------------------------------------
        # Observability: record agreement / disagreement metrics
        # ------------------------------------------------------------------
        try:
            from app.core.observability.metrics import (  # noqa: PLC0415 (lazy to avoid prometheus init at import time)
                algorithmic_risk_band_agreement_total,
                algorithmic_risk_band_confidence_bucket_total,
                algorithmic_risk_band_disagreement_total,
            )

            conf_bucket = (
                "high" if rb.confidence >= 0.8 else ("medium" if rb.confidence >= 0.5 else "low")
            )

            if alg_band == llm_band:
                algorithmic_risk_band_agreement_total.labels(
                    flow=FLOW_INVESTIGATE,
                    is_mandatory=str(is_mandatory),
                    category=category,
                ).inc()
                algorithmic_risk_band_confidence_bucket_total.labels(
                    confidence_bucket=conf_bucket,
                    agree="true",
                ).inc()
            else:
                algorithmic_risk_band_disagreement_total.labels(
                    flow=FLOW_INVESTIGATE,
                    alg_band=alg_band,
                    llm_band=llm_band,
                    is_mandatory=str(is_mandatory),
                ).inc()
                algorithmic_risk_band_confidence_bucket_total.labels(
                    confidence_bucket=conf_bucket,
                    agree="false",
                ).inc()
        except Exception:  # noqa: BLE001
            pass

        # ------------------------------------------------------------------
        # Verdict
        # ------------------------------------------------------------------
        if alg_band == llm_band:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason=f"risk_band一致: alg={alg_band} == llm={llm_band}",
                confidence=rb.confidence,
                trace={"contributing_factors": rb.contributing_factors},
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="warn",
            severity=self.default_severity,
            reason=f"risk_band分歧: alg={alg_band} vs llm={llm_band}",
            suggested_patch={
                "risk_level": alg_band,
                "failure_state": "needs_review",
            },
            confidence=rb.confidence,
            trace={
                "contributing_factors": rb.contributing_factors,
                "llm_band": llm_band,
            },
        )
