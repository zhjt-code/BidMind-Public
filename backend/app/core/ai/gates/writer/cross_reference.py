"""CrossReferenceGate — 交叉引用一致性（B5.1 迁移 + B5.8 rapidfuzz 前置优化）。

stage=post_pipeline, default_severity=enforce, order=30。

B5.8 扩展：
- ratio >= 90 直接通过
- ratio <= 60 直接拒绝
- 中间段才调 LLM
当前版本先做纯算法 fuzz 分档，LLM fallback 通过 evidence["llm_cross_ref_results"] 注入。
"""

from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

FUZZ_PASS_THRESHOLD = 90
FUZZ_REJECT_THRESHOLD = 60


class CrossReferenceGate(Gate):
    """全局：交叉引用（"详见第X章"）与大纲标题的一致性。"""

    gate_name = "CrossReferenceGate"
    flow = "writer"
    stage = "post_pipeline"
    order = 30
    default_severity = "enforce"
    description = "检查交叉引用是否指向有效章节标题（B5.8 rapidfuzz 前置）"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "writer" or ctx.stage != "post_pipeline":
            return False
        evidence = ctx.evidence or {}
        return bool(evidence.get("drafts"))

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        drafts: dict[int, Any] = evidence.get("drafts", {})
        nodes: list[Any] = evidence.get("outline_nodes", [])
        llm_results: list[dict] | None = evidence.get("llm_cross_ref_results")

        fragments = _extract_cross_ref_fragments(drafts)

        if not fragments:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason="no cross-references found",
            )

        titles = [getattr(n, "title", "") for n in nodes]

        algo_issues, needs_llm = _fuzz_check(fragments, titles)

        if llm_results is not None:
            for item in llm_results:
                if isinstance(item, dict) and not item.get("is_correct", True):
                    algo_issues.append({
                        "type": "cross_reference_error",
                        "severity": "medium",
                        "node_id": None,
                        "message": f"交叉引用错误: '{item.get('fragment', '')}'",
                        "detail": item.get("suggestion", ""),
                    })

        if not algo_issues:
            return GateVerdict(
                gate_name=self.gate_name,
                verdict="pass",
                severity=self.default_severity,
                reason=f"all {len(fragments)} cross-refs validated",
                trace={"total_refs": len(fragments), "needs_llm": len(needs_llm)},
            )

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason=f"{len(algo_issues)} cross-reference issues",
            suggested_patch={"cross_reference_issues": algo_issues},
            trace={
                "total_refs": len(fragments),
                "algo_rejected": len(algo_issues),
                "needs_llm": len(needs_llm),
            },
        )


# P1.1: 扩展正则, 覆盖常见交叉引用表达 (与 consistency_checker._CROSS_REF_PATTERN 对齐)。
# 两个分支:
# a) 见/详见/参见/参照/参考/如上述/如前所述 + 内容 + 章/节/部分/条/附件/附录
# b) 附录 + 标识符 (A/B/技术要求等, 1-10 字), 自成引用无需后缀
_REF_PATTERN = re.compile(
    r"(?:(?:见|详见|参见|参照|参考|如上述|如前所述)\s*"
    r"[「「\"'']?(.{2,20}?)[」」\"'']?"
    r"(?:章|节|部分|条|附件|附录))"
    r"|"
    r"(?:附录\s*([A-Za-z0-9\u4e00-\u9fff]{1,10}))"
)


def _extract_cross_ref_fragments(drafts: dict[int, Any]) -> list[str]:
    fragments: list[str] = []
    for d in drafts.values():
        content = getattr(d, "content", None) or ""
        for match in _REF_PATTERN.findall(content):
            # 正则含两个捕获组, findall 返回 tuple; 取第一个非空项
            frag = (
                match if isinstance(match, str)
                else next((m for m in match if m), "")
            )
            frag = (frag or "").strip()
            if frag:
                fragments.append(frag)
    return list(set(fragments))


def _fuzz_check(
    fragments: list[str],
    titles: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """对每个引用片段做 fuzz 匹配分档。

    Returns:
        (algo_issues, needs_llm_fragments)
    """
    issues: list[dict[str, Any]] = []
    needs_llm: list[str] = []

    for frag in fragments:
        best_ratio = max(
            (fuzz.partial_ratio(frag, t) for t in titles),
            default=0,
        )
        if best_ratio >= FUZZ_PASS_THRESHOLD:
            continue
        if best_ratio <= FUZZ_REJECT_THRESHOLD:
            issues.append({
                "type": "cross_reference_error",
                "severity": "medium",
                "node_id": None,
                "message": f"交叉引用错误: '{frag}' 与现有章节标题不匹配 (best_ratio={best_ratio})",
                "detail": "",
            })
        else:
            needs_llm.append(frag)

    return issues, needs_llm
