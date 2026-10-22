"""评分 VLM 视觉救援通道编排。

职责: 救援尾部产物仍非绿(needs_review)时——独立定位评分区页面送 vision 池,
 分批读图提取候选后交共享轮次引擎(rescue_rounds)完成门禁/复判/采纳:
 候选经 verdict 单源复判转绿即终点; 三轮未绿采跨轮最优——
 闭合 COMPLETE 直采, 未闭合但核计缺口严格优于现状则采纳并保持
 needs_review; 否则原样保持现状(最坏=现状, 红线偏序比较)。
不做: 不修改确定性链/救援尾部内部、不实现门禁/采纳裁决(rescue_rounds 职责)、
 不落库、不记录文档原文、绿色项目零开销(第一判定即返回)。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from loguru import logger

from app.core.extractor.scoring.vlm.rescue_rounds import (
    RescueChannelNotes,
    RescueRoundsRequest,
    run_candidate_rescue_rounds,
)

NODE_KEY = "bid_review.scoring_vlm_rescue"


def _pdf_ladder_already_adopted(tail: Any) -> bool:
    """职责: 识别第一梯队 PDF 直读是否已采纳。
    不做: 不裁决 VLM 候选、不修改 tail。
    """
    for entry in getattr(tail, "l3_diagnostics", None) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("channel") != "pdf_chat_rescue":
            continue
        if str(entry.get("status") or "").startswith("ok"):
            return True
    return False


_VLM_NOTES = RescueChannelNotes(
    channel="vlm_rescue",
    action_label="视觉救援",
    log_prefix="[评分VLM救援]",
    fidelity_note="VLM视觉救援已通过锚定+闭合双门禁(vlm_rescue)",
    fidelity_note_partial="VLM视觉救援已采纳待人工核对核计(vlm_rescue)",
)


async def attempt_vlm_scoring_rescue(
    tail: Any,
    *,
    db: Any,
    document_id: int,
    project_id: int | None,
    scoring_chunks: list[Any],
    volume_defs: dict,
    trigger_non_green: bool | None = None,
    judge_context: Any = None,
) -> Any:
    """非绿时尝试 VLM 视觉救援; 返回替换后的尾部结果或原样返回。

 入参 tail 为 ScoringRescueIntegrationResult; 任何异常均吞并保持现状,
 诊断元数据(无原文)追加到 tail.l3_diagnostics。

 judge_context (审查V2): 编排层装配的 CandidateJudgeContext——触发 verdict、
 权威文档级基准、scope 提示一体传递; 提供时优先于 trigger_non_green。
 trigger_non_green: 调用方给出的权威非绿判定(与前端判绿同轴)。
 两者均缺省时回退 legacy needs_review 判断(兼容独立调用)。
 """
    from app.core.scoring_vlm_rescue_config import scoring_vlm_rescue_settings as cfg

    if cfg.SCORING_VLM_RESCUE_MODE == "off":
        return tail
    if judge_context is not None:
        trigger_non_green = str(judge_context.trigger_verdict) != "green"
    if trigger_non_green is None:
        trigger_non_green = bool(getattr(tail.report, "needs_review", False))
    if not trigger_non_green:
        return tail
    # 触发判非绿而 legacy 自报绿 = 假绿现状(如权重行恰凑满100),
    # 其 gap=0 是虚假优势, 不得作为部分采纳的拒收基准。
    # 上一梯队 PDF 已采纳的闭合满分不是假绿, 关闭直采走偏序比较。
    false_green_current = not bool(getattr(tail.report, "needs_review", False))
    if false_green_current and _pdf_ladder_already_adopted(tail):
        false_green_current = False

    meta: dict = {"channel": "vlm_rescue", "status": "skipped", "calls": 0}
    if judge_context is not None and judge_context.missing:
        # fail-visible: 未装配齐的裁判上下文字段(如无权威基准回退默认100)
        meta["judge_context_missing"] = list(judge_context.missing)
    started = time.monotonic()
    try:
        result = await _rescue_non_green(
            tail,
            db=db,
            document_id=document_id,
            project_id=project_id,
            scoring_chunks=scoring_chunks,
            volume_defs=volume_defs,
            cfg=cfg,
            meta=meta,
            false_green_current=false_green_current,
            judge_context=judge_context,
        )
    except Exception as exc:  # noqa: BLE001 - 救援通道失败不阻断主流程
        logger.warning("[评分VLM救援] 通道异常, 保持现状 doc={}: {}", document_id, exc)
        meta["status"] = "error"
        meta["error"] = f"{type(exc).__name__}"
        result = None
    meta["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    if isinstance(tail.l3_diagnostics, list):
        tail.l3_diagnostics.append(meta)
    return result if result is not None else tail


async def _rescue_non_green(
    tail: Any,
    *,
    db: Any,
    document_id: int,
    project_id: int | None,
    scoring_chunks: list[Any],
    volume_defs: dict,
    cfg: Any,
    meta: dict,
    false_green_current: bool = False,
    judge_context: Any = None,
) -> Any | None:
    """非绿救援主体: 装配读图输入 + vision 客户端, 交共享轮次引擎裁决。"""
    from app.core.extractor.scoring.vlm.rescue_input_builder import (
        build_vlm_rescue_input,
    )
    from app.core.extractor.scoring.vlm.scope_context import build_scope_page_hint

    rescue_input = await build_vlm_rescue_input(
        db=db,
        document_id=document_id,
        scoring_chunks=list(scoring_chunks or []),
        dpi=cfg.SCORING_VLM_RESCUE_DPI,
        page_margin=cfg.SCORING_VLM_RESCUE_PAGE_MARGIN,
        max_pages=cfg.SCORING_VLM_RESCUE_MAX_PAGES,
    )
    if rescue_input is None:
        meta["status"] = "skipped_no_input"
        return None
    meta["pages"] = [rescue_input.pages[0], rescue_input.pages[-1]]
    if rescue_input.truncated:
        meta["pages_truncated"] = True

    from app.core.ai.ai_factory import get_vision_client

    client = get_vision_client(project_id=project_id)

    # /审查V1: 多 scope(lot/scheme)文档把已知页映射注入读图提示,
    # 否则 per-scope 闭合裁判拿不到归属只能按单包轴整体拒收。
    scope_hint = (
        judge_context.scope_hint
        if judge_context is not None
        else build_scope_page_hint(list(scoring_chunks or []))
    )

    async def _extract(gate_feedback: str) -> tuple[list, Optional[dict]]:
        return await _extract_candidate_items(
            client, rescue_input, cfg=cfg, meta=meta,
            gate_feedback=gate_feedback, volume_defs=volume_defs,
            lot_hint=scope_hint,
        )

    request = RescueRoundsRequest(
        tail=tail,
        db=db,
        document_id=document_id,
        scoring_chunks=list(scoring_chunks or []),
        volume_defs=volume_defs,
        anchor_text=rescue_input.anchor_text,
        max_rounds=max(1, cfg.SCORING_VLM_RESCUE_MAX_ROUNDS),
        meta=meta,
        false_green_current=false_green_current,
        judge_context=judge_context,
        notes=_VLM_NOTES,
        rescue_pages=tuple(rescue_input.pages),
        require_group_relation=not bool(scoring_chunks),
    )
    return await run_candidate_rescue_rounds(request, _extract)


async def _extract_candidate_items(
    client: Any,
    rescue_input: Any,
    *,
    cfg: Any,
    meta: dict,
    gate_feedback: str,
    volume_defs: dict,
    lot_hint: str = "",
) -> tuple[list, Optional[dict]]:
    """分批调用 VLM 并合并解析产物, 返回 (ExtractedScoringItem 列表, header)。"""
    from app.core.ai.prompt_registry import resolve_runtime_params
    from app.core.ai.prompts.scoring_vlm_rescue import (
        SCORING_VLM_RESCUE_RETRY_SUFFIX,
        get_scoring_vlm_rescue_prompt,
    )
    from app.core.extractor.scoring.vlm.candidate_contract import (
        extract_jsonl_records,
        normalize_vlm_items,
        parse_item_drafts,
        to_extracted_items,
    )

    temperature, max_tokens = resolve_runtime_params(
        NODE_KEY, 0.0, cfg.SCORING_VLM_RESCUE_MAX_TOKENS
    )
    per_call = max(1, cfg.SCORING_VLM_RESCUE_MAX_PAGES_PER_CALL)
    images = list(rescue_input.images)
    pages = list(rescue_input.pages)

    raw_items: list[dict] = []
    header: Optional[dict] = None
    broken_total = 0
    for start in range(0, len(images), per_call):
        batch = images[start : start + per_call]
        batch_pages = pages[start : start + per_call]
        system, user = get_scoring_vlm_rescue_prompt(
            page_hint=(
                f"本批第1张图片=第{batch_pages[0]}页, 依次对应后续页码。{lot_hint}"
            ),
            image_count=str(len(batch)),
            page_start=str(batch_pages[0]),
            page_end=str(batch_pages[-1]),
        )
        if gate_feedback:
            user += SCORING_VLM_RESCUE_RETRY_SUFFIX.format(gate_feedback=gate_feedback)
        meta["calls"] = int(meta.get("calls", 0)) + 1
        text = await asyncio.wait_for(
            client.chat_with_images(
                user,
                batch,
                system_message=system,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            timeout=cfg.SCORING_VLM_RESCUE_TIMEOUT_S,
        )
        batch_header, batch_items, broken = extract_jsonl_records(text)
        if header is None:
            header = batch_header
        raw_items.extend(batch_items)
        broken_total += broken

    meta["broken_lines"] = broken_total
    drafts, invalid = parse_item_drafts(raw_items)
    meta["invalid_lines"] = invalid
    normalized = normalize_vlm_items(drafts)
    meta["items"] = len(normalized)
    return to_extracted_items(normalized, volume_defs), header


__all__ = ["attempt_vlm_scoring_rescue", "NODE_KEY"]
