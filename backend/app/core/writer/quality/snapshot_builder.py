"""Writer quality snapshot builder.

职责: 将内存中的写标质量信号聚合为无原文快照。
不做: 不访问数据库、不调用 LLM、不重新计算一致性检查。
"""

from __future__ import annotations

from typing import Any

from app.core.writer.post_run_quality_audit import build_post_run_quality_audit
from app.core.writer.quality.contracts import (
    SignalAvailability,
    WriterQualitySnapshotV1,
    WriterQualitySource,
)


def _has_gate_flags(draft: Any) -> bool:
    flags = getattr(draft, "gate_flags", None)
    return isinstance(flags, dict) and bool(flags)


def _derive_gate_signal_availability(drafts: list[Any]) -> SignalAvailability:
    if not drafts:
        return SignalAvailability.unavailable
    if any(_has_gate_flags(draft) for draft in drafts):
        # Historical drafts only preserve non-pass compressed gate flags.
        return SignalAvailability.partial_nonpass_only
    return SignalAvailability.unavailable


def build_writer_quality_snapshot(
    source: WriterQualitySource,
) -> WriterQualitySnapshotV1:
    """Build a no-source-text writer quality snapshot from loaded objects."""

    nodes = list(source.nodes)
    drafts = list(source.drafts)
    consistency_issues = list(source.consistency_issues)
    bindings = list(source.bindings)
    audit = build_post_run_quality_audit(
        nodes=nodes,
        drafts=drafts,
        consistency_issues=consistency_issues,
        bindings=bindings,
        industry_profile_clause_ids=list(source.industry_profile_clause_ids),
    )
    return WriterQualitySnapshotV1(
        sample_set_digest=source.sample_set_digest,
        project_count=len(set(source.project_ids)),
        section_count=audit["current_drafts"],
        placeholder_count=audit["placeholder_count"],
        high_issue_count=audit["high_issue_count"],
        fixed_template_high_issue_count=audit["fixed_template_high_issue_count"],
        drafts_without_material_sources=audit["drafts_without_material_sources"],
        failed_drafts=audit["failed_drafts"],
        needs_review_drafts=audit["needs_review_drafts"],
        gate_signal_availability=_derive_gate_signal_availability(drafts),
        judge_signal_availability=source.judge_signal_availability,
        cove_signal_availability=source.cove_signal_availability,
    )


__all__ = ["build_writer_quality_snapshot"]
