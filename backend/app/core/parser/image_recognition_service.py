"""图片识别共享服务

收口 OCR 引擎、VL 客户端、自适应并发控制器的初始化与调用，
为投标审查、标书编写、串标审查提供统一的图片识别入口。

核心原则:
- 识别逻辑层（路由决策、OCR、VL prompt、后处理、结果写入）完全不动
- 本模块只统一"初始化 + 编排 + 重试"
- OCR 初始化失败时降级为纯 VL，不中断调用方
- 结果原地写入 ExtractedImage.visual_content
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.core.parser.image_extractor import ExtractedImage
from app.core.parser.image_analyzer import analyze_images, probe_vl_availability
from app.core.parser.ocr_engine import OcrEngine


@dataclass
class RecognitionStats:
    """图片识别批次统计"""
    total: int = 0
    recognized: int = 0
    ocr_ok: int = 0
    vl_ok: int = 0
    ocr_fallback: int = 0
    ocr_degraded: int = 0
    ocr_budget_exhausted: int = 0
    queued_vl: int = 0
    probe_skipped: bool = False


def get_ocr_engine() -> Optional[OcrEngine]:
    """统一获取 OCR 引擎单例

    内含 OCR_ENABLED 判定和初始化失败降级，调用方无需 try/except。

    Returns:
        OcrEngine 实例，不可用时返回 None（降级为纯 VL）
    """
    from app.core.config import settings

    if not settings.OCR_ENABLED:
        return None

    try:
        service_urls = None
        if settings.OCR_SERVICE_URLS:
            service_urls = [
                u.strip() for u in settings.OCR_SERVICE_URLS.split(",")
                if u.strip()
            ]

        engine = OcrEngine.get_instance(
            lang=settings.OCR_LANG,
            ocr_version=settings.OCR_VERSION,
            confidence_threshold=settings.OCR_CONFIDENCE_THRESHOLD,
            min_text_length=settings.OCR_MIN_TEXT_LENGTH,
            service_url=settings.OCR_SERVICE_URL,
            service_urls=service_urls,
            http_timeout=settings.OCR_HTTP_TIMEOUT,
            http_max_concurrency=settings.OCR_HTTP_MAX_CONCURRENCY,
            queue_warn_ms=settings.OCR_QUEUE_WARN_MS,
        )
        return engine
    except Exception as e:
        logger.warning(f"[图片识别] OCR引擎初始化失败(降级为纯VL): {e}")
        return None


@dataclass
class _VLDeps:
    """VL 识别依赖包"""
    vision_client: object
    controller: object
    available: bool = True


def get_vl_deps(
    purpose: str = "default",
    project_id: Optional[int] = None,
) -> _VLDeps:
    """统一获取 VL 客户端和自适应并发控制器

    Args:
        purpose: 控制器用途标签，用于日志区分

    Returns:
        _VLDeps，视觉调度池与遗留单选均未配置时 available=False
    """
    try:
        from app.core.ai import get_vision_client
        from app.core.ai_settings import load_ai_settings
        from app.core.ai.ai_factory import get_vision_dispatcher
        from app.core.ai_settings_purpose_ready import purpose_has_models
        from app.core.parser.adaptive_concurrency import AdaptiveConcurrency

        ai_settings = load_ai_settings()
        if not purpose_has_models(ai_settings, "vision"):
            logger.info(f"[图片识别/{purpose}] 未配置视觉模型，跳过VL")
            return _VLDeps(vision_client=None, controller=None, available=False)

        vision_client = get_vision_client(project_id=project_id)

        dispatcher = get_vision_dispatcher()
        vl_config_type = "cloud"
        if dispatcher and dispatcher.channel_count > 0:
            key_count = dispatcher.get_primary_group_channel_count()
        else:
            key_count = 1

        controller = AdaptiveConcurrency(
            config_type=vl_config_type,
            channel_count=key_count,
        )

        return _VLDeps(
            vision_client=vision_client,
            controller=controller,
            available=True,
        )
    except Exception as e:
        logger.error(f"[图片识别/{purpose}] VL依赖初始化失败: {e}")
        return _VLDeps(vision_client=None, controller=None, available=False)


async def recognize_images(
    images: list[ExtractedImage],
    *,
    progress_callback=None,
    purpose: str = "default",
    project_id: Optional[int] = None,
    max_retry_rounds: int = 2,
    probe_count: int = 2,
    offload_llm: bool = False,
) -> RecognitionStats:
    """统一图片识别入口

    封装 OCR/VL 初始化 + 探测 + 识别 + 重试，
    结果原地写入 ExtractedImage.visual_content。

    Args:
        images: 待识别图片列表
        progress_callback: 每完成一张的回调
        purpose: 日志标签
        max_retry_rounds: 最大重试轮数
        probe_count: VL 前置探测样本数

    Returns:
        RecognitionStats 统计信息
    """
    analyzable = [img for img in images if img.file_path]
    if not analyzable:
        logger.info(f"[图片识别/{purpose}] 无可识别图片，跳过")
        return RecognitionStats()

    if offload_llm and project_id is not None:
        return await recognize_images_with_llm_subtasks(
            images=analyzable,
            project_id=project_id,
            progress_callback=progress_callback,
            purpose=purpose,
            max_retry_rounds=max_retry_rounds,
        )

    ocr_engine = get_ocr_engine()
    vl = get_vl_deps(purpose=purpose, project_id=project_id)
    flow_id = f"project:{project_id}" if project_id is not None else "default"

    if not vl.available:
        return RecognitionStats(total=len(analyzable), probe_skipped=True)

    mode_tag = "OCR+VL混合" if ocr_engine else "纯VL"
    logger.info(
        f"[图片识别/{purpose}] 开始: {len(analyzable)}张图片, "
        f"模式={mode_tag}, 并发={vl.controller.current}"
    )

    probe_ok, probe_reason = await probe_vl_availability(
        scan_pages=[],
        per_images=analyzable,
        vision_client=vl.vision_client,
        probe_count=probe_count,
    )
    if not probe_ok:
        logger.error(
            f"[图片识别/{purpose}] VL探测失败: {probe_reason}, 跳过识别"
        )
        return RecognitionStats(
            total=len(analyzable), probe_skipped=True,
        )

    _ocr_ok_total = 0

    ai_stats = await analyze_images(
        images=analyzable,
        vision_client=vl.vision_client,
        controller=vl.controller,
        progress_callback=progress_callback,
        ocr_engine=ocr_engine,
        flow_id=flow_id,
    )
    if ai_stats:
        _ocr_ok_total += ai_stats.get("ocr_ok", 0)

    for retry_round in range(1, max_retry_rounds + 1):
        failed = [
            img for img in analyzable
            if not img.visual_content and img.file_path
        ]
        if not failed:
            break
        vl.controller.reset_window()
        logger.info(
            f"[图片识别/{purpose}] 重试第{retry_round}轮: {len(failed)}张"
        )
        retry_stats = await analyze_images(
            images=failed,
            vision_client=vl.vision_client,
            controller=vl.controller,
            progress_callback=progress_callback,
            ocr_engine=ocr_engine,
            flow_id=flow_id,
        )
        if retry_stats:
            _ocr_ok_total += retry_stats.get("ocr_ok", 0)

    recognized = sum(1 for img in analyzable if img.visual_content)
    logger.info(
        f"[图片识别/{purpose}] 完成: {recognized}/{len(analyzable)}张成功"
        + (f", OCR直接={_ocr_ok_total}" if _ocr_ok_total else "")
    )

    return RecognitionStats(
        total=len(analyzable),
        recognized=recognized,
        ocr_ok=_ocr_ok_total,
    )


async def recognize_images_with_llm_subtasks(
    images: list[ExtractedImage],
    *,
    project_id: int,
    progress_callback=None,
    purpose: str = "default",
    max_retry_rounds: int = 2,
) -> RecognitionStats:
    """OCR-first 本地执行, 仅将 VL 模型调用派发给 LLM worker。"""

    from app.core.config import settings as _settings
    from app.core.parser.image_analyzer import analyze_image_ocr_first, ocr_priority
    from app.core.tasks.writing_image_llm_task import (
        WRITING_IMAGE_VL_JOB,
        enqueue_image_llm_job,
        image_to_vl_item,
        make_image_key,
        wait_for_image_llm_result,
    )

    analyzable = [img for img in images if img.file_path]
    if not analyzable:
        logger.info(f"[图片识别/{purpose}] 无可识别图片，跳过")
        return RecognitionStats()

    ocr_engine = get_ocr_engine()
    stats = RecognitionStats(total=len(analyzable))
    needs_vl: list[tuple[ExtractedImage, str, str]] = []
    ocr_budget_max = int(getattr(_settings, "OCR_BUDGET_PER_BATCH", 50))
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
        "[图片识别/{}] 开始 OCR-first + LLM 子任务: {}张图片",
        purpose,
        len(analyzable),
    )

    for idx, img in enumerate(analyzable):
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
            # 计费计量: 动态标记OCR产出,供写标素材批处理按素材归集计费
            img._recognized_via = "ocr"
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
        if not remaining:
            break
        batch_id = f"writing-image-vl-{project_id}-{uuid.uuid4().hex}"
        items = [
            image_to_vl_item(img, image_key, route_hint)
            for img, image_key, route_hint in remaining
        ]
        queued, reason = await enqueue_image_llm_job(
            function_name=WRITING_IMAGE_VL_JOB,
            project_id=project_id,
            batch_id=batch_id,
            items=items,
        )
        if not queued:
            logger.warning(
                "[图片识别/{}] VL子任务未入队, 降级跳过: reason={}, items={}",
                purpose,
                reason,
                len(items),
            )
            if progress_callback:
                for img, _image_key, _route_hint in remaining:
                    progress_callback(img, False)
            break

        stats.queued_vl += len(items)
        result = await wait_for_image_llm_result(batch_id, project_id=project_id)
        if result.get("status") != "completed":
            logger.warning(
                "[图片识别/{}] VL子任务未完成: batch={}, status={}, reason={}",
                purpose,
                batch_id,
                result.get("status"),
                result.get("reason"),
            )
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
        "[图片识别/{}] 完成: {}/{}张成功, OCR直接={}, OCR回退VL={}, "
        "OCR拥堵降级={}, OCR预算耗尽={}, VL成功={}",
        purpose,
        stats.recognized,
        stats.total,
        stats.ocr_ok,
        stats.ocr_fallback,
        stats.ocr_degraded,
        stats.ocr_budget_exhausted,
        stats.vl_ok,
    )
    return stats
