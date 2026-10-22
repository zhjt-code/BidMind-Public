"""D5QualTypeExemptionGate — D5 同类资质豁免（B3.3）。

迁移自 analyzer.py ``_enforce_d5_business_rules`` 的同类资质分支。
"""

from __future__ import annotations

from app.core.ai.gates.base import Gate, GateContext, GateVerdict


class D5QualTypeExemptionGate(Gate):
    """D5 同类资质编号不同豁免：同行业同类型资质不构成串标证据。"""

    gate_name = "D5QualTypeExemptionGate"
    flow = "collusion"
    stage = "post_llm"
    order = 40
    default_severity = "enforce"
    description = "D5 同类资质编号不同豁免"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "collusion" or ctx.stage != "post_llm":
            return False
        target = ctx.target
        if not isinstance(target, dict):
            return False
        d5 = target.get("d5_qualification")
        if not isinstance(d5, dict) or (d5.get("score") or 0) <= 0:
            return False
        return not d5.get("gate_rejected", False)

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        d5 = target["d5_qualification"]

        findings = d5.get("findings") or []
        rejected_count = 0

        for f in findings:
            if not isinstance(f, dict):
                continue
            if f.get("verified") is False:
                continue
            summary = (f.get("summary") or "") + (f.get("detail") or "")
            summary_lower = summary.lower()

            if any(kw in summary_lower for kw in ("资质", "证书", "许可")):
                has_same_type = any(
                    kw in summary_lower for kw in ("相同", "一致", "重叠", "同类")
                )
                has_diff_id = any(
                    kw in summary_lower for kw in ("编号不同", "证号不同")
                )
                if has_same_type or has_diff_id:
                    f["verified"] = False
                    f["rejection_reason"] = (
                        "算法校验: 同行业同类型资质(编号不同)属正常现象, 不构成串标证据"
                    )
                    f["certainty"] = min(f.get("certainty", 0), 0.15)
                    rejected_count += 1
                    continue

            f.setdefault("verified", True)

        all_findings = [f for f in findings if isinstance(f, dict)]
        total_rejected = sum(1 for f in all_findings if f.get("verified") is False)
        if total_rejected > 0 and total_rejected == len(all_findings):
            d5["score"] = 0.0
            d5["certainty"] = 0.0
            d5["gate_rejected"] = True

        verdict = "enforce_patch" if rejected_count > 0 else "pass"
        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=f"D5 qual_type: rejected={rejected_count}",
            confidence=1.0,
        )
