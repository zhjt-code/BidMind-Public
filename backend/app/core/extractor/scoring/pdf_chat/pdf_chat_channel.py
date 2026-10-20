"""评分 chat-PDF 直传执行工具(pdf_direct 主路径消费)。

职责: 按能力档案解析的通道目标逐一尝试"chat直传PDF"(消息内file块/方舟
      file_url/DashScope fileid/extract-text 四方言), 解析候选产物
      (JSONL契约), 供 direct_primary 主路径经共享轮次引擎消费。
不做: 不实现门禁/采纳裁决(rescue_rounds 职责)、不实现方言传输(pdf_transport
      职责)、不编排轮次(direct_primary 职责)、不落库。

阶段3(2026-08-31): 旧"安全网第一梯队"救援编排(attempt_pdf_chat_scoring_rescue/
_rescue_non_green)已删除, pdf_direct 主路径直接消费本模块执行工具。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

NODE_KEY = "bid_review.scoring_pdf_rescue"


class _AllTargetsUnavailable(Exception):
    """本轮全部通道目标不可用(不支持或调用失败)。"""

    def __init__(self, detail: str, unsupported_only: bool):
        super().__init__(detail)
        self.unsupported_only = unsupported_only


async def _extract_candidate_items(
    available: list,
    rescue_input: Any,
    *,
    cfg: Any,
    meta: dict,
    gate_feedback: str,
    volume_defs: dict,
    scope_hint: str,
    project_id: int | None,
    document_id: int,
) -> tuple[list, Optional[dict]]:
    """逐目标尝试一次 PDF 直传并解析产物, 返回 (ExtractedScoringItem 列表, header)。

 不支持/失败的目标从 available 中移除(本次救援内不再重试);
 全部耗尽抛 _AllTargetsUnavailable, 由入口按 unsupported/error 归档。: 回复非空但解析零项(思考型模型吃满预算/格式不合作)时,
 输出预算自适应翻倍(封顶可配)并轮换下一目标(末目标保留防脱空)。
 """
    from app.core.ai.prompt_registry import resolve_runtime_params
    from app.core.ai.prompts.scoring_pdf_rescue import (
        SCORING_PDF_RESCUE_RETRY_SUFFIX,
        get_scoring_pdf_rescue_prompt,
    )
    from app.core.extractor.scoring.vlm.candidate_contract import (
        extract_jsonl_records,
        normalize_vlm_items,
        parse_item_drafts,
        to_extracted_items,
    )

    temperature, max_tokens = resolve_runtime_params(
        NODE_KEY, 0.0, cfg.SCORING_PDF_RESCUE_MAX_TOKENS
    )
    escalated = int(meta.get("max_tokens_escalated") or 0)
    if escalated > max_tokens:
        max_tokens = escalated
    pages = list(rescue_input.pages)
    system, user = get_scoring_pdf_rescue_prompt(
        page_hint=(
            f"附件PDF第1页=原文档第{pages[0]}页, 依次对应后续页码。{scope_hint}"
        ),
        page_count=str(len(pages)),
        page_start=str(pages[0]),
        page_end=str(pages[-1]),
    )
    if gate_feedback:
        user += SCORING_PDF_RESCUE_RETRY_SUFFIX.format(gate_feedback=gate_feedback)

    text = await _call_first_available(
        available, rescue_input,
        system=system, user=user,
        temperature=temperature, max_tokens=max_tokens,
        timeout_s=cfg.SCORING_PDF_RESCUE_TIMEOUT_S,
        meta=meta, project_id=project_id, document_id=document_id,
    )
    header, raw_items, broken = extract_jsonl_records(text)
    meta["broken_lines"] = int(meta.get("broken_lines", 0)) + broken
    drafts, invalid = parse_item_drafts(raw_items)
    meta["invalid_lines"] = int(meta.get("invalid_lines", 0)) + invalid
    normalized = normalize_vlm_items(drafts)
    meta["items"] = len(normalized)
    items = to_extracted_items(normalized, volume_defs)
    if str(text or "").strip() and not items:
        # 疑似思考截断/格式不合作——预算升档 + 轮换目标
        meta["max_tokens_escalated"] = min(
            max_tokens * 2, cfg.SCORING_PDF_RESCUE_MAX_TOKENS_CEILING
        )
        if len(available) > 1:
            noncompliant = available.pop(0)
            meta.setdefault("format_noncompliant", []).append(noncompliant.label)
            logger.info(
                "[评分PDF直读救援] 目标回复{}字符但解析零项, 轮换下一目标 doc={}: "
                "{} -> {}, 预算升档至{}",
                len(text or ""), document_id, noncompliant.label,
                available[0].label, meta["max_tokens_escalated"],
            )
        else:
            logger.info(
                "[评分PDF直读救援] 末目标解析零项, 保留并升档预算至{} doc={}",
                meta["max_tokens_escalated"], document_id,
            )
    return items, header


async def _call_first_available(
    available: list,
    rescue_input: Any,
    *,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
    timeout_s: float,
    meta: dict,
    project_id: int | None,
    document_id: int,
) -> str:
    """按序尝试目标, 首个成功者胜出; 失败目标即时移除(fail-fast)。"""
    from app.core.ai import pdf_transport

    unsupported_only = True
    last_error: Exception | None = None
    while available:
        target = available[0]
        meta["calls"] = int(meta.get("calls", 0)) + 1
        try:
            text = await asyncio.wait_for(
                pdf_transport.chat_with_pdf(
                    target,
                    pdf_bytes=rescue_input.pdf_bytes,
                    pdf_base64=rescue_input.pdf_base64,
                    filename=rescue_input.filename,
                    system_message=system,
                    user_message=user,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    node_key=NODE_KEY,
                    project_id=project_id,
                ),
                timeout=timeout_s,
            )
            if not str(text or "").strip():
                raise pdf_transport.EmptyPdfCompletion(
                    f"{target.label}: empty pdf completion"
                )
            meta["target"] = target.label
            meta["dialect"] = target.dialect
            logger.info(
                "[评分PDF直读救援] 目标命中 doc={}: {} ({}), 第{}次调用, 回复{}字符",
                document_id, target.label, target.dialect,
                meta["calls"], len(text or ""),
            )
            return text
        except pdf_transport.PdfInputUnsupported as exc:
            logger.info(
                "[评分PDF直读救援] 目标不支持PDF输入, 降级下一目标: {}", exc
            )
            meta.setdefault("unsupported_targets", []).append(target.label)
            available.pop(0)
            last_error = exc
        except Exception as exc:  # noqa: BLE001 - 单目标失败降级下一目标
            logger.warning(
                "[评分PDF直读救援] 目标调用失败 {}: {}", target.label, exc
            )
            meta.setdefault("failed_targets", []).append(
                f"{target.label}:{type(exc).__name__}"
            )
            available.pop(0)
            last_error = exc
            unsupported_only = False
    raise _AllTargetsUnavailable(
        str(last_error) if last_error else "无可用PDF直传目标",
        unsupported_only=unsupported_only,
    )


__all__ = ["NODE_KEY"]
