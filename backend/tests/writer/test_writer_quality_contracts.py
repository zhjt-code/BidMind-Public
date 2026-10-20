import pytest
from pydantic import ValidationError


def test_writer_quality_snapshot_rejects_unknown_fields() -> None:
    from app.core.writer.quality.contracts import (
        SignalAvailability,
        WriterQualitySnapshotV1,
    )

    with pytest.raises(ValidationError):
        WriterQualitySnapshotV1(
            schema_version="writer_quality_snapshot.v1",
            sample_set_digest="a" * 64,
            project_count=1,
            section_count=2,
            placeholder_count=0,
            high_issue_count=0,
            fixed_template_high_issue_count=0,
            drafts_without_material_sources=0,
            failed_drafts=0,
            needs_review_drafts=0,
            gate_signal_availability=SignalAvailability.unavailable,
            judge_signal_availability=SignalAvailability.unavailable,
            cove_signal_availability=SignalAvailability.partial_nonpass_only,
            raw_text="禁止出现原文",
        )


def test_writer_quality_snapshot_digest_is_stable_for_canonical_payload() -> None:
    from app.core.writer.quality.contracts import (
        SignalAvailability,
        WriterQualitySnapshotV1,
    )

    snapshot = WriterQualitySnapshotV1(
        schema_version="writer_quality_snapshot.v1",
        sample_set_digest="b" * 64,
        project_count=1,
        section_count=2,
        placeholder_count=3,
        high_issue_count=1,
        fixed_template_high_issue_count=0,
        drafts_without_material_sources=1,
        failed_drafts=0,
        needs_review_drafts=1,
        gate_signal_availability=SignalAvailability.partial_nonpass_only,
        judge_signal_availability=SignalAvailability.unavailable,
        cove_signal_availability=SignalAvailability.partial_nonpass_only,
    )

    assert snapshot.canonical_digest() == snapshot.canonical_digest()
    assert len(snapshot.canonical_digest()) == 64
