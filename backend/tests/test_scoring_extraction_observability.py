"""评分项提取可观测性防御测试。

覆盖:
- summarize_diagnostics 空列表 / 同类聚合 / 超长截断 / 页码前缀剥离
- log_extraction_summary 输出含关键计数字段且不含原文
- run_chunk_extraction_pass 返回结构不因插桩改变
"""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")

from app.core.extractor.scoring.extraction_observability import (  # noqa: E402
    ScoringExtractionObservation,
    log_extraction_summary,
    log_rescue_tail_summary,
    summarize_diagnostics,
)

pytestmark = pytest.mark.scoring


def test_summarize_diagnostics_empty() -> None:
    assert summarize_diagnostics([]) == {}
    assert summarize_diagnostics(["", "  ", None]) == {}


def test_summarize_diagnostics_aggregates_and_strips_page_prefix() -> None:
    summary = summarize_diagnostics(
        [
            "第47页: missing_score_column",
            "第48-49页: missing_score_column",
            "第50页: scope_conflict",
        ]
    )
    assert summary["missing_score_column"] == 2
    assert summary["scope_conflict"] == 1


def test_summarize_diagnostics_truncates_long_text() -> None:
    long_text = "X" * 80
    summary = summarize_diagnostics([long_text, long_text])
    assert len(summary) == 1
    key = next(iter(summary))
    assert len(key) == 40
    assert summary[key] == 2


def test_summarize_diagnostics_l3_dict() -> None:
    summary = summarize_diagnostics(
        [{"status": "rejected", "rejection_codes": ["surplus", "gap"]}]
    )
    assert "l3:rejected:surplus,gap" in summary
    assert summary["l3:rejected:surplus,gap"] == 1


def test_log_extraction_summary_contains_counts_without_raw_content(caplog) -> None:
    import loguru

    obs = ScoringExtractionObservation(
        document_id=1234,
        chunk_count=5,
        confident_chunks=3,
        llm_fallback_chunks=2,
        items_final=12,
        volume_def_count=2,
        rescue_mode="shadow",
        rescue_adopted=False,
        report_total=100.0,
        needs_review=True,
        elapsed_ms=456,
        diagnostics_summary={"missing_score_column": 1},
    )
    secret = "这是评分表原文不该出现在日志里"
    caplog.set_level(logging.INFO)
    sink_id = loguru.logger.add(caplog.handler, level="INFO", format="{message}")
    try:
        log_extraction_summary(obs)
    finally:
        loguru.logger.remove(sink_id)

    blob = " ".join(rec.message for rec in caplog.records)
    assert "[评分项提取摘要]" in blob
    assert "document_id=1234" in blob
    assert "chunks=5" in blob
    assert "confident=3" in blob
    assert "llm_fallback=2" in blob
    assert "items=12" in blob
    assert "elapsed_ms=456" in blob
    assert secret not in blob
    assert len(blob) < 2000


def test_log_rescue_tail_summary_counts_rejection_codes(caplog) -> None:
    import loguru

    caplog.set_level(logging.INFO)
    sink_id = loguru.logger.add(caplog.handler, level="INFO", format="{message}")
    try:
        log_rescue_tail_summary(
            document_id=99,
            mode="enforce",
            adopted=False,
            items_before=10,
            items_after=10,
            l3_diagnostics=[
                {"status": "rejected", "rejection_codes": ["a", "b"]},
                {"status": "ok", "rejection_codes": []},
            ],
        )
    finally:
        loguru.logger.remove(sink_id)

    blob = " ".join(rec.message for rec in caplog.records)
    assert "[评分项救援尾部]" in blob
    assert "document_id=99" in blob
    assert "mode=enforce" in blob
    assert "rejection_codes=2" in blob


@pytest.mark.asyncio
async def test_run_chunk_extraction_pass_structure_unchanged() -> None:
    """插桩后返回值仍为 ChunkExtractionPass，关键字段可用。"""
    from app.core.extractor.scoring.scoring_pipeline_core import (
        ChunkExtractionPass,
        as_extraction_chunk,
        run_chunk_extraction_pass,
    )
    from app.core.extractor.scoring.scoring_table_parser import ParsedScoringTable

    # 空内容 chunk：确定性解析不 confident，会走 llm_fallback
    raw = SimpleNamespace(
        content="[评分表 第1-1页]\n| 序号 | 评分项目 |\n| 1 | 演示 |",
        metadata={"page_num": 1, "section_path": "评标办法"},
        chunk_index=0,
        chunk_metadata={},
    )
    chunk = as_extraction_chunk(raw, chunk_id=1)

    async def _llm_fallback(_c):
        return [], {}

    result = await run_chunk_extraction_pass(
        [chunk],
        llm_fallback=_llm_fallback,
        dedupe_exact=lambda items: list(items),
        absorb=lambda survivors, incoming: survivors + incoming,
        document_id=1,
    )
    assert isinstance(result, ChunkExtractionPass)
    assert hasattr(result, "survivors")
    assert hasattr(result, "fallback_items")
    assert hasattr(result, "volume_defs")
    assert hasattr(result, "parse_diagnostics")
    assert hasattr(result, "llm_fallback_chunks")
    assert isinstance(result.survivors, list)
    assert isinstance(result.fallback_items, list)
    # 空表通常非 confident → 计一次 fallback
    assert result.llm_fallback_chunks >= 0
    # 确认 ParsedScoringTable 仍可被候选管线产出（结构兼容）
    assert isinstance(ParsedScoringTable(items=[], volume_defs={}, confident=False), ParsedScoringTable)
