"""文档质检(单文档)阶段

职责:
1. 门禁: 仅 bid_review 项目的 BID 文档且 DOCUMENT_QA_ENABLED 时运行
2. 构建 DocumentModel(线程池) -> 运行确定性检查器 -> 持久化 document run
3. fail-open: 任何异常只记 failed run + 日志, 禁止阻断文档处理主流程
4. 由处理队列任务 document_qa_job 调用, 不占用处理进度心跳
5. 运行期间发布跨进程在途标记, 供当前性检查区分「在途」与「缺失」

不做: 不在检查器内调 LLM、不改 parsed_doc、不把质检阶段写进 PDF解析/OCR 进度
复核: 候选交 LLM worker 子任务判定(verify/), 池空或失败时原样保留
信封收敛由质检任务收尾时调用, 处理链结束时的调用仍保留(幂等)
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from loguru import logger

# 在途标记 TTL 须远大于保活间隔(30s); 持有进程被杀后标记分钟级自愈, 其后允许补算
_INFLIGHT_TTL_SEC = 180
_INFLIGHT_KEY_PREFIX = "document_qa:inflight:"


async def run_document_qa_stage(
    *,
    parsed_doc: Any,
    file_path: str,
    project_id: int,
    document_id: int,
    heartbeat: Callable[..., None],
) -> str:
    """执行单文档质检。返回状态串(写入心跳 extra, 供观测)。"""
    from app.core.config import settings

    if not bool(getattr(settings, "DOCUMENT_QA_ENABLED", True)):
        return "disabled"

    try:
        gate = await _load_gate(project_id, document_id)
        if gate is None:
            return "skipped:not_applicable"
        bid_part, file_hash, content_hash = gate
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 门禁查询失败(跳过): document={document_id}, {exc}")
        return "skipped:gate_error"

    from app.core.tasks.heartbeat_keepalive import await_with_task_keepalive

    heartbeat("parsing", "1/1", extra={"document_qa_status": "running"})
    try:
        status = await await_with_task_keepalive(
            _analyze_and_persist(
                parsed_doc=parsed_doc,
                file_path=file_path,
                project_id=project_id,
                document_id=document_id,
                bid_part=bid_part,
                file_hash=file_hash,
                content_hash=content_hash,
            ),
            task_name=_inflight_key(document_id),
            stage="document_qa",
            progress="running",
            heartbeat=_touch_inflight,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[文档质检] 单文档质检失败(fail-open): document={document_id}, {exc}",
        )
        await _mark_failed(project_id, document_id, str(exc))
        status = "failed"
    finally:
        _clear_inflight(document_id)
    heartbeat("parsing", "1/1", extra={"document_qa_status": status})
    return status


def _inflight_key(document_id: int) -> str:
    return f"{_INFLIGHT_KEY_PREFIX}{document_id}"


def _touch_inflight(key: str, **_kwargs: Any) -> None:
    """保活写入: 刷新在途标记 TTL。无 Redis 时跳过, 写入异常由保活层吞掉。"""
    from app.core.redis import get_sync_redis

    client = get_sync_redis()
    if client is not None:
        client.set(key, "1", ex=_INFLIGHT_TTL_SEC)


def _clear_inflight(document_id: int) -> None:
    try:
        from app.core.redis import get_sync_redis

        client = get_sync_redis()
        if client is not None:
            client.delete(_inflight_key(document_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[文档质检] 在途标记清理失败(TTL 兜底): document={document_id}, {exc}")


def document_qa_inflight(document_id: int) -> bool:
    """其他执行体正在跑该文档的单文档质检。读失败按不在途处理, 退回补算。"""
    try:
        from app.core.redis import get_sync_redis

        client = get_sync_redis()
        return bool(client is not None and client.exists(_inflight_key(document_id)))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[文档质检] 在途标记读取失败(按不在途): document={document_id}, {exc}")
        return False


async def _load_gate(
    project_id: int,
    document_id: int,
) -> Optional[tuple[Optional[str], Optional[str], Optional[str]]]:
    """门禁+取哈希: 非投标审查 BID 文档返回 None。"""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.document import Document, DocumentType
    from app.models.project import Project, ProjectType

    async with async_session_factory() as db:
        document = (await db.execute(
            select(Document).where(Document.id == document_id)
        )).scalar_one_or_none()
        if document is None or document.doc_type != DocumentType.BID:
            return None
        project = (await db.execute(
            select(Project).where(Project.id == project_id)
        )).scalar_one_or_none()
        if project is None or project.project_type != ProjectType.BID_REVIEW:
            return None
        return document.bid_part, document.file_hash, document.content_hash


async def _analyze_and_persist(
    *,
    parsed_doc: Any,
    file_path: str,
    project_id: int,
    document_id: int,
    bid_part: Optional[str],
    file_hash: Optional[str],
    content_hash: Optional[str],
) -> str:
    from app.core.config import settings
    from app.core.database import async_session_factory
    from app.core.document_qa.analyzer import analyze_document, findings_to_dicts
    from app.core.document_qa.contracts import RULESET_VERSION
    from app.core.document_qa.model_builder import build_document_model
    from app.core.document_qa.rules.loader import load_rules
    from app.services.document_qa.fingerprint import document_run_fingerprint
    from app.services.document_qa.persistence import persist_document_run

    fingerprint = document_run_fingerprint(
        document_id=document_id, file_hash=file_hash, content_hash=content_hash,
    )
    rules = load_rules()
    max_span_pages = int(getattr(settings, "DOCUMENT_QA_MAX_SPAN_PAGES", 800))

    model = await asyncio.to_thread(
        build_document_model,
        parsed_doc,
        file_path,
        document_id=document_id,
        bid_part=bid_part,
        max_span_pages=max_span_pages,
    )
    analysis = await asyncio.to_thread(analyze_document, model, rules)
    findings, verify_stats = await _verify_candidates(
        model, analysis.findings, project_id=project_id,
    )
    if verify_stats.get("degraded"):
        analysis.degraded = True
    analysis.features["verify"] = verify_stats
    status = "degraded" if analysis.degraded else "completed"
    rows = findings_to_dicts(
        findings, document_id=document_id, bid_part=bid_part,
    )

    async with async_session_factory() as db:
        await persist_document_run(
            db,
            project_id=project_id,
            document_id=document_id,
            input_fingerprint=fingerprint,
            ruleset_version=str(
                getattr(settings, "DOCUMENT_QA_RULESET_VERSION", "") or RULESET_VERSION
            ),
            status=status,
            findings_rows=rows,
            checker_status=analysis.checker_status,
            features=analysis.features,
            error_message=model.degraded_reason,
        )
        await db.commit()

    logger.info(
        f"[文档质检] 单文档完成: document={document_id}, part={bid_part or '整本'}, "
        f"status={status}, findings={len(rows)}"
    )
    return status


async def _verify_candidates(model: Any, findings: list, *, project_id: int) -> tuple[list, dict]:
    """复核候选, 模型调用交给 LLM worker 子任务。模块自身异常也 fail-open, 不让复核拖垮质检落库。"""
    try:
        from app.core.document_qa.verify.finding_verifier import verify_findings
        from app.core.tasks.document_qa_verify_task import request_verdicts_from_llm_worker

        async def _ask_llm_worker(messages: list[str]) -> Optional[list[Any]]:
            return await request_verdicts_from_llm_worker(project_id, messages)

        return await verify_findings(
            model, findings, project_id=project_id, ask_requests=_ask_llm_worker,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 复核层异常(候选原样保留): {exc}")
        return findings, {"status": "failed", "degraded": True}


async def _mark_failed(project_id: int, document_id: int, error: str) -> None:
    """失败显形: 落一条 failed run(fail-open 但不静默)。"""
    try:
        from app.core.config import settings
        from app.core.database import async_session_factory
        from app.core.document_qa.contracts import RULESET_VERSION
        from app.services.document_qa.fingerprint import document_run_fingerprint
        from app.services.document_qa.persistence import persist_document_run

        async with async_session_factory() as db:
            await persist_document_run(
                db,
                project_id=project_id,
                document_id=document_id,
                input_fingerprint=document_run_fingerprint(
                    document_id=document_id, file_hash=None, content_hash=None,
                ),
                ruleset_version=str(
                    getattr(settings, "DOCUMENT_QA_RULESET_VERSION", "") or RULESET_VERSION
                ),
                status="failed",
                findings_rows=[],
                checker_status={},
                features={},
                error_message=error[:500],
            )
            await db.commit()
    except Exception as inner:  # noqa: BLE001
        logger.warning(f"[文档质检] 失败态落库失败(忽略): {inner}")
