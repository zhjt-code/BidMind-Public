"""RequiredSectionCompletenessGate — 必填章节完整性检测（B5.1 迁移自 consistency_checker._check_completeness）。

stage=post_pipeline, default_severity=enforce, order=20。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

_MIN_CONTENT_LENGTH = 20


class RequiredSectionCompletenessGate(Gate):
    """全局：必填叶子节点是否都有有效内容。"""

    gate_name = "RequiredSectionCompletenessGate"
    flow = "writer"
    stage = "post_pipeline"
    order = 20
    default_severity = "enforce"
    description = "检查必填章节是否都有内容"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "writer" or ctx.stage != "post_pipeline":
            return False
        evidence = ctx.evidence or {}
        return bool(evidence.get("outline_nodes"))

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        drafts: dict[int, Any] = evidence.get("drafts", {})
        nodes: list[Any] = evidence.get("outline_nodes", [])

        issues = _check_completeness(drafts, nodes)

        if not issues:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="all required sections have content",
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason=f"{len(issues)} required sections missing content",
            suggested_patch={"missing_content_issues": issues},
            trace={"issue_count": len(issues)},
        )


def _check_completeness(
    drafts: dict[int, Any],
    nodes: list[Any],
) -> list[dict[str, Any]]:
    """纯函数：返回必填却缺失内容的章节清单。"""
    parent_ids = {
        getattr(n, "parent_id", None)
        for n in nodes
        if getattr(n, "parent_id", None) is not None
    }
    leaves = [n for n in nodes if getattr(n, "id", None) not in parent_ids]

    issues: list[dict[str, Any]] = []
    for node in leaves:
        if getattr(node, "is_required", 0) != 1:
            continue
        node_id = getattr(node, "id", None)
        draft = drafts.get(node_id)
        content = getattr(draft, "content", None) or "" if draft else ""
        if len(content.strip()) < _MIN_CONTENT_LENGTH:
            title = getattr(node, "title", "")
            issues.append({
                "type": "missing_content",
                "severity": "high",
                "node_id": node_id,
                "message": f"必填章节'{title}'缺少内容",
            })
    return issues
