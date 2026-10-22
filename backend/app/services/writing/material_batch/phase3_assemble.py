"""素材批处理 Phase 3 + 4a：VL 结果组装 + 落库 + 向量化索引（R2-4 Step 5）

承担原 ``MaterialService._execute_material_batch`` 内 L885-974 段职责，
保持以下契约：
- 心跳一次：``indexing`` @ ``62%|组装内容并写入索引``
- 每个素材：assemble_with_vl → 落 ``content`` + ``material_type`` → 长文本日志 →
  向量化索引（失败降级） → 累加 ``completed`` / ``successfully_indexed_ids``
- ``indexing_substage`` 字典语义保持：``degraded_to_cpu`` / ``fallback_reason`` /
  ``stick_to_cpu``，仅 ``degraded_to_cpu=True`` 时输出末尾 info 日志
- ``MaterialIndexer.index_material`` 调用参数集合零变化（含
  ``task_stick_to_cpu=indexing_substage["stick_to_cpu"]``）
- 异常分支：``mat.processing_status = "failed"`` 副作用与原行为一致

返回 ``(completed_inc, failed_inc, successfully_indexed_ids, indexing_substage)``，
``_inc`` 表示本阶段新增数，调用方负责累加。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from app.models.writing_material import MaterialType, WritingMaterial

if TYPE_CHECKING:
    from app.core.writer.material_content_extractor import (
        MaterialContentExtractor,
        ParseResult,
    )
    from app.services.writing.material_batch.heartbeat import MaterialBatchHeartbeat
    from app.services.writing.material_service import MaterialService


async def assemble_and_index(
    service: "MaterialService",
    project_id: int,
    processing_materials: list[WritingMaterial],
    parse_results: dict[int, "ParseResult"],
    extractor: "MaterialContentExtractor",
    hb: "MaterialBatchHeartbeat",
) -> tuple[int, int, set[int], dict[str, Any]]:
    """Phase 3 (组装+落库) + Phase 4a (向量化索引)。"""
    from app.core.writer.material_indexer import MaterialIndexer, chunk_ids_to_json

    hb.emit(hb.PHASE_INDEXING, progress_pct=62, message="组装内容并写入索引")
    indexer = MaterialIndexer(service.db)

    completed_inc = 0
    failed_inc = 0
    successfully_indexed_ids: set[int] = set()
    indexing_substage: dict[str, Any] = {
        "degraded_to_cpu": False,
        "fallback_reason": None,
        "stick_to_cpu": False,
    }

    def _on_material_index_phase(
        _phase: str,
        _done: int,
        _total: int,
        meta: dict[str, Any],
    ) -> None:
        if meta.get("degraded_to_cpu"):
            indexing_substage["degraded_to_cpu"] = True
        new_reason = meta.get("fallback_reason")
        if new_reason:
            indexing_substage["fallback_reason"] = new_reason
        if meta.get("stick_to_cpu"):
            indexing_substage["stick_to_cpu"] = True

    for mat in processing_materials:
        if mat.processing_status == "failed":
            continue

        pr = parse_results.get(mat.id)
        if not pr:
            mat.processing_status = "failed"
            failed_inc += 1
            continue

        try:
            content_result = extractor.assemble_with_vl(pr)
            full_text = service._build_material_db_content(content_result.text)
            mat.material_type = MaterialType(content_result.material_type)
            mat.content = full_text
            await service.db.commit()
            if full_text and len(full_text) > service._MATERIAL_DB_CONTENT_PREVIEW_LIMIT:
                logger.info(
                    "[素材批处理] 长文本保留全文: id={}, full_chars={}, preview_chars={}",
                    mat.id,
                    len(full_text),
                    len(service._build_material_preview_excerpt(full_text)),
                )

            # Phase 4a: 向量化
            indexed_ok = False
            if full_text and len(full_text.strip()) > 50:
                try:
                    chunk_ids = await indexer.index_material(
                        material_id=mat.id,
                        project_id=project_id,
                        content=full_text,
                        page_texts=content_result.parsed_pages or content_result.page_texts or None,
                        phase_callback=_on_material_index_phase,
                        task_stick_to_cpu=indexing_substage["stick_to_cpu"],
                    )
                    mat.chunk_ids = chunk_ids_to_json(chunk_ids)
                    await service.db.commit()
                    indexed_ok = True
                except Exception as e:
                    logger.warning(
                        f"[素材批处理] 向量化失败: id={mat.id}, err={e}"
                    )
            else:
                indexed_ok = True

            if indexed_ok:
                successfully_indexed_ids.add(mat.id)
                completed_inc += 1
        except Exception as e:
            logger.warning(
                f"[素材批处理] 组装/索引失败: id={mat.id}, err={e}"
            )
            mat.processing_status = "failed"
            failed_inc += 1

    await service.db.commit()
    if indexing_substage["degraded_to_cpu"]:
        logger.info(
            "[素材批处理] 索引阶段降级统计: reason={}, stick_to_cpu={}",
            indexing_substage["fallback_reason"],
            indexing_substage["stick_to_cpu"],
        )

    return completed_inc, failed_inc, successfully_indexed_ids, indexing_substage


__all__ = ["assemble_and_index"]
