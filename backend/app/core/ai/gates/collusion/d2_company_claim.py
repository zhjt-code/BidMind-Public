"""D2CompanyClaimGate — D2 企业关联 claim-evidence 算法硬校验（B3.3）。

迁移自 analyzer.py ``_verify_d1_d2_claims`` 的 D2 分支。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.collusion.normalizers import (
    is_credit_code_related,
    is_nearby_phone,
    is_same_address_building,
    normalize_phone,
)

_D2_LEGAL_REP_KEYWORDS = ("法定代表人", "法人", "执行董事")
_D2_ADDRESS_KEYWORDS = ("注册地址", "办公地址", "经营地址", "地址")
_D2_PHONE_KEYWORDS = ("联系电话", "电话")
_D2_CREDIT_KEYWORDS = ("信用代码", "社会信用")


class D2CompanyClaimGate(Gate):
    """D2 企业关联：LLM 声称企业关联时用结构化字段交叉验证。"""

    gate_name = "D2CompanyClaimGate"
    flow = "collusion"
    stage = "post_llm"
    order = 20
    default_severity = "enforce"
    description = "D2 claim-evidence 企业关联硬校验"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "collusion" or ctx.stage != "post_llm":
            return False
        target = ctx.target
        if not isinstance(target, dict):
            return False
        d2 = target.get("d2_company")
        return isinstance(d2, dict) and (d2.get("score") or 0) > 0

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        evidence = ctx.evidence or {}
        info_a = evidence.get("info_a") or {}
        info_b = evidence.get("info_b") or {}

        d2 = target["d2_company"]
        comp_a = info_a.get("company_info") or {}
        comp_b = info_b.get("company_info") or {}

        if not (isinstance(comp_a, dict) and isinstance(comp_b, dict)):
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason="company_info not available for either side",
            )

        lr_a = (comp_a.get("legal_representative") or "").strip()
        lr_b = (comp_b.get("legal_representative") or "").strip()
        addr_a = (comp_a.get("registered_address") or "").strip()
        addr_b = (comp_b.get("registered_address") or "").strip()
        phone_a = normalize_phone(comp_a.get("contact_phone"))
        phone_b = normalize_phone(comp_b.get("contact_phone"))
        credit_a = (comp_a.get("unified_social_credit_code") or "").strip()
        credit_b = (comp_b.get("unified_social_credit_code") or "").strip()

        findings = d2.get("findings") or []
        rejected_count = 0
        any_modified = False

        for f in findings:
            if not isinstance(f, dict):
                continue
            summary = (f.get("summary") or "") + (f.get("detail") or "")
            rejected = False

            if any(kw in summary for kw in _D2_LEGAL_REP_KEYWORDS):
                if not (lr_a and lr_b):
                    rejected = True
                    f["rejection_reason"] = (
                        "算法校验: 声称法定代表人相同, 但至少一侧结构化字段缺失, "
                        "不能把单侧字段当成成对证据"
                    )
                elif lr_a != lr_b:
                    rejected = True
                    f["rejection_reason"] = (
                        f"算法校验: LLM声称法定代表人相同, "
                        f"但实际为 '{lr_a}' vs '{lr_b}'"
                    )
            elif any(kw in summary for kw in _D2_ADDRESS_KEYWORDS):
                if not (addr_a and addr_b):
                    rejected = True
                    f["rejection_reason"] = (
                        "算法校验: 声称注册地址相同, 但至少一侧结构化字段缺失, "
                        "不能把单侧字段当成成对证据"
                    )
                elif addr_a != addr_b:
                    if not is_same_address_building(addr_a, addr_b):
                        rejected = True
                        f["rejection_reason"] = "算法校验: 注册地址不一致"
            elif any(kw in summary for kw in _D2_PHONE_KEYWORDS):
                if not (phone_a and phone_b):
                    rejected = True
                    f["rejection_reason"] = (
                        "算法校验: 声称联系电话相同, 但至少一侧结构化字段缺失, "
                        "不能把单侧字段当成成对证据"
                    )
                elif phone_a != phone_b:
                    if not is_nearby_phone(
                        comp_a.get("contact_phone"),
                        comp_b.get("contact_phone"),
                    ):
                        rejected = True
                        f["rejection_reason"] = "算法校验: 联系电话不一致"
            elif any(kw in summary for kw in _D2_CREDIT_KEYWORDS):
                if not (credit_a and credit_b):
                    rejected = True
                    f["rejection_reason"] = (
                        "算法校验: 声称信用代码相同, 但至少一侧结构化字段缺失, "
                        "不能把单侧字段当成成对证据"
                    )
                elif credit_a != credit_b:
                    if not is_credit_code_related(credit_a, credit_b):
                        rejected = True
                        f["rejection_reason"] = "算法校验: 统一社会信用代码不一致"

            if rejected:
                f["verified"] = False
                rejected_count += 1
                any_modified = True
            else:
                f.setdefault("verified", True)

        patch: dict[str, Any] | None = None
        total_findings = [f for f in findings if isinstance(f, dict)]
        if rejected_count > 0 and rejected_count == len(total_findings):
            d2["score"] = 0.0
            d2["certainty"] = 0.0
            d2["gate_rejected"] = True
            patch = {"d2_company.gate_rejected": True}

        verdict = "enforce_patch" if any_modified else "pass"
        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=f"D2 company: rejected={rejected_count}/{len(total_findings)}",
            suggested_patch=patch,
            confidence=1.0,
        )
