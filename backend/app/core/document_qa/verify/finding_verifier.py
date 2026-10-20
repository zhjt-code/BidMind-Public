"""
职责: 把规则候选交给提取池做真伪判定, 并按置信度取舍
不做: 不生成候选、不落库、不调用视觉通道、提取池空时不回落审查池
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Optional

from loguru import logger

from app.core.document_qa.contracts import DocumentModel, Finding, QaCertainty
from app.core.document_qa.verify.contracts import PageVerifyResult, coerce_page_verify_result
from app.core.document_qa.verify.evidence_pack import (
    VerifyCandidate,
    build_batches,
    confirmed_by_measurement,
    excerpt_supports_claim,
    page_context,
)
from app.core.document_qa.verify.prompts import VERIFY_SYSTEM, VERIFY_USER

_NODE_KEY = "document_qa.verify"
_REVIEW_HINT = "建议人工核对"
_MAX_TOKENS = 1200

# 批量发出复核请求的执行体: 结果与请求同序(失败位为异常); 返回 None 表示提取池不可用
AskRequests = Callable[[list[str]], Awaitable[Optional[list[Any]]]]


def normalize_confidence(value: float) -> float:
    """把 0-1 或 0-100 的把握度收成 0-1。"""
    number = float(value)
    if number > 1:
        number = number / 100.0
    return max(0.0, min(1.0, number))


def apply_verdicts(
    findings: list[Finding],
    verdicts_by_id: dict[str, tuple[bool, float]],
    candidates: list[VerifyCandidate],
    *,
    threshold: float,
    keep_ids: Optional[set[str]] = None,
) -> tuple[list[Finding], dict[str, int]]:
    """高置信误报丢弃, 高置信真缺陷保留, 低置信或漏答降为疑似。

    keep_ids 是调用失败的候选, 原样保留, 不降级。
    """
    forced = keep_ids or set()
    index_to_id = {item.finding_index: item.candidate_id for item in candidates}
    kept: list[Finding] = []
    counts = {"kept": 0, "dropped": 0, "softened": 0}
    for index, finding in enumerate(findings):
        candidate_id = index_to_id.get(index)
        if candidate_id is None or candidate_id in forced:
            kept.append(finding)
            counts["kept"] += 1
            continue
        verdict = verdicts_by_id.get(candidate_id)
        if verdict is None:
            kept.append(_soften(finding))
            counts["softened"] += 1
            continue
        is_real, confidence = verdict
        if confidence >= threshold and not is_real:
            if excerpt_supports_claim(finding):
                kept.append(finding)
                counts["kept"] += 1
                continue
            counts["dropped"] += 1
            continue
        if confidence >= threshold and is_real:
            kept.append(finding)
            counts["kept"] += 1
            continue
        kept.append(_soften(finding))
        counts["softened"] += 1
    return kept, counts


def _soften(finding: Finding) -> Finding:
    suggestion = finding.suggestion or ""
    if _REVIEW_HINT not in suggestion:
        suggestion = f"{suggestion} {_REVIEW_HINT}".strip()
    return replace(finding, certainty=QaCertainty.POSSIBLE, suggestion=suggestion)


def _request_payload(
    model: DocumentModel,
    page: Optional[int],
    candidates: list[VerifyCandidate],
) -> str:
    excerpts: list[str] = []
    for candidate in candidates:
        excerpts.extend(candidate.payload.get("excerpts") or [])
    page_text = model.body_texts.get(page or -1, "") if page is not None else ""
    body = {
        "page": page,
        "page_char_count": len(page_text.strip()),
        "context": page_context(model, page, excerpts),
        "candidates": [candidate.payload for candidate in candidates],
    }
    return json.dumps(body, ensure_ascii=False)


async def _ask_page(
    client: Any,
    user_message: str,
) -> PageVerifyResult:
    from app.core.ai.prompt_registry import get_registry

    from app.core.ai.constrained_decoding import build_response_format

    resolved = get_registry().get_prompt(_NODE_KEY)
    system_message = resolved.system_message or VERIFY_SYSTEM
    template = resolved.user_template or VERIFY_USER
    data = await client.chat_json(
        user_message=template.format(payload=user_message),
        system_message=system_message,
        temperature=0.0,
        max_tokens=_MAX_TOKENS,
        json_schema=build_response_format(PageVerifyResult),
    )
    return coerce_page_verify_result(data)


async def ask_requests_concurrently(client: Any, messages: list[str]) -> list[Any]:
    """一次发出全部复核请求, 实际并发由调度器 AIMD 信号量限流。结果与请求同序, 失败位为异常。"""
    return list(await asyncio.gather(
        *(_ask_page(client, message) for message in messages),
        return_exceptions=True,
    ))


def _in_process_executor(project_id: int, client: Any) -> Optional[AskRequests]:
    if client is None:
        from app.core.ai.ai_factory import get_judge_client

        client = get_judge_client(project_id)
    if client is None:
        return None

    async def _ask(messages: list[str]) -> Optional[list[Any]]:
        return await ask_requests_concurrently(client, messages)

    return _ask


async def verify_findings(
    model: DocumentModel,
    findings: list[Finding],
    *,
    project_id: int,
    client: Any = None,
    ask_requests: Optional[AskRequests] = None,
) -> tuple[list[Finding], dict[str, Any]]:
    """复核全部候选。提取池不可用或调用失败时原样保留。

    ask_requests 缺省时在本进程用提取池并发执行; 编排层可注入 LLM worker 子任务。
    """
    from app.core.config import settings

    if not findings or not bool(getattr(settings, "DOCUMENT_QA_VERIFY_ENABLED", True)):
        return findings, {"status": "skipped", "degraded": False}

    skipped = {"status": "skipped_no_extract_pool", "degraded": False}
    if ask_requests is None:
        ask_requests = _in_process_executor(project_id, client)
    if ask_requests is None:
        logger.info("[文档质检] 提取池无通道, 复核跳过, 候选原样保留")
        return findings, skipped

    threshold = float(getattr(settings, "DOCUMENT_QA_VERIFY_CONFIDENCE", 0.8))
    keep_ids: set[str] = set()
    all_candidates: list[VerifyCandidate] = []
    messages: list[str] = []
    asked: list[list[VerifyCandidate]] = []
    for page, candidates in build_batches(findings):
        all_candidates.extend(candidates)
        live = [
            item for item in candidates
            if not confirmed_by_measurement(model, findings[item.finding_index])
        ]
        keep_ids.update(
            item.candidate_id for item in candidates if item not in live
        )
        if live:
            messages.append(_request_payload(model, page, live))
            asked.append(candidates)

    outcomes = await ask_requests(messages) if messages else []
    if outcomes is None:
        logger.info("[文档质检] 提取池无通道, 复核跳过, 候选原样保留")
        return findings, skipped
    outcomes = list(outcomes) + [RuntimeError("missing_outcome")] * (len(asked) - len(outcomes))

    verdicts_by_id: dict[str, tuple[bool, float]] = {}
    failed_calls = 0
    for candidates, outcome in zip(asked, outcomes):
        if not isinstance(outcome, PageVerifyResult):
            logger.warning(
                f"[文档质检] 复核调用失败(本批保留): page={candidates[0].page}, {outcome}"
            )
            failed_calls += 1
            keep_ids.update(item.candidate_id for item in candidates)
            continue
        for item in outcome.verdicts:
            verdicts_by_id[item.candidate_id] = (
                item.is_real_defect,
                normalize_confidence(item.confidence),
            )

    current, counts = apply_verdicts(
        findings,
        verdicts_by_id,
        all_candidates,
        threshold=threshold,
        keep_ids=keep_ids,
    )
    return current, {
        "status": "degraded" if failed_calls else "completed",
        "degraded": bool(failed_calls),
        "calls": len(messages),
        "failed_calls": failed_calls,
        **counts,
    }
