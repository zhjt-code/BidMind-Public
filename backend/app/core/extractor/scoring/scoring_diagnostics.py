"""评分结构化诊断。

职责: 表达评分事实缺口、scope冲突和守恒异常的结构化诊断。
不做: 不选择LLM上下文、不调用LLM、不修改评分事实。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.extractor.scoring.scoring_facts import ScoringEvidenceRef, ScoringScopeKey

DiagnosticKind = Literal["complete", "evidence_gap", "scope_conflict", "transformation_loss"]


class ScoringDiagnostic(BaseModel):
    """单条评分诊断。"""

    code: str
    kind: DiagnosticKind
    scope: ScoringScopeKey
    evidence_refs: list[ScoringEvidenceRef] = Field(default_factory=list)
    message: str
    expected: Any | None = None
    actual: Any | None = None


__all__ = ["ScoringDiagnostic"]
