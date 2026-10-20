"""评分提取 pdf_direct 主路径（2026-08-31 用户裁决: 直传PDF为唯一提取通道）。

职责: 组装评分区子PDF → 直传配置模型（共享轮次引擎: 锚定门禁 + verdict 复判 +
      缺口反馈重询, 首轮+2次重试）→ 采纳产物构建 ScoringStructureOutcome。
不做: 不向模型提示词喂任何 chunk/markdown 文本（chunk 仅用于评分页定位与
      重询缺项提示）、不做门禁/采纳裁决（rescue_rounds 职责）、不做方言传输
      （pdf_transport 职责）、不落库、不处理零文本层扫描件（返回 None,
      由调用方落零chunk VLM入口兜底）。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, Optional

from loguru import logger

from app.core.extractor.scoring.extraction_observability import (
    ScoringExtractionObservation,
    build_verdict_probe,
    log_extraction_summary,
    summarize_diagnostics,
)
from app.core.extractor.scoring.scoring_facts import build_scoring_fact_set
from app.core.extractor.scoring.scoring_outcome import ScoringStructureOutcome
from app.core.extractor.scoring.scoring_progress import emit_scoring_progress
from app.core.extractor.scoring.scoring_rescue_integration import (
    ScoringRescueIntegrationResult,
)
from app.core.extractor.scoring.score_totals import verify_score_totals
from app.core.extractor.scoring.vlm.rescue_rounds import (
    RescueChannelNotes,
    RescueRoundsRequest,
    run_candidate_rescue_rounds,
)

# channel 沿用 pdf_chat_rescue 使 verdict 探针(build_verdict_probe)与既有
# 看板统计口径无缝衔接; 文案区分"主路径直读"与旧"救援"语义。
_DIRECT_NOTES = RescueChannelNotes(
    channel="pdf_chat_rescue",
    action_label="PDF直读提取",
    log_prefix="[评分PDF直读]",
    fidelity_note="PDF直读提取已通过锚定+闭合双门禁(pdf_chat_rescue)",
    fidelity_note_partial="PDF直读提取已采纳待人工核对核计(pdf_chat_rescue)",
)


def _empty_tail() -> ScoringRescueIntegrationResult:
    """零现状尾部: pdf_direct 从空起步, 不存在可被覆盖的更好现状(根除)。"""
    return ScoringRescueIntegrationResult(
        items=[],
        report=verify_score_totals([]),
        fidelity_sidecar={},
        diagnostics=[],
        l3_diagnostics=[],
        adopted=False,
        mode="pdf_direct",
    )


def _volume_defs_from_items(items: list[Any]) -> dict[str, Any]:
    """从已盖章条目还原卷定义视图(仅观测/落库元数据消费, 非裁判输入)。"""
    defs: dict[str, Any] = {}
    for item in items or []:
        vol = getattr(item, "volume", None)
        if not vol or vol in defs:
            continue
        volume_max = getattr(item, "volume_max", None)
        if volume_max is None:
            continue
        defs[vol] = SimpleNamespace(
            weight=getattr(item, "volume_weight", None), volume_max=volume_max
        )
    return defs


def _build_outcome(
    *,
    document_id: int,
    tail: Any,
    chunk_count: int,
    final_verdict: str,
    started: float,
) -> ScoringStructureOutcome:
    """尾部产物 → ScoringStructureOutcome + 摘要日志(与 legacy 出口同口径)。"""
    volume_defs = _volume_defs_from_items(list(tail.items or []))
    report = tail.report
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    diag_blob = list(tail.diagnostics or []) + list(tail.l3_diagnostics or [])
    log_extraction_summary(ScoringExtractionObservation(
        document_id=document_id,
        chunk_count=chunk_count,
        confident_chunks=0,
        llm_fallback_chunks=0,
        items_final=len(tail.items or []),
        volume_def_count=len(volume_defs),
        rescue_mode="pdf_direct",
        rescue_adopted=bool(getattr(tail, "adopted", False)),
        report_total=getattr(report, "total", None),
        needs_review=bool(getattr(report, "needs_review", False)),
        elapsed_ms=elapsed_ms,
        diagnostics_summary=summarize_diagnostics(diag_blob),
        trigger_verdict=final_verdict,
    ))
    return ScoringStructureOutcome(
        items=list(tail.items or []),
        volume_defs=volume_defs,
        report=report,
        chunk_count=chunk_count,
        fidelity_sidecar=dict(tail.fidelity_sidecar or {}),
        parse_diagnostics=list(tail.diagnostics or []),
        fact_set=build_scoring_fact_set(
            list(tail.items or []), source_id=f"document:{document_id}"
        ),
        l3_diagnostics=list(tail.l3_diagnostics or []),
        verdict_probe=build_verdict_probe(trigger_verdict=final_verdict, tail=tail),
    )


def _absent_outcome(
    *,
    document_id: int,
    scoring_chunks: list[Any],
    started: float,
    decision: Any,
) -> ScoringStructureOutcome:
    """存在性门禁阻断: 成功空集, 不组装子PDF、不落 VLM。"""
    from app.core.extractor.scoring.scoring_table_presence import ABSENT_NOTICE

    tail = _empty_tail()
    meta: dict[str, Any] = {
        "channel": "pdf_chat_rescue",
        "mode": "pdf_direct",
        "status": "skipped_no_scoring_table",
        "calls": 0,
        "presence": "absent",
        "presence_verdict": getattr(decision, "verdict", "absent"),
        "presence_prior": getattr(decision, "prior", ""),
        "presence_reasons": list(getattr(decision, "reasons", ()) or ()),
        "final_verdict": "absent",
    }
    tail.diagnostics.append(ABSENT_NOTICE)
    tail.l3_diagnostics.append(meta)
    logger.info(
        "[评分存在性门禁] doc={} 跳过抽取 verdict={} prior={}",
        document_id,
        getattr(decision, "verdict", ""),
        getattr(decision, "prior", ""),
    )
    return _build_outcome(
        document_id=document_id,
        tail=tail,
        chunk_count=len(scoring_chunks or []),
        final_verdict="absent",
        started=started,
    )


def _stamp_presence(outcome: Any, decision: Any) -> Any:
    """把未阻断的存在性判定写入探针与通道 meta。"""
    if not isinstance(outcome, ScoringStructureOutcome):
        return outcome
    presence = getattr(decision, "verdict", "")
    prior = getattr(decision, "prior", "")
    reasons = list(getattr(decision, "reasons", ()) or ())
    stamped: list[Any] = []
    for diag in outcome.l3_diagnostics or []:
        if isinstance(diag, dict) and diag.get("channel") == "pdf_chat_rescue":
            stamped.append({**diag, "presence": presence, "presence_prior": prior})
        else:
            stamped.append(diag)
    probe = dict(outcome.verdict_probe or {})
    probe["presence"] = presence
    probe["presence_prior"] = prior
    probe["presence_reasons"] = reasons
    return ScoringStructureOutcome(
        items=outcome.items,
        volume_defs=outcome.volume_defs,
        report=outcome.report,
        chunk_count=outcome.chunk_count,
        fidelity_sidecar=outcome.fidelity_sidecar,
        parse_diagnostics=outcome.parse_diagnostics,
        fact_set=outcome.fact_set,
        l3_diagnostics=stamped,
        verdict_probe=probe,
    )


async def try_pdf_direct_primary(
    extractor: Any,
    document_id: int,
    chunks: list[Any],
    started: float,
) -> ScoringStructureOutcome:
    """评分提取主路径分流(供 collect_scoring_structure 单行委托)。

    pdf_direct 直读提取(输入不可用且有chunk则 fail-visible 空产物);
    直读输入不可用且零chunk(扫描件/无文本层)时落 VLM 零chunk入口兜底。
    存在性门禁阻断时不组装子PDF、不落 VLM。
    """
    from app.core.extractor.scoring.scoring_table_presence import (
        evaluate_document_scoring_presence,
    )
    from app.core.scoring_pdf_rescue_config import scoring_pdf_rescue_settings as cfg

    scoring_chunks = list(chunks or [])
    decision = None
    if cfg.SCORING_PRESENCE_GATE_ENABLED:
        decision = await evaluate_document_scoring_presence(
            extractor.db, document_id, scoring_chunks
        )
        if decision.blocks_extraction:
            return _absent_outcome(
                document_id=document_id,
                scoring_chunks=scoring_chunks,
                started=started,
                decision=decision,
            )

    outcome = await collect_structure_via_pdf_direct(
        db=extractor.db,
        document_id=document_id,
        project_id=extractor.project_id,
        scoring_chunks=scoring_chunks,
        progress_callback=extractor.progress_callback,
        started=started,
    )
    if outcome is not None:
        if decision is not None and isinstance(outcome, ScoringStructureOutcome):
            return _stamp_presence(outcome, decision)
        return outcome
    from app.core.extractor.scoring.vlm.zero_chunk_entry import (
        collect_structure_via_vlm_when_no_chunks,
    )

    vlm_outcome = await collect_structure_via_vlm_when_no_chunks(
        db=extractor.db,
        document_id=document_id,
        project_id=extractor.project_id,
        started=started,
        progress_callback=extractor.progress_callback,
    )
    if decision is not None and isinstance(vlm_outcome, ScoringStructureOutcome):
        return _stamp_presence(vlm_outcome, decision)
    return vlm_outcome


async def collect_structure_via_pdf_direct(
    *,
    db: Any,
    document_id: int,
    project_id: Optional[int],
    scoring_chunks: list[Any],
    progress_callback: Any,
    started: float,
) -> Optional[ScoringStructureOutcome]:
    """pdf_direct 主路径入口; 直读输入不可用且无评分chunk时返回 None(扫描件兜底)。"""
    from app.core.ai import pdf_transport
    from app.core.extractor.scoring.pdf_chat.pdf_chat_channel import (
        _AllTargetsUnavailable,
        _extract_candidate_items,
    )
    from app.core.extractor.scoring.pdf_chat.pdf_rescue_input_builder import (
        build_pdf_chat_rescue_input,
    )
    from app.core.extractor.scoring.vlm.scope_context import build_scope_page_hint
    from app.core.scoring_pdf_rescue_config import scoring_pdf_rescue_settings as cfg

    max_rounds = max(1, cfg.SCORING_PDF_RESCUE_MAX_ROUNDS)
    progress_units = max_rounds + 1
    meta: dict[str, Any] = {
        "channel": "pdf_chat_rescue", "mode": "pdf_direct",
        "status": "skipped", "calls": 0,
    }
    tail = _empty_tail()

    def _fail_outcome(diagnostic: str) -> ScoringStructureOutcome:
        tail.diagnostics.append(diagnostic)
        tail.l3_diagnostics.append(meta)
        logger.warning("[评分PDF直读] doc={}: {}", document_id, diagnostic)
        return _build_outcome(
            document_id=document_id, tail=tail,
            chunk_count=len(scoring_chunks or []),
            final_verdict="amber", started=started,
        )

    emit_scoring_progress(
        progress_callback, "pdf_direct", 0, progress_units, "定位评分页并组装子PDF"
    )
    rescue_input = await build_pdf_chat_rescue_input(
        db=db,
        document_id=document_id,
        scoring_chunks=list(scoring_chunks or []),
        page_margin=cfg.SCORING_PDF_RESCUE_PAGE_MARGIN,
        max_pages=cfg.SCORING_PDF_RESCUE_MAX_PAGES,
    )
    if rescue_input is None:
        if not scoring_chunks:
            return None  # 零文本层/零chunk: 调用方落VLM零chunk入口
        meta["status"] = "skipped_no_input"
        return _fail_outcome("评分区子PDF组装失败(原PDF不可用), 未能直读提取, 请人工核对")
    max_bytes = int(cfg.SCORING_PDF_RESCUE_MAX_PDF_MB * 1024 * 1024)
    if len(rescue_input.pdf_bytes) > max_bytes:
        meta["status"] = "skipped_pdf_too_large"
        meta["pdf_kb"] = round(len(rescue_input.pdf_bytes) / 1024, 1)
        return _fail_outcome("评分区子PDF超出直传上限, 未能直读提取, 请人工核对")
    if rescue_input.truncated:
        # 3484 教训: 页窗口截断必须用户可见, 否则 amber 清单会被误当作
        # "只是分值不齐"而人工补录确认——带误导的失败比失败更危险。
        chunk_pages = [
            int(getattr(c, "page_num", 0) or 0)
            for c in (scoring_chunks or [])
            if getattr(c, "page_num", None)
        ]
        span = (
            f"第{min(chunk_pages)}-{max(chunk_pages)}页" if chunk_pages else "多页"
        )
        tail.diagnostics.append(
            f"评分内容跨{span}, 超出单次直读页数上限, 本次仅直读"
            f"第{rescue_input.pages[0]}-{rescue_input.pages[-1]}页, "
            f"提取结果可能不完整, 请对照原文核对遗漏部分"
        )

    targets = pdf_transport.resolve_pdf_transport_targets()
    if not targets:
        meta["status"] = "skipped_no_channel"
        return _fail_outcome("未配置支持PDF直传的模型通道, 未能直读提取, 请人工核对")
    meta["targets"] = [target.label for target in targets]
    meta["pages"] = [rescue_input.pages[0], rescue_input.pages[-1]]
    meta["pdf_kb"] = round(len(rescue_input.pdf_bytes) / 1024, 1)
    if rescue_input.truncated:
        meta["pages_truncated"] = True
    logger.info(
        "[评分PDF直读] doc={} 直传子PDF p{}-{} ({}KB) 目标: {}",
        document_id, rescue_input.pages[0], rescue_input.pages[-1],
        meta["pdf_kb"], ", ".join(f"{t.label}({t.dialect})" for t in targets),
    )

    scope_hint = build_scope_page_hint(list(scoring_chunks or []))
    available: list = list(targets)
    round_counter = {"n": 0}

    async def _extract(gate_feedback: str) -> tuple[list, Optional[dict]]:
        round_counter["n"] += 1
        emit_scoring_progress(
            progress_callback, "pdf_direct",
            min(round_counter["n"], progress_units), progress_units,
            f"模型直读评分表 第{round_counter['n']}/{max_rounds}轮",
        )
        return await _extract_candidate_items(
            available, rescue_input, cfg=cfg, meta=meta,
            gate_feedback=gate_feedback, volume_defs={},
            scope_hint=scope_hint, project_id=project_id,
            document_id=document_id,
        )

    request = RescueRoundsRequest(
        tail=tail,
        db=db,
        document_id=document_id,
        scoring_chunks=list(scoring_chunks or []),
        volume_defs={},
        anchor_text=rescue_input.anchor_text,
        max_rounds=max_rounds,
        meta=meta,
        false_green_current=False,
        judge_context=None,
        notes=_DIRECT_NOTES,
        rescue_pages=tuple(rescue_input.pages),
        require_group_relation=not bool(scoring_chunks),
    )
    try:
        result = await run_candidate_rescue_rounds(request, _extract)
    except _AllTargetsUnavailable as exc:
        meta["status"] = "skipped_unsupported" if exc.unsupported_only else "error"
        meta["detail"] = str(exc)[:200]
        result = None
    except Exception as exc:  # noqa: BLE001 - 直读失败不阻断, 空结果fail-visible
        logger.warning("[评分PDF直读] 通道异常 doc={}: {}", document_id, exc)
        meta["status"] = "error"
        meta["error"] = f"{type(exc).__name__}"
        result = None

    final_tail = result if result is not None else tail
    final_tail.l3_diagnostics.append(meta)
    if result is None and not final_tail.diagnostics:
        final_tail.diagnostics.append(
            "PDF直读提取未获得可采纳的评分项, 请人工核对评分表"
        )
    final_verdict = str(meta.get("final_verdict") or "") or "amber"
    emit_scoring_progress(
        progress_callback, "pdf_direct",
        progress_units, progress_units, "评分分值核计完成",
    )
    return _build_outcome(
        document_id=document_id, tail=final_tail,
        chunk_count=len(scoring_chunks or []),
        final_verdict=final_verdict, started=started,
    )


__all__ = ["collect_structure_via_pdf_direct", "try_pdf_direct_primary"]
