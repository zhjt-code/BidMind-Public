"""PageAnchorVerifyGate — source_pages 与 search_results 页码交叉校验（B3.6）。

迁移自 bid_extractor.py ``_source_pages_cross_verify``。
Gate 通过 ctx.evidence 接收预提取的 chunk_pages 集合。

B3 升级（与 collusion FindingPageQuoteVerifyGate 同源治理）：
- 当 source_pages 中存在 unverified 页码时，除了截断 source_pages 列表外，
  在 comment 末尾追加显式警示，让用户在 UI 上能看到"原文位置未通过自动核验"。
- 该升级是最小化扩展：不引入 quote 字段（reviewer 流当前 LLM 输出无 quote），
  只把"页码超出 chunk 集合"这一已能识别的失配信号显式呈现给用户。
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict

_UNVERIFIED_COMMENT_SUFFIX = (
    "\n\n（原文位置未通过自动核验：以下页码未出现在检索到的投标 chunk 中："
    "{pages}。建议人工核对页码或在投标文件中重新检索关键词。）"
)


class PageAnchorVerifyGate(Gate):
    """source_pages 在检索 chunk 页码集合中的锚点校验。"""

    gate_name = "PageAnchorVerifyGate"
    flow = "investigate"
    stage = "post_llm"
    order = 10
    default_severity = "enforce"
    description = "source_pages 与 chunk 页码交叉校验"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "post_llm":
            return False
        evidence = ctx.evidence or {}
        return "chunk_pages" in evidence and "source_pages" in evidence

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        evidence = ctx.evidence or {}
        chunk_pages: set[int] = evidence["chunk_pages"]
        source_pages: list[int] | None = evidence["source_pages"]

        if not source_pages:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason="no source_pages to verify",
            )

        if not chunk_pages:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason="no chunk_pages reference set",
            )

        verified = [p for p in source_pages if p in chunk_pages]
        unverified = [p for p in source_pages if p not in chunk_pages]

        if not unverified:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason=f"all {len(verified)} pages verified",
            )

        final_pages = verified if verified else sorted(chunk_pages)[:10]
        # B3: 把警示挂到 comment 末尾（_comment_suffix 由 reviewer apply patch 时拼接）
        suffix = _UNVERIFIED_COMMENT_SUFFIX.format(
            pages=",".join(str(p) for p in unverified)
        )
        patch: dict[str, Any] = {
            "source_pages_verified": final_pages,
            "source_pages_unverified": unverified,
            "_comment_suffix": suffix,
        }

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch",
            severity=self.default_severity,
            reason=f"unverified pages {unverified}, keeping {final_pages}",
            suggested_patch=patch,
            confidence=1.0,
        )
