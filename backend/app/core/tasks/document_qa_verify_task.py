"""文档质检复核 LLM 子任务。

职责: 处理侧把复核请求整批投给 LLM worker 并等待结果; LLM worker 侧并发执行后写回 Redis
不做: 不组装证据包、不按置信度取舍候选、不落库
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Optional

from loguru import logger

from app.core.config import settings

DOCUMENT_QA_VERIFY_JOB = "document_qa_verify_job"

_RESULT_KEY_PREFIX = "bidmind:document_qa:verify:"
_RESULT_TTL_SEC = 3600
_POLL_INTERVAL_SEC = 0.5


def _result_key(batch_id: str) -> str:
    return f"{_RESULT_KEY_PREFIX}{batch_id}"


def _encode_outcome(outcome: Any) -> dict[str, Any]:
    from app.core.document_qa.verify.contracts import PageVerifyResult

    if isinstance(outcome, PageVerifyResult):
        return {"ok": True, "verdicts": [item.model_dump() for item in outcome.verdicts]}
    return {"ok": False, "error": f"{type(outcome).__name__}: {str(outcome)[:200]}"}


def _decode_outcome(row: Any) -> Any:
    from app.core.document_qa.verify.contracts import coerce_page_verify_result

    if isinstance(row, dict) and row.get("ok"):
        return coerce_page_verify_result({"verdicts": row.get("verdicts") or []})
    error = row.get("error") if isinstance(row, dict) else "invalid_row"
    return RuntimeError(str(error or "llm_worker_error"))


async def _write_result(ctx: dict, batch_id: str, payload: dict[str, Any]) -> None:
    redis = ctx.get("redis")
    if redis is not None:
        await redis.setex(_result_key(batch_id), _RESULT_TTL_SEC, json.dumps(payload, ensure_ascii=False))


async def run_document_qa_verify_batch(
    ctx: dict,
    project_id: int,
    batch_id: str,
    messages: list[str],
) -> dict[str, Any]:
    """LLM worker 侧: 并发执行一批复核请求并写回 Redis。单条失败只记在该位。"""
    from app.core.ai.ai_factory import get_judge_client
    from app.core.document_qa.verify.finding_verifier import ask_requests_concurrently

    client = get_judge_client(project_id)
    if client is None:
        payload: dict[str, Any] = {"status": "skipped_no_extract_pool", "results": []}
    else:
        outcomes = await ask_requests_concurrently(client, messages)
        payload = {"status": "completed", "results": [_encode_outcome(item) for item in outcomes]}
    await _write_result(ctx, batch_id, payload)
    return payload


async def document_qa_verify_job(
    ctx: dict,
    project_id: int,
    batch_id: str,
    messages: list[str],
) -> None:
    """ARQ 入口, 只在 LLM worker 注册。项目已删除时写回 cancelled, 让等待方立即结束。"""
    from app.core.arq_runtime import is_project_cancelled_async

    if await is_project_cancelled_async(ctx.get("redis"), project_id):
        await _write_result(ctx, batch_id, {"status": "cancelled", "results": []})
        return
    started = time.monotonic()
    payload = await run_document_qa_verify_batch(ctx, project_id, batch_id, messages)
    failed = sum(1 for row in payload.get("results") or [] if not row.get("ok"))
    logger.info(
        f"[文档质检] 复核子任务完成: project={project_id}, requests={len(messages)}, "
        f"failed={failed}, status={payload.get('status')}, "
        f"elapsed_ms={(time.monotonic() - started) * 1000:.0f}"
    )


async def request_verdicts_from_llm_worker(
    project_id: int,
    messages: list[str],
) -> Optional[list[Any]]:
    """处理侧: 投递复核批次并等待 LLM worker 写回。

    返回与 messages 同序的结果(失败位为异常); 提取池不可用返回 None。
    未入队、超时或子任务失败时整批按失败返回, 由调用方原样保留候选。
    ponytail: 一个文档只投一批; 候选页过多时整批可能超过等待上限, 升级路径是分块投递。
    """
    from app.core.arq_runtime import enqueue_job_detailed

    batch_id = f"document-qa-verify-{project_id}-{uuid.uuid4().hex}"
    dispatch = await enqueue_job_detailed(DOCUMENT_QA_VERIFY_JOB, project_id, batch_id, messages)
    if not dispatch.queued:
        return [RuntimeError(f"not_queued:{dispatch.reason}") for _ in messages]

    payload = await _wait_for_result(batch_id)
    status = payload.get("status")
    if status == "skipped_no_extract_pool":
        return None
    if status != "completed":
        reason = str(payload.get("reason") or status)
        return [RuntimeError(reason) for _ in messages]
    return [_decode_outcome(row) for row in payload.get("results") or []]


async def _wait_for_result(batch_id: str) -> dict[str, Any]:
    """轮询 Redis 结果键直到超时。

    ponytail: 与 document_image_llm_task 的等待循环同构; 出现第三个批次通道时再抽公共模块。
    """
    from app.core.redis import get_redis_pool

    redis = await get_redis_pool()
    if redis is None:
        return {"status": "failed", "reason": "redis_unavailable"}

    timeout = float(getattr(settings, "DOCUMENT_QA_VERIFY_WAIT_TIMEOUT", 600.0))
    deadline = time.monotonic() + timeout
    key = _result_key(batch_id)
    while time.monotonic() < deadline:
        raw = await redis.get(key)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"status": "failed", "reason": "invalid_result_json"}
        await asyncio.sleep(_POLL_INTERVAL_SEC)
    return {"status": "timeout", "reason": "wait_timeout"}
