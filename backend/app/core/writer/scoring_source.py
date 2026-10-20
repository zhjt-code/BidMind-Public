"""标书编写 -- 评分项权威提取源

职责: 调用审查侧共享评分提取核心(ScoringExtractor.collect_scoring_structure)，把评分表
 结构化评分项以权威分值替换写标 LLM 自由提取的 scoring_criterion 约束并落库；
 同步 stamp 裁决探针(零项也写, 与审查零提取同为看板样本)。
不做: 不做表格解析与合计校验(extractor/scoring 职责)、不做散落条目归并(scoring_clause_link)、
 不做确认页报告构建(services/writing/scoring_report)。

定位: 写标招标文件与审查走同一 process 管线，已生成跨页整表 scoring_table chunk。写标原用
 LLM 从普通文本 chunk 猜分值(不跨页/无校验)，本模块改用表头驱动确定性解析产出权威评分项，
 解决评分项提取不全/分值不准/无 100 分校验问题(复用审查侧 打磨的能力)。
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extractor.scoring.scoring_extractor import (
    ScoringExtractor,
    normalize_score_weight,
)
from app.core.extractor.scoring.verdict_probe_store import stamp_document_verdict_probe
from app.core.writer.scoring_constants import SCORING_AUTHORITY_SOURCE
from app.core.extractor.scoring.operational_assessment import stamp_clause_assessment_flags
from app.models.chunk import Chunk
from app.models.writing_clause import WritingClause, WritingClauseLayer
from app.schemas.requirement import ExtractedScoringItem

_SCORING_SUB_TYPE = "scoring_criterion"


def _to_scoring_clause(
    project_id: int,
    document_id: int,
    item: ExtractedScoringItem,
    *,
    sort_order: int,
    totals_needs_review: bool,
) -> WritingClause:
    """将权威评分项转换为 generation_guide 层的 scoring_criterion 约束(落库形态与 LLM 条目同构)。"""
    pages = sorted({p for p in (item.source_pages or []) if isinstance(p, int) and p >= 1})
    page_start = pages[0] if pages else None
    page_end = pages[-1] if pages else None
    effective_weight = normalize_score_weight(item.score_weight)
    metadata_json = {
        "score_item_name": item.score_item_name,
        "max_score": effective_weight,
        "score_rules": list(item.score_rules or []),
        "deduction_rules": list(item.deduction_rules or []),
        "volume": item.volume,
        # 多包文档 lot 必须落库, 否则写标核计横幅把两包分值混算成 200/100 误报
        "lot": item.lot,
        # 多方案文档 scheme 同理必须落库, 写标核计按方案分组
        "scheme": getattr(item, "scheme", None),
        # 阶段3: 事实类型透传(penalty/bonus 调整项不参与写标核计)
        "fact_type": getattr(item, "fact_type", None) or "base_score",
        "volume_weight": item.volume_weight,
        "volume_max": item.volume_max,
        # 权威来源标记: 供第二期归并收口与第三期响应检查区分权威条目
        "source": SCORING_AUTHORITY_SOURCE,
        "totals_needs_review": totals_needs_review,
    }
    return WritingClause(
        project_id=project_id,
        document_id=document_id,
        layer=WritingClauseLayer.generation_guide,
        sub_type=_SCORING_SUB_TYPE,
        content=item.description,
        raw_text=item.description,
        source_page=page_start,
        source_page_start=page_start,
        source_page_end=page_end,
        score_weight=effective_weight,
        metadata_json=metadata_json,
        confirmed=0,
        is_manual=0,
        sort_order=sort_order,
    )


async def _next_sort_order(db: AsyncSession, project_id: int) -> int:
    """返回项目当前最大 sort_order + 1(权威评分项追加在末尾，不打乱既有排序)。"""
    result = await db.execute(
        select(func.max(WritingClause.sort_order)).where(
            WritingClause.project_id == project_id
        )
    )
    current_max = result.scalar()
    return int(current_max) + 1 if current_max is not None else 0


async def collect_scoring_outcome(
    db: AsyncSession,
    project_id: int,
    document_id: int,
):
    """只读收集评分结构产物，不 stamp、不删插。"""
    extractor = ScoringExtractor(db=db, project_id=project_id)
    return await extractor.collect_scoring_structure(document_id)


async def apply_scoring_authority(
    db: AsyncSession,
    project_id: int,
    document_id: int,
    outcome,
) -> Optional[list[WritingClause]]:
    """在调用方会话内 stamp 探针并替换未确认评分约束。"""
    await stamp_document_verdict_probe(
        db, document_id, outcome.verdict_probe, flow="writer"
    )
    if not outcome.items:
        logger.info(
            f"[写标评分权威] 未提取到评分项(chunks={outcome.chunk_count})，"
            f"保留 LLM 自由提取结果: project={project_id}"
        )
        return None

    await db.execute(
        delete(WritingClause).where(
            WritingClause.project_id == project_id,
            WritingClause.sub_type == _SCORING_SUB_TYPE,
            WritingClause.confirmed == 0,
            WritingClause.is_manual == 0,
        )
    )

    sort_order = await _next_sort_order(db, project_id)
    saved: list[WritingClause] = []
    for offset, item in enumerate(outcome.items):
        clause = _to_scoring_clause(
            project_id,
            document_id,
            item,
            sort_order=sort_order + offset,
            totals_needs_review=outcome.report.needs_review,
        )
        db.add(clause)
        saved.append(clause)

    chunks_result = await db.execute(
        select(Chunk).where(
            Chunk.document_id == document_id,
            Chunk.chunk_type == "scoring_table",
        )
    )
    stamp_clause_assessment_flags(saved, chunks_result.scalars().all())

    await db.commit()
    for clause in saved:
        await db.refresh(clause)

    logger.info(
        f"[写标评分权威] 已用权威评分项替换 scoring_criterion: "
        f"project={project_id}, count={len(saved)}, "
        f"totals_needs_review={outcome.report.needs_review}"
    )
    return saved


async def replace_scoring_clauses_with_authority(
    db: AsyncSession,
    project_id: int,
    document_id: int,
) -> Optional[list[WritingClause]]:
    """用评分表权威提取结果替换写标自动提取的 scoring_criterion 约束。

    Returns:
        - 权威评分项列表(已落库): 提取到评分项并完成替换
        - None: 无 scoring_table chunk 或未提取到评分项 → 保留原 LLM 提取结果(回退现状)

    幂等: 仅删除未确认(confirmed=0)且非人工(is_manual=0)的 scoring_criterion，
          保护用户已确认/手动新增的评分约束。
    """
    outcome = await collect_scoring_outcome(db, project_id, document_id)
    return await apply_scoring_authority(db, project_id, document_id, outcome)
