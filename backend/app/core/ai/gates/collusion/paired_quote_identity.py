"""
职责: 拦截身份维度上「双侧引文指纹撞车且未锚定到本侧主体页」的 finding。
不做: LLM 调用、抽取、D3/D4 文本重合判定、页码-引文模糊匹配。
"""

from __future__ import annotations

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.collusion.paired_evidence_identity import (
    IDENTITY_DIM_KEYS,
    mark_quote_collision,
    page_num_from_ref,
    quotes_collide,
    retract_dim_if_all_unverified,
    side_has_subject,
    side_subject_tokens,
)

_METADATA_PAGE_MARKERS = ("pdf元数据", "元数据")


class PairedQuoteIdentityGate(Gate):
    """D1/D2 成对引文必须落在本侧主体页，禁止一份原文贴成两家证据。"""

    gate_name = "PairedQuoteIdentityGate"
    flow = "collusion"
    stage = "post_llm"
    order = 51
    default_severity = "enforce"
    description = "成对身份引文分侧主体锚定"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "collusion" or ctx.stage != "post_llm":
            return False
        target = ctx.target
        if not isinstance(target, dict):
            return False
        return any(
            isinstance(target.get(key), dict) and (target[key].get("score") or 0) > 0
            for key in IDENTITY_DIM_KEYS
        )

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        evidence = ctx.evidence or {}
        pair_ref = (target or {}).get("pair_ref") or {}
        chunk_texts: dict[int, list[tuple[int | None, str]]] = evidence.get(
            "chunk_texts"
        ) or {}
        doc_a_id = evidence.get("doc_a_id", 0)
        doc_b_id = evidence.get("doc_b_id", 0)
        info_a = evidence.get("info_a") or {}
        info_b = evidence.get("info_b") or {}
        tokens_a = side_subject_tokens(
            (info_a.get("company_info") or {}).get("company_name")
            if isinstance(info_a.get("company_info"), dict) else None,
            evidence.get("doc_a_name") or pair_ref.get("doc_a_name"),
        )
        tokens_b = side_subject_tokens(
            (info_b.get("company_info") or {}).get("company_name")
            if isinstance(info_b.get("company_info"), dict) else None,
            evidence.get("doc_b_name") or pair_ref.get("doc_b_name"),
        )
        chunks_a = chunk_texts.get(doc_a_id) or []
        chunks_b = chunk_texts.get(doc_b_id) or []

        rejected = 0
        for dim_key in IDENTITY_DIM_KEYS:
            dim = target.get(dim_key)
            if not isinstance(dim, dict):
                continue
            for finding in dim.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                if finding.get("verified") is False:
                    continue
                if not quotes_collide(finding.get("quote_a"), finding.get("quote_b")):
                    continue
                if _is_metadata_page(finding.get("page_a")) and _is_metadata_page(
                    finding.get("page_b")
                ):
                    continue
                ok_a = side_has_subject(
                    chunks_a, page_num_from_ref(finding.get("page_a")), tokens_a,
                )
                ok_b = side_has_subject(
                    chunks_b, page_num_from_ref(finding.get("page_b")), tokens_b,
                )
                if ok_a and ok_b:
                    continue
                mark_quote_collision(
                    finding,
                    "成对引文指纹撞车且未能分别锚定到两侧文档主体页，"
                    "疑似把一份原文复用为两家证据",
                )
                rejected += 1
            retract_dim_if_all_unverified(dim)

        return GateVerdict(
            gate_name=self.gate_name,
            verdict="enforce_patch" if rejected else "pass",
            severity=self.default_severity,
            reason=f"paired_quote_identity: rejected={rejected}",
            confidence=1.0,
            trace={"rejected_count": rejected},
        )


def _is_metadata_page(page_ref: object) -> bool:
    text = str(page_ref or "").strip().lower()
    return any(marker in text for marker in _METADATA_PAGE_MARKERS)
