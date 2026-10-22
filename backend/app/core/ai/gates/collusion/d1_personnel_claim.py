"""D1PersonnelClaimGate — D1 人员重叠 claim-evidence 算法硬校验（B3.3）。

迁移自 analyzer.py ``_verify_d1_d2_claims`` 的 D1 分支。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

_D1_CLAIM_KEYWORDS = (
    "人员重叠", "重叠人员", "同一人员", "相同人员",
    "项目经理相同", "技术负责人相同", "人员交叉",
)


class D1PersonnelClaimGate(Gate):
    """D1 人员重叠：LLM 声称人员重叠时用结构化字段交叉验证。"""

    gate_name = "D1PersonnelClaimGate"
    flow = "collusion"
    stage = "post_llm"
    order = 10
    default_severity = "enforce"
    description = "D1 claim-evidence 人员重叠硬校验"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "collusion" or ctx.stage != "post_llm":
            return False
        target = ctx.target
        if not isinstance(target, dict):
            return False
        d1 = target.get("d1_personnel")
        return isinstance(d1, dict) and (d1.get("score") or 0) > 0

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        evidence = ctx.evidence or {}
        info_a = evidence.get("info_a") or {}
        info_b = evidence.get("info_b") or {}

        d1 = target["d1_personnel"]
        personnel_a = info_a.get("personnel_list") or []
        personnel_b = info_b.get("personnel_list") or []

        names_a = {
            (p.get("name") or "").strip().lower()
            for p in personnel_a if isinstance(p, dict) and (p.get("name") or "").strip()
        }
        names_b = {
            (p.get("name") or "").strip().lower()
            for p in personnel_b if isinstance(p, dict) and (p.get("name") or "").strip()
        }
        shared_names = names_a & names_b

        findings = d1.get("findings") or []
        all_rejected = True
        any_modified = False

        for f in findings:
            if not isinstance(f, dict):
                continue
            summary = (f.get("summary") or "") + (f.get("detail") or "")
            has_claim = any(kw in summary for kw in _D1_CLAIM_KEYWORDS)
            if has_claim and not shared_names:
                f["verified"] = False
                f["rejection_reason"] = (
                    "算法校验: LLM声称人员重叠, 但结构化提取中两份文件无任何同名人员"
                )
                any_modified = True
            else:
                f["verified"] = True
                all_rejected = False

        patch: dict[str, Any] | None = None
        if all_rejected and findings:
            d1["score"] = 0.0
            d1["certainty"] = 0.0
            d1["gate_rejected"] = True
            patch = {"d1_personnel.gate_rejected": True}

        verdict = "enforce_patch" if any_modified else "pass"
        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=f"D1 personnel: shared_names={len(shared_names)}, "
                   f"all_rejected={all_rejected}",
            suggested_patch=patch,
            confidence=1.0,
        )
