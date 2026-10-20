"""无 scoring_table chunk 时的 VLM 视觉救援入口。

职责: 构造空尾部产物并走 VLM 视觉救援, 供零 chunk 文档(扫描件/无文本层)兜底。
不做: 不渲染页面、不解析确定性表格、不落库、不改 volume_defs。

阶段3(2026-08-31): 原第一梯队 chat-PDF 直读已删除——本入口仅在 pdf_direct
主路径的直读输入不可用时到达, 此处 PDF 直读必然同样不可用, 直接走 VLM。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from loguru import logger

from app.core.extractor.scoring.extraction_observability import (
    ScoringExtractionObservation,
    build_verdict_probe,
    log_extraction_summary,
    summarize_diagnostics,
)
from app.core.extractor.scoring.score_totals import verify_score_totals
from app.core.extractor.scoring.scoring_outcome import ScoringStructureOutcome
from app.core.extractor.scoring.scoring_rescue_integration import (
    ScoringRescueIntegrationResult,
)
from app.core.extractor.scoring.vlm.vlm_rescue_channel import attempt_vlm_scoring_rescue


async def collect_structure_via_vlm_when_no_chunks(
    *,
    db: Any,
    document_id: int,
    project_id: Optional[int],
    started: float,
    progress_callback: Optional[Any] = None,
) -> ScoringStructureOutcome:
    """零 scoring_table chunk 时走空 tail + VLM 救援; 定位器无命中则秒退。"""
    from app.core.extractor.scoring.scoring_progress import emit_scoring_progress

    logger.info(f"[评分项提取] 文档 {document_id} 无评分表 chunk，走VLM零chunk入口")
    empty_tail = ScoringRescueIntegrationResult(
        items=[],
        report=verify_score_totals([]),
        fidelity_sidecar={},
        diagnostics=[],
        l3_diagnostics=[],
        adopted=False,
        mode="off",
    )
    # 零 chunk = 未提取到任何评分结构, 必然非绿(触发口径统一)
    emit_scoring_progress(progress_callback, "vlm", 0, 1, "VLM视觉救援中")
    tail = await attempt_vlm_scoring_rescue(
        empty_tail,
        db=db,
        document_id=document_id,
        project_id=project_id,
        scoring_chunks=[],
        volume_defs={},
        trigger_non_green=True,
    )
    emit_scoring_progress(
        progress_callback, "vlm", 1, 1, "VLM视觉救援完成"
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    report = tail.report
    log_extraction_summary(ScoringExtractionObservation(
        document_id=document_id,
        chunk_count=0,
        confident_chunks=0,
        llm_fallback_chunks=0,
        items_final=len(tail.items),
        volume_def_count=0,
        rescue_mode=str(getattr(tail, "mode", "off") or "off"),
        rescue_adopted=bool(getattr(tail, "adopted", False)),
        report_total=getattr(report, "total", None),
        needs_review=bool(getattr(report, "needs_review", False)),
        elapsed_ms=elapsed_ms,
        trigger_verdict="amber",  # 零 chunk = 零提取, 恒非绿(阶段1埋点口径)
        diagnostics_summary=summarize_diagnostics(
            list(tail.diagnostics or []) + list(tail.l3_diagnostics or [])
        ),
    ))
    return ScoringStructureOutcome(
        items=tail.items,
        volume_defs={},
        report=tail.report,
        chunk_count=0,
        fidelity_sidecar=tail.fidelity_sidecar,
        parse_diagnostics=tail.diagnostics,
        l3_diagnostics=tail.l3_diagnostics,
        # 零 chunk 恒非绿(amber), 与摘要日志埋点同口径
        verdict_probe=build_verdict_probe(trigger_verdict="amber", tail=tail),
    )


__all__ = ["collect_structure_via_vlm_when_no_chunks"]
