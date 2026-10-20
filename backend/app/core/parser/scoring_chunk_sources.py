"""评分表chunk来源收集。

职责: 分别收集逻辑装配表、未认领物理表和文字价格公式三类评分chunk来源。
不做: 不定位评分区段、不解析评分项、不入库、不调用LLM。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from loguru import logger

from app.core.config import settings
from app.core.extractor.scoring.scoring_grid_payload import write_parse_diagnostics
from app.core.parser.price_formula_recognizer import recognize_price_formula_sections
from app.core.parser.scoring_segment_acceptance import build_partial_rejection_diagnostics, compute_span_claim
from app.core.parser.scoring_stub_recovery import recover_stub_continuation
from app.core.parser.scoring_table_assembler import assemble_scoring_segments
from app.core.parser.scoring_table_detector import analyze_scoring_table_rows
from app.core.parser.scoring_template_boundaries import extract_template_anchors
from app.core.parser.scoring_text_tail_recovery import append_trailing_text_score_rows
from app.core.parser.table_stitcher import render_stitched_table_markdown, stitch_cross_page_tables

if TYPE_CHECKING:
    from app.core.parser.chunk_planner import ChunkPlanner
    from app.core.parser.pdf_parser import ParsedDocument
    from app.core.parser.text_chunker import TextChunk


def has_multi_template_scheme_signal(page_texts: list[str]) -> bool:
    """页文本是否含"多套备选评分模板"信号（A1 修订：委托 extract_template_anchors）。

    职责: 从全量锚点中判断不同编码数 >= 2，不受 SCORING_TEMPLATE_BOUNDARY_ENABLED 开关影响。
          恢复 sample09 无冒号 FWJS16/FWJS18 识别及 BUG753 开闸逻辑。
    不做: 不判断方案边界位置（由 table_stitcher 内部判据决定）、不访问 DB/LLM。

    架构说明: 使用 extract_template_anchors（第一阶段）而非 detect_template_boundaries
              （第二阶段），确保开关 off 时仍能识别多模板信号，不破坏表内方案切分信号。
    """
    indexed = [(i + 1, text) for i, text in enumerate(page_texts) if text]
    per_page = extract_template_anchors(indexed)
    all_codes = {code for codes in per_page.values() for code in codes}
    return len(all_codes) >= 2


@dataclass
class ScoringChunkBuildState:
    """评分chunk构建过程中的可变状态。"""

    idx: int
    claimed_global: set[tuple[int, int]]
    seen_segments: set[tuple[int, int, str]]
    seen_tables: set[tuple[int, int, int, str]]
    rejected_with_score_signal: list[str]
    volume_prelude_used: bool = False


@dataclass(frozen=True)
class TextFormulaChunkConfig:
    """collect_text_formula_scoring_chunks 的策略配置（C1: 收敛参数至 ≤6）。

    职责: 承载文字载体价格公式 chunk 收集的可配置策略，与可变状态隔离。
    不做: 不包含可变状态、不直接触发 IO、不持有 ParsedDocument。
    """

    volume_prelude: str | None
    markdown_is_scoring_candidate: Callable[[str], bool]
    attach_scoring_payload: Callable[..., None]
    price_contexts: list | None = None


def collect_assembled_scoring_chunks(
    *,
    parsed_doc: "ParsedDocument",
    assembly_spans: list,
    planner: "ChunkPlanner",
    state: ScoringChunkBuildState,
    lots: list,
    volume_prelude: str | None,
    pages_by_num: dict[int, object],
    build_segment_chunk: Callable[..., "TextChunk | None"],
    lot_label_for_page: Callable[[list, int], str | None],
    volume_title_for_page: Callable[[dict[int, object], int], str | None],
) -> list["TextChunk"]:
    """收集逻辑表装配产生的评分chunk。"""
    chunks: list["TextChunk"] = []
    stub_recovery_enabled = bool(
        getattr(settings, "SCORING_STUB_RECOVERY_ENABLED", True)
    )
    for span in assembly_spans:
        segments, _span_claimed = assemble_scoring_segments(
            parsed_doc, span, exclude=state.claimed_global
        )
        # I1: stub recovery 必须在 build 前修改段内容（渲染依赖续接行），
        # 但不得提前修改 global claim。用 provisional 局部集合运行 recovery，
        # 收集各段 stub 键；chunk 验收后才将其纳入 span 级 claim 决策。
        stub_keys_per_seg: list[set[tuple[int, int]]] = []
        if stub_recovery_enabled:
            provisional = set(state.claimed_global)
            for seg in segments:
                keys = recover_stub_continuation(parsed_doc, seg, provisional)
                provisional |= keys
                stub_keys_per_seg.append(keys)
        else:
            stub_keys_per_seg = [set() for _ in segments]

        # C2: 按 span 收集所有 (extended_source_keys, chunk) 对，
        # 最后统一计算可 claim 的物理表键，避免"同表一接受段→误 claim 拒绝段物理表"。
        span_results: list[tuple[set[tuple[int, int]], object]] = []
        seg_chunks: list[tuple[object, object]] = []  # diag: (seg, chunk) pairs
        for i, seg in enumerate(segments):
            prelude = (
                volume_prelude
                if volume_prelude and not state.volume_prelude_used
                else None
            )
            chunk = build_segment_chunk(
                seg,
                span,
                planner,
                state.idx,
                state.seen_segments,
                volume_prelude=prelude,
                lot_label=lot_label_for_page(lots, seg.page_start),
                volume_title=volume_title_for_page(pages_by_num, seg.page_start),
            )
            # extended_keys: 段自身来源表 + stub 续接表
            extended_keys: set[tuple[int, int]] = seg.source_keys | stub_keys_per_seg[i]
            span_results.append((extended_keys, chunk))
            seg_chunks.append((seg, chunk))
            if chunk is not None:
                chunks.append(chunk)
                state.idx += 1
                if prelude:
                    state.volume_prelude_used = True

        # C2: claim 所有被接受段引用的物理表键（accepted-only）；
        # 若同一物理表同时被接受段和拒绝段引用，仍 claim（防 fallback 重复处理）
        # 并由 compute_span_claim 触发 warning 标记数据可能不完整。
        state.claimed_global |= compute_span_claim(span_results)

        # diag: 共享物理表被拒段有评分行时，向接受段 scoring_payload 写入诊断
        span_diags = build_partial_rejection_diagnostics(span_results, seg_chunks)
        if span_diags:
            for _ext_keys, _chunk in span_results:
                if _chunk is not None:
                    write_parse_diagnostics(_chunk, span_diags)
    return chunks


def collect_unclaimed_table_scoring_chunks(
    *,
    parsed_doc: "ParsedDocument",
    located_sections: list,
    planner: "ChunkPlanner",
    state: ScoringChunkBuildState,
    lots: list,
    volume_prelude: str | None,
    pages_by_num: dict[int, object],
    inject_volume_prelude: Callable[[str, str | None], str],
    inject_lot_label: Callable[[str, str | None], str],
    join_section_path: Callable[[str, str | None], str],
    lot_label_for_page: Callable[[list, int], str | None],
    volume_title_for_page: Callable[[dict[int, object], int], str | None],
    attach_scoring_payload: Callable[..., None],
) -> list["TextChunk"]:
    """收集未被逻辑装配认领的物理评分表chunk。"""
    chunks: list["TextChunk"] = []
    for sec in located_sections:
        page_tables = []
        for page_num in range(sec.page_start, sec.page_end + 1):
            page = pages_by_num.get(page_num)
            if not page or not getattr(page, "tables", None):
                continue
            for table_idx, table in enumerate(page.tables):
                if (page_num, table_idx) in state.claimed_global:
                    continue
                page_tables.append((page_num, table))
        if not page_tables:
            continue

        scheme_split_enabled = has_multi_template_scheme_signal(
            [
                getattr(pages_by_num.get(page_num), "text", "") or ""
                for page_num, _table in page_tables
            ]
        )
        for stitched in stitch_cross_page_tables(
            page_tables, split_scheme_restarts=scheme_split_enabled
        ):
            analysis = analyze_scoring_table_rows(stitched.rows)
            if not analysis.is_scoring_candidate:
                if not getattr(stitched, "scheme_split", False):
                    if analysis.score_columns:
                        state.rejected_with_score_signal.append(
                            f"p{stitched.page_start}-{stitched.page_end}"
                            f"({','.join(analysis.reasons)})"
                        )
                    logger.debug(
                        "[文档处理] 跳过非评分明细表/模板: pages={}-{} reasons={}",
                        stitched.page_start,
                        stitched.page_end,
                        analysis.reasons,
                    )
                    continue
                logger.debug(
                    "[文档处理] 方案切分段未过候选闸门但保留解析: pages={}-{} reasons={}",
                    stitched.page_start,
                    stitched.page_end,
                    analysis.reasons,
                )
            table_key = (
                stitched.page_start,
                stitched.page_end,
                stitched.column_count,
                _first_row_key(stitched),
            )
            if table_key in state.seen_tables:
                continue
            state.seen_tables.add(table_key)
            markdown = render_stitched_table_markdown(stitched)
            if not markdown or len(markdown) < 20:
                continue
            markdown = append_trailing_text_score_rows(
                markdown, parsed_doc, sec, stitched.page_end
            )
            prelude = (
                volume_prelude
                if volume_prelude and not state.volume_prelude_used
                else None
            )
            markdown = inject_volume_prelude(markdown, prelude)
            lot_label = lot_label_for_page(lots, stitched.page_start)
            markdown = inject_lot_label(markdown, lot_label)
            chunk = planner.build_manual_chunk(
                content=markdown,
                page_num=stitched.page_start,
                chunk_type="scoring_table",
                parent_block_id=f"scoring-table:{stitched.page_start}-{stitched.page_end}",
                section_path=join_section_path(
                    (sec.title or "评分表") + (" · 方案切分模板" if getattr(stitched, "scheme_split", False) else ""),
                    volume_title_for_page(pages_by_num, stitched.page_start),
                ),
            )
            chunk.chunk_index = state.idx
            state.idx += 1
            try:
                chunk.metadata["is_scoring_table"] = True
                chunk.metadata["page_end"] = stitched.page_end
            except Exception:
                pass
            row_pages = list(stitched.row_pages)
            if prelude:
                row_pages = [row_pages[0], None, *row_pages[1:]]
            attach_scoring_payload(
                chunk,
                markdown,
                lot=lot_label,
                page_start=stitched.page_start,
                page_end=stitched.page_end,
                source="stitched",
                row_pages=row_pages,
            )
            chunks.append(chunk)
            if prelude:
                state.volume_prelude_used = True
    return chunks


def collect_text_formula_scoring_chunks(
    *,
    parsed_doc: "ParsedDocument",
    scan_ranges: list,
    planner: "ChunkPlanner",
    state: ScoringChunkBuildState,
    config: "TextFormulaChunkConfig",
) -> list["TextChunk"]:
    """收集文字载体价格公式评分 chunk。

 Task4B review C1: 参数收敛为 ≤6（策略配置由 TextFormulaChunkConfig 承载）。
 """
    chunks: list["TextChunk"] = []
    for recognized in recognize_price_formula_sections(
        parsed_doc, scan_ranges, config.volume_prelude or "",
        price_contexts=config.price_contexts or [],
    ):
        if not config.markdown_is_scoring_candidate(recognized.markdown):
            logger.debug(
                "[文档处理] 价格公式段落未过评分候选闸门: page={}",
                recognized.page_num,
            )
            continue
        chunk = planner.build_manual_chunk(
            content=recognized.markdown,
            page_num=recognized.page_num,
            chunk_type="scoring_table",
            parent_block_id=f"scoring-price-formula:{recognized.page_num}",
            section_path="价格评分标准",
        )
        chunk.chunk_index = state.idx
        state.idx += 1
        try:
            chunk.metadata["is_scoring_table"] = True
            chunk.metadata["page_end"] = recognized.page_num
            chunk.metadata["recovered_from"] = "price_formula_text"
        except Exception:
            pass
        # Task4B: lot_label 来自 RecognizedPriceFormula，写入 payload
        config.attach_scoring_payload(
            chunk,
            recognized.markdown,
            lot=recognized.lot_label,
            page_start=recognized.page_num,
            page_end=recognized.page_num,
            source="price_formula_text",
        )
        chunks.append(chunk)
    return chunks


def _first_row_key(table) -> str:
    if not table.rows:
        return ""
    return "||".join(str(c or "").strip() for c in table.rows[0])


__all__ = [
    "ScoringChunkBuildState",
    "TextFormulaChunkConfig",
    "collect_assembled_scoring_chunks",
    "collect_text_formula_scoring_chunks",
    "collect_unclaimed_table_scoring_chunks",
    "has_multi_template_scheme_signal",
]
