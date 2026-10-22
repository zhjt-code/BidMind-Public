"""S.5.3 · D3TextAlgorithmicGate — 纯算法 D3 与 LLM D3 对跑观测（advisory）。

stage=post_llm, default_severity=advisory, order=55（在 FindingPageQuoteVerifyGate=50 之后）。

与 B4.7 AlgorithmicRiskBandGate 同构：
- 算法 D3 分数与 LLM 的 `d3_text.score` 对比
- 阈值 0.7 作为"高相似"判定边界
- 一致 → pass + 采集 agreement 指标
- 分歧 → warn（不改判）+ 采集 disagreement 指标 + 输出算法推荐到 suggested_patch
- 升 enforce 条件：真实流量一致率 >= 85%（I-011 P3-3 候选）

evidence 需求：`chunk_texts[doc_a_id] / chunk_texts[doc_b_id]`（已由 analyzer
`_prefetch_chunks_for_gate` 提供）；`info_a / info_b` 的 `whitelist` 字段（可选）。
"""

from __future__ import annotations

from app.core.ai.critic.flows import FLOW_COLLUSION
from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.collusion.d3_algorithmic import compute_d3_algorithmic
from app.core.collusion.dimension_keys import COLLUSION_DIM_KEYS  # noqa: F401 (防 A6 复发)

_HIGH_THRESHOLD = 0.7


class D3TextAlgorithmicGate(Gate):
    """D3 纯算法判定对照（advisory）。"""

    gate_name = "D3TextAlgorithmicGate"
    flow = FLOW_COLLUSION
    stage = "post_llm"
    order = 55
    default_severity = "advisory"
    description = "D3 文本相似度纯算法对跑（SimHash/LCS/TF-IDF 综合）"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != FLOW_COLLUSION or ctx.stage != "post_llm":
            return False
        evidence = ctx.evidence or {}
        chunk_texts = evidence.get("chunk_texts")
        doc_a_id = evidence.get("doc_a_id")
        doc_b_id = evidence.get("doc_b_id")
        return bool(chunk_texts) and doc_a_id is not None and doc_b_id is not None

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        evidence = ctx.evidence or {}
        chunk_texts: dict = evidence.get("chunk_texts", {})
        doc_a_id = evidence.get("doc_a_id")
        doc_b_id = evidence.get("doc_b_id")

        # _prefetch_chunks_for_gate 产出 {doc_id: list[(page, text)]}
        def _texts(doc_id: int | None) -> list[str]:
            items = chunk_texts.get(doc_id, []) if doc_id is not None else []
            return [t for (_p, t) in items if t]

        chunks_a = _texts(doc_a_id)
        chunks_b = _texts(doc_b_id)
        if not chunks_a or not chunks_b:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason="chunk_texts 不足，跳过算法判定",
                confidence=0.0,
            )

        # 招标白名单关键词（若 analyzer 传入 tender_format_whitelist 含 required_fonts 等）
        whitelist_keywords: list[str] = []
        wf = evidence.get("tender_format_whitelist") or {}
        if isinstance(wf, dict):
            for key in ("required_keywords", "required_fonts"):
                val = wf.get(key)
                if isinstance(val, list):
                    whitelist_keywords.extend([str(x) for x in val])

        alg = compute_d3_algorithmic(
            chunks_a, chunks_b, whitelist_keywords=whitelist_keywords or None,
            high_threshold=_HIGH_THRESHOLD,
        )

        # 从 target 取 LLM 的 D3 判定
        llm_d3 = (target.get("d3_text") if isinstance(target, dict) else None) or {}
        llm_score = float(llm_d3.get("score", 0.0) or 0.0)

        # agreement 判定：同在高相似区（>= 0.7）或同在低相似区（< 0.7）
        alg_high = alg.score >= _HIGH_THRESHOLD
        llm_high = llm_score >= _HIGH_THRESHOLD
        agreement = (alg_high == llm_high)

        # 采集指标
        try:
            from app.core.observability.metrics import (  # noqa: PLC0415
                d3_algorithmic_agreement_total,
                d3_algorithmic_disagreement_total,
            )
            if agreement:
                d3_algorithmic_agreement_total.labels(
                    flow=FLOW_COLLUSION, alg_high=str(alg_high), llm_high=str(llm_high),
                ).inc()
            else:
                d3_algorithmic_disagreement_total.labels(
                    flow=FLOW_COLLUSION, alg_high=str(alg_high), llm_high=str(llm_high),
                ).inc()
        except Exception:
            pass

        if agreement:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason=f"D3 一致：alg={alg.score:.2%} llm={llm_score:.2%}（均{'高' if alg_high else '低'}相似）",
                confidence=alg.certainty,
                trace={"alg_score": alg.score, "llm_score": llm_score,
                       "breakdown": alg.breakdown},
            )

        return GateVerdict(
            gate_name=self.gate_name, verdict="warn",
            severity=self.default_severity,
            reason=(
                f"D3 分歧：alg={alg.score:.2%} ({'高' if alg_high else '低'}相似) "
                f"vs llm={llm_score:.2%} ({'高' if llm_high else '低'}相似)"
            ),
            suggested_patch={
                "d3_text_algorithmic": alg.to_dict(),
            },
            confidence=alg.certainty,
            trace={
                "alg_score": alg.score, "llm_score": llm_score,
                "breakdown": alg.breakdown, "findings": alg.findings,
            },
        )
