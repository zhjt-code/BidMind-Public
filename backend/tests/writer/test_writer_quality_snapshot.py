from types import SimpleNamespace


def test_build_writer_quality_snapshot_counts_delivery_risks() -> None:
    from app.core.writer.quality.contracts import SignalAvailability, WriterQualitySource
    from app.core.writer.quality.snapshot_builder import build_writer_quality_snapshot

    source = WriterQualitySource(
        sample_set_digest="c" * 64,
        project_ids=(101,),
        nodes=[
            SimpleNamespace(id=1, parent_id=None),
            SimpleNamespace(id=2, parent_id=1),
        ],
        drafts=[
            SimpleNamespace(
                outline_node_id=2,
                content="<p>[待补充:编号]</p>",
                source_material_ids="[]",
                source_clause_ids="[9]",
                failure_state="needs_review",
                review_required=1,
                gate_flags={"DeAIFilterGate": {"verdict": "warn"}},
            )
        ],
        consistency_issues=[
            {"type": "scoring_response_missing", "severity": "high"},
            {"type": "fixed_template_placeholder", "severity": "high"},
        ],
        bindings=[],
        industry_profile_clause_ids=[],
        judge_signal_availability=SignalAvailability.unavailable,
        cove_signal_availability=SignalAvailability.partial_nonpass_only,
    )

    snapshot = build_writer_quality_snapshot(source)

    assert snapshot.project_count == 1
    assert snapshot.section_count == 1
    assert snapshot.placeholder_count == 1
    assert snapshot.high_issue_count == 2
    assert snapshot.fixed_template_high_issue_count == 1
    assert snapshot.drafts_without_material_sources == 1
    assert snapshot.needs_review_drafts == 1
    assert snapshot.failed_drafts == 0
    assert snapshot.gate_signal_availability == SignalAvailability.partial_nonpass_only
    assert snapshot.judge_signal_availability == SignalAvailability.unavailable


def test_build_writer_quality_snapshot_handles_zero_denominators() -> None:
    from app.core.writer.quality.contracts import SignalAvailability, WriterQualitySource
    from app.core.writer.quality.snapshot_builder import build_writer_quality_snapshot

    snapshot = build_writer_quality_snapshot(
        WriterQualitySource(
            sample_set_digest="d" * 64,
            project_ids=(),
            nodes=[],
            drafts=[],
            consistency_issues=[],
            bindings=[],
            industry_profile_clause_ids=[],
            judge_signal_availability=SignalAvailability.unavailable,
            cove_signal_availability=SignalAvailability.unavailable,
        )
    )

    assert snapshot.project_count == 0
    assert snapshot.section_count == 0
    assert snapshot.gate_signal_availability == SignalAvailability.unavailable
