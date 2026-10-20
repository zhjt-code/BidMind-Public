"""写标素材图片 LLM 子任务。

该模块承接素材批处理节点内部的模型调用:
- VL fallback: 仅处理 OCR 无法直接产出可信文本的图片;
- image classify: 基于已识别文本做图片类型/业务子类/结构化字段提取。

子任务通过 Redis 写回批次结果, 调用方等待结果后继续原素材批处理流程。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from app.core.config import settings
from app.core.arq_runtime import ProjectCancelledError, is_project_cancelled
from app.core.parser.image_extractor import ExtractedImage

WRITING_IMAGE_VL_JOB = "writing_image_vl_job"
WRITING_IMAGE_CLASSIFY_JOB = "writing_image_classify_job"

_RESULT_KEY_PREFIX = "bidmind:writing:image_llm:"
_DEFAULT_RESULT_TTL = 3600
_DEFAULT_WAIT_TIMEOUT = 600.0
_DEFAULT_POLL_INTERVAL = 0.5
_MAX_CLASSIFY_CONCURRENCY = 4


def result_key(batch_id: str) -> str:
    """返回图片 LLM 子任务批次结果键。"""

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


def image_to_vl_item(image: object, image_key: str, route_hint: str = "vl") -> dict[str, Any]:
    """把 ExtractedImage 转成可跨 ARQ 传输的轻量 payload。"""

    return {
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


async def enqueue_image_llm_job(
    *,
    function_name: str,
    project_id: int,
    batch_id: str,
    items: list[dict[str, Any]],
) -> tuple[bool, str]:
    """投递图片 LLM 子任务, 返回 (queued, reason)。"""

    if not items:
        return True, "empty"

    from app.core.arq_runtime import enqueue_job_detailed

    dispatch = await enqueue_job_detailed(
        function_name,
        project_id,
        batch_id,
        items,
    )
    if dispatch.queued:
        return True, dispatch.reason
    return False, dispatch.reason


async def wait_for_image_llm_result(
    batch_id: str,
    *,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    project_id: int | None = None,
) -> dict[str, Any]:
    """等待图片 LLM 子任务写回 Redis 结果。"""

    from app.core.redis import get_redis_pool

    redis = await get_redis_pool()
    if redis is None:
        return {"status": "failed", "reason": "redis_unavailable", "items": []}

    resolved_timeout = float(
        timeout
        if timeout is not None
        else getattr(settings, "WRITING_IMAGE_LLM_WAIT_TIMEOUT", _DEFAULT_WAIT_TIMEOUT)
    )
    resolved_poll = max(
        float(
            poll_interval
            if poll_interval is not None
            else getattr(settings, "WRITING_IMAGE_LLM_POLL_INTERVAL", _DEFAULT_POLL_INTERVAL)
        ),
        0.1,
    )
    deadline = time.monotonic() + resolved_timeout
    key = result_key(batch_id)

    while time.monotonic() < deadline:
        if project_id is not None:
            _raise_if_cancelled(project_id)
        raw = await redis.get(key)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"status": "failed", "reason": "invalid_result_json", "items": []}
        await asyncio.sleep(resolved_poll)

    return {"status": "timeout", "reason": "wait_timeout", "items": []}


def _raise_if_cancelled(project_id: int) -> None:
    if is_project_cancelled(project_id):
        raise ProjectCancelledError(project_id)


async def _write_result(
    redis: object,
    batch_id: str,
    payload: dict[str, Any],
) -> None:
    ttl = int(getattr(settings, "WRITING_IMAGE_LLM_RESULT_TTL", _DEFAULT_RESULT_TTL))
    data = json.dumps(payload, ensure_ascii=False)
    await redis.setex(result_key(batch_id), ttl, data)  # type: ignore[attr-defined]


async def run_image_vl_batch(
    ctx: dict,
    project_id: int,
    batch_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """LLM worker 执行 VL fallback 批次。"""

    redis = ctx.get("redis")
    results: list[dict[str, Any]] = []
    try:
        _raise_if_cancelled(project_id)
        from app.core.ai import get_vision_client
        from app.core.parser.image_analyzer import analyze_image_vl_only

        vision_client = get_vision_client(project_id=project_id)
        for item in items:
            _raise_if_cancelled(project_id)
            image_key = str(item.get("image_key") or "")
            try:
                image = item_to_image(item)
                content, route = await analyze_image_vl_only(
                    image,
                    vision_client,
                    route_label=str(item.get("route_hint") or "vl"),
                )
                _raise_if_cancelled(project_id)
                results.append({
                    "image_key": image_key,
                    "content": content,
                    "route": route,
                    "success": bool(content),
                })
            except ProjectCancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "image_key": image_key,
                    "content": "",
                    "route": str(item.get("route_hint") or "vl"),
                    "success": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                })

        payload = {"status": "completed", "items": results}
    except ProjectCancelledError:
        payload = {"status": "cancelled", "reason": "project_cancelled", "items": results}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "图片 VL 子任务批次失败: project={}, batch={}, err={}",
            project_id,
            batch_id,
            exc,
        )
        payload = {
            "status": "failed",
            "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
            "items": results,
        }

    if redis is not None:
        _raise_if_cancelled(project_id)
        await _write_result(redis, batch_id, payload)
    return payload


async def classify_image_items_with_llm(
    llm_client: Any,
    items: list[dict[str, Any]],
    *,
    project_id: int,
    concurrency: int = _MAX_CLASSIFY_CONCURRENCY,
) -> list[Optional[dict[str, Any]]]:
    """有界并发调用图片分类 LLM，不访问数据库。

    返回与 items 等长的列表；缺 image_id/vl 的项为 None。
    """
    from app.core.ai.prompt_registry import resolve_runtime_params
    from app.core.writer.prompts.image_classify import get_image_classify_prompt

    semaphore = asyncio.Semaphore(max(int(concurrency), 1))

    async def _classify_one(item: dict[str, Any]) -> Optional[dict[str, Any]]:
        image_id = int(item.get("image_id") or 0)
        vl_text = str(item.get("vl_content") or "")
        if not image_id or not vl_text:
            return None
        async with semaphore:
            _raise_if_cancelled(project_id)
            try:
                sys_msg, usr_msg = get_image_classify_prompt(
                    material_title=str(item.get("material_title") or ""),
                    image_index=int(item.get("image_index") or 0) + 1,
                    width=int(item.get("width") or 0),
                    height=int(item.get("height") or 0),
                    vl_content=vl_text,
                )
                temperature, max_tokens = resolve_runtime_params(
                    "writing.image_classify", 0.1, 1500,
                )
                result = await llm_client.chat_json(
                    user_message=usr_msg,
                    system_message=sys_msg,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                _raise_if_cancelled(project_id)
                if not isinstance(result, dict):
                    raise ValueError("image_classify_result_not_dict")
                return {
                    "success": True,
                    "image_id": image_id,
                    "payload": result,
                    "vl_text": vl_text,
                }
            except ProjectCancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                return {
                    "success": False,
                    "image_id": image_id,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                }

    return list(await asyncio.gather(*(_classify_one(item) for item in items)))


async def run_image_classify_batch(
    ctx: dict,
    project_id: int,
    batch_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """LLM worker 执行图片分类与结构化批次, 并直接更新 DB 记录。"""

    redis = ctx.get("redis")
    stats = {
        "created": len(items),
        "classified": 0,
        "validated": 0,
        "needs_review": 0,
        "unknown": 0,
        "invalid_subtype": 0,
    }
    results: list[dict[str, Any]] = []

    try:
        _raise_if_cancelled(project_id)
        from app.core.ai import get_review_client
        from app.core.database import async_session_factory
        from app.core.writer.image_subtype_contract import (
            NEEDS_REVIEW_SUBTYPE,
            UNKNOWN_SUBTYPE,
            normalize_business_subtype,
            validate_business_subtype,
        )
        from app.models.writing_material_image import ImageType, WritingMaterialImage

        llm_client = get_review_client(project_id=project_id)
        llm_results = await classify_image_items_with_llm(
            llm_client, items, project_id=project_id,
        )
        async with async_session_factory() as db:
            for item, llm_result in zip(items, llm_results, strict=True):
                _raise_if_cancelled(project_id)
                if llm_result is None:
                    continue
                image_id = int(llm_result.get("image_id") or 0)
                record = await db.get(WritingMaterialImage, image_id)
                if record is None:
                    results.append({
                        "image_id": image_id,
                        "success": False,
                        "error": "image_record_missing",
                    })
                    continue
                if not llm_result.get("success"):
                    record.image_type = ImageType.other
                    record.business_subtype = "unknown"
                    stats["unknown"] += 1
                    results.append({
                        "image_id": image_id,
                        "success": False,
                        "error": str(llm_result.get("error") or "classify_failed"),
                    })
                    continue

                payload = llm_result.get("payload") or {}
                vl_text = str(llm_result.get("vl_text") or item.get("vl_content") or "")
                raw_type = payload.get("image_type", "other")
                try:
                    record.image_type = ImageType(raw_type)
                except ValueError:
                    record.image_type = ImageType.other
                record.caption = payload.get("caption", "")[:500] or None

                sf = payload.get("structured_fields")
                if sf and isinstance(sf, dict):
                    record.structured_fields = json.dumps(sf, ensure_ascii=False)

                raw_subtype = payload.get("business_subtype")
                normalized = normalize_business_subtype(raw_subtype)
                if (
                    isinstance(raw_subtype, str)
                    and raw_subtype.strip()
                    and normalized == UNKNOWN_SUBTYPE
                    and raw_subtype.strip().lower() not in {UNKNOWN_SUBTYPE, NEEDS_REVIEW_SUBTYPE}
                ):
                    stats["invalid_subtype"] += 1

                final_subtype, reasons = validate_business_subtype(
                    normalized,
                    vl_text=vl_text,
                    caption=record.caption or "",
                    structured_fields=sf if isinstance(sf, dict) else None,
                )
                record.business_subtype = final_subtype[:50]
                if final_subtype == UNKNOWN_SUBTYPE:
                    stats["unknown"] += 1
                elif final_subtype == NEEDS_REVIEW_SUBTYPE:
                    stats["needs_review"] += 1
                else:
                    stats["validated"] += 1

                stats["classified"] += 1
                results.append({
                    "image_id": image_id,
                    "success": True,
                    "business_subtype": final_subtype,
                    "reasons": reasons,
                })
            _raise_if_cancelled(project_id)
            await db.commit()

        payload = {"status": "completed", "stats": stats, "items": results}
    except ProjectCancelledError:
        payload = {
            "status": "cancelled",
            "reason": "project_cancelled",
            "stats": stats,
            "items": results,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "图片分类子任务批次失败: project={}, batch={}, err={}",
            project_id,
            batch_id,
            exc,
        )
        payload = {
            "status": "failed",
            "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
            "stats": stats,
            "items": results,
        }

    if redis is not None:
        _raise_if_cancelled(project_id)
        await _write_result(redis, batch_id, payload)
    return payload


def file_exists_for_item(item: dict[str, Any]) -> bool:
    """测试与调用方可复用的轻量文件存在性判断。"""

    file_path = item.get("file_path")
    return bool(file_path and Path(str(file_path)).exists())
