from types import SimpleNamespace

import pytest


def test_quality_export_service_builds_anonymous_corpus_from_sources() -> None:
    from app.core.writer.quality.contracts import SignalAvailability, WriterQualitySource
    from app.services.writing.quality_export_service import WriterQualityExportService

    service = WriterQualityExportService(db=None)
    corpus = service.build_anonymous_corpus_from_sources(
        [
            WriterQualitySource(
                sample_set_digest="e" * 64,
                project_ids=(1,),
                nodes=[SimpleNamespace(id=1, parent_id=None)],
                drafts=[],
                consistency_issues=[],
                bindings=[],
                industry_profile_clause_ids=[],
                judge_signal_availability=SignalAvailability.unavailable,
                cove_signal_availability=SignalAvailability.unavailable,
            )
        ],
        secret=b"x" * 32,
    )

    assert corpus.sample_set_digest == "e" * 64
    assert len(corpus.snapshots) == 1
    assert corpus.blind_review_items == []


def test_quality_export_service_rejects_empty_sources() -> None:
    from app.services.writing.quality_export_service import WriterQualityExportService

    service = WriterQualityExportService(db=None)

    with pytest.raises(ValueError, match="no writer quality sources"):
        service.build_anonymous_corpus_from_sources([], secret=b"x" * 32)
