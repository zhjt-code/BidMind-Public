"""评分提取 chunk 遍历编排的唯一实现（治理阶段 4 的生产同源 seam）。

职责: 承载"逐 chunk 确定性解析 → 副本指纹判重 → 卷定义合入 → scheme 盖章 →
 去重吸收 → 非 confident 交兜底"的编排本体，供生产提取器与离线语料测试
 **执行同一份代码**。
不做: 不访问 DB、不直接调用 LLM（兜底经 llm_fallback 回调注入）、不落库、
 不做表格解析（scoring_table_parser 职责）、不做合计校验（score_totals 职责）。

为什么需要它（F18）:
 此前 backend/tests/test_scoring_sample_corpus.py 的 harness 自述"复刻
 collect_scoring_structure 的确定性链路"，是生产编排的**手工副本**。副本与生产
 必然漂移（该文件内已出现"与生产 execute_scoring_structure_tail 对齐"
 这类人工追平注释），意味着 golden 全绿不能证明生产未变。本模块把编排收敛为单点，
 测试与生产从此跑同一份代码。

为什么是 async:
 原循环中 confident 与非 confident 分支**交错共享** SchemeSegmenter 状态，
 scheme 边界依赖 chunk 顺序。若把 LLM 兜底拆出循环，segmenter 状态序列即被改变，
 属语义变更。故保留交错顺序，仅把 LLM 调用改为注入式回调。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, Sequence

from loguru import logger

from app.core.extractor.scoring.physical_page_remap import (
    collect_chunk_physical_pages,
    remap_item_source_pages,
)
from app.core.extractor.scoring.scheme_segmentation import SchemeSegmenter
from app.core.extractor.scoring.scoring_candidate_pipeline import (
    parse_scoring_chunk_candidate,
)
from app.core.extractor.scoring.scoring_fidelity import FidelityAnnotation, FidelityKey
from app.core.extractor.scoring.scoring_rescue_trace import ScoringExtractionTraceState
from app.core.extractor.scoring.scoring_scheme_assignment import assign_and_stamp_scheme
from app.schemas.requirement import ExtractedScoringItem

__all__ = [
    "ChunkExtractionPass",
    "as_extraction_chunk",
    "backfill_source_pages",
    "format_chunk_diagnostics",
    "run_chunk_extraction_pass",
]


class _ExtractionChunkView:
    """内存 TextChunk → 生产 DB Chunk 形态适配（只做列提升，不改语义）。

    生产 `_get_scoring_chunks` 返回 ORM Chunk，其 `id / page_num / section_path`
    是独立列；而 `chunk_planner._make_chunk` 产出的 TextChunk 把 page_num 与
    section_path 放在 `metadata` 字典里，落库时才提升为列。
    离线场景（语料测试、T0 快照）必须复现这次提升，否则
    `parse_scoring_chunk_candidate` 读到空 section_path，卷归属整体退化——
    这正是旧语料 harness 手工传 section_path 所掩盖的形态差异。
    """

    __slots__ = ("_chunk", "id", "page_num", "section_path", "chunk_metadata")

    def __init__(self, chunk: Any, chunk_id: int) -> None:
        metadata = getattr(chunk, "metadata", None) or {}
        self._chunk = chunk
        self.id = chunk_id
        self.page_num = metadata.get("page_num")
        self.section_path = metadata.get("section_path") or ""
        self.chunk_metadata = metadata

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chunk, name)


def as_extraction_chunk(chunk: Any, chunk_id: int = 0) -> Any:
    """把内存 chunk 适配为生产提取链期望的 chunk 形态；已是 DB Chunk 则原样返回。"""
    if getattr(chunk, "section_path", None) is not None:
        return chunk
    return _ExtractionChunkView(chunk, chunk_id)

# 评分表整表 chunk 内容头: "[评分表 第47-48页]" (scoring_chunk_builder 渲染)
_PAGE_RANGE_RE = re.compile(r"第\s*(\d+)\s*-\s*(\d+)\s*页")


def chunk_page_range(chunk: Any) -> list[int]:
    """从 chunk 内容头解析评分表页范围, 失败回退 chunk.page_num。"""
    content = getattr(chunk, "content", "") or ""
    m = _PAGE_RANGE_RE.search(content)
    if m:
        try:
            start, end = int(m.group(1)), int(m.group(2))
        except (TypeError, ValueError):
            start = end = 0
        if 1 <= start <= end and (end - start) <= 50:
            return list(range(start, end + 1))
    pn = getattr(chunk, "page_num", None)
    try:
        pn_int = int(pn)
    except (TypeError, ValueError):
        return []
    return [pn_int] if pn_int >= 1 else []


def backfill_source_pages(items: list, chunk: Any) -> None:
    """为缺少来源页码的评分项回填 chunk 页范围(就地修改)。

 根因: 表头驱动确定性解析(scoring_table_parser)按设计与页码无关,
 产出的评分项 source_pages 恒为空 → 确认页"暂无来源页码"、无法溯源/跳转 PDF。
 页码上下文只在提取器(持有 chunk)可得, 故在此回填; 不覆盖已带页码者(LLM 兜底)。
 """
    pages = chunk_page_range(chunk)
    if not pages:
        return
    for it in items:
        if not getattr(it, "source_pages", None):
            try:
                it.source_pages = list(pages)
            except (AttributeError, ValueError):
                pass


def format_chunk_diagnostics(diagnostics: list[str], chunk: Any) -> list[str]:
    """给解析诊断补充 chunk 页范围, 便于确认页人工定位。"""
    if not diagnostics:
        return []
    pages = chunk_page_range(chunk)
    if pages:
        page_label = f"第{pages[0]}页" if len(pages) == 1 else f"第{pages[0]}-{pages[-1]}页"
    else:
        page_label = "未知页"
    return [f"{page_label}: {detail}" for detail in diagnostics if str(detail).strip()]


class _ItemSetOps(Protocol):
    """去重/吸收算子（由调用方注入，避免核心反向依赖 ScoringExtractor）。"""

    def __call__(self, *args: Any) -> list[ExtractedScoringItem]: ...


@dataclass
class ChunkExtractionPass:
    """chunk 遍历产物（未做卷定义回填与合计校验，后续阶段由调用方续接）。"""

    survivors: list[ExtractedScoringItem] = field(default_factory=list)
    fallback_items: list[ExtractedScoringItem] = field(default_factory=list)
    volume_defs: dict = field(default_factory=dict)
    fidelity_sidecar: dict[FidelityKey, FidelityAnnotation] = field(default_factory=dict)
    parse_diagnostics: list[str] = field(default_factory=list)
    duplicate_relations: list[dict] = field(default_factory=list)
    trace_state: ScoringExtractionTraceState = field(
        default_factory=ScoringExtractionTraceState
    )
    llm_fallback_chunks: int = 0


async def run_chunk_extraction_pass(
    chunks: Sequence[Any],
    *,
    llm_fallback: Callable[
        [Any], Awaitable[tuple[list[ExtractedScoringItem], dict]]
    ],
    dedupe_exact: _ItemSetOps,
    absorb: _ItemSetOps,
    document_id: int = 0,
    on_chunk_done: Callable[[int, int], None] | None = None,
) -> ChunkExtractionPass:
    """逐 chunk 执行确定性解析与兜底编排（生产与语料测试共用的唯一实现）。

    llm_fallback: 非 confident chunk 的兜底提取；离线场景传入返回 ([], {}) 的桩即可，
                  此时行为等价于"该 chunk 无产出"，与旧语料 harness 口径一致。
    """
    out = ChunkExtractionPass()
    segmenter = SchemeSegmenter()
    seen_fingerprints: dict[str, int] = {}
    confident_chunks = 0

    chunk_total = len(chunks)
    for chunk_i, chunk in enumerate(chunks, start=1):
        candidate = parse_scoring_chunk_candidate(chunk)
        payload = candidate.payload
        parsed = candidate.parsed
        chunk_diags = format_chunk_diagnostics(parsed.diagnostics, chunk)
        out.parse_diagnostics.extend(chunk_diags)
        fingerprint = candidate.fingerprint
        if fingerprint:
            if fingerprint in seen_fingerprints:
                out.duplicate_relations.append({
                    "type": "duplicate_copy_of",
                    "fingerprint": fingerprint,
                    "chunk_index": int(getattr(chunk, "chunk_index", 0) or 0),
                    "first_chunk_index": seen_fingerprints[fingerprint],
                })
            else:
                seen_fingerprints[fingerprint] = int(
                    getattr(chunk, "chunk_index", 0) or 0
                )
        pages = chunk_page_range(chunk)
        page_label = (
            f"{pages[0]}" if len(pages) == 1
            else (f"{pages[0]}-{pages[-1]}" if pages else "?")
        )
        chunk_id = getattr(chunk, "id", None)
        if parsed.confident:
            confident_chunks += 1
            for vol, vdef in parsed.volume_defs.items():
                out.volume_defs.setdefault(vol, vdef)
            chunk_items = list(parsed.items)
            backfill_source_pages(chunk_items, chunk)
            scheme = assign_and_stamp_scheme(
                segmenter, chunk_items, chunk, payload, out.survivors, out.volume_defs
            )
            # Task-5 trace: scheme 赋值后单行记录（lot 唯一且非 UNASSIGNED 时）
            _lots = {str(getattr(it, "lot", None) or "__single__") for it in chunk_items}
            out.trace_state.try_record_chunk(
                chunk, scheme,
                _lots.pop() if len(_lots) == 1 else "__unassigned__",
                parsed.volume_defs,
            )
            deduped = dedupe_exact(chunk_items)
            merged_before = len(out.survivors)
            out.survivors = absorb(out.survivors, deduped)
            absorbed = merged_before + len(deduped) - len(out.survivors)
            if absorbed > 0:
                logger.info(
                    "[评分去重影子] chunk指纹={} 与既有集合发生{}项跨表相似度合并"
                    "(document_id={}, scheme={})",
                    str((payload or {}).get("table_fingerprint") or "无载荷")[:12],
                    absorbed, document_id, scheme,
                )
            logger.debug(
                "[评分chunk] document_id={} chunk_id={} pages={} path=confident "
                "items={} diags={} scheme={}",
                document_id, chunk_id, page_label, len(chunk_items),
                len(chunk_diags), scheme,
            )
        else:
            out.llm_fallback_chunks += 1
            llm_items, chunk_sidecar = await llm_fallback(chunk)
            llm_items = remap_item_source_pages(
                llm_items, collect_chunk_physical_pages([chunk])
            )
            backfill_source_pages(llm_items, chunk)
            # I7/C7: LLM fallback 使用 strict len(llm_items)，0 不消费边界
            llm_scheme = assign_and_stamp_scheme(
                segmenter, llm_items or [], chunk, payload, out.survivors, out.volume_defs
            )
            # Task-5 trace: L1 fallback 同样只在 lot 唯一时记录
            _fb_lots = {str(getattr(it, "lot", None) or "__single__") for it in llm_items}
            out.trace_state.try_record_chunk(
                chunk, llm_scheme,
                _fb_lots.pop() if len(_fb_lots) == 1 else "__unassigned__",
                {},
            )
            out.fallback_items.extend(llm_items)
            out.fidelity_sidecar.update(chunk_sidecar)
            logger.debug(
                "[评分chunk] document_id={} chunk_id={} pages={} path=llm_fallback "
                "items={} diags={} scheme={}",
                document_id, chunk_id, page_label, len(llm_items or []),
                len(chunk_diags), llm_scheme,
            )
        if on_chunk_done is not None:
            on_chunk_done(chunk_i, chunk_total)

    logger.info(
        "[评分chunk遍历] document_id={} chunks={} confident={} llm_fallback={} "
        "survivors={} fallback_items={} volume_defs={}",
        document_id,
        len(chunks),
        confident_chunks,
        out.llm_fallback_chunks,
        len(out.survivors),
        len(out.fallback_items),
        len(out.volume_defs),
    )
    return out
