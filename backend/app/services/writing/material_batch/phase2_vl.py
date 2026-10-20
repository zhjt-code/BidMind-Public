"""素材批处理 Phase 2 + 2.5：VL 批量识别 + 重复图回填（R2-4 Step 4）

承担原 ``MaterialService._execute_material_batch`` 内 L831-883 段职责，
保持以下契约：
- ``vl_total`` 与原逻辑一致：``len([img for img in unique_images if img.file_path])``
- ``vl_done`` 初始 ``0``，每张图回调后 += 1
- ``progress_pct`` 计算公式：``25 + int(35 * vl_done / vl_total)``（vl_total > 0 时）
  ``vl_total <= 0`` 时保持 ``25``
- VL 识别异常降级到 warning 日志（``[素材批处理] VL批量识别异常(降级继续): {e}``）
- 回填逻辑：仅当 ``original.visual_content`` 非空时拷贝到 ``dup.visual_content``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from app.core.parser.image_extractor import ExtractedImage
    from app.core.writer.material_content_extractor import ParseResult
    from app.models.writing_material import WritingMaterial
    from app.services.writing.material_batch.heartbeat import MaterialBatchHeartbeat


async def recognize_vl_pool(
    unique_images: list["ExtractedImage"],
    project_id: int,
    hb: "MaterialBatchHeartbeat",
) -> None:
    """Phase 2: 对去重后的唯一图片池做 VL 批量识别 + 心跳进度上报。

    异常时降级 warning 日志，继续后续流程（与原行为一致：VL 失败不阻断索引）。
    """
    from app.core.writer.material_content_extractor import MaterialContentExtractor

    vl_done = 0
    vl_total = len([img for img in unique_images if img.file_path])
    hb.emit(
        hb.PHASE_VL_RECOGNITION,
        progress_pct=25,
        message=f"VL识别 {vl_done}/{vl_total}",
        vl_done=vl_done,
        vl_total=vl_total,
    )

    def _on_vl_progress(img, success):  # noqa: ARG001 - 与 batch_vl_recognize 回调签名对齐
        nonlocal vl_done
        vl_done += 1
        vl_pct = 25
        if vl_total > 0:
            vl_pct = 25 + int(35 * vl_done / vl_total)
        hb.emit(
            hb.PHASE_VL_RECOGNITION,
            progress_pct=vl_pct,
            message=f"VL识别 {vl_done}/{vl_total}",
            vl_done=vl_done,
            vl_total=vl_total,
        )

    if unique_images:
        try:
            await MaterialContentExtractor.batch_vl_recognize(
                images=unique_images,
                progress_callback=_on_vl_progress,
                project_id=project_id,
            )
        except Exception as e:
            logger.warning(f"[素材批处理] VL批量识别异常(降级继续): {e}")


def backfill_duplicates(
    duplicates_map: dict[str, list],
    hash_to_first: dict[str, object],
) -> None:
    """Phase 2.5: VL 结果回填到重复图片。

    仅当 ``original.visual_content`` 非空时把内容拷贝到所有重复图，避免覆盖空值。
    """
    for file_hash, duplicates in duplicates_map.items():
        original = hash_to_first.get(file_hash)
        if original and original.visual_content:
            for dup in duplicates:
                dup.visual_content = original.visual_content


def collect_ocr_usage(
    processing_materials: list["WritingMaterial"],
    parse_results: dict[int, "ParseResult"],
) -> int:
    """Phase 2.6: 按素材归集本批次实际执行 OCR 的图片数(计费计量,)。

 职责: 统计各素材 parse_result 中带 ``_recognized_via == "ocr"`` 动态标记的
 图片张数, 写入 ``mat.ocr_image_count``(累加, 兼容重试批次)。
 不做: 不 commit(由调用方事务收口)、不参与识别结果组装。

 两类重复的排除:
 - 拆分子素材的 images 与父素材共享同一批对象引用, 用对象 id 去重,
 每张物理图只计入首个归属素材(父在前);
 - 去重回填图(backfill_duplicates 仅拷贝 visual_content)不带标记,
 与"实际执行 OCR 次数"的成本口径一致。

 返回本批次 OCR 总张数。
 """
    total_ocr = 0
    counted_ids: set[int] = set()
    for mat in processing_materials:
        pr = parse_results.get(mat.id)
        if not pr or not pr.images:
            continue
        ocr_count = 0
        for img in pr.images:
            if getattr(img, "_recognized_via", None) != "ocr":
                continue
            if id(img) in counted_ids:
                continue
            counted_ids.add(id(img))
            ocr_count += 1
        if ocr_count > 0:
            mat.ocr_image_count = int(mat.ocr_image_count or 0) + ocr_count
            total_ocr += ocr_count
    return total_ocr


__all__ = ["recognize_vl_pool", "backfill_duplicates", "collect_ocr_usage"]
