"""图片VL分析器

使用视觉语言模型（VL Model）对fitz提取的图片进行内容识别。
将识别结果写入 ExtractedImage.visual_content 字段，
后续由 generate_image_annotations() 融入文本标注。

调用链:
  fitz提取图片 → ImageAnalyzer.analyze_images() → VL模型识别 → visual_content
"""

import asyncio
import base64
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from loguru import logger

from app.core.parser.image_extractor import ExtractedImage

if TYPE_CHECKING:
    from app.core.parser.adaptive_concurrency import AdaptiveConcurrency
    from app.core.parser.ocr_engine import OcrEngine


# VL识别的系统提示
VL_SYSTEM_PROMPT = """你是一个专业的投标文件图片识别助手。你的任务是用自然语言描述图片内容,使其易于被文本检索系统匹配。

请识别图片中的关键信息,并按以下格式输出:

第一行: 用一句话说明这是什么文件(如"这是一份建筑工程施工总承包一级资质证书的扫描件")
后续行: 列出关键信息
- 证书/执照: 全称、编号、持证人/单位、有效期、颁发机构
- 资质证明: 资质等级、认证范围、有效期
- 签章/印章: 公司名称、印章类型
- 合同/报告: 标题、甲乙方、金额、签署日期
- 业绩证明: 项目名称、合同金额、完成日期
- 设备照片: 设备名称、型号、规格

判定文件类型的准则(重要):
- 类型必须依据文件"标题"与该类文件的"专有标识",而不是页面上偶然出现的其他字段。
- 一页可能同时含多份/多种材料,必须逐一列出,不要只报第一种或占版面最大的一种。
- 举例避免误判:
  * 发票看"发票号码、发票代码、价税合计、增值税专用发票/电子发票"等专有标识;
    不要因为票面上出现"统一社会信用代码/纳税人识别号"就误判成营业执照或税务登记证。
  * 社保缴费证明看"社会保险、缴费基数、参保、险种"等标识;
    不要因为附带经办人的"公民身份号码"就误判成身份证。
  * 中标通知书看"中标人、中标金额、中标通知"标识;不要因含公司名就误判成营业执照。
- 每识别出一种材料,单独用一句"这是一份XX"起头, 再跟其关键字段。

输出要求:
1. 用自然语言描述,不超过500字
2. 第一行必须明确说明文件类型和用途(如"作为投标人资质证明材料")
3. 只输出识别到的事实信息,不要推测
4. 如果图片模糊无法识别,说明"图片内容无法清晰识别"
5. 关键字段(证书编号、持证人姓名、有效期、单位名称、发票号码等)务必完整提取
"""

COLLUSION_IMAGE_RECOGNITION_SYSTEM = """你是专业的串标审查图片识别助手。你的任务是从投标文件图片中提取可复核的客观事实,供后续串标证据链分析使用。

请识别图片中的关键信息,并按以下要求输出:
1. 只描述图片中明确可见的事实,不要直接下串标结论。
2. 优先提取企业名称、人员姓名、证书编号、资质等级、有效期、合同/业绩名称、金额、签章、联系方式、开户地址等可比对字段。
3. 如果图片属于扫描件或一页多份材料,请逐份列出材料类型和关键字段。
4. 如果内容模糊无法识别,说明"图片内容无法清晰识别"。
5. 输出自然语言,不超过500字,便于后续文本检索和交叉比对。
"""


_OCR_HINT_KEYWORDS = [
    "证书", "执照", "许可证", "身份证", "资质", "认证",
    "社保", "保险", "营业", "注册", "登记", "中标",
    "合同", "协议", "报告", "审计", "发票", "授权",
    "委托", "投标函", "开户", "缴费", "证明",
]


def _ocr_priority(image: ExtractedImage) -> Literal["high", "medium", "low"]:
    """根据图片元数据评估 OCR 静态优先级

    - high: 大幅扫描件，始终尝试 OCR
    - medium: 中等图片且上下文含证照关键词，OCR 不拥堵时尝试
    - low: 其余图片，直接走 VL
    """
    category = image.size_category
    if category == "大幅扫描件":
        return "high"
    if category == "中等图片" and image.spatial_context:
        ctx_lower = image.spatial_context.lower()
        if any(kw in ctx_lower for kw in _OCR_HINT_KEYWORDS):
            return "medium"
    return "low"


def ocr_priority(image: ExtractedImage) -> Literal["high", "medium", "low"]:
    """公开 OCR 路由优先级, 供跨进程子任务编排使用。"""

    return _ocr_priority(image)


def _image_recognition_node_key(business_flow: str = "bid_review") -> str:
    """按业务 flow 选择图片识别 prompt node。"""

    return {
        "collusion": "collusion.image_recognition",
        "collusion_check": "collusion.image_recognition",
        "bid_review": "bid_review.image_recognition",
    }.get(business_flow or "bid_review", "bid_review.image_recognition")


def _resolve_vl_system(
    business_flow: str = "bid_review",
    *,
    default_system: str | None = None,
) -> str:
    """VL 系统 Prompt: Registry 优先 + 代码默认兜底。

    user 消息由 _build_vl_prompt 动态构建, 不走模板; 故此节点只从 Registry 读 system。
    """
    from app.core.ai.prompt_registry import get_registry

    node_key = _image_recognition_node_key(business_flow)
    resolved = get_registry().get_prompt(node_key)
    fallback = (
        COLLUSION_IMAGE_RECOGNITION_SYSTEM
        if node_key == "collusion.image_recognition"
        else VL_SYSTEM_PROMPT
    )
    if resolved.system_message:
        return resolved.system_message
    if node_key == "collusion.image_recognition":
        return fallback
    return default_system or fallback


def _build_vl_prompt(image: ExtractedImage) -> str:
    """根据图片元数据构建VL识别提示词

    利用fitz已提取的空间上下文信息增强识别准确率。
    引导VL模型输出自然语言描述, 便于后续文本检索命中。

    Args:
        image: fitz提取的图片对象

    Returns:
        str: 针对此图片的提示词
    """
    parts = [
        "这是投标文件中的一张图片。"
        "请先用一句话说明这是什么文件/材料,然后列出关键信息。"
    ]

    if image.spatial_context:
        parts.append(f"图片附近的文字为: \"{image.spatial_context}\"")

    category = image.size_category
    if category == "大幅扫描件":
        parts.append(
            "这很可能是证书、执照、资质证明、合同或报告的扫描件。"
            "请重点识别: 文件全称、编号、持证单位/人、有效期。"
        )
    elif category == "中等图片":
        parts.append(
            "这可能是照片、图表、签章或其他证明材料。"
            "请识别其中的关键内容。"
        )

    return "\n".join(parts)


def _load_image_as_base64(file_path: str) -> Optional[str]:
    """读取图片文件并转换为base64编码

    Args:
        file_path: 图片文件路径

    Returns:
        str: base64编码字符串, 失败返回None
    """
    try:
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"图片文件不存在: {file_path}")
            return None

        with open(path, "rb") as f:
            image_bytes = f.read()

        return base64.b64encode(image_bytes).decode("ascii")
    except Exception as e:
        logger.warning(f"读取图片失败: {file_path}, error={e}")
        return None


async def analyze_single_image(
    image: ExtractedImage,
    vision_client: object,
    ocr_engine: Optional["OcrEngine"] = None,
    flow_id: str = "default",
) -> tuple[str, str]:
    """使用 OCR 或 VL 模型分析单张图片

    当 ocr_engine 存在时，由 _ocr_priority() + 拥堵感知决定路由:
    - high 优先级: 始终尝试 OCR，置信度不足时回退 VL
    - medium 优先级: OCR 不拥堵时尝试，拥堵时降级到 VL
    - low 优先级: 直接走 VL

    ocr_engine=None 时行为与改动前完全一致（向后兼容）。

    Returns:
        (content, route): content 为识别结果文本（空串=失败），
        route 为 "ocr"/"ocr->vl"/"ocr_degraded"/"vl"
    """
    content, route_label, needs_vl = await analyze_image_ocr_first(
        image,
        ocr_engine,
        flow_id=flow_id,
    )
    if content or not needs_vl:
        return content, route_label

    return await analyze_image_vl_only(
        image,
        vision_client,
        route_label=route_label,
    )


async def analyze_image_ocr_first(
    image: ExtractedImage,
    ocr_engine: Optional["OcrEngine"] = None,
    flow_id: str = "default",
) -> tuple[str, str, bool]:
    """仅执行 OCR-first 路由判定, 不直接调用 VL。

    Returns:
        (content, route, needs_vl)。content 非空表示 OCR 已产出可信文本;
        needs_vl=True 表示调用方应将该图片派发到 VL 模型通道。
    """
    if not image.file_path:
        return ("", "vl", False)

    if ocr_engine is None:
        return ("", "vl", True)

    priority = _ocr_priority(image)
    if priority == "low":
        return ("", "vl", True)

    if priority == "medium" and ocr_engine.is_congested:
        return ("", "ocr_degraded", True)

    if priority == "high" and ocr_engine.is_congested:
        try:
            from app.core.config import settings as _settings
            recovered = await ocr_engine.wait_for_recovery(
                timeout=float(getattr(_settings, "OCR_STEAL_BACK_WAIT_SEC", 3.0)),
            )
            if not recovered:
                return ("", "ocr_degraded", True)
        except Exception:
            return ("", "ocr_degraded", True)

    try:
        ocr_result = await ocr_engine.recognize(
            image.file_path,
            flow_id=flow_id,
        )
        if ocr_result.is_confident:
            from app.core.parser.ocr_postprocessor import postprocess_ocr
            content = postprocess_ocr(
                ocr_result,
                spatial_context=image.spatial_context or "",
                size_category=image.size_category,
            )
            if content:
                logger.info(
                    f"[OCR] page={image.page_num}, "
                    f"img={image.image_index}, "
                    f"conf={ocr_result.avg_confidence:.2f}, "
                    f"len={len(content)}"
                )
                return (content, "ocr", False)
        logger.info(
            f"[OCR->VL] page={image.page_num}, "
            f"img={image.image_index}, "
            f"conf={ocr_result.avg_confidence:.2f}, "
            f"text_len={len(ocr_result.raw_text)}, 置信度不足回退VL"
        )
    except Exception as e:
        logger.warning(
            f"[OCR->VL] page={image.page_num}, "
            f"img={image.image_index}, OCR异常回退VL: {e}"
        )
    return ("", "ocr->vl", True)


async def analyze_image_vl_only(
    image: ExtractedImage,
    vision_client: object,
    route_label: str = "vl",
    business_flow: str = "bid_review",
) -> tuple[str, str]:
    """仅执行 VL 模型识别, 用于 LLM worker 子任务。"""
    if not image.file_path:
        return ("", route_label or "vl")

    img_b64 = _load_image_as_base64(image.file_path)
    if not img_b64:
        return ("", route_label or "vl")

    prompt = _build_vl_prompt(image)

    try:
        result = await vision_client.chat_with_images(
            text_message=prompt,
            image_data_list=[img_b64],
            system_message=_resolve_vl_system(business_flow),
            temperature=0.1,
            max_tokens=1000,
        )
        content = result.strip() if result else ""
        if not content:
            logger.warning(
                f"[VL] 返回空: page={image.page_num}, img={image.image_index}, "
                f"size={image.width}x{image.height}, category={image.size_category}"
            )

        return (content, route_label or "vl")
    except Exception as e:
        logger.warning(
            f"[VL] 异常: page={image.page_num}, "
            f"img={image.image_index}, error={e}"
        )
        return ("", route_label or "vl")


async def analyze_images(
    images: list[ExtractedImage],
    vision_client: object,
    concurrency: int = 3,
    progress_callback: Optional[callable] = None,
    controller: Optional["AdaptiveConcurrency"] = None,
    ocr_engine: Optional["OcrEngine"] = None,
    flow_id: str = "default",
) -> dict:
    """批量使用VL模型分析图片, 结果写入 image.visual_content

    当 controller 存在时, 使用自适应并发控制(忽略 concurrency 参数);
    否则回退到固定信号量模式(向后兼容)。

    当 ocr_engine 存在时, 文字密集型图片优先走本地 OCR，
    不足时回退 VL（由 analyze_single_image 内部处理）。

    Args:
        images: fitz提取的图片列表（需有file_path）
        vision_client: VL模型客户端
        concurrency: 最大并发数(仅在无 controller 时使用)
        progress_callback: 每完成一张图片时的回调, 参数为(图片对象, 是否识别成功)
        controller: 自适应并发控制器(可选)
        ocr_engine: PaddleOCR 引擎实例(可选)
    """
    from app.core.config import settings as _settings

    analyzable = [img for img in images if img.file_path]
    if not analyzable:
        return

    effective_c = controller.current if controller else concurrency
    mode_tag = "OCR+VL混合" if ocr_engine else "VL"
    logger.info(
        f"开始图片识别({mode_tag}): 共{len(analyzable)}张, "
        f"并发={effective_c}{'(自适应)' if controller else '(固定)'}"
    )

    fallback_sem = None if controller else asyncio.Semaphore(concurrency)

    _ocr_ok = 0
    _ocr_fallback = 0
    _ocr_degraded = 0
    _ocr_budget_exhausted = 0
    _ocr_steal_back = 0
    _vl_ok = 0
    _counter_lock = asyncio.Lock()

    # OCR 预算控制: 限制单批次进入 OCR 的总量，避免洪峰
    _ocr_budget_max = _settings.OCR_BUDGET_PER_BATCH
    _ocr_budget_remaining = _ocr_budget_max
    _budget_lock = asyncio.Lock()

    async def _try_consume_ocr_budget(priority: str) -> bool:
        """尝试消耗一个 OCR 预算槽位，高优先级不受预算限制"""
        nonlocal _ocr_budget_remaining
        if _ocr_budget_max <= 0:
            return True
        if priority == "high":
            return True
        async with _budget_lock:
            if _ocr_budget_remaining > 0:
                _ocr_budget_remaining -= 1
                return True
            return False

    async def _race_ocr_vl(
        img: ExtractedImage,
        ocr_eng: "OcrEngine",
    ) -> tuple[str, str, bool]:
        """双通道竞争: VL 槽位 vs OCR 恢复

        同时发起 VL 信号量获取和 OCR 恢复等待，谁先就绪走谁。
        OCR 抢回成功时不占用 VL 槽位，等于为 VL 让出了吞吐。

        Returns:
            (content, route_label, vl_slot_held)
            content 非空表示 OCR 抢回成功;
            vl_slot_held=True 表示调用方已持有 VL 槽位。
        """
        steal_timeout = _settings.OCR_STEAL_BACK_WAIT_SEC

        vl_fut = asyncio.ensure_future(
            controller.acquire(weight=1) if controller
            else fallback_sem.acquire()
        )
        ocr_fut = asyncio.ensure_future(
            ocr_eng.wait_for_recovery(timeout=steal_timeout)
        )

        done, pending = await asyncio.wait(
            [vl_fut, ocr_fut], return_when=asyncio.FIRST_COMPLETED,
        )

        vl_done = vl_fut in done
        ocr_recovered = (
            ocr_fut in done
            and not ocr_fut.cancelled()
            and ocr_fut.result()
        )

        if ocr_recovered:
            if not vl_done:
                vl_fut.cancel()
                try:
                    await vl_fut
                except (asyncio.CancelledError, Exception):
                    pass
            else:
                if controller:
                    await controller.release(weight=1)
                else:
                    fallback_sem.release()

            try:
                ocr_result = await ocr_eng.recognize(
                    img.file_path,
                    flow_id=flow_id,
                )
                if ocr_result.is_confident:
                    from app.core.parser.ocr_postprocessor import postprocess_ocr
                    text = postprocess_ocr(
                        ocr_result,
                        spatial_context=img.spatial_context or "",
                        size_category=img.size_category,
                    )
                    if text:
                        logger.info(
                            f"[OCR抢回] page={img.page_num}, "
                            f"img={img.image_index}, "
                            f"conf={ocr_result.avg_confidence:.2f}, "
                            f"len={len(text)}"
                        )
                        return (text, "ocr_steal_back", False)
                logger.info(
                    f"[OCR抢回->VL] page={img.page_num}, "
                    f"img={img.image_index}, "
                    f"OCR置信度不足, 回退VL"
                )
            except Exception as e:
                logger.warning(
                    f"[OCR抢回] page={img.page_num}, "
                    f"img={img.image_index}, error={e}"
                )
            return ("", "", False)

        # VL 先就绪或 OCR 恢复超时
        if not vl_done:
            await vl_fut
        if ocr_fut in pending:
            ocr_fut.cancel()
            try:
                await ocr_fut
            except (asyncio.CancelledError, Exception):
                pass

        return ("", "ocr_degraded", True)

    async def _analyze_one(img: ExtractedImage) -> None:
        nonlocal _ocr_ok, _ocr_fallback, _ocr_degraded, _ocr_budget_exhausted
        nonlocal _ocr_steal_back, _vl_ok
        from app.core.parser.adaptive_concurrency import RequestMetric

        effective_ocr = ocr_engine
        budget_blocked = False
        if ocr_engine is not None:
            priority = _ocr_priority(img)
            if priority != "low":
                allowed = await _try_consume_ocr_budget(priority)
                if not allowed:
                    effective_ocr = None
                    budget_blocked = True

        is_racing = (
            effective_ocr is not None
            and _ocr_priority(img) in ("high", "medium")
            and effective_ocr.is_congested
        )

        start = time.monotonic()
        is_timeout = False
        is_rate_limited = False
        success = True
        is_empty = False
        content = ""
        route_used = "vl"
        vl_held = False

        try:
            if is_racing:
                content, route_used, vl_held = await _race_ocr_vl(
                    img, effective_ocr,
                )
                if content:
                    img.visual_content = content

            if not content:
                if not vl_held:
                    if controller:
                        await controller.acquire(weight=1)
                    else:
                        await fallback_sem.acquire()
                    vl_held = True

                content, route_used = await analyze_single_image(
                    img, vision_client,
                    ocr_engine=(None if is_racing else effective_ocr),
                    flow_id=flow_id,
                )
                is_empty = not content
                if content:
                    img.visual_content = content
        except asyncio.TimeoutError:
            is_timeout = True
            success = False
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "rate" in err_str:
                is_rate_limited = True
            success = False
        finally:
            if vl_held:
                if controller:
                    await controller.release(weight=1)
                else:
                    fallback_sem.release()

        elapsed = (time.monotonic() - start) * 1000

        if budget_blocked:
            route_used = "ocr_budget"

        # 计费计量: 动态标记OCR产出,供写标素材批处理按素材归集计费
        if content and route_used in ("ocr", "ocr_steal_back"):
            img._recognized_via = "ocr"

        if controller and route_used not in ("ocr", "ocr_budget", "ocr_steal_back"):
            await controller.record(RequestMetric(
                latency_ms=elapsed,
                success=success,
                is_empty=is_empty,
                is_timeout=is_timeout,
                is_rate_limited=is_rate_limited,
                weight=1,
            ))

        async with _counter_lock:
            if route_used == "ocr":
                _ocr_ok += 1
            elif route_used == "ocr->vl":
                _ocr_fallback += 1
                if content:
                    _vl_ok += 1
            elif route_used == "ocr_degraded":
                _ocr_degraded += 1
                if content:
                    _vl_ok += 1
            elif route_used == "ocr_budget":
                _ocr_budget_exhausted += 1
                if content:
                    _vl_ok += 1
            elif route_used == "ocr_steal_back":
                _ocr_steal_back += 1
            else:
                if content:
                    _vl_ok += 1

        if progress_callback:
            try:
                progress_callback(img, bool(content))
            except Exception:
                pass

    tasks = [_analyze_one(img) for img in analyzable]
    await asyncio.gather(*tasks, return_exceptions=True)

    recognized = sum(1 for img in analyzable if getattr(img, 'visual_content', ''))
    parts = [f"图片识别完成({mode_tag}): {recognized}/{len(analyzable)}张成功"]
    if ocr_engine:
        parts.append(
            f"OCR直接={_ocr_ok}, OCR回退VL={_ocr_fallback}, "
            f"OCR拥堵降级={_ocr_degraded}, OCR抢回={_ocr_steal_back}, "
            f"OCR预算耗尽={_ocr_budget_exhausted}, 纯VL={_vl_ok}"
        )
    logger.info(", ".join(parts))

    return {
        "ocr_ok": _ocr_ok,
        "ocr_fallback": _ocr_fallback,
        "ocr_degraded": _ocr_degraded,
        "ocr_steal_back": _ocr_steal_back,
        "vl_ok": _vl_ok,
        "recognized": recognized,
        "total": len(analyzable),
    }


# ── 整页VL识别(扫描密集页) ────────────────────────────────────────────

FULL_PAGE_VL_SYSTEM = """你是投标文件图片识别专家。当前页面为扫描件,请逐一识别页面中的所有证明材料。

请按以下格式输出每份材料:

材料1:
- 文件类型: (身份证/社保缴费证明/资质证书/营业执照/税务登记证/发票/中标通知书/合同/安全生产许可证/建造师证书/...)
- 持有人或单位:
- 关键编号:
- 有效期:
- 其他关键信息:

材料2:
...

判定文件类型的准则(重要):
- 类型依据文件"标题"与"专有标识",不要被页面上偶然出现的其他字段带偏。
- 举例避免误判:
  * 发票看"发票号码、发票代码、价税合计、增值税专用发票/电子发票";不要因出现
    "统一社会信用代码/纳税人识别号"就误判成营业执照或税务登记证。
  * 社保缴费证明看"社会保险、缴费基数、参保、险种";不要因附带"公民身份号码"就误判成身份证。
  * 中标通知书看"中标人、中标金额、中标通知";不要因含公司名就误判成营业执照。

输出要求:
1. 如有多份材料必须全部列出,不得遗漏(同一页含发票+合同+证照时逐份列出)
2. 只输出识别到的事实信息,不要推测
3. 关键字段(姓名、编号、有效期、单位、发票号码)务必完整提取
4. 如果某份材料无法识别,注明"该材料内容无法清晰识别"
"""


async def analyze_full_page(
    page_b64: str,
    vision_client: object,
    page_num: int,
    context: str = "",
    business_flow: str = "bid_review",
) -> str:
    """使用VL模型整页识别扫描件密集页面

    Args:
        page_b64: 页面渲染的base64 PNG
        vision_client: VL模型客户端
        page_num: 页码
        context: 页面附近的文字上下文

    Returns:
        str: VL识别结果, 失败返回空字符串
    """
    prompt_parts = [
        f"这是投标文件第{page_num}页的完整扫描件。"
        "请识别页面中的所有证明材料并逐一列出关键信息。"
    ]
    if context:
        prompt_parts.append(f"页面附近的文字信息: \"{context[:300]}\"")

    prompt = "\n".join(prompt_parts)

    try:
        result = await vision_client.chat_with_images(
            text_message=prompt,
            image_data_list=[page_b64],
            system_message=_resolve_vl_system(
                business_flow,
                default_system=FULL_PAGE_VL_SYSTEM,
            ),
            temperature=0.1,
            max_tokens=1500,
        )
        if result:
            result = result.strip()
            logger.info(
                f"整页VL识别完成: page={page_num}, "
                f"content_len={len(result)}"
            )
        else:
            logger.warning(
                f"整页VL识别返回空: page={page_num}, "
                f"b64_len={len(page_b64)}"
            )
        return result or ""
    except Exception as e:
        logger.warning(f"整页VL识别异常: page={page_num}, error={e}")
        return ""


async def probe_vl_availability(
    scan_pages: list,
    per_images: list,
    vision_client: object,
    probe_count: int = 2,
    pdf_path: str = "",
) -> tuple[bool, str]:
    """前置探测VL模型是否正常响应

    从 scan_pages 和 per_images 中各取样本进行快速探测,
    全部返回空则判定模型不可用。

    Args:
        scan_pages: 扫描密集页面列表, 每项需有 page_num 属性和 rendered_b64 数据
        per_images: 逐图识别图片列表 (ExtractedImage)
        vision_client: VL模型客户端
        probe_count: 最大探测数量
        pdf_path: PDF路径 (仅用于日志)

    Returns:
        (is_ok, reason): is_ok=True 表示模型可用
    """
    if not scan_pages and not per_images:
        logger.info(f"[VL探测] 无可用探测目标, 跳过探测: {pdf_path}")
        return (True, "无探测目标")

    probed = 0
    success = 0

    try:
        if scan_pages:
            page = scan_pages[0]
            page_b64 = getattr(page, "rendered_b64", None) or ""
            if page_b64:
                result = await analyze_full_page(
                    page_b64=page_b64,
                    vision_client=vision_client,
                    page_num=getattr(page, "page_num", 0),
                    context="[探测]",
                )
                probed += 1
                if result:
                    success += 1

        if per_images and probed < probe_count:
            largest = max(
                (img for img in per_images if img.file_path),
                key=lambda img: (img.width or 0) * (img.height or 0),
                default=None,
            )
            if largest:
                result = await analyze_single_image(
                    image=largest,
                    vision_client=vision_client,
                )
                probed += 1
                if result:
                    success += 1

    except Exception as e:
        logger.error(f"[VL探测] 探测过程异常: {e}, pdf={pdf_path}")
        return (False, f"探测异常: {e}")

    if probed == 0:
        logger.info(f"[VL探测] 实际探测数为0, 跳过: {pdf_path}")
        return (True, "无有效探测目标")

    if success == 0:
        reason = (
            f"探测{probed}张图片全部返回空, "
            f"视觉模型可能未正确配置或响应超时"
        )
        logger.error(f"[VL探测] {reason}: pdf={pdf_path}")
        return (False, reason)

    logger.info(
        f"[VL探测] 通过: {success}/{probed}成功, pdf={pdf_path}"
    )
    return (True, "")
