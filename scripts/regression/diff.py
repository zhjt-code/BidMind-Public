"""字段级 diff 计算。

比较 baseline 与 candidate 产出的结构化结果，产出逐字段差异列表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldDiff:
    sample_id: str
    field_name: str
    baseline_value: Any
    candidate_value: Any
    severity: str = "info"


@dataclass
class DiffSummary:
    total_samples: int = 0
    total_fields_compared: int = 0
    diff_count: int = 0
    diffs: list[FieldDiff] = field(default_factory=list)

    @property
    def is_zero_diff(self) -> bool:
        return self.diff_count == 0


def compute_field_diffs(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    key_field: str = "id",
) -> DiffSummary:
    """逐字段比较两份结果列表。

    Parameters
    ----------
    baseline : list[dict]
        基线（A 侧）结构化结果。
    candidate : list[dict]
        候选（B 侧）结构化结果。
    key_field : str
        用于匹配同一样本的主键字段名。

    Returns
    -------
    DiffSummary
        字段级差异汇总。
    """
    summary = DiffSummary()
    b_index = {str(r.get(key_field, i)): r for i, r in enumerate(baseline)}
    c_index = {str(r.get(key_field, i)): r for i, r in enumerate(candidate)}

    all_keys = sorted(set(b_index) | set(c_index))
    summary.total_samples = len(all_keys)

    for k in all_keys:
        b_rec = b_index.get(k, {})
        c_rec = c_index.get(k, {})
        all_fields = sorted(set(b_rec) | set(c_rec))
        for f in all_fields:
            summary.total_fields_compared += 1
            bv = b_rec.get(f)
            cv = c_rec.get(f)
            if bv != cv:
                severity = _classify_severity(f, bv, cv)
                summary.diffs.append(FieldDiff(
                    sample_id=k,
                    field_name=f,
                    baseline_value=bv,
                    candidate_value=cv,
                    severity=severity,
                ))
                summary.diff_count += 1

    return summary


_HIGH_SEVERITY_FIELDS = {
    "expected_compliance_level", "compliance_level", "is_compliant",
    "expected_overall_risk", "overall_risk",
    "expected_failure_state", "failure_state",
    "expected_is_compliant",
}

_MEDIUM_SEVERITY_FIELDS = {
    "score", "risk_level", "expected_risk_level",
    "expected_relevance_score", "relevance_score",
}


def _classify_severity(field_name: str, bv: Any, cv: Any) -> str:
    if field_name in _HIGH_SEVERITY_FIELDS:
        return "high"
    if field_name in _MEDIUM_SEVERITY_FIELDS:
        return "medium"
    return "low"
