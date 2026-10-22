"""DisqualificationRiskGate — 废标条款违反检测（P1.1: 接入共享 detector）。

签名按 docs/evidence_gate_design.md §4.3 定义。
stage=post_pipeline, default_severity=enforce, order=10。

P1.1 重写:
    原实现与 consistency_checker._check_disqualification 是同源伪算法 (复制粘贴),
    现统一调用 app.core.writer.disqualification_detector.find_disqualification_issues,
    保证两处同步演化, 消除高 false-positive 噪音。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.writer.disqualification_detector import find_disqualification_issues


class DisqualificationRiskGate(Gate):
    """全文废标条款违反检测。"""

    gate_name = "DisqualificationRiskGate"
    flow = "writer"
    stage = "post_pipeline"
    order = 10
    default_severity = "enforce"
    description = "全文中是否违反废标条款的否定约束"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "writer" or ctx.stage != "post_pipeline":
            return False
        evidence = ctx.evidence or {}
        return bool(evidence.get("hard_clauses"))

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        drafts: dict[int, Any] = evidence.get("drafts", {})
        hard_clauses: list[Any] = evidence.get("hard_clauses", [])

        full_text = " ".join(
            (getattr(d, "content", None) or "") for d in drafts.values()
        )

        issues = find_disqualification_issues(full_text, hard_clauses)

        if not issues:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="no disqualification risk detected",
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason=f"{len(issues)} disqualification risks detected",
            suggested_patch={"disqualification_issues": issues},
            trace={"issue_count": len(issues)},
        )
