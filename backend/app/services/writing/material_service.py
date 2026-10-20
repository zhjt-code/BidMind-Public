"""标书编写素材域（最大子服务）

承载约 1238 行的素材域代码：12 个 public 方法 + 6 个 async private helper +
4 个 staticmethod + 2 个 classmethod + ``_MATERIAL_DB_CONTENT_PREVIEW_LIMIT`` ClassVar。

接口约定（Task 5 Step 8）：
- ``project_resolver`` 注入：lambda 包装 ``_get_project_or_raise``
- ``refresh_material_title_derivatives_hook`` 注入：lambda 包装主类同名方法，
  保证 ``update_material_title`` / ``batch_update_material_titles`` 内部调用
  时仍受 ``test_writer_text_material_consumption`` 的
  ``service._refresh_material_title_derivatives = AsyncMock(...)`` monkey patch 影响

其余 private 协作（_refine_sub_material_titles / _persist_and_classify_images /
_resolve_material_labels / _mark_unclassified_images_unknown）测试只直接调用
``service.xxx(...)`` 而不 monkey patch 实例方法，因此可以在 MaterialService 内部
通过 ``self.method`` 直接互调，无需 hook。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Optional

from loguru import logger
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ServiceError
from app.core.writer.material_titles import (
    get_effective_material_title,
    get_original_material_title,
    is_poor_material_title,
    is_strong_title_candidate,
    normalize_image_caption_title,
)
from app.models.project import Project
from app.models.user import User
from app.models.writing_binding import WritingBinding
from app.models.writing_material import (
    MaterialRole,
    MaterialType,
    SplitReviewStatus,
    WritingMaterial,
)
from app.models.writing_material_chunk import WritingMaterialChunk
from app.models.writing_material_image import ImageType, WritingMaterialImage
from app.services.writing.material_batch import (
    MaterialBatchHeartbeat,
    phase1_parse,
    phase2_vl,
    phase3_assemble,
    phase4_postprocess,
)


class MaterialService:
    """素材域子服务"""

    _MATERIAL_DB_CONTENT_PREVIEW_LIMIT: ClassVar[int] = 50000

    def __init__(
        self,
        db: AsyncSession,
        *,
        project_resolver: Callable[..., Awaitable[Project]],
        refresh_material_title_derivatives_hook: Callable[
            [WritingMaterial], Awaitable[None]
        ],
    ) -> None:
        self.db = db
        self._resolve_project = project_resolver
        # 测试通过 `service._refresh_material_title_derivatives = AsyncMock()`
        # 实例 monkey patch；本子服务通过 lambda hook 间接调用以兼容契约。
        self._refresh_title_derivatives_via = refresh_material_title_derivatives_hook

    # ==================== Static / Classmethod Helpers ====================

    @classmethod
    def _build_material_db_content(cls, text: Optional[str]) -> Optional[str]:
        """素材正文持久化为全文，避免长文本后半段丢失。"""
        if not text:
            return None
        return text

    @classmethod
    def _build_material_preview_excerpt(cls, text: Optional[str]) -> str:
        """列表/日志用的预览摘要，避免长文本日志过大。"""
        if not text:
            return ""
        return text[: cls._MATERIAL_DB_CONTENT_PREVIEW_LIMIT]

    @staticmethod
    def _attach_effective_title(material: WritingMaterial) -> None:
        """为 ORM 对象挂载前端消费的有效标题。"""
        material.effective_title = get_effective_material_title(material)

    @staticmethod
    def _image_review_status(image: WritingMaterialImage) -> str:
        subtype = str(getattr(image, "business_subtype", "") or "").strip()
        return "needs_review" if subtype in {"", "unknown", "needs_review"} else "validated"

    @staticmethod
    def _build_material_quality_flags(
        material: WritingMaterial,
        *,
        chunk_count: int = 0,
        review_image_count: int = 0,
    ) -> tuple[list[str], str | None]:
        """汇总素材是否真正可被匹配/生成稳定消费。"""
        flags: list[str] = []
        try:
            tags = json.loads(material.metadata_tags or "{}")
        except (TypeError, json.JSONDecodeError):
            tags = {}
        if not isinstance(tags, dict):
            tags = {}

        tag_status = str(tags.get("_tag_status") or "")
        if tag_status == "llm_failed":
            flags.append("llm_failed")
        elif tag_status == "image_skipped":
            flags.append("image_skipped")

        processing_status = str(getattr(material, "processing_status", "") or "")
        material_type = (
            material.material_type.value
            if hasattr(material.material_type, "value")
            else str(material.material_type or "")
        )
        if processing_status == "completed" and chunk_count == 0 and material_type != "image":
            flags.append("no_chunks")

        label_conflict = str(getattr(material, "label_conflict", "") or "")
        if label_conflict and label_conflict != "matched":
            flags.append("label_conflict")
        if review_image_count > 0:
            flags.append("image_needs_review")

        labels = {
            "llm_failed": "素材打标降级",
            "image_skipped": "图片未完成一致性验证",
            "no_chunks": "缺少可检索分块",
            "label_conflict": "素材标签需复核",
            "image_needs_review": "存在待复核图片",
        }
        summary = "；".join(labels.get(flag, flag) for flag in flags) or None
        return flags, summary

    @staticmethod
    def _select_refined_child_title(
        material: WritingMaterial,
        images: list[WritingMaterialImage],
    ) -> str | None:
        """基于结构提示和图片 caption 为低质量标题挑选更好的候选。"""
        current_title = get_original_material_title(material)
        if not is_poor_material_title(current_title):
            return None

        structure_hint = str(getattr(material, "structure_hint_label", "") or "").strip()
        normalized_structure_hint = (
            structure_hint if is_strong_title_candidate(structure_hint) else ""
        )

        normalized_captions = [
            normalize_image_caption_title(getattr(image, "caption", "") or "")
            for image in images
        ]
        strong_captions = [
            caption for caption in normalized_captions
            if is_strong_title_candidate(caption)
        ]

        if len(strong_captions) == 1:
            return strong_captions[0]
        if len(strong_captions) > 1 and len(set(strong_captions)) == 1:
            return strong_captions[0]
        if normalized_structure_hint:
            return normalized_structure_hint
        return None

    @staticmethod
    def _refresh_material_title_labels(
        material: WritingMaterial,
        images: list[WritingMaterialImage],
    ) -> None:
        """刷新标题相关的标签决议，不触发 LLM 打标。"""
        from app.core.writer.filename_classifier import classify_by_filename
        from app.core.writer.label_resolver import resolve_material_label

        effective_title = get_effective_material_title(material)
        _biz_type, detail_label = classify_by_filename(effective_title)
        material.file_name_label = detail_label

        resolution = resolve_material_label(
            file_name_label=material.file_name_label,
            structure_hint_label=getattr(material, "structure_hint_label", None),
            material_content=material.content,
            images=images,
        )
        material.content_label = resolution.content_label
        material.resolved_label = resolution.resolved_label
        material.label_conflict = resolution.conflict_status
        material.label_source = resolution.source

    @staticmethod
    def _default_split_review_status(material: WritingMaterial) -> str:
        confidence = float(getattr(material, "split_confidence", 0.0) or 0.0)
        if confidence and confidence < 0.7:
            return SplitReviewStatus.needs_review.value
        return SplitReviewStatus.confirmed.value

    @staticmethod
    def _dedup_image_pool(
        all_images: list,
    ) -> tuple[list, dict[str, list], dict[str, object]]:
        """基于文件内容哈希对图片池去重

        Returns:
            (unique_images, duplicates_map, hash_to_first)
        """
        hash_to_first: dict[str, object] = {}
        duplicates_map: dict[str, list] = {}
        unique_images: list = []

        for img in all_images:
            fp = getattr(img, "file_path", None)
            if not fp or not Path(fp).exists():
                unique_images.append(img)
                continue

            try:
                h = hashlib.sha256()
                with open(fp, "rb") as f:
                    h.update(f.read(65536))
                file_hash = h.hexdigest()
            except Exception:
                unique_images.append(img)
                continue

            img._file_hash = file_hash

            if file_hash not in hash_to_first:
                hash_to_first[file_hash] = img
                unique_images.append(img)
                duplicates_map[file_hash] = []
            else:
                duplicates_map[file_hash].append(img)

        return unique_images, duplicates_map, hash_to_first

    # ==================== Async Private Helpers ====================

    async def _refresh_material_title_derivatives(
        self,
        material: WritingMaterial,
    ) -> None:
        """根据当前有效标题刷新文件名标签、标签决议与元数据打标。"""
        from app.core.writer.material_tagger import get_material_tagger
        from app.models.writing_outline_node import WritingOutlineNode

        img_result = await self.db.execute(
            select(WritingMaterialImage).where(
                WritingMaterialImage.material_id == material.id,
            ).order_by(WritingMaterialImage.image_index)
        )
        mat_images = list(img_result.scalars().all())
        self._refresh_material_title_labels(material, mat_images)

        if str(getattr(material, "processing_status", "") or "") == "completed":
            outline_result = await self.db.execute(
                select(WritingOutlineNode).where(
                    WritingOutlineNode.project_id == material.project_id,
                ).order_by(WritingOutlineNode.depth, WritingOutlineNode.sort_order)
            )
            outline_nodes = list(outline_result.scalars().all())
            outline_tree_text = "\n".join(
                f"{'  ' * node.depth}- {node.title}" for node in outline_nodes
            ) if outline_nodes else ""
            tagger = get_material_tagger(db=self.db, project_id=material.project_id)
            await tagger.tag(material, outline_tree=outline_tree_text)

    async def _refine_sub_material_titles(
        self,
        processing_materials: list[WritingMaterial],
    ) -> int:
        """在图片分类后用 caption/结构提示精炼低质量子素材标题。"""
        child_materials = [
            material
            for material in processing_materials
            if str(getattr(material, "material_role", "") or "") == MaterialRole.split_child.value
        ]
        if not child_materials:
            return 0

        material_ids = [material.id for material in child_materials]
        image_result = await self.db.execute(
            select(WritingMaterialImage).where(
                WritingMaterialImage.material_id.in_(material_ids)
            ).order_by(
                WritingMaterialImage.material_id,
                WritingMaterialImage.image_index,
            )
        )
        image_map: dict[int, list[WritingMaterialImage]] = {}
        for image in image_result.scalars().all():
            image_map.setdefault(image.material_id, []).append(image)

        refined_count = 0
        for material in child_materials:
            if str(getattr(material, "display_title", "") or "").strip():
                continue
            candidate = self._select_refined_child_title(
                material,
                image_map.get(material.id, []),
            )
            if not candidate or candidate == get_original_material_title(material):
                continue
            material.display_title = candidate
            self._refresh_material_title_labels(
                material,
                image_map.get(material.id, []),
            )
            refined_count += 1
            logger.info(
                "[素材批处理] 子素材标题精炼: id={}, original={!r}, refined={!r}",
                material.id,
                get_original_material_title(material),
                candidate,
            )

        if refined_count > 0:
            await self.db.commit()
        return refined_count

    # ==================== 素材 CRUD / 查询 ====================

    async def list_materials(
        self, project_id: int, user: User,
    ) -> list[WritingMaterial]:
        """素材列表"""
        await self._resolve_project(project_id, user)
        result = await self.db.execute(
            select(WritingMaterial).where(
                WritingMaterial.project_id == project_id,
            ).order_by(WritingMaterial.id)
        )
        materials = list(result.scalars().all())
        if not materials:
            return materials

        material_ids = [material.id for material in materials]
        image_result = await self.db.execute(
            select(
                WritingMaterialImage.material_id,
                func.count(WritingMaterialImage.id),
                func.sum(
                    case(
                        (
                            WritingMaterialImage.business_subtype.notin_(("needs_review", "unknown")),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (
                            or_(
                                WritingMaterialImage.business_subtype.in_(("needs_review", "unknown")),
                                WritingMaterialImage.business_subtype.is_(None),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).where(
                WritingMaterialImage.material_id.in_(material_ids)
            ).group_by(WritingMaterialImage.material_id)
        )
        image_summary_map = {
            row[0]: {
                "image_count": int(row[1] or 0),
                "validated_image_count": int(row[2] or 0),
                "review_image_count": int(row[3] or 0),
            }
            for row in image_result.all()
        }
        chunk_result = await self.db.execute(
            select(
                WritingMaterialChunk.material_id,
                func.count(WritingMaterialChunk.id),
            ).where(
                WritingMaterialChunk.material_id.in_(material_ids)
            ).group_by(WritingMaterialChunk.material_id)
        )
        chunk_count_map = {
            row[0]: int(row[1] or 0)
            for row in chunk_result.all()
        }

        for material in materials:
            summary = image_summary_map.get(material.id, {})
            material.image_count = summary.get("image_count", 0)
            material.validated_image_count = summary.get("validated_image_count", 0)
            material.review_image_count = summary.get("review_image_count", 0)
            flags, quality_summary = self._build_material_quality_flags(
                material,
                chunk_count=chunk_count_map.get(material.id, 0),
                review_image_count=material.review_image_count,
            )
            material.material_quality_flags = flags
            material.material_quality_summary = quality_summary
            self._attach_effective_title(material)

        image_records_result = await self.db.execute(
            select(WritingMaterialImage).where(
                WritingMaterialImage.material_id.in_(material_ids)
            ).order_by(
                WritingMaterialImage.material_id,
                WritingMaterialImage.image_index,
            )
        )
        assets_by_material: dict[int, list[dict[str, object]]] = {}
        for image in image_records_result.scalars().all():
            assets_by_material.setdefault(image.material_id, []).append({
                "id": image.id,
                "caption": image.caption or f"图片{image.image_index + 1}",
                "image_type": image.image_type.value if image.image_type else None,
                "business_subtype": image.business_subtype,
                "review_status": self._image_review_status(image),
            })
        for material in materials:
            material.image_assets = assets_by_material.get(material.id, [])
        return materials

    async def update_material_split_state(
        self,
        project_id: int,
        material_id: int,
        action: str,
        user: User,
    ) -> WritingMaterial:
        """更新拆分素材的使用状态。"""
        await self._resolve_project(project_id, user)
        material = await self.db.get(WritingMaterial, material_id)
        if not material or material.project_id != project_id:
            raise NotFoundError("素材不存在")

        if action in {"use_whole", "use_split"}:
            result = await self.db.execute(
                select(WritingMaterial).where(
                    WritingMaterial.parent_material_id == material.id,
                )
            )
            children = list(result.scalars().all())
            if not children:
                raise ServiceError("当前素材不存在拆分结果")

            if action == "use_whole":
                material.material_role = MaterialRole.normal.value
                material.split_review_status = SplitReviewStatus.confirmed.value
                for child in children:
                    child.split_review_status = SplitReviewStatus.ignored.value
            else:
                material.material_role = MaterialRole.split_parent.value
                material.split_review_status = (
                    SplitReviewStatus.needs_review.value
                    if any(
                        (child.split_confidence or 0.0) < 0.7
                        for child in children
                    )
                    else SplitReviewStatus.confirmed.value
                )
                for child in children:
                    child.split_review_status = self._default_split_review_status(child)
            await self.db.commit()
            await self.db.refresh(material)
            self._attach_effective_title(material)
            material.image_assets = []
            return material

        if material.parent_material_id is None:
            raise ServiceError("仅子素材支持该操作")
        if action == "ignore_child":
            material.split_review_status = SplitReviewStatus.ignored.value
        elif action == "restore_child":
            material.split_review_status = self._default_split_review_status(material)
        else:
            raise ServiceError(f"不支持的素材操作: {action}")
        await self.db.commit()
        await self.db.refresh(material)
        self._attach_effective_title(material)
        material.image_assets = []
        return material

    async def update_material_title(
        self,
        project_id: int,
        material_id: int,
        display_title: str,
        user: User,
    ) -> WritingMaterial:
        """更新素材展示标题。"""
        await self._resolve_project(project_id, user)
        material = await self.db.get(WritingMaterial, material_id)
        if not material or material.project_id != project_id:
            raise NotFoundError("素材不存在")

        normalized = display_title.strip()
        if not normalized:
            raise ServiceError("素材标题不能为空")

        material.display_title = normalized
        await self._refresh_title_derivatives_via(material)
        await self.db.commit()
        await self.db.refresh(material)
        self._attach_effective_title(material)
        material.image_assets = []
        return material

    async def batch_update_material_titles(
        self,
        project_id: int,
        items: list[dict[str, Any]],
        user: User,
    ) -> dict[str, Any]:
        """批量更新素材标题，并统一重算标题衍生字段。"""
        await self._resolve_project(project_id, user)
        if not items:
            raise ServiceError("缺少待更新的素材标题")

        material_ids = [int(item.get("material_id") or 0) for item in items]
        if any(material_id <= 0 for material_id in material_ids):
            raise ServiceError("存在非法素材ID")
        if len(material_ids) != len(set(material_ids)):
            raise ServiceError("批量更新中存在重复素材ID")

        result = await self.db.execute(
            select(WritingMaterial).where(
                WritingMaterial.project_id == project_id,
                WritingMaterial.id.in_(material_ids),
            )
        )
        materials = list(result.scalars().all())
        material_map = {material.id: material for material in materials}
        if len(material_map) != len(material_ids):
            raise NotFoundError("部分素材不存在")

        updated_materials: list[WritingMaterial] = []
        for item in items:
            material_id = int(item["material_id"])
            normalized = str(item.get("display_title") or "").strip()
            if not normalized:
                raise ServiceError("素材标题不能为空")
            material = material_map[material_id]
            current_effective_title = get_effective_material_title(material)
            if normalized == current_effective_title:
                continue
            material.display_title = normalized
            updated_materials.append(material)

        if not updated_materials:
            return {
                "updated_count": 0,
                "updated_material_ids": [],
                "requires_rematch": False,
            }

        for material in updated_materials:
            await self._refresh_title_derivatives_via(material)

        await self.db.commit()
        for material in updated_materials:
            await self.db.refresh(material)
            self._attach_effective_title(material)
            material.image_assets = []

        return {
            "updated_count": len(updated_materials),
            "updated_material_ids": [material.id for material in updated_materials],
            "requires_rematch": True,
        }

    async def get_material_preview_path(
        self,
        project_id: int,
        material_id: int,
        user: User,
    ) -> Path:
        """获取素材原文预览文件路径。"""
        await self._resolve_project(project_id, user)
        material = await self.db.get(WritingMaterial, material_id)
        if not material or material.project_id != project_id:
            raise NotFoundError("素材不存在")
        file_path = Path(str(material.file_path or ""))
        if not file_path.exists():
            raise NotFoundError("素材文件不存在")
        if file_path.suffix.lower() != ".pdf":
            raise ServiceError("当前素材仅支持PDF预览")
        return file_path

    async def save_material(
        self, project_id: int, user: User,
        filename: str, file_bytes: bytes,
    ) -> WritingMaterial:
        """保存素材文件(阶段1: 仅保存,不做LLM处理)

        轻量操作: 校验 -> 保存到磁盘 -> 创建DB记录(status=saved)
        后续由 process_materials_batch() 统一批处理。
        """
        from app.core.config import settings

        await self._resolve_project(project_id, user)

        from app.core.filename import sanitize_filename, resolve_within_dir

        # 清洗不可信的上传文件名, 防止路径穿越/任意覆盖
        safe_name = sanitize_filename(filename, fallback="material")

        ext = os.path.splitext(safe_name)[1].lower()
        allowed = [e.strip() for e in settings.WRITING_SUPPORTED_MATERIAL_TYPES.split(",")]
        if ext not in allowed:
            raise ServiceError(f"不支持的文件类型: {ext}")

        from app.core.convert.office_to_pdf import (
            ALLOWED_UPLOAD_SUFFIXES,
            sniff_upload_magic,
        )

        if ext in ALLOWED_UPLOAD_SUFFIXES and not sniff_upload_magic(file_bytes, ext):
            raise ServiceError(f"文件内容与扩展名不符，请上传真实的 {ext} 文件")

        max_bytes = settings.WRITING_MAX_MATERIAL_SIZE_MB * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise ServiceError(f"文件大小超限: {len(file_bytes)} > {max_bytes}")

        save_dir = Path(settings.UPLOAD_DIR) / str(project_id) / "materials"
        save_dir.mkdir(parents=True, exist_ok=True)
        # 纵深防御: 归一化校验落点未逃逸出 materials 目录
        try:
            file_path = resolve_within_dir(save_dir, safe_name)
        except ValueError as e:
            raise ServiceError(str(e))
        file_path.write_bytes(file_bytes)

        logger.info(
            f"素材文件已保存: project={project_id}, file={filename}, "
            f"size={len(file_bytes)/1024:.1f}KB, type={ext}"
        )

        from app.models.writing_material import WritingMaterial as WM
        from app.core.writer.filename_classifier import classify_by_filename

        _biz_type, detail_label = classify_by_filename(safe_name)

        mat = WM(
            project_id=project_id,
            material_type="text",
            title=safe_name,
            file_path=str(file_path),
            processing_status="saved",
            file_name_label=detail_label,
        )
        self.db.add(mat)
        await self.db.commit()
        await self.db.refresh(mat)

        logger.info(
            f"素材入库完成(待批处理): id={mat.id}, project={project_id}, "
            f"title={filename}, file_name_label={detail_label}"
        )
        self._attach_effective_title(mat)
        mat.image_assets = []
        return mat

    async def process_materials_batch(
        self, project_id: int, user: User,
    ) -> dict:
        """触发素材批处理后台任务(阶段2).

 该入口不再在请求内执行长流程, 而是:
 1. 校验项目归属
 2. 计费预占门: 确保存在 HOLDING 预占承接本批次消耗
 3. 将 saved 素材标记为 processing (幂等)
 4. 通过统一任务契约分派到 worker, 不可用时回退 asyncio
 """
        project = await self._resolve_project(project_id, user)

        result = await self.db.execute(
            select(WritingMaterial).where(
                WritingMaterial.project_id == project_id,
                WritingMaterial.processing_status == "saved",
            )
        )
        pending_materials = list(result.scalars().all())

        if not pending_materials:
            return {"total": 0, "message": "无待处理素材"}

        from app.core.tasks import dispatch_background_job, is_task_active
        from app.core.tasks.writing_material_process_task import (
            get_writing_material_process_task_name,
            run_writing_material_process_task,
        )

        task_name = get_writing_material_process_task_name(project_id)
        if is_task_active(task_name):
            return {
                "total": len(pending_materials),
                "message": "素材处理任务已在进行中",
                "queue_mode": "active",
            }

        from app.core.tasks.orchestrator import guard_writing_material_process

        guard = await guard_writing_material_process(self.db, project_id)
        if guard.decision != "ready":
            return {
                "total": len(pending_materials),
                "message": "素材处理等待前置节点完成",
                "queue_mode": "waiting_dependency",
                "reason": guard.reason,
            }

        # 计费预占门: 已有 HOLDING 复用不重复预扣;
        # hold 已结算后补传素材则新建 hold 承接, 余额不足抛 403
        from app.services.writing.material_billing_gate import (
            ensure_material_billing_hold,
        )
        await ensure_material_billing_hold(self.db, project, project.owner_id)

        for mat in pending_materials:
            mat.processing_status = "processing"
        await self.db.commit()

        from app.services.retry_service import RetryService

        retry_svc = RetryService(self.db)
        run = await retry_svc.begin_writing_material_attempt(
            project_id=project_id,
            user_id=getattr(user, "id", None),
            task_name=task_name,
            explicit_retry=False,
            target_material_ids=[int(mat.id) for mat in pending_materials],
            last_stage="queued",
        )
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_material_process_job",
            fallback_factory=lambda: run_writing_material_process_task(project_id),
            task_type="writing_material_process",
            project_id=project_id,
            arq_args=(project_id,),
        )
        await retry_svc.attach_dispatch_result(
            run,
            queue_mode=queue_mode,
            arq_job_id=arq_job_id,
            last_stage="queued",
        )
        logger.info(
            f"[素材批处理] 触发: project={project_id}, pending={len(pending_materials)}, "
            f"queue_mode={queue_mode}, arq_job_id={arq_job_id}"
        )
        return {
            "total": len(pending_materials),
            "message": "素材批处理已启动",
            "queue_mode": queue_mode,
        }

    async def _execute_material_batch(
        self,
        project_id: int,
        task_name: Optional[str] = None,
    ) -> dict:
        """素材批处理主流程编排 (worker/asyncio 双入口共享实现).

        阶段细节见 ``material_batch/`` 子包；各阶段间状态通过函数参数显式传递，
        主类不持有跨 phase 实例字段。
        """
        from app.core.writer.material_content_extractor import MaterialContentExtractor
        from app.core.writer.material_splitter import MaterialSplitter
        from app.core.arq_runtime import raise_if_project_cancelled

        hb = MaterialBatchHeartbeat(task_name)
        pending_materials = await self._load_pending_materials(project_id)

        if not pending_materials:
            hb.emit(hb.PHASE_COMPLETED, progress_pct=100, message="无待处理素材")
            return {"total": 0, "message": "无待处理素材"}

        await self._force_processing_status(pending_materials)
        total = len(pending_materials)
        logger.info(f"[素材批处理] 启动: project={project_id}, 待处理={total}个")

        extractor = MaterialContentExtractor(enable_vl=True)
        splitter = MaterialSplitter()

        # Phase 1 + 1.6: 解析 + PDF 拆分
        raise_if_project_cancelled(project_id)
        hb.emit(hb.PHASE_PARSING, progress_pct=5, message=f"解析文档 0/{total}")
        parse_results, failed = await phase1_parse.parse_documents(
            self, pending_materials, extractor, project_id,
        )
        children, split_children = await phase1_parse.split_pdf_drafts(
            self, pending_materials, parse_results, splitter,
        )
        processing_materials: list[WritingMaterial] = list(pending_materials) + children
        all_images = phase1_parse.collect_image_pool(processing_materials, parse_results)
        total = len(processing_materials)
        self._log_phase1_completion(total, split_children, len(all_images), failed)

        # Phase 1.5: 跨素材图片去重
        unique_images, duplicates_map, hash_to_first = self._dedup_image_pool(all_images)
        self._log_dedup_savings(len(all_images), len(unique_images))

        # Phase 2 + 2.5: VL 识别 + 回填重复图
        raise_if_project_cancelled(project_id)
        await phase2_vl.recognize_vl_pool(unique_images, project_id, hb)
        phase2_vl.backfill_duplicates(duplicates_map, hash_to_first)
        # Phase 2.6: OCR 计费计量归集, 落库由 Phase 3 事务收口
        batch_ocr_total = phase2_vl.collect_ocr_usage(
            processing_materials, parse_results,
        )
        if batch_ocr_total > 0:
            logger.info(
                f"[素材批处理] OCR计量归集: project={project_id}, "
                f"ocr_images={batch_ocr_total}"
            )

        # Phase 3 + 4a: 组装 + 向量化
        raise_if_project_cancelled(project_id)
        completed, failed_inc, successfully_indexed_ids, indexing_substage = (
            await phase3_assemble.assemble_and_index(
                self, project_id, processing_materials, parse_results, extractor, hb,
            )
        )
        failed += failed_inc

        # Phase 3.5 + 3.55 + 3.6 + 4b: 图片分类 + 标题精炼 + 标签决议 + 打标
        await phase4_postprocess.run_postprocess(
            self, project_id, processing_materials, parse_results,
            hash_to_first, successfully_indexed_ids, hb,
        )

        # Phase 5: 终态写入 + 完成心跳
        raise_if_project_cancelled(project_id)
        finalized_count = await self._finalize_terminal_state(
            processing_materials, successfully_indexed_ids,
        )
        hb.emit(
            hb.PHASE_COMPLETED,
            progress_pct=100,
            message=f"完成 total={total}, ok={completed}, failed={failed}",
        )
        self._log_batch_completion(
            project_id, total, completed, failed, finalized_count, indexing_substage,
        )
        return {"total": total, "completed": completed, "failed": failed}

    async def _load_pending_materials(
        self, project_id: int,
    ) -> list[WritingMaterial]:
        """加载待批处理的素材列表 (status in saved/processing)。"""
        result = await self.db.execute(
            select(WritingMaterial).where(
                WritingMaterial.project_id == project_id,
                WritingMaterial.processing_status.in_(("saved", "processing")),
            )
        )
        return list(result.scalars().all())

    async def _force_processing_status(
        self, pending_materials: list[WritingMaterial],
    ) -> None:
        """把 saved 素材统一改为 processing, 与原行为一致单次 commit。"""
        for mat in pending_materials:
            if mat.processing_status != "processing":
                mat.processing_status = "processing"
        await self.db.commit()

    async def _finalize_terminal_state(
        self,
        processing_materials: list[WritingMaterial],
        successfully_indexed_ids: set[int],
    ) -> int:
        """Phase 5: 把成功索引的素材统一写回 completed 终态, 返回 finalized 数。"""
        finalized_count = 0
        for mat in processing_materials:
            if mat.id in successfully_indexed_ids and mat.processing_status != "failed":
                mat.processing_status = "completed"
                finalized_count += 1
        await self.db.commit()
        return finalized_count

    def _log_batch_completion(
        self,
        project_id: int,
        total: int,
        completed: int,
        failed: int,
        finalized_count: int,
        indexing_substage: dict[str, Any],
    ) -> None:
        """完成日志: 与原 ``_execute_material_batch`` 末尾 logger 字段集 byte-equal。"""
        logger.info(
            f"[素材批处理] 完成: project={project_id}, "
            f"total={total}, completed={completed}, failed={failed}, "
            f"finalized={finalized_count}, "
            f"encode_degraded={indexing_substage['degraded_to_cpu']}, "
            f"stick_to_cpu={indexing_substage['stick_to_cpu']}"
        )

    @staticmethod
    def _log_phase1_completion(
        total: int, split_children: int, image_pool_size: int, failed: int,
    ) -> None:
        """Phase 1 收尾日志: 与原 _execute_material_batch L763-766 byte-equal。"""
        logger.info(
            f"[素材批处理] Phase1完成: 处理单元={total}个, "
            f"拆分子素材={split_children}个, 图片池={image_pool_size}张, 失败={failed}个"
        )

    @staticmethod
    def _log_dedup_savings(original_count: int, unique_count: int) -> None:
        """Phase 1.5 去重日志: 与原 _execute_material_batch L772-777 byte-equal。"""
        saved = original_count - unique_count
        if saved > 0:
            logger.info(
                f"[素材批处理] 图片去重: 原始={original_count}, "
                f"唯一={unique_count}, 节省={saved}次VL调用"
            )

    # ==================== 图片去重与持久化 ====================

    async def _persist_and_classify_images(
        self,
        project_id: int,
        pending_materials: list[WritingMaterial],
        parse_results: dict,
        hash_to_first: dict[str, object],
    ) -> None:
        """Phase 3.5: 图片记录持久化 + 分类 + 条件化结构提取"""
        from app.core.tasks.writing_image_llm_task import (
            WRITING_IMAGE_CLASSIFY_JOB,
            enqueue_image_llm_job,
            wait_for_image_llm_result,
        )

        created_count = 0
        classified_count = 0
        unknown_count = 0
        needs_review_count = 0
        invalid_subtype_count = 0
        validated_subtype_count = 0
        classify_items: list[dict[str, Any]] = []
        classify_records: list[WritingMaterialImage] = []

        for mat in pending_materials:
            if mat.processing_status == "failed":
                continue
            pr = parse_results.get(mat.id)
            if not pr:
                continue

            for idx, img in enumerate(pr.images):
                fp = getattr(img, "file_path", None)
                if not fp:
                    continue

                file_hash = getattr(img, "_file_hash", None)
                vl_text = getattr(img, "visual_content", "") or ""
                w = getattr(img, "width", 0) or 0
                h = getattr(img, "height", 0) or 0

                img_record = WritingMaterialImage(
                    material_id=mat.id,
                    image_path=str(fp),
                    image_index=idx,
                    vl_content=vl_text[:10000] if vl_text else None,
                    width=w,
                    height=h,
                    file_hash=file_hash,
                )

                if vl_text:
                    img_record._material_title = get_effective_material_title(mat)
                    classify_records.append(img_record)

                self.db.add(img_record)
                created_count += 1

        await self.db.flush()
        for record in classify_records:
            classify_items.append({
                "image_id": record.id,
                "material_title": getattr(record, "_material_title", "") or "",
                "image_index": record.image_index,
                "width": record.width,
                "height": record.height,
                "vl_content": (record.vl_content or "")[:10000],
            })

        await self.db.commit()

        if classify_items:
            batch_id = f"writing-image-classify-{project_id}-{uuid.uuid4().hex}"
            queued, reason = await enqueue_image_llm_job(
                function_name=WRITING_IMAGE_CLASSIFY_JOB,
                project_id=project_id,
                batch_id=batch_id,
                items=classify_items,
            )
            if queued:
                result = await wait_for_image_llm_result(batch_id, project_id=project_id)
                stats = result.get("stats") if isinstance(result, dict) else None
                if isinstance(stats, dict):
                    classified_count = int(stats.get("classified") or 0)
                    validated_subtype_count = int(stats.get("validated") or 0)
                    needs_review_count = int(stats.get("needs_review") or 0)
                    unknown_count = int(stats.get("unknown") or 0)
                    invalid_subtype_count = int(stats.get("invalid_subtype") or 0)
                else:
                    unknown_count = len(classify_items)
                    await self._mark_unclassified_images_unknown(classify_items)
                    logger.warning(
                        "[素材批处理] 图片分类子任务无有效结果, batch={}, status={}",
                        batch_id,
                        result.get("status") if isinstance(result, dict) else None,
                    )
            else:
                unknown_count = len(classify_items)
                await self._mark_unclassified_images_unknown(classify_items)
                logger.warning(
                    "[素材批处理] 图片分类子任务未入队, 降级 unknown: reason={}, count={}",
                    reason,
                    len(classify_items),
                )
            for record in classify_records:
                self.db.expire(
                    record,
                    ["image_type", "caption", "business_subtype", "structured_fields"],
                )

        logger.info(
            f"[素材批处理] Phase3.5完成: "
            f"图片记录={created_count}, 分类成功={classified_count}, "
            f"validated={validated_subtype_count}, "
            f"needs_review={needs_review_count}, unknown={unknown_count}, "
            f"invalid_subtype={invalid_subtype_count}"
        )

    async def _mark_unclassified_images_unknown(
        self,
        classify_items: list[dict[str, Any]],
    ) -> None:
        """图片分类子任务不可用时, 将待分类图片显式降级为 unknown。"""

        for item in classify_items:
            image_id = int(item.get("image_id") or 0)
            if not image_id:
                continue
            record = await self.db.get(WritingMaterialImage, image_id)
            if record is None:
                continue
            record.image_type = ImageType.other
            record.business_subtype = "unknown"
        await self.db.commit()

    async def _resolve_material_labels(
        self,
        project_id: int,
        pending_materials: list[WritingMaterial],
        ready_material_ids: Optional[set[int]] = None,
    ) -> None:
        """Phase 3.6: 交叉校验文件名标签与内容识别标签, 产出决议"""
        from app.core.writer.label_resolver import resolve_material_label

        resolved_count = 0
        conflict_count = 0
        needs_review_count = 0
        unknown_count = 0

        for mat in pending_materials:
            if ready_material_ids is not None:
                if mat.id not in ready_material_ids:
                    continue
            elif mat.processing_status != "completed":
                continue

            mat_images = []
            try:
                img_result = await self.db.execute(
                    select(WritingMaterialImage).where(
                        WritingMaterialImage.material_id == mat.id,
                    ).order_by(WritingMaterialImage.image_index)
                )
                mat_images = list(img_result.scalars().all())
            except Exception as e:
                logger.warning(
                    f"[标签决议] 加载图片失败: mat={mat.id}, err={e}"
                )

            result = resolve_material_label(
                file_name_label=mat.file_name_label,
                structure_hint_label=getattr(mat, "structure_hint_label", None),
                material_content=mat.content,
                images=mat_images,
            )

            mat.content_label = result.content_label
            mat.resolved_label = result.resolved_label
            mat.label_conflict = result.conflict_status
            mat.label_source = result.source
            if result.resolution_reason:
                logger.info(
                    "[标签决议] mat={}, content_label={!r}, resolved_label={!r}, conflict={!r}, source={!r}, reason={}",
                    mat.id,
                    result.content_label,
                    result.resolved_label,
                    result.conflict_status,
                    result.source,
                    result.resolution_reason,
                )

            resolved_count += 1
            if result.conflict_status == "conflicted":
                conflict_count += 1
            if result.conflict_status == "needs_review":
                needs_review_count += 1
            if any(
                (getattr(img, "business_subtype", "") or "") == "unknown"
                for img in mat_images
            ):
                unknown_count += 1

        await self.db.commit()
        logger.info(
            f"[素材批处理] Phase3.6完成: "
            f"决议={resolved_count}, 冲突={conflict_count}, "
            f"needs_review={needs_review_count}, unknown={unknown_count}"
        )

    async def get_batch_process_status(
        self, project_id: int, user: User,
    ) -> dict:
        """查询素材批处理状态。"""
        from app.core.config import settings
        from app.core.tasks import inspect_task_liveness
        from app.core.tasks.writing_material_process_task import (
            get_writing_material_process_task_name,
        )

        await self._resolve_project(project_id, user)

        result = await self.db.execute(
            select(WritingMaterial).where(
                WritingMaterial.project_id == project_id,
            )
        )
        all_mats = list(result.scalars().all())

        saved = sum(1 for m in all_mats if m.processing_status == "saved")
        processing = sum(1 for m in all_mats if m.processing_status == "processing")
        completed_count = sum(1 for m in all_mats if m.processing_status == "completed")
        failed_count = sum(1 for m in all_mats if m.processing_status == "failed")

        task_name = get_writing_material_process_task_name(project_id)
        liveness = inspect_task_liveness(
            task_name,
            heartbeat_timeout=int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", 180)),
            arq_function="writing_material_process_job",
        )
        heartbeat = liveness.heartbeat or {}

        hb_phase = str(heartbeat.get("phase") or heartbeat.get("stage") or "").strip()
        vl_progress = heartbeat.get("vl_progress", "")
        if not isinstance(vl_progress, str):
            vl_progress = str(vl_progress)

        _FRONTEND_PHASE_ALIAS = {
            "label_resolve": "tagging",
            "starting": "parsing",
            "queued": "parsing",
        }
        _TERMINAL_HB_PHASES = {"completed", "done", "failed", "cancelled"}

        task_is_live = liveness.is_live
        hb_non_terminal = (
            bool(hb_phase)
            and hb_phase not in _TERMINAL_HB_PHASES
            and not liveness.is_stale
            and not liveness.is_missing
        )

        if task_is_live or hb_non_terminal:
            if hb_phase and hb_phase not in _TERMINAL_HB_PHASES:
                phase = _FRONTEND_PHASE_ALIAS.get(hb_phase, hb_phase)
            else:
                phase = "parsing"
        else:
            if hb_phase in {"completed", "done"}:
                phase = "done"
            elif hb_phase == "failed":
                phase = "failed"
            elif liveness.state in {"stale", "missing"} and processing > 0:
                phase = "failed"
            elif saved == 0 and processing == 0:
                phase = "idle"
            else:
                phase = "parsing"

        from app.services.retry_service import RetryService

        retry_state = await RetryService(self.db).get_writing_material_retry_state(project_id)
        return {
            "total": saved + processing,
            "completed": completed_count,
            "failed": failed_count,
            "processing": processing,
            "phase": phase,
            "vl_progress": vl_progress,
            "task_active": task_is_live,
            "heartbeat_phase": hb_phase or None,
            **retry_state,
        }

    async def delete_material(
        self, project_id: int, material_id: int, user: User,
    ) -> None:
        """删除素材(含级联删除关联的分块向量)"""
        await self._resolve_project(project_id, user, require_owner=True)
        mat = await self.db.get(WritingMaterial, material_id)
        if not mat or mat.project_id != project_id:
            raise NotFoundError("素材不存在")

        from app.core.writer.material_indexer import MaterialIndexer
        indexer = MaterialIndexer(self.db)
        result = await self.db.execute(
            select(WritingMaterial.id).where(
                (WritingMaterial.id == material_id)
                | (WritingMaterial.parent_material_id == material_id)
            )
        )
        target_ids = [row[0] for row in result.all()]
        for mid in target_ids:
            deleted_chunks = await indexer.delete_material_chunks(mid)
            if deleted_chunks > 0:
                logger.info(f"级联删除素材分块: material_id={mid}, deleted={deleted_chunks}")

        if target_ids:
            await self.db.execute(
                delete(WritingMaterialImage).where(
                    WritingMaterialImage.material_id.in_(target_ids)
                )
            )
            await self.db.execute(
                delete(WritingBinding).where(
                    WritingBinding.material_id.in_(target_ids)
                )
            )
            await self.db.execute(
                delete(WritingMaterial).where(
                    WritingMaterial.id.in_(target_ids)
                )
            )
        await self.db.commit()

    async def get_material_checklist(
        self, project_id: int, user: User,
    ) -> list[dict]:
        """素材缺失清单"""
        await self._resolve_project(project_id, user)
        from app.core.writer.outline_generator import get_outline_generator
        generator = get_outline_generator(db=self.db, project_id=project_id)
        checklist = await generator.generate_material_checklist(project_id)
        return [item.model_dump() for item in checklist]


__all__ = ["MaterialService"]
