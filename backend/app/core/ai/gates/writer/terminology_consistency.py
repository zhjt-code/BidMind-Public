"""TerminologyConsistencyGate — 术语一致性（B5.1 迁移自 consistency_checker._check_terminology）。

stage=post_pipeline, default_severity=advisory, order=40。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict


_COMMON_VARIANTS: list[tuple[list[str], str]] = [
    (["甲方", "采购人", "招标人", "业主"], "招标方称谓"),
    (["乙方", "投标人", "供应商", "承包商", "中标人"], "投标方称谓"),
]


class TerminologyConsistencyGate(Gate):
    """全局：同一实体在全文中名称是否统一。"""

    gate_name = "TerminologyConsistencyGate"
    flow = "writer"
    stage = "post_pipeline"
    order = 40
    default_severity = "advisory"
    description = "检查全文术语称谓是否统一"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "writer" or ctx.stage != "post_pipeline":
            return False
        evidence = ctx.evidence or {}
        return bool(evidence.get("drafts"))

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        drafts: dict[int, Any] = evidence.get("drafts", {})

        full_text = " ".join(
            (getattr(d, "content", None) or "") for d in drafts.values()
        )

        issues = _check_terminology(full_text)

        if not issues:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="terminology consistent",
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="warn",
            severity=self.default_severity,
            reason=f"{len(issues)} terminology inconsistencies",
            suggested_patch={"terminology_issues": issues},
        )


def _check_terminology(full_text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for variants, category in _COMMON_VARIANTS:
        found = [v for v in variants if v in full_text]
        if len(found) > 1:
            issues.append({
                "type": "terminology_inconsistency",
                "severity": "low",
                "node_id": None,
                "message": f"{category}不统一: 同时使用了 {', '.join(found)}",
                "detail": "建议全文统一使用同一称谓",
            })
    return issues
