"""PlaceholderDensityGate — 占位符密度检测（B5.3 新建）。

stage=post_pipeline, default_severity=advisory, order=50。
阈值：每千字 5 个 [待补充:xxx]（Plan §5.7 红线基线 2-3，余量 150%）。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

DEFAULT_THRESHOLD_PER_1K = 5
_PLACEHOLDER_PATTERN = re.compile(r"\[待补充[:：].*?\]")


class PlaceholderDensityGate(Gate):
    """全局：全文 [待补充:xxx] 密度是否超阈值。"""

    gate_name = "PlaceholderDensityGate"
    flow = "writer"
    stage = "post_pipeline"
    order = 50
    default_severity = "advisory"
    description = "全文占位符 [待补充:xxx] 密度检查"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "writer" or ctx.stage != "post_pipeline":
            return False
        evidence = ctx.evidence or {}
        return bool(evidence.get("drafts"))

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        drafts: dict[int, Any] = evidence.get("drafts", {})
        threshold = (ctx.config or {}).get(
            "placeholder_threshold_per_1k", DEFAULT_THRESHOLD_PER_1K,
        )

        total_chars, total_placeholders, per_section = _count_placeholders(drafts)

        density = (total_placeholders / max(total_chars, 1)) * 1000
        violated = density > threshold

        if not violated:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason=f"placeholder density {density:.2f}/1k <= {threshold}",
                trace={
                    "total_chars": total_chars,
                    "total_placeholders": total_placeholders,
                    "density_per_1k": round(density, 2),
                },
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="warn",
            severity=self.default_severity,
            reason=f"placeholder density {density:.2f}/1k > {threshold}",
            suggested_patch={
                "placeholder_density_violated": True,
                "density_per_1k": round(density, 2),
            },
            trace={
                "total_chars": total_chars,
                "total_placeholders": total_placeholders,
                "density_per_1k": round(density, 2),
                "per_section": per_section,
            },
        )


def _count_placeholders(
    drafts: dict[int, Any],
) -> tuple[int, int, list[dict[str, Any]]]:
    total_chars = 0
    total_count = 0
    per_section: list[dict[str, Any]] = []

    for node_id, d in drafts.items():
        content = getattr(d, "content", None) or ""
        chars = len(content)
        count = len(_PLACEHOLDER_PATTERN.findall(content))
        total_chars += chars
        total_count += count
        if count > 0:
            per_section.append({"node_id": node_id, "count": count, "chars": chars})

    return total_chars, total_count, per_section
