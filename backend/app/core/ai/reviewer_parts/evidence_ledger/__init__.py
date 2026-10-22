"""
职责: 项目级证据账本（事实原子契约 / 构建 / 材料级判定）
不做: 检索执行、LLM 调用、符合性打分、ORM 持久化账本表
"""

from app.core.ai.reviewer_parts.evidence_ledger.builder import (
    build_project_evidence_ledger,
    collect_atoms_from_rows,
)
from app.core.ai.reviewer_parts.evidence_ledger.contracts import (
    EvidenceAtom,
    EvidenceLedger,
)
from app.core.ai.reviewer_parts.evidence_ledger.grading import grade_atom

__all__ = [
    "EvidenceAtom",
    "EvidenceLedger",
    "build_project_evidence_ledger",
    "collect_atoms_from_rows",
    "grade_atom",
]
