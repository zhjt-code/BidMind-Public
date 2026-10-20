"""DeAIFilterGate — AI 套话密度检测（B5.4 新建，替代 B2.5 metrics-only）。

stage=post_llm, default_severity=advisory, order=30。
仅对 free_writing 章节触发。阈值默认 10（基于 B2.5 指标 P90 估算）。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

DEFAULT_AI_PHRASE_THRESHOLD = 10

_AI_PHRASES = [
    "综上所述", "值得注意的是", "毋庸置疑", "不言而喻",
    "众所周知", "总而言之", "一言以蔽之", "由此可见",
    "不难看出", "显而易见", "至关重要", "应运而生",
    "与时俱进", "高度重视", "高质量发展", "赋能",
]
_AI_PATTERN = re.compile("|".join(re.escape(p) for p in _AI_PHRASES))


class DeAIFilterGate(Gate):
    """单章级：free_writing 章节 AI 套话密度。"""

    gate_name = "DeAIFilterGate"
    flow = "writer"
    stage = "post_llm"
    order = 30
    default_severity = "advisory"
    description = "检测 free_writing 章节 AI 套话密度（B5.4 真正接入 Gate）"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "writer" or ctx.stage != "post_llm":
            return False
        evidence = ctx.evidence or {}
        node_type = evidence.get("node_type", "")
        return node_type == "free_writing"

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        content = getattr(target, "content", None) or ""
        threshold = (ctx.config or {}).get(
            "ai_phrase_threshold", DEFAULT_AI_PHRASE_THRESHOLD,
        )

        count = len(_AI_PATTERN.findall(content))

        if count <= threshold:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason=f"ai_phrase_count={count} <= {threshold}",
                trace={"ai_phrase_count": count},
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="warn",
            severity=self.default_severity,
            reason=f"ai_phrase_count={count} > {threshold}",
            suggested_patch={"ai_phrase_density_violated": True, "ai_phrase_count": count},
            trace={"ai_phrase_count": count},
        )
