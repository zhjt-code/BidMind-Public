"""D5BusinessRelationGate — D5 甲乙方合作关系豁免（B3.3）。

迁移自 analyzer.py ``_enforce_d5_business_rules`` 的甲乙方合作关系分支。
包含 D5 维度的"双方资质均为空 → score 归零"前置检查。
"""

from __future__ import annotations

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

_EMPTY_VALUES = {"", "无", "不适用", "N/A", "/", "-", "null", "none"}


def _is_empty_qual_list(quals: list) -> bool:
    if not quals:
        return True
    return all(
        str(q).strip().lower() in _EMPTY_VALUES
        for q in quals if isinstance(q, str)
    )


class D5BusinessRelationGate(Gate):
    """D5 甲乙方合作关系豁免 + 空资质拦截。"""

    gate_name = "D5BusinessRelationGate"
    flow = "collusion"
    stage = "post_llm"
    order = 30
    default_severity = "enforce"
    description = "D5 甲乙方合作关系检测与空资质拦截"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "collusion" or ctx.stage != "post_llm":
            return False
        target = ctx.target
        if not isinstance(target, dict):
            return False
        d5 = target.get("d5_qualification")
        if not isinstance(d5, dict) or (d5.get("score") or 0) <= 0:
            return False
        evidence = ctx.evidence or {}
        return bool(evidence.get("info_a")) and bool(evidence.get("info_b"))

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        evidence = ctx.evidence or {}
        info_a = evidence["info_a"]
        info_b = evidence["info_b"]

        d5 = target["d5_qualification"]

        quals_a = info_a.get("qualifications") or []
        quals_b = info_b.get("qualifications") or []

        if _is_empty_qual_list(quals_a) and _is_empty_qual_list(quals_b):
            d5["score"] = 0.0
            d5["certainty"] = 0.0
            d5["gate_rejected"] = True
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="enforce_patch",
                severity=self.default_severity,
                reason="D5: both qualification lists empty, score zeroed",
                suggested_patch={"d5_qualification.gate_rejected": True},
                confidence=1.0,
            )

        comp_a = info_a.get("company_info") or {}
        comp_b = info_b.get("company_info") or {}
        company_name_a = (comp_a.get("company_name") or "").strip()
        company_name_b = (comp_b.get("company_name") or "").strip()
        projects_a = info_a.get("past_projects") or []
        projects_b = info_b.get("past_projects") or []

        findings = d5.get("findings") or []
        rejected_count = 0

        for f in findings:
            if not isinstance(f, dict):
                continue
            summary = (f.get("summary") or "") + (f.get("detail") or "")

            if company_name_a and company_name_b:
                is_client = (
                    (company_name_a in summary or company_name_b in summary)
                    and any(kw in summary for kw in ("委托", "合同方", "甲方", "业主"))
                )
                if not is_client:
                    for proj in projects_a:
                        if isinstance(proj, str) and company_name_b in proj:
                            is_client = True
                            break
                if not is_client:
                    for proj in projects_b:
                        if isinstance(proj, str) and company_name_a in proj:
                            is_client = True
                            break

                if is_client:
                    f["verified"] = False
                    f["business_relationship"] = True
                    f["rejection_reason"] = (
                        "算法校验: 检测到甲乙方合作关系, 不构成业绩重叠证据"
                    )
                    rejected_count += 1

        verdict = "enforce_patch" if rejected_count > 0 else "pass"
        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=f"D5 business_relation: rejected={rejected_count}",
            confidence=1.0,
        )
