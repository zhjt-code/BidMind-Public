"""通用文档图片 LLM 子任务。

该模块承接投标审查/串标审查 PDF 预处理阶段中的视觉模型调用。
Process worker 负责解析、OCR、整页渲染和分块；本模块只在 LLM worker
中执行真正的 VL 调用，并通过 Redis 批次结果回写给调用方。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from loguru import logger

from app.core.arq_runtime import ProjectCancelledError, is_project_cancelled
from app.core.config import settings
from app.core.parser.image_extractor import ExtractedImage


# wait_for 期间不刷心跳会让 ProcessWorker 在大批次或慢 LLM API 场景下
# 超过 ARQ_JOB_HEARTBEAT_TIMEOUT(180s), 被 API 误判 running_heartbeat_stale
# 触发"自动恢复"。所有透传到 wait_for 的调用都接受可选 keepalive_callback,
# 在 polling 期间定期触发, 调用方负责真正的 update_heartbeat。
_DEFAULT_KEEPALIVE_INTERVAL = 60.0


DOCUMENT_IMAGE_VL_JOB = "document_image_vl_job"

_RESULT_KEY_PREFIX = "bidmind:document:image_llm:"
_DEFAULT_RESULT_TTL = 3600
_DEFAULT_WAIT_TIMEOUT = 600.0
_DEFAULT_POLL_INTERVAL = 0.5


@dataclass
class DocumentImageRecognitionStats:
    """文档图片识别批次统计。"""

    total: int = 0
    recognized: int = 0
    ocr_ok: int = 0
    vl_ok: int = 0
    ocr_fallback: int = 0
    ocr_degraded: int = 0
    ocr_budget_exhausted: int = 0
    queued_vl: int = 0


@dataclass(frozen=True)
class FullPageVlRequest:
    """整页 VL 批量请求项。"""

    page_b64: str
    page_num: int
    context: str = ""
    route_hint: str = "full_page"


def _raise_if_project_cancelled(project_id: int, *, stage: str) -> None:
    """项目删除后停止父任务继续生产图片 VL 子任务。"""

    if is_project_cancelled(project_id):
        logger.info(
            "[文档图片识别] 项目已取消/删除, 停止VL子任务生产: project={}, stage={}",
            project_id,
            stage,
        )
        raise ProjectCancelledError(
            project_id,
            detail=f"document_image_llm_task:{stage}",
        )


def result_key(batch_id: str) -> str:
    """返回文档图片 LLM 子任务批次结果键。"""

    return f"{_RESULT_KEY_PREFIX}{batch_id}"


def make_image_key(image: object, index: int = 0) -> str:
    """为内存图片对象生成稳定批次内 key。"""

    file_hash = getattr(image, "_file_hash", None)
    if file_hash:
        return str(file_hash)
    file_path = getattr(image, "file_path", "") or ""
    page_num = getattr(image, "page_num", 0) or 0
    image_index = getattr(image, "image_index", index) or index
    return f"{file_path}|{page_num}|{image_index}"


def image_to_vl_item(
    image: object,
    image_key: str,
    *,
    business_flow: str,
    route_hint: str = "vl",
) -> dict[str, Any]:
    """把 ExtractedImage 转成可跨 ARQ 传输的轻量 payload。"""

    return {
        "kind": "image",
        "business_flow": business_flow,
        "image_key": image_key,
        "file_path": getattr(image, "file_path", None),
        "page_num": int(getattr(image, "page_num", 1) or 1),
        "image_index": int(getattr(image, "image_index", 0) or 0),
        "width": int(getattr(image, "width", 0) or 0),
        "height": int(getattr(image, "height", 0) or 0),
        "image_format": getattr(image, "image_format", "") or "jpeg",
        "xref": int(getattr(image, "xref", 0) or 0),
        "bbox": getattr(image, "bbox", None),
        "spatial_context": getattr(image, "spatial_context", "") or "",
        "route_hint": route_hint,
    }


def full_page_to_vl_item(
    *,
    image_key: str,
    page_b64: str,
    page_num: int,
    context: str = "",
    business_flow: str,
    route_hint: str = "full_page",
) -> dict[str, Any]:
    """构造整页扫描件 VL payload。"""

    return {
        "kind": "full_page",
        "business_flow": business_flow,
        "image_key": image_key,
        "page_b64": page_b64,
        "page_num": int(page_num or 0),
        "context": context or "",
        "route_hint": route_hint,
    }


def item_to_image(item: dict[str, Any]) -> ExtractedImage:
    """从 ARQ payload 还原 VL 调用所需的 ExtractedImage。"""

    bbox_raw = item.get("bbox")
    bbox = tuple(bbox_raw) if isinstance(bbox_raw, (list, tuple)) else None
    return ExtractedImage(
        image_index=int(item.get("image_index") or 0),
        page_num=int(item.get("page_num") or 1),
        width=int(item.get("width") or 0),
        height=int(item.get("height") or 0),
        image_format=str(item.get("image_format") or "jpeg"),
        xref=int(item.get("xref") or 0),
        bbox=bbox,  # type: ignore[arg-type]
        file_path=item.get("file_path"),
        spatial_context=str(item.get("spatial_context") or ""),
    )


async def enqueue_document_image_llm_job(
    *,
    project_id: int,
    business_flow: str,
    batch_id: str,
    items: list[dict[str, Any]],
) -> tuple[bool, str]:
    """投递文档图片 LLM 子任务，返回 (queued, reason)。"""

    if not items:
        return True, "empty"

    _raise_if_project_cancelled(project_id, stage="before_enqueue")

    from app.core.arq_runtime import enqueue_job_detailed

    dispatch = await enqueue_job_detailed(
        DOCUMENT_IMAGE_VL_JOB,
        project_id,
        business_flow,
        batch_id,
        items,
    )
    if dispatch.queued:
        return True, dispatch.reason
    return False, dispatch.reason


def _image_llm_wait_timeout() -> float:
    return float(
        getattr(
            settings,
            "IMAGE_LLM_WAIT_TIMEOUT",
            getattr(settings, "WRITING_IMAGE_LLM_WAIT_TIMEOUT", _DEFAULT_WAIT_TIMEOUT),
        )
    )


def _image_llm_poll_interval() -> float:
    return max(
        float(
            getattr(
                settings,
                "IMAGE_LLM_POLL_INTERVAL",
                getattr(settings, "WRITING_IMAGE_LLM_POLL_INTERVAL", _DEFAULT_POLL_INTERVAL),
            )
        ),
        0.1,
    )


def _image_llm_result_ttl() -> int:
    return int(
        getattr(
            settings,
            "IMAGE_LLM_RESULT_TTL",
            getattr(settings, "WRITING_IMAGE_LLM_RESULT_TTL", _DEFAULT_RESULT_TTL),
        )
    )


async def wait_for_document_image_llm_result(
    batch_id: str,
    *,
    project_id: int | None = None,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    keepalive_callback: Optional[Callable[[], None]] = None,
    keepalive_interval: float = _DEFAULT_KEEPALIVE_INTERVAL,
) -> dict[str, Any]:
    """等待文档图片 LLM 子任务写回 Redis 结果。

 keepalive_callback: 长 batch 等待期间(几分钟)定期触发, 让调用方
 刷新 ARQ 业务心跳, 避免被 API 误判 running_heartbeat_stale。
 回调应是同步且非阻塞的; 异常会被吞掉只记 warning, 不影响 wait 主流程。
 """

    from app.core.redis import get_redis_pool

    redis = await get_redis_pool()
    if redis is None:
        return {"status": "failed", "reason": "redis_unavailable", "items": []}

    resolved_timeout = timeout if timeout is not None else _image_llm_wait_timeout()
    resolved_poll = poll_interval if poll_interval is not None else _image_llm_poll_interval()
    deadline = time.monotonic() + float(resolved_timeout)
    key = result_key(batch_id)
    last_keepalive = time.monotonic()
    # 0.05 下限避免极端 0/负值导致每次 tick 都触发回调; 生产默认 60s 完全不受影响
    safe_keepalive_interval = max(float(keepalive_interval), 0.05)

    while time.monotonic() < deadline:
        if project_id is not None:
            _raise_if_project_cancelled(project_id, stage="waiting_result")
        raw = await redis.get(key)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"status": "failed", "reason": "invalid_result_json", "items": []}
        await asyncio.sleep(float(resolved_poll))
        # 心跳保活: 不依赖额外协程, 在轮询节拍中按时间窗触发回调
        if keepalive_callback is not None:
            now = time.monotonic()
            if now - last_keepalive >= safe_keepalive_interval:
                try:
                    keepalive_callback()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[文档图片识别] keepalive 回调异常(已忽略): batch_id={}, err={}",
                        batch_id,
                        exc,
                    )
                last_keepalive = now

    return {"status": "timeout", "reason": "wait_timeout", "items": []}


async def _write_result(
    redis: object,
    batch_id: str,
    payload: dict[str, Any],
) -> None:
    data = json.dumps(payload, ensure_ascii=False)
    await redis.setex(result_key(batch_id), _image_llm_result_ttl(), data)  # type: ignore[attr-defined]


async def run_document_image_vl_batch(
    ctx: dict,
    project_id: int,
    business_flow: str,
    batch_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """LLM worker 执行文档图片 VL 批次。: items 并发执行(asyncio.gather), 共享同一 vision_client 实例;
 实际并发数受 Dispatcher 内部 dispatcher_concurrency semaphore 控制(AIMD 自适应),
 不会暴击远端 API。单 item 失败被局部 try/except 包住, 不污染整批。
 结果顺序与 items 原序保持一致, 上层按 image_key 重映射逻辑无影响。
 """

    redis = ctx.get("redis")
    try:
        from app.core.ai import get_vision_client
        from app.core.parser.image_analyzer import (
            analyze_full_page,
            analyze_image_vl_only,
        )

        vision_client = get_vision_client(project_id=project_id)

        async def _process_one(item: dict[str, Any]) -> dict[str, Any]:
            image_key = str(item.get("image_key") or "")
            route_hint = str(item.get("route_hint") or "vl")
            item_flow = str(item.get("business_flow") or business_flow or "bid_review")
            try:
                kind = str(item.get("kind") or "image")
                if kind == "full_page":
                    content = await analyze_full_page(
                        page_b64=str(item.get("page_b64") or ""),
                        vision_client=vision_client,
                        page_num=int(item.get("page_num") or 0),
                        context=str(item.get("context") or ""),
                        business_flow=item_flow,
                    )
                    route = route_hint
                else:
                    image = item_to_image(item)
                    content, route = await analyze_image_vl_only(
                        image,
                        vision_client,
                        route_label=route_hint,
                        business_flow=item_flow,
                    )
                return {
                    "image_key": image_key,
                    "kind": kind,
                    "content": content,
                    "route": route,
                    "success": bool(content),
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "image_key": image_key,
                    "kind": str(item.get("kind") or "image"),
                    "content": "",
                    "route": route_hint,
                    "success": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                }

        # 并发执行: 单个 _process_one 已包 try/except, 不会抛到 gather 层。
        # 即便万一抛出, return_exceptions=True 也会捕获, 防止局部失败拖垮整批。
        raw_results = await asyncio.gather(
            *[_process_one(item) for item in items],
            return_exceptions=True,
        )
        results: list[dict[str, Any]] = []
        for idx, res in enumerate(raw_results):
            if isinstance(res, dict):
                results.append(res)
            elif isinstance(res, BaseException):
                # 理论不会触达(每个 _process_one 内部已 catch), 防御兜底
                fallback_item = items[idx] if idx < len(items) else {}
                results.append({
                    "image_key": str(fallback_item.get("image_key") or ""),
                    "kind": str(fallback_item.get("kind") or "image"),
                    "content": "",
                    "route": str(fallback_item.get("route_hint") or "vl"),
                    "success": False,
                    "error": f"{type(res).__name__}: {str(res)[:200]}",
                })
            else:
                results.append({
                    "image_key": "",
                    "kind": "image",
                    "content": "",
                    "route": "vl",
                    "success": False,
                    "error": f"unexpected_result_type: {type(res).__name__}",
                })

        payload = {"status": "completed", "items": results}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "文档图片 VL 子任务批次失败: project={}, flow={}, batch={}, err={}",
            project_id,
            business_flow,
            batch_id,
            exc,
        )
        payload = {
            "status": "failed",
            "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
            "items": [],
        }

    if redis is not None:
        await _write_result(redis, batch_id, payload)
    return payload


async def _enqueue_and_wait(
    *,
    project_id: int,
    business_flow: str,
    batch_id: str,
    items: list[dict[str, Any]],
    keepalive_callback: Optional[Callable[[], None]] = None,
    keepalive_interval: float = _DEFAULT_KEEPALIVE_INTERVAL,
) -> dict[str, Any]:
    _raise_if_project_cancelled(project_id, stage="before_enqueue_wait")
    queued, reason = await enqueue_document_image_llm_job(
        project_id=project_id,
        business_flow=business_flow,
        batch_id=batch_id,
        items=items,
    )
    if not queued:
        logger.warning(
            "[文档图片识别/{}] VL子任务未入队: reason={}, items={}",
            business_flow,
            reason,
            len(items),
        )
        return {"status": "failed", "reason": reason, "items": []}
    return await wait_for_document_image_llm_result(
        batch_id,
        project_id=project_id,
        keepalive_callback=keepalive_callback,
        keepalive_interval=keepalive_interval,
    )


async def analyze_full_pages_with_document_llm_subtasks(
    *,
    project_id: int,
    business_flow: str,
    pages: list[FullPageVlRequest],
    batch_size: Optional[int] = None,
    keepalive_callback: Optional[Callable[[], None]] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict[int, str]:
    """通过 LLM worker 有界批量执行整页 VL。"""

    if not pages:
        return {}

    resolved_batch_size = max(
        int(
            batch_size
            if batch_size is not None
            else getattr(settings, "IMAGE_LLM_FULL_PAGE_BATCH_SIZE", 8)
        ),
        1,
    )
    results_by_page: dict[int, str] = {}

    for start in range(0, len(pages), resolved_batch_size):
        _raise_if_project_cancelled(project_id, stage="full_page_batch_loop")
        batch_pages = pages[start : start + resolved_batch_size]
        image_key_to_page: dict[str, int] = {
            f"full_page|{page.page_num}": int(page.page_num)
            for page in batch_pages
        }
        batch_id = f"document-image-vl-{project_id}-{uuid.uuid4().hex}"
        result = await _enqueue_and_wait(
            project_id=project_id,
            business_flow=business_flow,
            batch_id=batch_id,
            items=[
                full_page_to_vl_item(
                    image_key=image_key,
                    page_b64=page.page_b64,
                    page_num=page.page_num,
                    context=page.context,
                    business_flow=business_flow,
                    route_hint=page.route_hint,
                )
                for image_key, page in zip(image_key_to_page.keys(), batch_pages)
            ],
            keepalive_callback=keepalive_callback,
        )
        if result.get("status") != "completed":
            logger.warning(
                "[文档图片识别/{}] 整页VL批次失败: batch={}, pages={}, status={}",
                business_flow,
                batch_id,
                len(batch_pages),
                result.get("status") or result.get("reason"),
            )
            if progress_callback:
                for page in batch_pages:
                    progress_callback(int(page.page_num), "")
            continue

        reported_pages: set[int] = set()
        for item in result.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            page_num = image_key_to_page.get(str(item.get("image_key") or ""))
            content = str(item.get("content") or "")
            if page_num is None:
                continue
            reported_pages.add(page_num)
            if content:
                results_by_page[page_num] = content
            if progress_callback:
                progress_callback(page_num, content)
        if progress_callback:
            for page in batch_pages:
                page_num = int(page.page_num)
                if page_num not in reported_pages:
                    progress_callback(page_num, "")

    return results_by_page


async def analyze_full_page_with_document_llm_subtask(
    *,
    project_id: int,
    business_flow: str,
    page_b64: str,
    page_num: int,
    context: str = "",
    route_hint: str = "full_page",
    keepalive_callback: Optional[Callable[[], None]] = None,
) -> str:
    """通过 LLM worker 执行单页整页 VL。"""

    image_key = f"full_page|{page_num}|{uuid.uuid4().hex}"
    batch_id = f"document-image-vl-{project_id}-{uuid.uuid4().hex}"
    result = await _enqueue_and_wait(
        project_id=project_id,
        business_flow=business_flow,
        batch_id=batch_id,
        items=[
            full_page_to_vl_item(
                image_key=image_key,
                page_b64=page_b64,
                page_num=page_num,
                context=context,
                business_flow=business_flow,
                route_hint=route_hint,
            )
        ],
        keepalive_callback=keepalive_callback,
    )
    if result.get("status") != "completed":
        return ""
    first = next(iter(result.get("items", []) or []), {})
    return str(first.get("content") or "")


async def probe_document_vl_availability(
    *,
    project_id: int,
    business_flow: str,
    scan_pages: list,
    per_images: list,
    probe_count: int = 2,
    pdf_path: str = "",
) -> tuple[bool, str]:
    """通过 LLM worker 前置探测 VL 模型是否正常响应。"""

    if not scan_pages and not per_images:
        logger.info(f"[VL探测] 无可用探测目标, 跳过探测: {pdf_path}")
        return (True, "无探测目标")

    items: list[dict[str, Any]] = []
    if scan_pages:
        page = scan_pages[0]
        page_b64 = getattr(page, "rendered_b64", None) or ""
        if page_b64:
            items.append(
                full_page_to_vl_item(
                    image_key=f"probe-full-page|{getattr(page, 'page_num', 0)}",
                    page_b64=page_b64,
                    page_num=int(getattr(page, "page_num", 0) or 0),
                    context="[探测]",
                    business_flow=business_flow,
                    route_hint="probe_full_page",
                )
            )

    if per_images and len(items) < probe_count:
        largest = max(
            (img for img in per_images if getattr(img, "file_path", None)),
            key=lambda img: (getattr(img, "width", 0) or 0) * (getattr(img, "height", 0) or 0),
            default=None,
        )
        if largest is not None:
            items.append(
                image_to_vl_item(
                    largest,
                    make_image_key(largest, 0),
                    business_flow=business_flow,
                    route_hint="probe_image",
                )
            )

    if not items:
        return (True, "无有效探测目标")

    batch_id = f"document-image-probe-{project_id}-{uuid.uuid4().hex}"
    result = await _enqueue_and_wait(
        project_id=project_id,
        business_flow=business_flow,
        batch_id=batch_id,
        items=items,
    )
    if result.get("status") != "completed":
        reason = str(result.get("reason") or result.get("status") or "unknown")
        logger.error(f"[VL探测] LLM子任务失败: {reason}, pdf={pdf_path}")
        return (False, f"VL探测子任务失败: {reason}")

    probed = len(items)
    success = sum(1 for item in result.get("items", []) if item.get("content"))
    if success > 0:
        logger.info(f"[VL探测] 成功: {success}/{probed}, pdf={pdf_path}")
        return (True, f"探测成功 {success}/{probed}")

    logger.error(f"[VL探测] 失败: {success}/{probed}, pdf={pdf_path}")
    return (False, "VL模型探测全部返回空")


async def recognize_images_with_document_llm_subtasks(
    images: list[ExtractedImage],
    *,
    project_id: int,
    business_flow: str,
    progress_callback=None,
    ocr_engine: object = None,
    max_retry_rounds: int = 2,
    keepalive_callback: Optional[Callable[[], None]] = None,
) -> DocumentImageRecognitionStats:
    """OCR-first 本地执行，仅将逐图 VL 模型调用派发给 LLM worker。

 keepalive_callback: LLM worker 串行处理 batch 可能耗时数分钟,
 由调用方传入此回调, 用于在 wait 期间定期刷新 ARQ 业务心跳。
 """

    from app.core.parser.image_analyzer import analyze_image_ocr_first, ocr_priority

    analyzable = [img for img in images if img.file_path]
    if not analyzable:
        logger.info(f"[文档图片识别/{business_flow}] 无可识别图片，跳过")
        return DocumentImageRecognitionStats()

    stats = DocumentImageRecognitionStats(total=len(analyzable))
    needs_vl: list[tuple[ExtractedImage, str, str]] = []
    ocr_budget_max = int(getattr(settings, "OCR_BUDGET_PER_BATCH", 50))
    ocr_budget_remaining = ocr_budget_max

    def _consume_budget(priority: str) -> bool:
        nonlocal ocr_budget_remaining
        if ocr_budget_max <= 0 or priority == "high":
            return True
        if ocr_budget_remaining > 0:
            ocr_budget_remaining -= 1
            return True
        return False

    logger.info(
        "[文档图片识别/{}] 开始 OCR-first + LLM 子任务: {}张图片",
        business_flow,
        len(analyzable),
    )

    for idx, img in enumerate(analyzable):
        _raise_if_project_cancelled(project_id, stage="image_ocr_loop")
        route_hint = "vl"
        if ocr_engine is not None:
            try:
                priority = ocr_priority(img)
            except Exception:
                priority = "low"
            if priority != "low" and not _consume_budget(priority):
                stats.ocr_budget_exhausted += 1
                needs_vl.append((img, make_image_key(img, idx), "ocr_budget"))
                continue
        content, route_hint, need_vl = await analyze_image_ocr_first(
            img,
            ocr_engine,
            flow_id=f"project:{project_id}",
        )
        if content:
            img.visual_content = content
            stats.ocr_ok += 1
            if progress_callback:
                progress_callback(img, True)
        elif need_vl:
            if route_hint == "ocr->vl":
                stats.ocr_fallback += 1
            elif route_hint == "ocr_degraded":
                stats.ocr_degraded += 1
            needs_vl.append((img, make_image_key(img, idx), route_hint))
        elif progress_callback:
            progress_callback(img, False)

    remaining = needs_vl
    for round_no in range(0, max_retry_rounds + 1):
        _raise_if_project_cancelled(project_id, stage="image_vl_retry_loop")
        if not remaining:
            break
        batch_id = f"document-image-vl-{project_id}-{uuid.uuid4().hex}"
        items = [
            image_to_vl_item(
                img,
                image_key,
                business_flow=business_flow,
                route_hint=route_hint,
            )
            for img, image_key, route_hint in remaining
        ]
        result = await _enqueue_and_wait(
            project_id=project_id,
            business_flow=business_flow,
            batch_id=batch_id,
            items=items,
            keepalive_callback=keepalive_callback,
        )
        stats.queued_vl += len(items)
        if result.get("status") != "completed":
            if progress_callback:
                for img, _image_key, _route_hint in remaining:
                    progress_callback(img, False)
            break

        result_items = {
            str(item.get("image_key")): item
            for item in result.get("items", [])
            if isinstance(item, dict)
        }
        next_remaining: list[tuple[ExtractedImage, str, str]] = []
        for img, image_key, route_hint in remaining:
            item_result = result_items.get(image_key)
            content = str((item_result or {}).get("content") or "")
            if content:
                img.visual_content = content
                stats.vl_ok += 1
                if progress_callback:
                    progress_callback(img, True)
            else:
                if progress_callback and round_no >= max_retry_rounds:
                    progress_callback(img, False)
                if round_no < max_retry_rounds:
                    next_remaining.append((img, image_key, route_hint))
        remaining = next_remaining

    stats.recognized = sum(1 for img in analyzable if img.visual_content)
    logger.info(
        "[文档图片识别/{}] 完成: {}/{}张成功, OCR直接={}, OCR回退VL={}, "
        "OCR拥堵降级={}, OCR预算耗尽={}, VL成功={}",
        business_flow,
        stats.recognized,
        stats.total,
        stats.ocr_ok,
        stats.ocr_fallback,
        stats.ocr_degraded,
        stats.ocr_budget_exhausted,
        stats.vl_ok,
    )
    return stats
