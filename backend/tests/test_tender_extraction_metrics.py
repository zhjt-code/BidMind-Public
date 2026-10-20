from app.core.extractor.tender_extractor_parts.performance_metrics import (
    TenderExtractionMetrics,
)


def test_tender_extraction_metrics_records_chunk_and_cache_counts():
    metrics = TenderExtractionMetrics()

    metrics.set_total_chunks(3)
    metrics.record_llm_call()
    metrics.record_chunk_result(item_count=2)
    metrics.record_cache_hit(item_count=1)
    metrics.record_chunk_result(item_count=0)
    metrics.record_chunk_failure()
    metrics.record_premerge(before_count=4, after_count=3)
    metrics.record_merge_result(merged_count=2)

    meta = metrics.to_heartbeat_meta()

    assert meta["total_chunks"] == 3
    assert meta["llm_calls"] == 1
    assert meta["cache_hits"] == 1
    assert meta["empty_chunks"] == 1
    assert meta["failed_chunks"] == 1
    assert meta["candidate_requirements"] == 3
    assert meta["premerge_before"] == 4
    assert meta["premerge_after"] == 3
    assert meta["merged_requirements"] == 2


def test_tender_extraction_metrics_stage_timings_are_reported():
    metrics = TenderExtractionMetrics()

    metrics.mark_stage_start("extracting")
    metrics.mark_stage_end("extracting")

    meta = metrics.to_heartbeat_meta()

    assert "extracting_ms" in meta["stage_durations_ms"]
    assert meta["stage_durations_ms"]["extracting_ms"] >= 0
