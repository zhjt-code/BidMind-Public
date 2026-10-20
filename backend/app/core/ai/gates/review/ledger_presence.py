"""
职责: 材料级账本原子与「未发现」意见冲突时发出检测信号
不做: 提升符合度、改分数、句内改写否认词、自持 patch、处理禁止项
"""

from __future__ import annotations

from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
from app.core.ai.gates.review.claim_response_consistency import _comment_has_denial
from app.core.ai.reviewer_parts.existence_finalizer import (
    REBUTTAL_MARKERS,
    collect_existence_conflicts,
)
from app.core.retriever.evidence_coverage import build_evidence_facets
from app.core.review_contracts import parse_json_list


def _material_atoms(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    atoms = evidence.get("ledger_atoms") or ()
    return [
        atom for atom in atoms
        if isinstance(atom, dict) and atom.get("grade") == "material" and (
            atom.get("kind") or atom.get("canonical_material_key")
        )
    ]


class LedgerPresenceGate(Gate):
    """材料级账本命中且意见否认同一对象时，只发检测信号，不改判。"""

    gate_name = "LedgerPresenceGate"
    flow = "investigate"
    stage = "post_llm"
    order = 41
    default_severity = "advisory"
    description = "项目证据账本材料级原子与未发现主张冲突"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "investigate" or ctx.stage != "post_llm":
            return False
        comment = getattr(ctx.target, "comment", "") or ""
        if not _comment_has_denial(comment) and "未发现" not in comment:
            return False
        return bool(_material_atoms(ctx.evidence or {})) or bool(
            (ctx.evidence or {}).get("facet_states")
        )

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        comment = getattr(ctx.target, "comment", "") or ""
        evidence = ctx.evidence or {}
        atoms = _material_atoms(evidence)
        requirement_text = str(evidence.get("requirement_text") or "")
        facets = build_evidence_facets(
            requirement_text,
            parse_json_list(evidence.get("sub_requirements")),
            keywords=parse_json_list(evidence.get("keywords")),
        ) if requirement_text else ()
        conflicts = collect_existence_conflicts(
            comment,
            atoms,
            facet_states=list(evidence.get("facet_states") or ()),
            facets=facets,
        )
        if not conflicts:
            return GateVerdict(
                gate_name=self.gate_name, verdict="pass",
                severity=self.default_severity,
                reason="no material ledger conflict",
            )
        return GateVerdict(
            gate_name=self.gate_name,
            verdict="warn",
            severity=self.default_severity,
            reason="ledger material atom conflicts with not-found claim",
            suggested_patch=None,
            confidence=0.9,
            trace={
                "conflict_kinds": [
                    str(atom.get("kind") or atom.get("canonical_material_key") or "")
                    for atom in conflicts[:5]
                ],
                "pages": [
                    atom.get("page", atom.get("physical_page"))
                    for atom in conflicts[:5]
                ],
            },
        )
