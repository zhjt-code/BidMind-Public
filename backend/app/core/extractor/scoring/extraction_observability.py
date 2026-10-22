"""评分项提取可观测性（日志摘要）。

职责: 汇总一次评分项提取的中间过程统计, 输出结构化日志摘要与诊断聚合。
不做: 不修改提取行为/落库、不注册 Prometheus 指标、不记录文档原文或评分项全文。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from loguru import logger

# format_chunk_diagnostics 产出形如 "第47页: xxx" / "第47-48页: xxx"
_PAGE_PREFIX_RE = re.compile(r"^第\s*\d+(?:\s*-\s*\d+)?\s*页\s*:\s*")
_DIAG_KEY_MAX_LEN = 40


@dataclass
class ScoringExtractionObservation:
    """单次评分项提取的结构化过程摘要（仅计数与枚举，无原文）。"""

    document_id: int
    chunk_count: int = 0
    confident_chunks: int = 0
    llm_fallback_chunks: int = 0
    items_final: int = 0
    volume_def_count: int = 0
    rescue_mode: str = "off"
    rescue_adopted: bool = False
    report_total: float | None = None
    needs_review: bool = False
    elapsed_ms: int = 0
    diagnostics_summary: dict[str, int] = field(default_factory=dict)
    # 阶段1埋点: VLM 触发时点的判绿三态(green/blue/amber)。运维聚合口径:
    # green=链直绿; 非green且rescue_adopted=VLM转化; 非green且未采纳=仍非绿。
    trigger_verdict: str | None = None


def summarize_diagnostics(diagnostics: Sequence[Any]) -> dict[str, int]:
    """将解析/救援诊断聚合为低基数计数。

    - 去掉页码前缀（如「第47页:」）
    - 截断至 40 字符作为归类键，避免高基数与原文泄露
    - 空/空白诊断忽略
    """
    counter: Counter[str] = Counter()
    for raw in diagnostics:
        text = str(raw or "").strip()
        if not text:
            continue
        # l3_diagnostics 可能是 dict，只取 status/rejection 等短字段摘要
        if isinstance(raw, Mapping):
            status = str(raw.get("status") or "").strip()
            codes = raw.get("rejection_codes") or []
            if status and codes:
                text = f"l3:{status}:{','.join(str(c) for c in codes[:5])}"
            elif status:
                text = f"l3:{status}"
            else:
                text = str(dict(raw))[:_DIAG_KEY_MAX_LEN]
        else:
            text = _PAGE_PREFIX_RE.sub("", text).strip()
        if not text:
            continue
        key = text[:_DIAG_KEY_MAX_LEN]
        counter[key] += 1
    return dict(counter.most_common())


def log_extraction_summary(obs: ScoringExtractionObservation) -> None:
    """输出单次评分项提取的 info 级结构化摘要。"""
    logger.info(
        "[评分项提取摘要] document_id={} chunks={} confident={} llm_fallback={} "
        "items={} volume_defs={} rescue_mode={} rescue_adopted={} "
        "report_total={} needs_review={} trigger_verdict={} elapsed_ms={} diagnostics={}",
        obs.document_id,
        obs.chunk_count,
        obs.confident_chunks,
        obs.llm_fallback_chunks,
        obs.items_final,
        obs.volume_def_count,
        obs.rescue_mode,
        obs.rescue_adopted,
        obs.report_total,
        obs.needs_review,
        obs.trigger_verdict,
        obs.elapsed_ms,
        obs.diagnostics_summary or {},
    )


def build_verdict_probe(*, trigger_verdict: str, tail: Any) -> dict[str, Any]:
    """构建文档级 verdict 探针(阶段2 VLM-first 裁决数据, 由调用方落库)。

 统计口径: trigger_verdict=green → 链直绿;
 非绿且 rescue_adopted → 救援转化(final_verdict 区分完全/部分,: 闭合报告的 needs_review 会把 verdict 仍非绿的采纳误记为完全转化);
 非绿且未采纳 → 双双失守。仅计数与枚举, 无原文。: PDF/VLM 分通道记轮次与状态; 已采纳 PDF 不得被后续 VLM rejected 覆盖。
 """
    from datetime import datetime, timezone

    pdf_meta: Mapping[str, Any] = {}
    vlm_meta: Mapping[str, Any] = {}
    for diag in getattr(tail, "l3_diagnostics", None) or ():
        if not isinstance(diag, Mapping):
            continue
        channel = diag.get("channel")
        if channel == "vlm_rescue":
            vlm_meta = diag
        elif channel == "pdf_chat_rescue":
            pdf_meta = diag

    pdf_status = str(pdf_meta.get("status") or "")
    vlm_status = str(vlm_meta.get("status") or "")
    adopted_statuses = ("ok", "ok_needs_review")
    if vlm_status in adopted_statuses:
        status_source, rounds_source = vlm_meta, vlm_meta
        final_source = vlm_meta
    elif pdf_status in adopted_statuses:
        status_source, rounds_source = pdf_meta, pdf_meta
        final_source = pdf_meta
    else:
        status_source = vlm_meta or pdf_meta
        rounds_source = vlm_meta or pdf_meta
        final_source = vlm_meta or pdf_meta

    presence_meta = pdf_meta or vlm_meta
    presence = str(presence_meta.get("presence") or "")
    probe = {
        "trigger_verdict": str(trigger_verdict or ""),
        "rescue_adopted": bool(getattr(tail, "adopted", False)),
        "rescue_rounds": int(rounds_source.get("rounds") or 0),
        "rescue_status": str(status_source.get("status") or ""),
        "pdf_rescue_rounds": int(pdf_meta.get("rounds") or 0),
        "pdf_rescue_status": pdf_status,
        "vlm_rescue_rounds": int(vlm_meta.get("rounds") or 0),
        "vlm_rescue_status": vlm_status,
        "final_needs_review": bool(
            getattr(getattr(tail, "report", None), "needs_review", False)
        ),
        # 采纳后候选的 verdict 单源复判结果; 未采纳/未触发时=触发时点 verdict
        "final_verdict": str(final_source.get("final_verdict") or trigger_verdict or ""),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if presence:
        probe["presence"] = presence
        prior = str(presence_meta.get("presence_prior") or "")
        if prior:
            probe["presence_prior"] = prior
        reasons = presence_meta.get("presence_reasons") or []
        if reasons:
            probe["presence_reasons"] = list(reasons)
    return probe


def log_rescue_tail_summary(
    *,
    document_id: int,
    mode: str,
    adopted: bool,
    items_before: int,
    items_after: int,
    l3_diagnostics: Sequence[Any] | None = None,
) -> None:
    """输出 rescue 尾部阶段摘要（mode/采纳/条数变化/拒绝码计数）。"""
    rejection_code_count = 0
    for diag in l3_diagnostics or ():
        if isinstance(diag, Mapping):
            codes = diag.get("rejection_codes") or []
            if isinstance(codes, (list, tuple)):
                rejection_code_count += len(codes)
    logger.info(
        "[评分项救援尾部] document_id={} mode={} adopted={} "
        "items_before={} items_after={} rejection_codes={}",
        document_id,
        mode,
        adopted,
        items_before,
        items_after,
        rejection_code_count,
    )


__all__ = [
    "ScoringExtractionObservation",
    "build_verdict_probe",
    "summarize_diagnostics",
    "log_extraction_summary",
    "log_rescue_tail_summary",
]
