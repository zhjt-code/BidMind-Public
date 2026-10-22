"""
职责: 编排写标目录规格提取、质量门控和结果持久化。
不做: 不提取三层约束、不生成固定模板目录、不生成 writing_outline_nodes。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import LLMClient
from app.core.ai.prompt_registry import resolve_runtime_params
from app.core.parser.bookmark_reader import read_pdf_bookmarks
from app.core.writer.bookmark_directory_source import build_spec_from_bookmarks
from app.core.writer.directory_spec_alignment import (
    SpecCoverageReport,
    build_numbered_line_inventory,
    evaluate_spec_coverage,
    render_structure_line_view,
    verify_spec_against_source,
)
from app.core.writer.directory_spec_context import (
    downgrade_empty_directory_spec,
    is_usable_directory_spec,
    load_document_toc_map,
    select_directory_spec_chunks,
    select_directory_spec_chunks_by_keywords,
)
from app.core.writer.locator_volume import strip_locator_wrappers_from_spec
from app.core.writer.prompts.clause_extract import get_bid_directory_extract_prompt
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.writing_clause import WritingClause, WritingClauseLayer
from app.schemas.writing import BidDirectorySpec, BidStructureAnalysis

ProgressCallback = Callable[[str], None]

_INPUT_BUDGET_CHARS = 30000
_TAIL_FALLBACK_CHUNKS = 20


@dataclass(frozen=True)
class DirectorySpecCollectResult:
    """目录规格收集产物（未落库）。"""

    spec: BidDirectorySpec
    extra_metadata: Optional[dict[str, Any]] = None


async def extract_bid_directory_spec(
    *,
    db: AsyncSession,
    llm_client: LLMClient,
    project_id: int,
    document_id: int,
    progress_callback: Optional[ProgressCallback] = None,
) -> WritingClause:
    """从招标文件中提取明确的投标文件目录结构并落库。"""

    collected = await collect_directory_spec(
        db=db,
        llm_client=llm_client,
        project_id=project_id,
        document_id=document_id,
        progress_callback=progress_callback,
    )
    if progress_callback:
        progress_callback("保存目录提取结果")
    return await save_directory_spec(
        db,
        project_id,
        document_id,
        collected.spec,
        extra_metadata=collected.extra_metadata,
    )


async def collect_directory_spec(
    *,
    db: AsyncSession,
    llm_client: LLMClient,
    project_id: int,
    document_id: int,
    progress_callback: Optional[ProgressCallback] = None,
) -> DirectorySpecCollectResult:
    """只读收集目录规格，不落库。"""

    chunks = await _load_chunks(db, document_id)
    if not chunks:
        logger.warning(f"文档 {document_id} 无分块，跳过目录提取")
        return DirectorySpecCollectResult(
            spec=BidDirectorySpec(
                has_explicit_directory=False, reasoning="文档无分块"
            ),
            extra_metadata={
                "extraction_debug": {
                    "selection_path": "empty",
                    "downgrade_reason": "文档无分块",
                }
            },
        )

    # 最高优先级：PDF书签为出版方声明的结构化事实，命中即零LLM照搬
    bookmark_spec = await _try_bookmark_spec(db, document_id)
    if bookmark_spec is not None:
        logger.info(
            "目录规格提取: path=bookmark, volumes={}, reasoning={}",
            len(bookmark_spec.volumes),
            bookmark_spec.reasoning,
        )
        if progress_callback:
            progress_callback("从PDF书签照搬目录结构")
        return DirectorySpecCollectResult(
            spec=bookmark_spec,
            extra_metadata=_build_extraction_metadata(
                debug={
                    "selection_path": "bookmark",
                    "page_range": [],
                    "chunk_count": 0,
                    "char_count": 0,
                    "condensed": False,
                    "retried": False,
                    "downgrade_reason": "",
                    "has_explicit_directory": True,
                },
                report=_empty_coverage_report(),
                spec=bookmark_spec,
            ),
        )

    toc_map = await load_document_toc_map(db, document_id)
    relevant_chunks, selection_path = _select_directory_chunks(chunks, toc_map)
    logger.info(
        "目录规格提取: path={}, chunks={}",
        selection_path,
        len(relevant_chunks),
    )

    if progress_callback:
        progress_callback("筛选目录相关内容")
    source_text = "\n\n".join(c.content or "" for c in relevant_chunks)
    llm_input, used_condensed = _budget_llm_input(source_text)

    temperature, max_tokens = resolve_runtime_params(
        "writing.bid_directory_extract", 0.1, 6000,
    )
    retried = False
    downgrade_reason = ""
    spec = BidDirectorySpec(has_explicit_directory=False, reasoning="未提取")
    report = _empty_coverage_report()

    try:
        if progress_callback:
            progress_callback("等待目录提取模型返回")
        spec = await _extract_spec_from_content(
            llm_client, llm_input, temperature, max_tokens,
        )
        spec, report = _align_spec(spec, source_text)
        if report.status == "partial" and spec.has_explicit_directory:
            retried = True
            if progress_callback:
                progress_callback("目录覆盖率不足，使用全章文本重试")
            try:
                retry_spec = await _extract_spec_from_content(
                    llm_client, llm_input, temperature, max_tokens,
                )
                retry_spec, retry_report = _align_spec(retry_spec, source_text)
                if _prefer_retry_result(retry_spec, retry_report, spec, report):
                    spec, report = retry_spec, retry_report
            except Exception as retry_exc:
                logger.warning(f"目录提取全章重试失败, 保留首次结果: {retry_exc}")
        if spec.has_explicit_directory and not is_usable_directory_spec(spec):
            downgrade_reason = "空显式目录降级"
    except Exception as e:
        logger.warning(f"目录原文提取LLM调用失败: {e}")
        downgrade_reason = f"LLM提取失败: {str(e)[:100]}"
        spec = BidDirectorySpec(
            has_explicit_directory=False,
            reasoning=downgrade_reason,
        )
        report = _empty_coverage_report()

    extra_metadata = _build_extraction_metadata(
        debug={
            "selection_path": selection_path,
            "page_range": _chunk_pages(relevant_chunks),
            "chunk_count": len(relevant_chunks),
            "char_count": len(source_text),
            "condensed": used_condensed,
            "retried": retried,
            "downgrade_reason": downgrade_reason,
            "has_explicit_directory": spec.has_explicit_directory,
        },
        report=report,
        spec=spec,
    )
    return DirectorySpecCollectResult(spec=spec, extra_metadata=extra_metadata)


async def save_directory_spec(
    db: AsyncSession,
    project_id: int,
    document_id: int,
    spec: BidDirectorySpec,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> WritingClause:
    """将目录提取结果存入 writing_clauses。"""

    spec = strip_locator_wrappers_from_spec(spec)
    content_json = spec.model_dump_json(indent=2)
    metadata_json: dict[str, Any] = dict(extra_metadata or {})
    warning = await build_directory_structure_warning(db, project_id, spec)
    if warning:
        existing = str(metadata_json.get("structure_consistency_warning") or "").strip()
        metadata_json["structure_consistency_warning"] = (
            f"{existing} {warning}".strip() if existing else warning
        )

    clause = WritingClause(
        project_id=project_id,
        document_id=document_id,
        layer=WritingClauseLayer.structure_constraint,
        sub_type="bid_directory_spec",
        content=content_json,
        raw_text=None,
        source_page=None,
        score_weight=None,
        metadata_json=metadata_json or None,
        confirmed=1,
        sort_order=9997,
    )
    db.add(clause)
    await db.commit()
    await db.refresh(clause)

    logger.info(
        f"目录原文提取完成: project={project_id}, "
        f"has_explicit={spec.has_explicit_directory}, "
        f"volumes={len(spec.volumes)}"
    )
    return clause


async def build_directory_structure_warning(
    db: AsyncSession,
    project_id: int,
    spec: BidDirectorySpec,
) -> str:
    """对目录规格与结构分析做轻量一致性校验，异常时给下游可见信号。"""

    if not spec.has_explicit_directory or not spec.volumes:
        return ""
    result = await db.execute(
        select(WritingClause).where(
            WritingClause.project_id == project_id,
            WritingClause.sub_type == "bid_structure_analysis",
        ).limit(1)
    )
    structure_clause = result.scalars().first()
    if not structure_clause or not structure_clause.content:
        return ""
    try:
        analysis = BidStructureAnalysis(**json.loads(structure_clause.content))
    except Exception:
        return ""

    volume_types = {
        (volume.volume_type or "").lower().strip()
        for volume in spec.volumes
        if volume.volume_type
    }
    expected_modes = {
        frozenset({"technical"}): "tech_only",
        frozenset({"commercial"}): "comm_only",
        frozenset({"technical", "commercial"}): "tech_comm",
        frozenset({"qualification", "technical", "commercial"}): "qual_tech_comm",
        frozenset({"qualification", "pricing", "technical"}): "qual_tech_comm",
    }
    expected = expected_modes.get(frozenset(volume_types))
    if expected and analysis.structure_mode != expected:
        warning = (
            f"目录规格显示分卷 {sorted(volume_types)}，"
            f"但结构分析为 {analysis.structure_mode}，请人工复核目录结构。"
        )
        logger.warning(
            "目录/结构分析不一致: project={}, expected={}, actual={}, volumes={}",
            project_id, expected, analysis.structure_mode, sorted(volume_types),
        )
        return warning
    return ""


async def _try_bookmark_spec(
    db: AsyncSession,
    document_id: int,
) -> Optional[BidDirectorySpec]:
    """尝试从PDF书签构建目录规格；文件缺失/无书签/门槛不过返回 None。"""

    result = await db.execute(
        select(Document.file_path).where(Document.id == document_id).limit(1)
    )
    file_path = result.scalar_one_or_none()
    if not file_path:
        return None
    entries = read_pdf_bookmarks(file_path)
    if not entries:
        return None
    spec = build_spec_from_bookmarks(
        [(entry.level, entry.title, entry.page) for entry in entries]
    )
    if spec is not None and not is_usable_directory_spec(spec):
        logger.warning(
            "PDF书签目录规格未过可用性门控, 回退文本链路: document={}", document_id,
        )
        return None
    return spec


def _select_directory_chunks(
    chunks: list[Chunk],
    toc_map: Optional[dict[str, Any]],
) -> tuple[list[Chunk], str]:
    """按 toc -> 关键词 -> 尾部 选择目录上下文，并返回路径名。"""

    toc_chunks = select_directory_spec_chunks(chunks, toc_map)
    if toc_chunks:
        return toc_chunks, "toc"
    keyword_chunks = select_directory_spec_chunks_by_keywords(chunks)
    if keyword_chunks:
        return keyword_chunks, "keyword"
    tail = chunks[-min(len(chunks), _TAIL_FALLBACK_CHUNKS):]
    logger.info("目录规格提取: 定向与回退均空, 使用尾部兜底 chunks={}", len(tail))
    return tail, "tail"


def _budget_llm_input(source_text: str) -> tuple[str, bool]:
    """超预算时用编号行浓缩视图，避免钝截断丢掉格式章尾部。"""

    if len(source_text) <= _INPUT_BUDGET_CHARS:
        return source_text, False
    condensed = render_structure_line_view(source_text)
    if condensed.strip():
        if len(condensed) > _INPUT_BUDGET_CHARS:
            condensed = condensed[:_INPUT_BUDGET_CHARS]
        return condensed, True
    return source_text[:_INPUT_BUDGET_CHARS], False


async def _extract_spec_from_content(
    llm_client: LLMClient,
    content: str,
    temperature: float,
    max_tokens: int,
) -> BidDirectorySpec:
    """调用目录提取模型并做空卷门控。"""

    system_msg, user_msg = get_bid_directory_extract_prompt(content)
    raw = await llm_client.chat_json(
        user_message=user_msg,
        system_message=system_msg,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    spec = BidDirectorySpec(**(raw if isinstance(raw, dict) else {}))
    spec = strip_locator_wrappers_from_spec(spec)
    if spec.has_explicit_directory and not is_usable_directory_spec(spec):
        logger.warning(
            "目录原文提取返回空显式目录, 已降级: volumes={}",
            len(spec.volumes),
        )
        return downgrade_empty_directory_spec(spec)
    return spec


def _align_spec(
    spec: BidDirectorySpec,
    source_text: str,
) -> tuple[BidDirectorySpec, SpecCoverageReport]:
    """原文对齐并评估覆盖率。"""

    verified = verify_spec_against_source(spec, source_text)
    inventory = build_numbered_line_inventory(source_text)
    return verified, evaluate_spec_coverage(verified, inventory)


def _prefer_retry_result(
    retry_spec: BidDirectorySpec,
    retry_report: SpecCoverageReport,
    current_spec: BidDirectorySpec,
    current_report: SpecCoverageReport,
) -> bool:
    """重试结果更完整时采用重试规格。"""

    if retry_spec.has_explicit_directory and not current_spec.has_explicit_directory:
        return True
    if not retry_spec.has_explicit_directory and current_spec.has_explicit_directory:
        return False
    rank = {"complete": 1, "partial": 0}
    retry_rank = rank.get(retry_report.status, -1)
    current_rank = rank.get(current_report.status, -1)
    if retry_rank != current_rank:
        return retry_rank > current_rank
    return len(retry_report.missing_titles) < len(current_report.missing_titles)


def _empty_coverage_report() -> SpecCoverageReport:
    return SpecCoverageReport(
        status="complete",
        inventory_count=0,
        extracted_count=0,
        missing_titles=[],
        unverified_titles=[],
        warning="",
    )


def _chunk_pages(chunks: list[Chunk]) -> list[int]:
    return sorted(
        {
            int(page)
            for page in (getattr(chunk, "page_num", None) for chunk in chunks)
            if isinstance(page, int)
        }
    )


def _build_extraction_metadata(
    *,
    debug: dict[str, Any],
    report: SpecCoverageReport,
    spec: BidDirectorySpec,
) -> dict[str, Any]:
    """组装 S0 观测字段；partial 时写入可见告警。"""

    debug["coverage"] = {
        "status": report.status,
        "inventory_count": report.inventory_count,
        "extracted_count": report.extracted_count,
        "missing_titles": report.missing_titles[:20],
        "unverified_titles": report.unverified_titles[:20],
    }
    metadata: dict[str, Any] = {"extraction_debug": debug}
    if report.status == "partial" and spec.has_explicit_directory and report.warning:
        metadata["structure_consistency_warning"] = report.warning
    return metadata


async def _load_chunks(db: AsyncSession, document_id: int) -> list[Chunk]:
    """加载文档的全部分块。"""

    result = await db.execute(
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index)
    )
    return list(result.scalars().all())
