"""评分指派内核子包——证据规范化与受限指派（P2~P5 阶段）。

职责: 提供 EvidenceAtom/Declaration/CandidateFact/Assignment/InvariantReport
      五个不可变契约类型与 make_physical_key helper；供各 producer 适配器与内核共享。
不做: 不执行提取/裁决逻辑，不访问 DB/LLM，不导入 app.api.*。
"""

from __future__ import annotations

from app.core.extractor.scoring.assignment.contracts import (
    Assignment,
    AssignmentStatus,
    CandidateFact,
    Declaration,
    EvidenceAtom,
    FactType,
    InvariantCheck,
    InvariantReport,
    SourceChannel,
    VALID_ASSIGNMENT_STATUSES,
    VALID_FACT_TYPES,
    make_physical_key,
)

__all__ = [
    "Assignment",
    "AssignmentStatus",
    "CandidateFact",
    "Declaration",
    "EvidenceAtom",
    "FactType",
    "InvariantCheck",
    "InvariantReport",
    "SourceChannel",
    "VALID_ASSIGNMENT_STATUSES",
    "VALID_FACT_TYPES",
    "make_physical_key",
]
