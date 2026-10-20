"""
职责: 复核层的模型输出契约, 以及把模型常见变体收成该契约
不做: 不调用模型、不改写发现项
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

_ROW_KEYS = ("verdicts", "findings", "results", "candidates")
_TRUE_TEXT = frozenset({"true", "yes", "1", "是"})


class CandidateVerdict(BaseModel):
    """单条候选的真伪判断。"""

    candidate_id: str
    is_real_defect: bool
    confidence: float = Field(description="0 到 1，把握程度")
    reason: str = ""


class PageVerifyResult(BaseModel):
    """同一页一批候选的复核结果。"""

    verdicts: list[CandidateVerdict] = Field(default_factory=list)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_TEXT
    return bool(value)


def _rows_from_payload(payload: Any) -> list[Any]:
    """顶层对象、别名键、裸数组都收成行列表。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _ROW_KEYS:
            rows = payload.get(key)
            if isinstance(rows, list):
                return rows
            if isinstance(rows, dict):
                return [rows]
        if "candidate_id" in payload or "is_real_defect" in payload:
            return [payload]
    return []


def coerce_page_verify_result(payload: Any) -> PageVerifyResult:
    """把 verdicts/findings/裸数组收成 PageVerifyResult。无法识别时返回空列表。"""
    verdicts: list[CandidateVerdict] = []
    for row in _rows_from_payload(payload):
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or row.get("id") or "").strip()
        if not candidate_id:
            continue
        verdicts.append(CandidateVerdict(
            candidate_id=candidate_id,
            is_real_defect=_as_bool(row.get("is_real_defect")),
            confidence=float(row.get("confidence") or 0),
            reason=str(row.get("reason") or row.get("detail") or ""),
        ))
    return PageVerifyResult(verdicts=verdicts)
