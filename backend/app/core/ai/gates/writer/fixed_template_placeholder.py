"""FixedTemplatePlaceholderGate — 固定模板篡改检测（B5.1 迁移自 consistency_checker._check_fixed_wording）。

stage=post_llm, default_severity=enforce, order=10。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict


class FixedTemplatePlaceholderGate(Gate):
    """单章级：fixed_template 内容是否被篡改或存在未填充占位符。"""

    gate_name = "FixedTemplatePlaceholderGate"
    flow = "writer"
    stage = "post_llm"
    order = 10
    default_severity = "enforce"
    description = "检查 fixed_template 章节是否被篡改或包含未填充占位符"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "writer" or ctx.stage != "post_llm":
            return False
        target = ctx.target
        node_type = _get_node_type(target, ctx.evidence)
        return node_type == "fixed_template"

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        content = getattr(target, "content", None) or ""

        issues = _check_fixed_template(content)

        if not issues:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="fixed_template content intact",
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason=f"{len(issues)} fixed_template issues detected",
            suggested_patch={"fixed_template_issues": issues},
            trace={"issue_count": len(issues)},
        )


def _get_node_type(target: Any, evidence: dict[str, Any] | None) -> str:
    """从 target 或 evidence 提取 node_type 字面量。"""
    nt = getattr(target, "node_type", None)
    if nt is not None:
        return str(nt.value) if hasattr(nt, "value") else str(nt)
    if evidence:
        return evidence.get("node_type", "")
    return ""


def _check_fixed_template(content: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if "[待补充" in content:
        issues.append({
            "type": "fixed_template_incomplete",
            "severity": "medium",
            "message": "固定模板中存在未填充的占位符",
        })
    return issues
