"""素材批处理 Phase 3.5 + 3.55 + 3.6 + 4b：图片分类 + 标题精炼 + 标签决议 + 打标
(R2-4 Step 6)

承担原 ``MaterialService._execute_material_batch`` 内 L795-852 段职责。
``_persist_and_classify_images`` / ``_resolve_material_labels`` /
``_refine_sub_material_titles`` 三个 method 保留在 MaterialService 主类
（测试通过 ``service.xxx(...)`` 直接调用）；本模块仅承担阶段编排 + 心跳上报 +
取消检查 + MaterialTagger 打标循环。

心跳契约（与原闭包 byte-equal）：
- ``image_classify`` @ ``80%|图片分类与结构化``
- ``label_resolve`` @ ``88%|标签交叉校验``
- ``tagging`` @ ``92%|素材打标与分类``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from app.models.writing_material import WritingMaterial

if TYPE_CHECKING:
    from app.core.writer.material_content_extractor import ParseResult
    from app.services.writing.material_batch.heartbeat import MaterialBatchHeartbeat
    from app.services.writing.material_service import MaterialService


async def run_postprocess(
    service: "MaterialService",
    project_id: int,
    processing_materials: list[WritingMaterial],
    parse_results: dict[int, "ParseResult"],
    hash_to_first: dict[str, object],
    successfully_indexed_ids: set[int],
    hb: "MaterialBatchHeartbeat",
) -> None:
    """Phase 3.5 + 3.55 + 3.6 + 4b 编排。

    各内部步骤之间无返回值依赖（仅副作用），按原顺序串行执行；
    每个 phase 前重新检查项目取消（与原 ``raise_if_project_cancelled`` 节奏一致）。
    """
    from sqlalchemy import select

    from app.core.arq_runtime import raise_if_project_cancelled
    from app.core.writer.material_tagger import get_material_tagger
    from app.models.writing_outline_node import WritingOutlineNode as _OutlineNode

    # Phase 3.5: 图片记录持久化 + 分类 + 结构化提取
    raise_if_project_cancelled(project_id)
    hb.emit(hb.PHASE_IMAGE_CLASSIFY, progress_pct=80, message="图片分类与结构化")
    await service._persist_and_classify_images(
        project_id, processing_materials, parse_results, hash_to_first,
    )

    # Phase 3.55: 用图片 caption / 结构提示精炼低质量子素材标题
    refined_titles = await service._refine_sub_material_titles(processing_materials)
    if refined_titles > 0:
        logger.info(
            "[素材批处理] 子素材标题精炼完成: project={}, refined={}",
            project_id,
            refined_titles,
        )

    # Phase 3.6: 标签交叉校验与决议
    raise_if_project_cancelled(project_id)
    hb.emit(hb.PHASE_LABEL_RESOLVE, progress_pct=88, message="标签交叉校验")
    await service._resolve_material_labels(
        project_id,
        processing_materials,
        ready_material_ids=successfully_indexed_ids,
    )

    # Phase 4b: 打标(审查模型)
    raise_if_project_cancelled(project_id)
    hb.emit(hb.PHASE_TAGGING, progress_pct=92, message="素材打标与分类")
    tagger = get_material_tagger(db=service.db, project_id=project_id)

    outline_result = await service.db.execute(
        select(_OutlineNode).where(
            _OutlineNode.project_id == project_id,
        ).order_by(_OutlineNode.depth, _OutlineNode.sort_order)
    )
    _outline_nodes = list(outline_result.scalars().all())
    outline_tree_text = "\n".join(
        f"{'  ' * n.depth}- {n.title}" for n in _outline_nodes
    ) if _outline_nodes else ""

    indexed_materials = [
        mat for mat in processing_materials
        if mat.id in successfully_indexed_ids
    ]
    await tagger.tag_batch(
        indexed_materials,
        outline_tree=outline_tree_text,
        auto_retry_failed=True,
    )


__all__ = ["run_postprocess"]
