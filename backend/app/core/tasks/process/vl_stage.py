"""文档处理阶段2: VL 视觉识别(无 DB 连接)

整段流程:
1. 加载 ai_settings + 初始化 OCR 引擎(可降级) + 初始化自适应并发控制器
2. VL 探测(probe): 失败则跳过 VL 阶段, 不抛异常
3. 扫描密集页判定(is_scan_heavy_page): 走整页 OCR/VL 兜底; 其他页走逐图 LLM
4. 两阶段并行: per_image + scan_heavy 异质资源并行(return_exceptions=True)
5. 整页 OCR 并发上限 _compute_scan_concurrency 给 per_image 留 1 槽(防御)
6. 失败项重试两轮(per_image + scan_heavy 都重试)
7. VL 质量门禁: 扫描页占比高且 VL 成功率低 → 抛 _VLQualityError(由主编排器外层 except 转 FAILED)

行为契约(前端依赖):
- vl_phase 字面量: round1_running / round1_done / retry_running / done
- 心跳 extra 字段: recognized / attempted / failed_pending / retry_round / vl_phase
 / vl_current_round / vl_round_done / vl_round_total / vl_retry_pending
 / vl_unrecognized_final / manual_review_required / unrecognized_images / unrecognized_pages
 / vl_concurrency / vl_p95_ms / vl_adjust_desc

异常策略(与原闭包等价):
- _VLQualityError: 透传给主编排器(走 FAILED + ProjectStatus.FAILED)
- 其他异常: 内部捕获 + warning 日志, 降级让流程继续(VL 仅是增强信号)
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from app.core.tasks.process.state import VLPhaseState, VLStageResult, image_key


async def run_vl_stage(
    *,
    parsed_doc: Any,
    file_path: str,
    doc_project_id: int,
    business_flow: str,
    image_count: int,
    task_name: str,
) -> VLStageResult:
    """执行 VL 阶段(若无图片或视觉调度池/遗留单选均未配置, 直接 early return).

    返回 total_ocr_ok / vl_count / unrecognized_count, 由主编排器透传给 vectorize_stage。
    _VLQualityError 透传, 其他异常吞掉降级。
    """
    # 函数内 import: 兼容外部测试 monkeypatch + 防循环依赖
    from app.core.ai_settings import load_ai_settings
    from app.core.arq_runtime import ProjectCancelledError, raise_if_project_cancelled
    from app.core.tasks.cancellation import rethrow_project_cancelled
    from app.core.config import settings
    from app.core.parser.adaptive_concurrency import AdaptiveConcurrency
    from app.core.parser.page_image_evidence import bind_full_page_visual_content
    from app.core.parser.page_renderer import is_scan_heavy_page
    from app.core.tasks import process_task as ptm
    from app.core.tasks.document_image_llm_task import (
        FullPageVlRequest,
        analyze_full_pages_with_document_llm_subtasks,
        probe_document_vl_availability,
        recognize_images_with_document_llm_subtasks,
    )

    all_images = parsed_doc.all_images
    state = VLPhaseState(image_count=image_count, vl_round_total=image_count)

    # 默认返回值: 没图片 / VL 跳过时也要给主编排器一个 0 计数结果
    default_result = VLStageResult(total_ocr_ok=0, vl_count=0, unrecognized_count=0)

    if image_count == 0:
        logger.info("[文档处理] 阶段2: 跳过VL识别(无图片)")
        return default_result

    try:
        ai_settings = load_ai_settings()
        from app.core.ai_settings_purpose_ready import purpose_has_models

        if not purpose_has_models(ai_settings, "vision"):
            logger.info("[文档处理] 阶段2: 跳过VL识别(未配置视觉模型)")
            return default_result

        logger.info(f"[文档处理] 阶段2: VL识别 ({image_count}张图片)")
        from app.core.tasks import update_heartbeat
        update_heartbeat(
            task_name, stage="vl_recognition",
            progress=f"0/{image_count}"
        )

        # 初始化 OCR 引擎(共享模块统一获取, 含降级保护)
        from app.core.parser.image_recognition_service import get_ocr_engine
        ocr_engine_instance = get_ocr_engine()
        if ocr_engine_instance:
            _ocr_init_stats = ocr_engine_instance.get_http_stats()
            logger.info(
                f"[文档处理] OCR引擎已就绪, 文字密集型图片将优先走OCR "
                f"(mode={_ocr_init_stats.get('mode', '?')}, "
                f"pool={_ocr_init_stats.get('pool_healthy', '?')}"
                f"/{_ocr_init_stats.get('pool_total', '?')}, "
                f"concurrency={_ocr_init_stats.get('concurrency', '?')})"
            )

        vision_config = ai_settings.get_config_by_id(
            "vision", ai_settings.vision_model_id
        )
        from app.core.ai.ai_factory import get_vision_dispatcher
        vl_config_type = (
            "cloud"
            if vision_config and vision_config.config_type == "cloud"
            else "local"
        )
        _vl_dispatcher = get_vision_dispatcher()
        vl_key_count = (
            _vl_dispatcher.get_primary_group_channel_count()
            if _vl_dispatcher and _vl_dispatcher.channel_count > 0
            else 1
        )
        vl_controller = AdaptiveConcurrency(
            config_type=vl_config_type,
            channel_count=vl_key_count,
        )

        vl_log_interval = max(10, image_count // 10)

        def _refresh_vl_status(
            phase_override: str | None = None,
            current_round_override: int | None = None,
            round_done_override: int | None = None,
            round_total_override: int | None = None,
            retry_pending_override: int | None = None,
            unrecognized_final_override: int | None = None,
            manual_review_required: bool = False,
            unrecognized_images: int = 0,
            unrecognized_pages: int = 0,
        ) -> tuple[int, int, int]:
            phase = phase_override if phase_override is not None else state.vl_phase
            current_round = (
                current_round_override
                if current_round_override is not None
                else state.vl_current_round
            )
            recognized = len(state.vl_recognized_keys)
            attempted = len(state.vl_attempted_keys)
            failed_pending = max(state.image_count - recognized, 0)
            round_done = (
                round_done_override
                if round_done_override is not None
                else len(state.vl_round_done_keys)
            )
            round_total = (
                round_total_override
                if round_total_override is not None
                else state.vl_round_total
            )
            if retry_pending_override is not None:
                retry_pending = retry_pending_override
            elif phase == "retry_running":
                retry_pending = max(round_total - round_done, 0)
            else:
                retry_pending = state.vl_retry_pending

            unrecognized_final = (
                unrecognized_final_override
                if unrecognized_final_override is not None
                else state.vl_unrecognized_final
            )
            vl_stats = vl_controller.get_stats()
            update_heartbeat(
                task_name,
                stage="vl_recognition",
                progress=f"{recognized}/{state.image_count}",
                extra={
                    "recognized": recognized,
                    "attempted": attempted,
                    "failed_pending": failed_pending,
                    "retry_round": state.vl_retry_round,
                    "vl_phase": phase,
                    "vl_current_round": current_round,
                    "vl_round_done": round_done,
                    "vl_round_total": round_total,
                    "vl_retry_pending": retry_pending,
                    "vl_unrecognized_final": unrecognized_final,
                    "manual_review_required": manual_review_required,
                    "unrecognized_images": unrecognized_images,
                    "unrecognized_pages": unrecognized_pages,
                    "vl_concurrency": vl_stats["concurrency"],
                    "vl_p95_ms": vl_stats["p95_ms"],
                    "vl_adjust_desc": vl_stats["last_adjust"],
                },
            )
            return recognized, attempted, failed_pending

        def on_vl_progress(images: list, success: bool) -> None:
            for img in images:
                key = image_key(img)
                state.vl_attempted_keys.add(key)
                state.vl_round_done_keys.add(key)
                if success:
                    state.vl_recognized_keys.add(key)

            recognized, attempted, _ = _refresh_vl_status()
            if attempted % vl_log_interval == 0 or attempted == state.image_count:
                logger.info(
                    f"[文档处理] VL识别进度(成功/总): "
                    f"{recognized}/{state.image_count}, 已尝试={attempted}, "
                    f"阶段={state.vl_phase}, 轮次={state.vl_current_round}, "
                    f"本轮={len(state.vl_round_done_keys)}/{state.vl_round_total}"
                )

        def vl_keepalive() -> None:
            """: LLM worker 长 batch 等待期间的心跳保活回调.

 在 wait_for_document_image_llm_result polling 期间被定期调用,
 刷新 ARQ 业务心跳, 避免被 API 误判 running_heartbeat_stale。
 复用 _refresh_vl_status, 不引入新心跳路径。
 """
            try:
                _refresh_vl_status()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[文档处理] VL心跳保活异常(已忽略): {exc}")

        def _page_clip_map(page_nums: list[int]) -> dict[int, tuple[float, float, float, float]]:
            metadata = getattr(parsed_doc, "metadata", {}) or {}
            raw_map = metadata.get("page_chrome_body_bboxes") or {}
            result: dict[int, tuple[float, float, float, float]] = {}
            for page_num in page_nums:
                raw = raw_map.get(str(page_num)) or raw_map.get(page_num)
                if not raw or len(raw) != 4:
                    continue
                try:
                    result[page_num] = tuple(float(v) for v in raw)  # type: ignore[assignment]
                except Exception:
                    continue
            return result

        def _check_project_cancelled() -> None:
            raise_if_project_cancelled(doc_project_id)

        _refresh_vl_status()
        _check_project_cancelled()

        # 2a. 识别扫描密集页, 用整页渲染 VL 替代逐图 VL
        scan_heavy_page_nums: set[int] = set()
        for p in parsed_doc.pages:
            if p.images and is_scan_heavy_page(p.text or "", p.images):
                scan_heavy_page_nums.add(p.page_num)

        per_image_list = [
            img for img in all_images
            if img.page_num not in scan_heavy_page_nums
        ]

        # VL 前置探测: 快速检测视觉模型是否正常响应
        vl_probe_failed = False
        probe_scan_data = []
        if scan_heavy_page_nums:
            _check_project_cancelled()
            probe_page = next(
                (p for p in parsed_doc.pages
                 if p.page_num in scan_heavy_page_nums),
                None,
            )
            if probe_page:
                prev_idx = probe_page.page_num - 2
                probe_ctx = ""
                if 0 <= prev_idx < len(parsed_doc.pages):
                    probe_ctx = (
                        parsed_doc.pages[prev_idx].text or ""
                    )
                probe_render = await asyncio.to_thread(
                    ptm._render_pages_sync,
                    file_path,
                    [(probe_page.page_num, probe_ctx)],
                    200,
                    _page_clip_map([probe_page.page_num]),
                )
                probe_b64, _ = probe_render[probe_page.page_num]
                from types import SimpleNamespace
                probe_scan_data = [SimpleNamespace(
                    rendered_b64=probe_b64,
                    page_num=probe_page.page_num,
                )]

        probe_ok, probe_reason = await probe_document_vl_availability(
            project_id=doc_project_id,
            business_flow=business_flow,
            scan_pages=probe_scan_data,
            per_images=per_image_list,
            probe_count=settings.VL_PROBE_COUNT,
            pdf_path=file_path,
        )
        if not probe_ok:
            logger.error(
                f"[文档处理] VL探测失败: {probe_reason}, "
                f"model={getattr(vision_config, 'model_name', 'unknown')}, "
                f"跳过VL阶段"
            )
            vl_probe_failed = True

        # ── 整页 OCR 兜底子函数(机会型优先策略) ──
        async def _try_ocr_full_page(page_b64: str, page_num: int) -> str:
            """尝试用 OCR 识别整页渲染图, 返回空串表示需要回退 VL.

            机会型优先策略: OCR 快且稳时优先, 慢或堵时快速让给 VL。
            即时回退信号(不依赖历史窗口):
            - OCR 池健康实例不足
            - 历史拥堵信号 + 等待恢复超时
            - OCR 信号量当前全满(所有槽位被占)
            """
            _check_project_cancelled()
            if not ocr_engine_instance:
                return ""

            _rt_stats = ocr_engine_instance.get_http_stats()
            _rt_healthy = _rt_stats.get("pool_healthy", 0)
            _rt_total = _rt_stats.get("pool_total", 0)
            if _rt_total > 0 and _rt_healthy == 0:
                state.scan_ocr_degraded_before += 1
                logger.info(
                    f"[OCR整页->VL] page={page_num} "
                    f"OCR池全部不健康({_rt_healthy}/{_rt_total}), 直接VL"
                )
                return ""

            if ocr_engine_instance.is_congested:
                from app.core.config import settings as _cfg
                recovered = await ocr_engine_instance.wait_for_recovery(
                    timeout=_cfg.OCR_STEAL_BACK_WAIT_SEC,
                )
                if not recovered:
                    state.scan_ocr_degraded_before += 1
                    logger.info(
                        f"[OCR整页->VL] page={page_num} OCR持续拥堵, 降级VL"
                    )
                    return ""
                logger.info(
                    f"[OCR整页] page={page_num} OCR拥堵已恢复, 继续OCR"
                )

            state.scan_ocr_attempted += 1
            try:
                _check_project_cancelled()
                import base64 as _b64
                import io
                import numpy as np
                from PIL import Image
                png_bytes = _b64.b64decode(page_b64)
                pil_img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
                np_img = np.array(pil_img)
                ocr_result = await ocr_engine_instance.recognize_from_array(
                    np_img,
                    request_class="full_page",
                    flow_id=f"project:{doc_project_id}",
                )
                _check_project_cancelled()
                if ocr_result.is_confident:
                    from app.core.parser.ocr_postprocessor import postprocess_ocr
                    text = postprocess_ocr(ocr_result)
                    if text:
                        state.scan_ocr_ok += 1
                        logger.info(
                            f"[OCR整页] page={page_num} 识别成功, "
                            f"conf={ocr_result.avg_confidence:.2f}, len={len(text)}"
                        )
                        return text
                state.scan_ocr_degraded_after += 1
                logger.info(
                    f"[OCR整页->VL] page={page_num} OCR置信度不足, "
                    f"conf={ocr_result.avg_confidence:.2f}, "
                    f"text_len={len(ocr_result.raw_text)}, 回退VL"
                )
            except Exception as e:
                state.scan_ocr_degraded_after += 1
                logger.warning(f"[OCR整页->VL] page={page_num} OCR异常, 回退VL: {e}")
            return ""

        # ── 两阶段并行: per_image + scan_heavy 异质资源并行 ──
        # 非扫描密集页优先逐图识别, 让前端更早看到识别进度增长。
        # 扫描密集页仍保留整页 OCR/VL 兜底, 以避免 fitz 逐图 tiles 碎片问题。
        # per_image 走远端 LLM API 通道, scan_heavy 走本地 OCR 池 + LLM 兜底,
        # 资源类型异质, 并行让 OCR 池前期不再空转。
        scan_render_cache: dict[int, tuple[str, str]] = {}

        async def _run_per_image_phase() -> None:
            if not (per_image_list and not vl_probe_failed):
                return
            _check_project_cancelled()
            _ai_stats = await recognize_images_with_document_llm_subtasks(
                images=per_image_list,
                project_id=doc_project_id,
                business_flow=business_flow,
                progress_callback=lambda img, success: on_vl_progress([img], bool(success)),
                ocr_engine=ocr_engine_instance,
                keepalive_callback=vl_keepalive,
            )
            state.total_ocr_ok += _ai_stats.ocr_ok

        async def _run_scan_heavy_phase() -> None:
            if not (scan_heavy_page_nums and not vl_probe_failed):
                return
            _check_project_cancelled()
            _ocr_stats = (
                ocr_engine_instance.get_http_stats()
                if ocr_engine_instance else {}
            )
            _pool_healthy = _ocr_stats.get("pool_healthy", 0)
            _pool_total = _ocr_stats.get("pool_total", 0)
            # 防御: 整页并发上限必须给 per_image 阶段预留 OCR 槽
            _scan_concurrency = ptm._compute_scan_concurrency(_pool_healthy)
            logger.info(
                f"[文档处理] 扫描密集页: {len(scan_heavy_page_nums)}页, "
                f"整页OCR/VL兜底(VL并发={vl_controller.current}, "
                f"OCR池={_pool_healthy}/{_pool_total}, "
                f"整页并发上限={_scan_concurrency}, 预留1槽给per_image)"
            )
            _scan_sem = asyncio.Semaphore(_scan_concurrency)
            try:
                scan_pages = [
                    p for p in parsed_doc.pages
                    if p.page_num in scan_heavy_page_nums
                ]

                page_render_list: list[tuple[int, str]] = []
                for p in scan_pages:
                    _check_project_cancelled()
                    prev_idx = p.page_num - 2
                    ctx = ""
                    if 0 <= prev_idx < len(parsed_doc.pages):
                        ctx = parsed_doc.pages[prev_idx].text or ""
                    page_render_list.append((p.page_num, ctx))

                rendered = await asyncio.to_thread(
                    ptm._render_pages_sync,
                    file_path,
                    page_render_list,
                    200,
                    _page_clip_map([page_num for page_num, _ctx in page_render_list]),
                )
                # 用 update 保留外层引用, 让重试阶段能继续读取缓存
                scan_render_cache.update(rendered)

                page_data: list[tuple] = []
                for p in scan_pages:
                    _check_project_cancelled()
                    b64, ctx = scan_render_cache[p.page_num]
                    page_data.append((p, b64, ctx))

                async def _analyze_page(p, page_b64, ctx):
                    _check_project_cancelled()
                    async with _scan_sem:
                        _check_project_cancelled()
                        ocr_content = await _try_ocr_full_page(page_b64, p.page_num)
                    _check_project_cancelled()
                    if ocr_content:
                        state.total_ocr_ok += 1
                        bind_full_page_visual_content(p, ocr_content)
                        on_vl_progress(p.images, True)
                        return None
                    return (p, page_b64, ctx)

                _ocr_results = await asyncio.gather(
                    *[_analyze_page(p, b64, ctx) for p, b64, ctx in page_data],
                    return_exceptions=True,
                )
                rethrow_project_cancelled(_ocr_results)
                vl_targets = [
                    result for result in _ocr_results
                    if isinstance(result, tuple) and len(result) == 3
                ]
                page_by_num = {p.page_num: p for p, _b64, _ctx in vl_targets}
                reported_full_pages: set[int] = set()

                def _on_full_page_progress(page_num: int, content: str) -> None:
                    page = page_by_num.get(page_num)
                    if page is None:
                        return
                    reported_full_pages.add(page_num)
                    if content:
                        bind_full_page_visual_content(page, content)
                    on_vl_progress(page.images, bool(content))

                vl_results = await analyze_full_pages_with_document_llm_subtasks(
                    project_id=doc_project_id,
                    business_flow=business_flow,
                    pages=[
                        FullPageVlRequest(
                            page_b64=b64,
                            page_num=p.page_num,
                            context=ctx,
                        )
                        for p, b64, ctx in vl_targets
                    ],
                    keepalive_callback=vl_keepalive,
                    progress_callback=_on_full_page_progress,
                )
                for page_num, p in page_by_num.items():
                    if page_num in reported_full_pages:
                        continue
                    content = vl_results.get(page_num, "")
                    if content:
                        bind_full_page_visual_content(p, content)
                    on_vl_progress(p.images, bool(content))
            except ProjectCancelledError:
                raise
            except Exception as e:
                logger.warning(f"[文档处理] 整页渲染VL失败(降级): {e}")
            logger.info(
                f"[文档处理] 整页OCR统计: "
                f"attempted={state.scan_ocr_attempted}, "
                f"ok={state.scan_ocr_ok}, "
                f"degraded_before_attempt={state.scan_ocr_degraded_before}, "
                f"degraded_after_attempt={state.scan_ocr_degraded_after}"
            )

        # 异质资源并行: 任一阶段异常不影响另一阶段(return_exceptions=True)
        _phase_results = await asyncio.gather(
            _run_per_image_phase(),
            _run_scan_heavy_phase(),
            return_exceptions=True,
        )
        for _idx, _result in enumerate(_phase_results):
            if isinstance(_result, BaseException):
                if isinstance(_result, ProjectCancelledError):
                    raise _result
                _phase_name = "per_image" if _idx == 0 else "scan_heavy"
                logger.warning(
                    f"[文档处理] {_phase_name} 阶段异常: "
                    f"{type(_result).__name__}: {_result}"
                )

        def _collect_retry_targets() -> tuple[list, list]:
            retry_scan_local = [
                p for p in parsed_doc.pages
                if p.page_num in scan_heavy_page_nums
                and p.images
                and not any(img.visual_content for img in p.images)
            ]
            retry_per_local = [
                img for img in (per_image_list if per_image_list else [])
                if img.file_path and not img.visual_content
            ]
            return retry_scan_local, retry_per_local

        def _count_retry_images(retry_scan_local: list, retry_per_local: list) -> int:
            retry_scan_images = sum(len(p.images) for p in retry_scan_local)
            return retry_scan_images + len(retry_per_local)

        retry_scan_after_round1, retry_per_after_round1 = _collect_retry_targets()
        state.vl_retry_pending = _count_retry_images(
            retry_scan_after_round1, retry_per_after_round1
        )
        state.vl_phase = "round1_done"
        _refresh_vl_status(
            phase_override=state.vl_phase,
            current_round_override=1,
            round_done_override=len(state.vl_round_done_keys),
            round_total_override=state.vl_round_total,
            retry_pending_override=state.vl_retry_pending,
        )
        logger.info(
            f"[文档处理] VL第一轮完成: 成功={len(state.vl_recognized_keys)}/{state.image_count}, "
            f"待重试={state.vl_retry_pending}"
        )

        failed_scan_page_nums = {
            p.page_num for p in retry_scan_after_round1
        }
        scan_render_cache = {
            k: v for k, v in scan_render_cache.items()
            if k in failed_scan_page_nums
        }

        # ---- 失败项重试(最多两轮) ----
        for retry_round in (1, 2):
            if vl_probe_failed:
                break
            _check_project_cancelled()
            retry_scan, retry_per = _collect_retry_targets()
            retry_total = _count_retry_images(retry_scan, retry_per)
            if retry_total <= 0:
                break

            vl_controller.reset_window()
            state.vl_retry_round = retry_round
            state.vl_phase = "retry_running"
            state.vl_current_round = retry_round + 1
            state.vl_round_total = retry_total
            state.vl_round_done_keys = set()
            state.vl_retry_pending = retry_total
            _refresh_vl_status(
                phase_override=state.vl_phase,
                current_round_override=state.vl_current_round,
                round_done_override=0,
                round_total_override=state.vl_round_total,
                retry_pending_override=state.vl_retry_pending,
            )
            logger.info(
                f"[文档处理] VL重试第{retry_round}轮开始(展示轮次={state.vl_current_round}): "
                f"{len(retry_scan)}页整页 + {len(retry_per)}张逐图, 目标图片={retry_total}"
            )

            if retry_scan:
                try:
                    retry_data: list[tuple] = []
                    need_render_pages: list = []
                    for p in retry_scan:
                        _check_project_cancelled()
                        cached = scan_render_cache.get(p.page_num)
                        if cached:
                            rb64, rctx = cached
                            retry_data.append((p, rb64, rctx))
                        else:
                            need_render_pages.append(p)

                    if need_render_pages:
                        extra_list: list[tuple[int, str]] = []
                        for p in need_render_pages:
                            _check_project_cancelled()
                            prev_idx = p.page_num - 2
                            ctx = ""
                            if 0 <= prev_idx < len(parsed_doc.pages):
                                ctx = parsed_doc.pages[prev_idx].text or ""
                            extra_list.append((p.page_num, ctx))
                        extra_cache = await asyncio.to_thread(
                            ptm._render_pages_sync,
                            file_path,
                            extra_list,
                            200,
                            _page_clip_map([page_num for page_num, _ctx in extra_list]),
                        )
                        for p in need_render_pages:
                            rb64, rctx = extra_cache[p.page_num]
                            retry_data.append((p, rb64, rctx))
                        scan_render_cache.update(extra_cache)

                    async def _retry_page(p, b64, ctx):
                        _check_project_cancelled()
                        ocr_content = await _try_ocr_full_page(b64, p.page_num)
                        _check_project_cancelled()
                        if ocr_content:
                            state.total_ocr_ok += 1
                            bind_full_page_visual_content(p, ocr_content)
                            on_vl_progress(p.images, True)
                            return None
                        return (p, b64, ctx)

                    _retry_ocr_results = await asyncio.gather(
                        *[_retry_page(p, b, c) for p, b, c in retry_data],
                        return_exceptions=True,
                    )
                    rethrow_project_cancelled(_retry_ocr_results)
                    retry_vl_targets = [
                        result for result in _retry_ocr_results
                        if isinstance(result, tuple) and len(result) == 3
                    ]
                    retry_page_by_num = {
                        p.page_num: p for p, _b64, _ctx in retry_vl_targets
                    }
                    reported_retry_full_pages: set[int] = set()

                    def _on_retry_full_page_progress(page_num: int, content: str) -> None:
                        page = retry_page_by_num.get(page_num)
                        if page is None:
                            return
                        reported_retry_full_pages.add(page_num)
                        if content:
                            bind_full_page_visual_content(page, content)
                        on_vl_progress(page.images, bool(content))

                    retry_vl_results = await analyze_full_pages_with_document_llm_subtasks(
                        project_id=doc_project_id,
                        business_flow=business_flow,
                        pages=[
                            FullPageVlRequest(
                                page_b64=b64,
                                page_num=p.page_num,
                                context=ctx,
                                route_hint="retry_full_page",
                            )
                            for p, b64, ctx in retry_vl_targets
                        ],
                        keepalive_callback=vl_keepalive,
                        progress_callback=_on_retry_full_page_progress,
                    )
                    for page_num, p in retry_page_by_num.items():
                        if page_num in reported_retry_full_pages:
                            continue
                        content = retry_vl_results.get(page_num, "")
                        if content:
                            bind_full_page_visual_content(p, content)
                        on_vl_progress(p.images, bool(content))
                except ProjectCancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"[文档处理] VL重试第{retry_round}轮(整页)失败: {e}")

            if retry_per:
                _check_project_cancelled()
                _retry_stats = await recognize_images_with_document_llm_subtasks(
                    images=retry_per,
                    project_id=doc_project_id,
                    business_flow=business_flow,
                    progress_callback=lambda img, success: on_vl_progress([img], bool(success)),
                    ocr_engine=ocr_engine_instance,
                    max_retry_rounds=0,
                    keepalive_callback=vl_keepalive,
                )
                state.total_ocr_ok += _retry_stats.ocr_ok

            retry_vl = sum(1 for img in all_images if img.visual_content)
            next_retry_scan, next_retry_per = _collect_retry_targets()
            state.vl_retry_pending = _count_retry_images(next_retry_scan, next_retry_per)
            _refresh_vl_status(
                phase_override=state.vl_phase,
                current_round_override=state.vl_current_round,
                round_done_override=len(state.vl_round_done_keys),
                round_total_override=state.vl_round_total,
                retry_pending_override=state.vl_retry_pending,
            )
            logger.info(
                f"[文档处理] VL重试第{retry_round}轮完成, 当前成功: {retry_vl}/{state.image_count}张, "
                f"下一轮待重试={state.vl_retry_pending}"
            )

            still_failed_pages = {
                p.page_num for p in next_retry_scan
            }
            scan_render_cache = {
                k: v for k, v in scan_render_cache.items()
                if k in still_failed_pages
            }

        scan_render_cache.clear()
        vl_count = sum(
            1 for img in all_images if img.visual_content
        )
        logger.info(
            f"[文档处理] VL识别完成: {vl_count}/{state.image_count}张"
        )

        unrecognized_images = [
            img for img in all_images if img.file_path and not img.visual_content
        ]
        unrecognized_count = len(unrecognized_images)
        vl_fail_pages = sorted(set(img.page_num for img in unrecognized_images))
        state.vl_phase = "done"
        state.vl_unrecognized_final = unrecognized_count
        if vl_fail_pages:
            _refresh_vl_status(
                phase_override=state.vl_phase,
                current_round_override=state.vl_current_round,
                round_done_override=len(state.vl_round_done_keys),
                round_total_override=state.vl_round_total,
                retry_pending_override=0,
                unrecognized_final_override=state.vl_unrecognized_final,
                manual_review_required=True,
                unrecognized_images=unrecognized_count,
                unrecognized_pages=len(vl_fail_pages),
            )
            logger.warning(
                f"[文档处理] VL识别仍有未识别图片: {unrecognized_count}张, "
                f"涉及{len(vl_fail_pages)}页({vl_fail_pages}), 需人工审核"
            )
        else:
            _refresh_vl_status(
                phase_override=state.vl_phase,
                current_round_override=state.vl_current_round,
                round_done_override=len(state.vl_round_done_keys),
                round_total_override=state.vl_round_total,
                retry_pending_override=0,
                unrecognized_final_override=0,
            )
            logger.info("[文档处理] VL识别完成: 全部图片识别成功")

        # VL 质量门禁: 扫描密集文档的成功率过低则中止
        if not vl_probe_failed and state.image_count > 0:
            scan_ratio = (
                len(scan_heavy_page_nums)
                / max(parsed_doc.page_count, 1)
            )
            vl_success_rate = vl_count / state.image_count
            if (scan_ratio > settings.VL_GATE_SCAN_CRITICAL_RATIO
                    and vl_success_rate < settings.VL_GATE_MIN_SUCCESS_RATE):
                raise ptm._VLQualityError(
                    f"VL识别质量不足: 成功率{vl_count}/{state.image_count}"
                    f"({vl_success_rate:.0%}), "
                    f"扫描页占比{scan_ratio:.0%}, "
                    f"低于阈值{settings.VL_GATE_MIN_SUCCESS_RATE:.0%}, "
                    f"请检查视觉模型配置"
                )

        return VLStageResult(
            total_ocr_ok=state.total_ocr_ok,
            vl_count=vl_count,
            unrecognized_count=unrecognized_count,
        )

    except ProjectCancelledError:
        raise
    except ptm._VLQualityError:
        raise
    except Exception as e:
        logger.warning(f"[文档处理] VL识别失败(降级处理): {e}")
        # 降级时仍返回当前已累积的统计
        vl_count_partial = sum(1 for img in all_images if img.visual_content)
        return VLStageResult(
            total_ocr_ok=state.total_ocr_ok,
            vl_count=vl_count_partial,
            unrecognized_count=max(state.image_count - vl_count_partial, 0),
        )
