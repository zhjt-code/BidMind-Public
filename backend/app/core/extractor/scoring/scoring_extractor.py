"""评分项提取器（pdf_direct 主路径入口与落库）。

职责: 定位评分 chunk → 委托 pdf_direct 直读主路径(零chunk扫描件落VLM入口) →
      将产物持久化为 scoring_item 类型招标要求（带满分/评分规则/重点评审标记）。
不做: 不做普通招标要求提取（tender_extractor）、不做直读编排（pdf_chat/
      direct_primary 职责）、不做门禁/判绿（rescue_rounds/scoring_green_verdict
      职责）、不做表格解析。

阶段3(2026-08-31 用户裁决): 确定性生产链(chunk遍历/LLM兜底/去重吸收/卷定义
组装/L2修补/L3尾部/VLM第二梯队)已删除, 评分提取宪法见 extractor.mdc。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import LLMClient, get_review_client
from app.core.extractor.scoring.pdf_chat.direct_primary import try_pdf_direct_primary
from app.core.extractor.scoring.scoring_fidelity import (
    FidelityAnnotation,
    FidelityKey,
    build_fidelity_key,
)
from app.core.extractor.scoring.scoring_item_metadata import build_scoring_item_metadata
from app.core.extractor.scoring.scoring_outcome import ScoringStructureOutcome
from app.core.extractor.scoring.scoring_pipeline_core import (
    # backfill_source_pages 为既有对外导出点(多个测试与审计脚本自此导入), 治理阶段4
    # 实现迁入 pipeline_core 后在此保留再导出, 避免破坏既有导入路径。
    backfill_source_pages,  # noqa: F401
)
from app.core.extractor.scoring.scoring_progress import emit_scoring_progress
from app.core.extractor.scoring.verdict_probe_store import stamp_document_verdict_probe
from app.core.review_contracts import serialize_json_list
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.requirement import (
    Requirement,
    RequirementCategory,
    non_manual_scoring_metadata_filter,
)
from app.schemas.requirement import ExtractedScoringItem

_CATEGORY_MAP = {
    "QUAL": RequirementCategory.QUAL,
    "TECH": RequirementCategory.TECH,
    "COMM": RequirementCategory.COMM,
    "PERF": RequirementCategory.PERF,
    "STAFF": RequirementCategory.STAFF,
    "FORMAT": RequirementCategory.FORMAT,
}


def normalize_score_weight(raw: Any) -> Optional[float]:
    """归一评分项满分: 无有效正分值 → None(资格性/符合性审查等"重点但无分值"说明项)。

 根因: scoring_extract Prompt 对"分值不明确项"指示填 0, 而 0 与"无分值"
 语义混淆, 前端按 != null 判断会显示误导性"评分项 0分·重点"。落库前统一: <=0 → None。
 """
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


class ScoringExtractor:
    """评分项提取器。"""

    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        project_id: Optional[int] = None,
        max_retries: int = 2,
        progress_callback: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.llm_client = llm_client or get_review_client(project_id=project_id)
        self.project_id = project_id  # VLM救援通道取vision池客户端需项目绑定
        self.max_retries = max_retries
        self.progress_callback = progress_callback

    async def extract_from_document(
        self, document_id: int, save_to_db: bool = True
    ) -> list[Requirement]:
        """从评分表整表 chunk 提取评分项并落库（审查侧 requirements 落库入口）。"""
        outcome = await self.collect_scoring_structure(document_id)
        if save_to_db and outcome.verdict_probe:
            # 阶段2裁决探针: 零提取(amber)同为样本, 故先于 items 判空写入
            await stamp_document_verdict_probe(
                self.db, document_id, outcome.verdict_probe, flow="review"
            )
        if not outcome.items:
            return []
        if save_to_db:
            emit_scoring_progress(
                self.progress_callback, "persist", 0, 1, "保存评分项"
            )
            saved = await self._save_items(
                document_id,
                outcome.items,
                outcome.report,
                outcome.fidelity_sidecar,
                outcome.parse_diagnostics,
            )
            from app.core.extractor.scoring.operational_assessment import (
                stamp_requirement_assessment_flags,
            )
            await stamp_requirement_assessment_flags(self.db, document_id, saved)
            logger.info(f"[评分项提取] 已保存 {len(saved)} 条评分项")
            emit_scoring_progress(
                self.progress_callback,
                "persist",
                1,
                1,
                f"已保存 {len(saved)} 条评分项",
            )
            return saved
        return []

    async def collect_scoring_structure(
        self, document_id: int
    ) -> ScoringStructureOutcome:
        """定位评分 chunk 并委托 pdf_direct 直读主路径提取(不落库)。

        职责: 收口"评分chunk定位 → 直读主路径(零chunk落VLM入口) → 产物"。
        不做: 不做直读编排、不做门禁/判绿、不做表格解析。
        """
        started = time.perf_counter()
        chunks = await self._get_scoring_chunks(document_id)
        return await try_pdf_direct_primary(self, document_id, chunks, started)

    async def _get_scoring_chunks(self, document_id: int) -> list[Chunk]:
        result = await self.db.execute(
            select(Chunk)
            .where(
                Chunk.document_id == document_id,
                Chunk.chunk_type == "scoring_table",
            )
            .order_by(Chunk.chunk_index)
        )
        return list(result.scalars().all())

    async def _save_items(
        self,
        document_id: int,
        items: list[ExtractedScoringItem],
        report,
        fidelity_sidecar: Optional[dict[FidelityKey, FidelityAnnotation]] = None,
        parse_diagnostics: Optional[list[str]] = None,
    ) -> list[Requirement]:
        doc = (
            await self.db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        if not doc:
            return []

        # 幂等：清理该文档既有"未确认的自动评分项"，避免重跑重复。
        # 自动项无 source 键(SQL NULL), 过滤必须 NULL 安全否则漏删。
        await self.db.execute(
            delete(Requirement).where(
                Requirement.document_id == document_id,
                Requirement.requirement_kind == "scoring_item",
                Requirement.confirmed == 0,
                non_manual_scoring_metadata_filter(),
            )
        )

        saved: list[Requirement] = []
        for it in items:
            category = _CATEGORY_MAP.get(it.category, RequirementCategory.TECH)
            # 无有效正分值(资格性/符合性审查等说明项)落 null, 避免前端显示误导性"0分·重点"
            effective_weight = normalize_score_weight(it.score_weight)
            # 字段清单单点维护于 scoring_item_metadata.build_scoring_item_metadata
            scoring_meta = build_scoring_item_metadata(
                it, effective_weight=effective_weight, totals_needs_review=report.needs_review,
            )
            if parse_diagnostics:
                scoring_meta["parse_diagnostics"] = list(parse_diagnostics)
            # P0 A4: 写入 LLM 兜底忠实性标记（仅 sidecar 命中时写入，确定性路径无此字段）
            if fidelity_sidecar:
                fidelity_ann = fidelity_sidecar.get(build_fidelity_key(it))
                if fidelity_ann is not None:
                    scoring_meta["fidelity_flag"] = fidelity_ann.flag
                    if fidelity_ann.note:
                        scoring_meta["fidelity_note"] = fidelity_ann.note
                    scoring_meta["fidelity_overlap"] = round(fidelity_ann.overlap_score, 3)
            req = Requirement(
                project_id=doc.project_id,
                document_id=document_id,
                category=category,
                description=it.description,
                is_mandatory=0,
                source_page=(it.source_pages[0] if it.source_pages else None),
                source_pages=serialize_json_list(it.source_pages),
                keywords=serialize_json_list([it.score_item_name]),
                requirement_kind="scoring_item",
                score_weight=effective_weight,
                is_key_review=1,
                scoring_metadata=scoring_meta,
                review_relevance="actionable",
                confirmed=0,
            )
            self.db.add(req)
            saved.append(req)

        await self.db.commit()
        for req in saved:
            await self.db.refresh(req)
        return saved


def get_scoring_extractor(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    project_id: Optional[int] = None,
) -> ScoringExtractor:
    """获取评分项提取器实例。"""
    return ScoringExtractor(db=db, llm_client=llm_client, project_id=project_id)
