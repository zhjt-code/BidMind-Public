"""素材批处理 Phase 1 + 1.6：纯解析 + PDF 综合拆分（R2-4 Step 3）

承担原 ``MaterialService._execute_material_batch`` 内 L750-817 段职责，
保持以下契约：
- 解析失败时把素材 ``processing_status`` 置为 ``"failed"``（与历史行为一致）
- 拆分失败降级时记录 warning 但不改 ``processing_status``（整份继续）
- 事务边界：Phase 1 末 / Phase 1.6 末 各自 ``await db.commit()``
- 日志前缀 ``[素材批处理]`` 与原字段集 byte-equal
- ``MaterialRole`` / ``SplitReviewStatus`` 枚举字面值零变化
- Office 素材先经 ``convert_office_to_pdf`` 归一为 PDF，再走 PDF 解析管线
  （与文档处理 ``run_convert_stage`` 同源能力；title 保留原文件名供展示）

Phase 间状态通过函数返回值显式传递，不持有跨 phase 实例字段。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from app.models.writing_material import MaterialRole, SplitReviewStatus, WritingMaterial

if TYPE_CHECKING:
    from app.core.parser.image_extractor import ExtractedImage
    from app.core.writer.material_content_extractor import (
        MaterialContentExtractor,
        ParseResult,
    )
    from app.core.writer.material_splitter import MaterialSplitter
    from app.services.writing.material_service import MaterialService


async def normalize_material_source_for_parse(mat: WritingMaterial) -> str:
    """
    职责: 将素材磁盘源归一为可解析路径，Office 转 PDF 后回写 file_path。
    不做: 不调用 LLM、不解析正文、不改 title/展示名。

    Returns:
        供 ``parse_only`` 使用的小写后缀（含点号）。
    """
    from app.core.config import settings
    from app.core.convert.office_to_pdf import normalize_office_path_for_parse

    source = mat.file_path or mat.title or ""
    prior = Path(mat.file_path or "")
    resolved, ext = await normalize_office_path_for_parse(
        source,
        timeout_sec=int(settings.OFFICE_CONVERT_TIMEOUT_SEC),
    )
    if resolved != prior:
        logger.info(
            "[素材批处理] Office→PDF 归一化: id={}, src={}, dest={}",
            mat.id,
            prior.name,
            resolved.name,
        )
        mat.file_path = str(resolved)
    if not ext:
        ext = Path(mat.title or "").suffix.lower()
    return ext


async def parse_documents(
    service: "MaterialService",
    pending_materials: list[WritingMaterial],
    extractor: "MaterialContentExtractor",
    project_id: int,
) -> tuple[dict[int, "ParseResult"], int]:
    """Phase 1: 对待处理素材执行纯解析（无 LLM），返回 ``(parse_results, failed_count)``。

    解析失败的素材会被原地置为 ``processing_status='failed'``，并在函数末尾统一
    ``await db.commit()``。Office 素材先转 PDF 再解析；``title`` 保留原上传名。
    """
    from app.core.config import settings

    parse_results: dict[int, "ParseResult"] = {}
    failed = 0

    for mat in pending_materials:
        try:
            ext = await normalize_material_source_for_parse(mat)
            save_dir = Path(settings.UPLOAD_DIR) / str(project_id) / "materials"
            image_save_dir = save_dir / f"material_{mat.id}_images"

            pr = extractor.parse_only(
                file_path=mat.file_path,
                ext=ext,
                image_save_dir=image_save_dir,
            )
            parse_results[mat.id] = pr
            # 计费计量: 素材页数是业务输入资源,解析成功即持久化;
            # 本函数仅处理原始上传素材,拆分子素材不在此列,天然避免双重计量
            mat.page_count = int(pr.page_count or 0)
        except Exception as e:
            logger.warning(
                f"[素材批处理] 解析失败: id={mat.id}, title={mat.title}, err={e}"
            )
            mat.processing_status = "failed"
            failed += 1

    await service.db.commit()
    return parse_results, failed


async def split_pdf_drafts(
    service: "MaterialService",
    pending_materials: list[WritingMaterial],
    parse_results: dict[int, "ParseResult"],
    splitter: "MaterialSplitter",
) -> tuple[list[WritingMaterial], int]:
    """Phase 1.6: 综合 PDF 结构分析与子素材草案生成。

    成功拆分的父素材标记 ``MaterialRole.split_parent`` + 计算
    ``split_confidence`` + ``split_review_status``；失败时降级整份继续，
    不改 ``processing_status``。``parse_results`` 会在原地追加新生成的子素材 id。

    返回 ``(children, split_count)``，其中 ``children`` 是新生成的子素材实体。
    """
    children_all: list[WritingMaterial] = []
    split_children = 0

    for mat in pending_materials:
        if mat.processing_status == "failed":
            continue
        pr = parse_results.get(mat.id)
        if not pr or pr.file_type != "pdf":
            continue
        try:
            drafts = await splitter.analyze_and_split(mat, pr)
            if not drafts:
                continue
            children = await splitter.create_sub_materials(mat, drafts, service.db)
            if not children:
                continue
            children_all.extend(children)
            for child, draft in zip(children, drafts):
                parse_results[child.id] = draft.parse_result
            split_children += len(children)
            mat.material_role = MaterialRole.split_parent.value
            mat.split_confidence = round(
                sum(draft.split_confidence for draft in drafts) / len(drafts),
                3,
            )
            mat.split_review_status = (
                SplitReviewStatus.needs_review.value
                if any(
                    draft.split_confidence < 0.7
                    for draft in drafts
                )
                else SplitReviewStatus.confirmed.value
            )
            logger.info(
                "[素材批处理] 综合PDF拆分: parent_id={}, children={}, confidence={}",
                mat.id,
                len(children),
                mat.split_confidence,
            )
        except Exception as e:
            logger.warning(
                f"[素材批处理] 综合PDF拆分失败(降级整份继续): id={mat.id}, err={e}"
            )

    await service.db.commit()
    return children_all, split_children


def collect_image_pool(
    processing_materials: list[WritingMaterial],
    parse_results: dict[int, "ParseResult"],
) -> list["ExtractedImage"]:
    """Phase 1 收尾：从 processing_materials 的 parse_results 中聚合图片池。

    与原 ``_execute_material_batch`` L819-823 段行为一致：仅追加 ``pr is not None``
    的素材图片，``pr=None`` 的素材跳过（既无图也不报错）。
    """
    all_images: list["ExtractedImage"] = []
    for mat in processing_materials:
        pr = parse_results.get(mat.id)
        if pr:
            all_images.extend(pr.images)
    return all_images


__all__ = [
    "normalize_material_source_for_parse",
    "parse_documents",
    "split_pdf_drafts",
    "collect_image_pool",
]
