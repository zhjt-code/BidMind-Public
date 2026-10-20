"""评分表整表 chunk 构建（P2 + 逻辑表装配）。

职责: 定位评分区段 -> 逻辑表装配(表头锚定+表尾封口)优先认领 -> 未认领表走跨页拼接
 兜底 -> 渲染为 scoring_table 整表 chunk，使 scoring_extractor 看到完整评分表。
不做: 不向量化/不入库/不调用 LLM；仅产出与现有逐行 chunk 并存的增量整表 chunk。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from app.core.config import settings
from app.core.extractor.scoring.volume_parser import extract_volume_composition_sentence
from app.core.parser.scoring_group_anchor import (
    GroupAnchor,
    build_group_composition_sentence,
    extract_closed_group_anchors_from_pages,
)
from app.core.parser.scoring_section_locator import locate_scoring_sections
from app.core.parser.scoring_table_detector import analyze_scoring_table_rows
from app.core.parser.table_linearizer import join_cell_lines_cjk_aware
from app.core.parser.table_stitcher import StitchedTable, stitch_cross_page_tables

from .lot_boundary_locator import locate_lot_boundaries, lot_for_page
from .scoring_chunk_sources import (
    ScoringChunkBuildState,
    TextFormulaChunkConfig,
    collect_assembled_scoring_chunks,
    collect_text_formula_scoring_chunks,
    collect_unclaimed_table_scoring_chunks,
)
from .scoring_table_assembler import (
    LogicalScoringSegment,
    render_assembled_segment_markdown,
    segment_to_grid,
)

if TYPE_CHECKING:
    from app.core.parser.chunk_planner import ChunkPlanner
    from app.core.parser.pdf_parser import ParsedDocument
    from app.core.parser.text_chunker import TextChunk


def build_scoring_table_chunks(
    parsed_doc: "ParsedDocument",
    planner: "ChunkPlanner",
    start_index: int,
    sections: list | None = None,
    *,
    scope_page_texts: dict[int, str] | None = None,
) -> list["TextChunk"]:
    """为评分区段构建逻辑表/跨页拼接整表 chunk。"""
    located_sections = _merge_sections([
        *(sections or []),
        *locate_scoring_sections(parsed_doc),
        *_scan_sections_by_table_signals(parsed_doc),
    ])
    if not located_sections:
        return []

    pages_by_num = {p.page_num: p for p in parsed_doc.pages}

    # 模板编码边界切分——在装配层面隔离不同模板的页范围，
    # 防止 stitch_cross_page_tables 将多模板页面拼接成单一超大逻辑表。
    from app.core.parser.scoring_assembly_scopes import split_sections_by_template_boundaries
    from app.core.parser.scoring_template_boundaries import (
        detect_template_boundaries_from_sections,
    )
    template_boundaries = detect_template_boundaries_from_sections(
        pages_by_num, located_sections
    )
    if template_boundaries:
        located_sections = split_sections_by_template_boundaries(
            located_sections, template_boundaries
        )

    anchor_enabled = bool(getattr(settings, "SCORING_GROUP_ANCHOR_ENABLED", True))
    group_anchors = (
        _find_group_anchors(parsed_doc, located_sections)
        if anchor_enabled
        else []
    )
    chunks: list["TextChunk"] = []
    state = ScoringChunkBuildState(
        idx=start_index,
        claimed_global=set(),
        seen_segments=set(),
        seen_tables=set(),
        rejected_with_score_signal=[],
    )

    assembly_enabled = bool(
        getattr(settings, "SCORING_LOGICAL_ASSEMBLY_ENABLED", True)
    )
    text_elements_enabled = bool(
        getattr(settings, "SCORING_TEXT_ELEMENTS_ENABLED", True)
    )
    lot_enabled = bool(getattr(settings, "SCORING_LOT_DIMENSION_ENABLED", True))
    lots = locate_lot_boundaries(parsed_doc) if lot_enabled else []

    # 第零层: 转置最终分值轴预扫描（Task4B / review I1）----
    # 必须在 lots 解析完成后执行，以便按 lot_for_page 精确绑定 PriceFormulaContext。
    # 生成声明型 chunk（只携 volume_defs，不产评分 item），预占物理表键防技术表吞并。
    from app.core.parser.scoring_axis_chunk_source import scan_transposed_axis_chunks

    axis_scan = scan_transposed_axis_chunks(parsed_doc, planner, state.idx, lots=lots)
    if axis_scan.chunks:
        chunks.extend(axis_scan.chunks)
        state.claimed_global.update(axis_scan.claimed_keys)
        state.idx += len(axis_scan.chunks)

    volume_prelude = (
        _find_volume_composition_sentence(parsed_doc, located_sections)
        if text_elements_enabled
        else None
    ) or build_group_composition_sentence(group_anchors)

    # 第一层: 逻辑表装配——表头锚定, 跨宽度/跨失表页重建 ----
    # 重叠区段(结构定位与表信号回扫常交叠)先合并为连续跨度, 防止窄区段
    # 抢先认领前半张表把同一逻辑表再次切碎。
    if assembly_enabled:
        if lots:
            # C1: lots 存在时，先与评分 sections 求交再按模板边界切分
            assembly_spans = _intersect_lots_and_split_by_boundaries(
                lots, located_sections, template_boundaries
            )
        else:
            assembly_spans = _resolve_assembly_spans(located_sections, group_anchors)
            # C1: _resolve_assembly_spans 内部会 coalesce，重新在装配范围上应用模板切分
            if template_boundaries:
                assembly_spans = split_sections_by_template_boundaries(
                    assembly_spans, template_boundaries
                )
        chunks.extend(
            collect_assembled_scoring_chunks(
                parsed_doc=parsed_doc,
                assembly_spans=assembly_spans,
                planner=planner,
                state=state,
                lots=lots,
                volume_prelude=volume_prelude,
                pages_by_num=pages_by_num,
                build_segment_chunk=_build_segment_chunk,
                lot_label_for_page=_lot_label_for_page,
                volume_title_for_page=_volume_title_for_page,
            )
        )

    if text_elements_enabled and group_anchors:
        synthesized = _synthesize_anchor_group_chunks(
            parsed_doc,
            planner,
            state.idx,
            group_anchors,
            _coalesce_section_spans(located_sections),
        )
        chunks.extend(synthesized)
        state.idx += len(synthesized)

    chunks.extend(
        collect_unclaimed_table_scoring_chunks(
            parsed_doc=parsed_doc,
            located_sections=located_sections,
            planner=planner,
            state=state,
            lots=lots,
            volume_prelude=volume_prelude,
            pages_by_num=pages_by_num,
            inject_volume_prelude=_inject_volume_prelude,
            inject_lot_label=_inject_lot_label,
            join_section_path=_join_section_path,
            lot_label_for_page=_lot_label_for_page,
            volume_title_for_page=_volume_title_for_page,
            attach_scoring_payload=_attach_scoring_payload,
        )
    )

    # 第三层: 文字载体价格公式(+ 缺口B) ----
    # 满分三来源（同 scope 须一致）：
    #   ① 转置轴期望（PriceFormulaContext.price_score）
    #   ② 卷定义声明句（volume_prelude 中 price 卷基准）
    #   ③ 行内显式声明（"价格项…满分为N分"）
    # 规则：同 scope 多个非空值不一致 → fail-closed，不产 chunk，logger.warning 诊断；
    #        全部缺失 → 不产 chunk；禁止从公式系数（如 ×40×100）猜分。
    if text_elements_enabled:
        price_scan_ranges = lots if lots else _coalesce_section_spans(located_sections)
        # Task4B review I1: axis_scan.conflict=True 时不传递 price_contexts
        price_ctxs = axis_scan.price_contexts if not axis_scan.conflict else []
        # Task4B review C1: 配置收敛到 TextFormulaChunkConfig（≤6 参数）
        text_formula_config = TextFormulaChunkConfig(
            volume_prelude=volume_prelude,
            markdown_is_scoring_candidate=_markdown_is_scoring_candidate,
            attach_scoring_payload=_attach_scoring_payload,
            price_contexts=price_ctxs,
        )
        chunks.extend(
            collect_text_formula_scoring_chunks(
                parsed_doc=parsed_doc,
                scan_ranges=price_scan_ranges,
                planner=planner,
                state=state,
                config=text_formula_config,
            )
        )

    # 为已产出 chunk 注入 scheme_boundary_id（后置写入，不修改 chunk_sources）
    if template_boundaries:
        from app.core.parser.scoring_assembly_scopes import inject_scheme_boundary_ids
        inject_scheme_boundary_ids(chunks, template_boundaries)

    # 组身份写在正文标题+表尾合计行的文档(2479), 组边界证据只在装配层
    # 可见(页文本+载荷网格同时在手), 此处统一补写 scope_rows 供解析器兜底归组。
    from app.core.extractor.scoring.table_scope_resolution import (
        attach_scope_rows_to_payload,
    )

    for chunk in chunks:
        attach_scope_rows_to_payload(
            chunk,
            pages_by_num,
            scope_page_texts=scope_page_texts,
        )

    if chunks:
        logger.info(
            f"[文档处理] 评分表整表 chunk: {len(chunks)} 个 (评分区段 {len(located_sections)} 处)"
        )
    else:
        _warn_all_candidates_rejected(state.rejected_with_score_signal)
    return chunks


def _find_group_anchors(parsed_doc: "ParsedDocument", sections: list) -> list[GroupAnchor]:
    """从评分区段页文本识别闭合组锚点。"""
    if not sections:
        return []
    pages_by_num = {p.page_num: p for p in parsed_doc.pages}
    seen_pages: set[int] = set()
    page_texts: list[tuple[int, str]] = []
    for sec in _coalesce_section_spans(sections):
        for page_num in range(max(1, sec.page_start - 1), sec.page_end + 2):
            if page_num in seen_pages:
                continue
            seen_pages.add(page_num)
            page = pages_by_num.get(page_num)
            if page is None:
                continue
            page_texts.append((page_num, getattr(page, "text", "") or ""))
    return extract_closed_group_anchors_from_pages(page_texts)


def _resolve_assembly_spans(sections: list, anchors: list[GroupAnchor]) -> list:
    """组锚点闭合时按锚点切分装配范围，否则沿用区段合并范围。"""
    spans = _coalesce_section_spans(sections)
    if len(anchors) < 2 or not spans:
        return spans
    from app.core.parser.scoring_section_locator import ScoringSection

    ordered = sorted(anchors, key=lambda a: a.page_num)
    out: list[ScoringSection] = []
    for idx, anchor in enumerate(ordered):
        parent = _span_containing_page(spans, anchor.page_num, include_adjacent=True)
        if parent is None:
            continue
        next_page = (
            ordered[idx + 1].page_num - 1
            if idx + 1 < len(ordered)
            else parent.page_end
        )
        page_end = max(anchor.page_num, min(next_page, parent.page_end))
        out.append(
            ScoringSection(
                title=anchor.label,
                page_start=anchor.page_num,
                page_end=page_end,
                detection="group_anchor",
            )
        )
    return out or spans


def _span_containing_page(spans: list, page_num: int, *, include_adjacent: bool = False):
    for span in spans:
        start = span.page_start - 1 if include_adjacent else span.page_start
        end = span.page_end + 1 if include_adjacent else span.page_end
        if start <= page_num <= end:
            return span
    return None


def _synthesize_anchor_group_chunks(
    parsed_doc: "ParsedDocument",
    planner: "ChunkPlanner",
    start_index: int,
    anchors: list[GroupAnchor],
    spans: list,
) -> list["TextChunk"]:
    """对无表但有公式信号的组锚点合成评分 chunk。"""
    if not anchors:
        return []
    pages_by_num = {p.page_num: p for p in parsed_doc.pages}
    chunks: list["TextChunk"] = []
    idx = start_index
    ordered = sorted(anchors, key=lambda a: a.page_num)
    for pos, anchor in enumerate(ordered):
        parent = _span_containing_page(spans, anchor.page_num, include_adjacent=True)
        if parent is None:
            continue
        end_page = ordered[pos + 1].page_num - 1 if pos + 1 < len(ordered) else parent.page_end
        page_nums = range(anchor.page_num, max(anchor.page_num, min(end_page, parent.page_end)) + 1)
        pages = [pages_by_num[pn] for pn in page_nums if pn in pages_by_num]
        if any(getattr(page, "tables", None) for page in pages):
            continue
        group_text = "\n".join(getattr(page, "text", "") or "" for page in pages)
        if not _has_anchor_formula_signal(group_text):
            continue
        markdown = _build_anchor_formula_markdown(anchor, group_text)
        if not _markdown_is_scoring_candidate(markdown):
            continue
        chunk = planner.build_manual_chunk(
            content=markdown,
            page_num=anchor.page_num,
            chunk_type="scoring_table",
            parent_block_id=f"scoring-group-formula:{anchor.page_num}",
            section_path=anchor.label,
        )
        chunk.chunk_index = idx
        idx += 1
        try:
            chunk.metadata["is_scoring_table"] = True
            chunk.metadata["page_end"] = pages[-1].page_num if pages else anchor.page_num
            chunk.metadata["recovered_from"] = "group_anchor_formula"
        except Exception:
            pass
        _attach_scoring_payload(
            chunk, markdown,
            page_start=anchor.page_num,
            page_end=pages[-1].page_num if pages else anchor.page_num,
            source="group_anchor_formula",
        )
        chunks.append(chunk)
    return chunks


def _has_anchor_formula_signal(text: str) -> bool:
    compact = str(text or "").replace(" ", "")
    return "得分=" in compact and any(term in compact for term in ("基准价", "评标价", "价格满分"))


def _build_anchor_formula_markdown(anchor: GroupAnchor, text: str) -> str:
    safe_text = join_cell_lines_cjk_aware(str(text or "")).replace("|", "/")
    score = f"{anchor.max_score:g}"
    group_label = "价格部分" if anchor.volume == "price" else anchor.label
    item_name = "报价评分" if anchor.volume == "price" else f"{anchor.label}评分"
    return "\n".join(
        [
            f"[评分表 第{anchor.page_num}-{anchor.page_num}页]",
            "| 分组 | 评分因素 | 评分内容与标准 | 分值 |",
            "| --- | --- | --- | --- |",
            f"| {group_label} |  |  |  |",
            f"|  | {item_name} | {safe_text} | {score} |",
        ]
    )


def _build_segment_chunk(
    seg: LogicalScoringSegment,
    sec,
    planner: "ChunkPlanner",
    idx: int,
    seen_segments: set[tuple[int, int, str]],
    *,
    volume_prelude: str | None = None,
    lot_label: str | None = None,
    volume_title: str | None = None,
) -> "TextChunk | None":
    """逻辑表段 -> detector 复验 -> scoring_table chunk; 不合格返回 None。"""
    grid = segment_to_grid(seg)
    if len(grid) < 2:
        return None
    analysis = analyze_scoring_table_rows(grid)
    if not analysis.is_scoring_candidate:
        logger.debug(
            "[文档处理] 逻辑表段未过评分候选闸门: pages={}-{} reasons={}",
            seg.page_start,
            seg.page_end,
            analysis.reasons,
        )
        return None
    seg_key = (seg.page_start, seg.page_end, _grid_row_key(grid))
    if seg_key in seen_segments:
        return None
    seen_segments.add(seg_key)
    markdown = _inject_volume_prelude(render_assembled_segment_markdown(seg), volume_prelude)
    markdown = _inject_lot_label(markdown, lot_label)
    if not markdown or len(markdown) < 20:
        return None
    chunk = planner.build_manual_chunk(
        content=markdown,
        page_num=seg.page_start,
        chunk_type="scoring_table",
        parent_block_id=f"scoring-table:{seg.page_start}-{seg.page_end}",
        section_path=_join_section_path(seg.title or (sec.title or "评分表"), volume_title),
    )
    chunk.chunk_index = idx
    try:
        chunk.metadata["is_scoring_table"] = True
        chunk.metadata["page_end"] = seg.page_end
        chunk.metadata["assembled"] = True
        chunk.metadata["assembly_repaired"] = seg.repair_applied
        if lot_label:
            chunk.metadata["lot"] = lot_label
    except Exception:
        pass
    # 原始行序渲染段(非canonical)透传装配器已验证的列角色, canonical 段无需
    rendered_canonical = seg.repair_applied and seg.scored_row_count > 0
    row_pages = None
    if rendered_canonical:
        row_pages = [None]
        row_pages.extend([None] * len(seg.prelude_rows))
        current_group = ""
        for scored_row in seg.rows:
            if scored_row.group and scored_row.group != current_group:
                current_group = scored_row.group
                row_pages.append(scored_row.page)
            row_pages.append(scored_row.page)
    elif seg.raw_rows and len(seg.raw_row_pages) == len(seg.raw_rows):
        row_pages = [None]
        row_pages.extend([None] * len(seg.prelude_rows))
        row_pages.extend(seg.raw_row_pages)
    if row_pages is not None and volume_prelude:
        row_pages = [row_pages[0], None, *row_pages[1:]]
    roles_payload = None
    resolved = getattr(seg, "resolved_roles", None)
    if not rendered_canonical and resolved is not None:
        roles_payload = {
            "score": resolved.score,
            "rule": resolved.rule,
            "name": resolved.name,
            "group": resolved.group,
        }
    _attach_scoring_payload(
        chunk, markdown,
        lot=lot_label, page_start=seg.page_start, page_end=seg.page_end,
        source="assembled", row_pages=row_pages,
        column_roles=roles_payload,
    )
    if seg.repair_applied:
        logger.info(
            "[文档处理] 逻辑表装配修复: pages={}-{} 评分行={} (跨宽度/失表页/接缝修复已应用)",
            seg.page_start,
            seg.page_end,
            seg.scored_row_count,
        )
    return chunk


def _warn_all_candidates_rejected(rejected_with_score_signal: list[str]) -> None:
    """闸门全拒但存在含分值列的候选表时升 WARNING（: 消除静默归零）。

 职责: 把"评分区段定位成功、却零产出且有疑似评分表被拒"暴露为可观测告警。
 不做: 不改变闸门判定结果、不重试。
 """
    if not rejected_with_score_signal:
        return
    logger.warning(
        "[文档处理] 评分区段内 {} 张含分值列候选表全部被候选闸门拒绝, "
        "疑似闸门误杀(评分项提取将零产出, 请核对表结构): {}",
        len(rejected_with_score_signal),
        "; ".join(rejected_with_score_signal),
    )


def _find_volume_composition_sentence(parsed_doc: "ParsedDocument", sections: list) -> str | None:
    """在评分区段文本中找可解析的卷定义声明句。"""
    if not sections:
        return None
    spans = _coalesce_section_spans(sections)
    pages_by_num = {p.page_num: p for p in parsed_doc.pages}
    seen_pages: set[int] = set()
    for sec in spans:
        for page_num in range(sec.page_start, sec.page_end + 1):
            if page_num in seen_pages:
                continue
            seen_pages.add(page_num)
            page = pages_by_num.get(page_num)
            if page is None:
                continue
            sentence = extract_volume_composition_sentence(getattr(page, "text", "") or "")
            if sentence:
                return sentence
    return None


def _inject_volume_prelude(markdown: str, sentence: str | None) -> str:
    """把卷定义声明句注入 markdown 表头后，供 parser 扫描 volume_defs。"""
    if not sentence or not markdown:
        return markdown
    lines = markdown.splitlines()
    if len(lines) < 3:
        return markdown
    # [评分表] / 表头 / |---| / 数据...
    return "\n".join([*lines[:3], f"| {sentence} |", *lines[3:]])


def _inject_lot_label(markdown: str, lot_label: str | None) -> str:
    """把 lot 标签注入 scoring table 页头: [评分表 第x-y页 · 采购包1]。"""
    if not lot_label or not markdown:
        return markdown
    lines = markdown.splitlines()
    if not lines or "·" in lines[0] or not lines[0].startswith("[评分表"):
        return markdown
    lines[0] = lines[0].rstrip("]") + f" · {lot_label}]"
    return "\n".join(lines)


def _lot_label_for_page(lots: list, page_num: int) -> str | None:
    """lot 标签查找: 范围命中优先; 多 lot 时可用最近前置锚点兜底。"""
    label = lot_for_page(lots, page_num)
    if label or not lots:
        return label
    previous = [lot for lot in lots if lot.page_start <= page_num]
    return previous[-1].label if previous else None


def _volume_title_for_page(pages_by_num: dict, page_num: int) -> str | None:
    """探测表起始页的表级卷标题句(: 多表方案的卷别证据), 供 section_path 附加。"""
    from app.core.extractor.scoring.volume_labeling import detect_table_volume_title

    page = pages_by_num.get(page_num)
    if page is None:
        return None
    return detect_table_volume_title(getattr(page, "text", "") or "")


def _attach_scoring_payload(
    chunk: "TextChunk",
    markdown: str,
    *,
    lot: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    source: str = "assembled",
    column_roles: dict | None = None,
    row_pages: list[int | None] | None = None,
) -> None:
    """把结构化载荷写入 chunk.metadata(阶段1.5), 供向量化持久化与提取直读。

 column_roles: 装配器经数据资格赛的列角色(原表列序), 仅原始行序渲染段
 附带——canonical 段表头词面固定无歧义; 解析器优先消费, 免重蹈词面错绑。
 """
    from app.core.extractor.scoring.scoring_grid_payload import (
        PAYLOAD_KEY,
        build_scoring_payload,
    )

    payload = build_scoring_payload(
        markdown, lot=lot, page_start=page_start, page_end=page_end,
        source=source, row_pages=row_pages,
    )
    if payload is None:
        return
    if column_roles:
        payload["column_roles"] = column_roles
    try:
        chunk.metadata[PAYLOAD_KEY] = payload
    except Exception:
        pass


def _join_section_path(base: str, volume_title: str | None) -> str:
    """把表级卷标题证据并入 section_path(解析器按其推断表初始卷别)。"""
    if not volume_title:
        return base
    return f"{base} · {volume_title}"


def _markdown_is_scoring_candidate(markdown: str) -> bool:
    """轻量解析 markdown 表格并复用 detector 校验评分候选。"""
    rows: list[list[str]] = []
    for line in (markdown or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if set(s.replace("|", "").replace("-", "").replace(" ", "")) == set():
            continue
        rows.append([cell.strip() for cell in s.strip().strip("|").split("|")])
    return bool(rows and analyze_scoring_table_rows(rows).is_scoring_candidate)


def _coalesce_section_spans(sections: list) -> list:
    """把重叠/邻接的评分区段合并为连续跨度(装配层专用, legacy 路径仍用原区段)。"""
    from app.core.parser.scoring_section_locator import ScoringSection

    ordered = sorted(sections, key=lambda s: (s.page_start, s.page_end))
    spans: list[ScoringSection] = []
    for sec in ordered:
        if spans and sec.page_start <= spans[-1].page_end + 1:
            prev = spans[-1]
            spans[-1] = ScoringSection(
                title=prev.title,
                page_start=prev.page_start,
                page_end=max(prev.page_end, sec.page_end),
                detection=prev.detection,
            )
        else:
            spans.append(
                ScoringSection(
                    title=getattr(sec, "title", "") or "评分表",
                    page_start=sec.page_start,
                    page_end=sec.page_end,
                    detection=getattr(sec, "detection", "structure"),
                )
            )
    return spans


def _merge_sections(sections: list) -> list:
    """合并去重评分区段, 保留调用方传入的预降噪坐标。"""
    if not sections:
        return []
    by_range: dict[tuple[int, int, str], object] = {}
    for sec in sections:
        key = (
            int(getattr(sec, "page_start", 0) or 0),
            int(getattr(sec, "page_end", 0) or 0),
            str(getattr(sec, "title", "") or "评分表"),
        )
        if key[0] < 1 or key[1] < key[0]:
            continue
        by_range.setdefault(key, sec)
    return list(sorted(by_range.values(), key=lambda s: (s.page_start, s.page_end)))


def scan_sections_by_table_signals(parsed_doc: "ParsedDocument") -> list:
    """按表内部评分结构回扫候选页范围（P2-1：公开导出，chunk_stage 同源消费）。

 职责: 不依赖章节关键词，仅凭表格行的评分信号（score_signal）判断是否含评分表，
 返回命中的页范围区段列表。供 chunk_stage 构建与 build_scoring_table_chunks
 同源的 pre_denoise_scoring_sections，消除"有 chunk 无 evidence"根因。
 不做: 不提取评分项/分值语义、不修改 ParsedDocument、不调用 LLM/DB。
 """
    from app.core.parser.scoring_section_locator import ScoringSection

    page_tables = []
    for page in parsed_doc.pages:
        for table in getattr(page, "tables", []) or []:
            page_tables.append((page.page_num, table))
    stitched_tables = stitch_cross_page_tables(page_tables)
    sections = []
    for stitched in stitched_tables:
        analysis = analyze_scoring_table_rows(stitched.rows)
        if not analysis.is_scoring_candidate:
            continue
        # 末页续表因列宽抖动被拼表按"列数一致"单独分组, 单页判定必然不是
        # 候选(缺表头), 需把紧邻续表纳入右边界, 否则该页评分行从未进入装配范围——
        # 装配器本身能处理列宽抖动(宽度组分别推断角色)。
        sections.append(
            ScoringSection(
                title="评分表内部结构候选",
                page_start=stitched.page_start,
                page_end=_tail_continuation_page_end(stitched, stitched_tables),
                detection="table_signal",
            )
        )
    return sections


def _tail_continuation_page_end(
    candidate: StitchedTable, all_tables: list[StitchedTable]
) -> int:
    """把紧邻候选表的续表页纳入区段右边界。

 职责: 仅向后逐页吞并"紧邻 + 非候选 + 自身带评分规则信号"的表, 修复跨页表列宽
 抖动(pdfplumber)致末页续表落在所有区段之外、评分行永不进入装配的缺口。
 不做: 不向前扩展(防吞入前导说明表)、不吞候选表(它们自成区段)、不吞无评分信号表
 (防吞入资源/工期等说明表, 事故教训: 区段范围放宽会改变装配路径)。
 """
    page_end = candidate.page_end
    by_start = {t.page_start: t for t in all_tables if t is not candidate}
    while True:
        nxt = by_start.get(page_end + 1)
        if nxt is None:
            return page_end
        analysis = analyze_scoring_table_rows(nxt.rows)
        # 候选表自成区段, 不并入; 无评分规则信号的表是说明表, 不并入
        if analysis.is_scoring_candidate or "rule_signal" not in analysis.reasons:
            return page_end
        page_end = nxt.page_end


def _scan_sections_by_table_signals(parsed_doc: "ParsedDocument") -> list:
    """内部别名，保持 build_scoring_table_chunks 内调用不变。"""
    return scan_sections_by_table_signals(parsed_doc)


def _first_row_key(table: StitchedTable) -> str:
    if not table.rows:
        return ""
    return "|".join(str(cell or "").replace(" ", "") for cell in table.rows[0])[:120]


def _grid_row_key(grid: list[list[str]]) -> str:
    if not grid:
        return ""
    probe = grid[1] if len(grid) > 1 else grid[0]
    return "|".join(str(cell or "").replace(" ", "") for cell in probe)[:120]


# ---------------------------------------------------------------------------
# lots × template boundary 交集辅助
# ---------------------------------------------------------------------------


def _intersect_lots_and_split_by_boundaries(
    lots: list,
    sections: list,
    template_boundaries: list,
) -> list:
    """sections×lot 求交 + 模板边界切分（B4）。

    职责:
    - template_boundaries 为空：直接返回 list(lots)，不收窄范围；
    - template_boundaries 非空：coalesce sections，逐 lot 单独求交再切分，不跨 lot 合并。
    不做: 不跨 lot 合并、lot 变化不等于 scheme 变化。
    """
    # 无模板边界：lots 即为装配范围，无需与 sections 求交
    if not template_boundaries:
        return list(lots)

    from app.core.parser.scoring_assembly_scopes import split_sections_by_template_boundaries
    from app.core.parser.scoring_section_locator import ScoringSection

    coalesced = _coalesce_section_spans(sections)
    result: list[ScoringSection] = []

    for lot in lots:
        # 求交：找所有与当前 lot 有重叠的 coalesced sections
        lot_sections: list[ScoringSection] = []
        for sec in coalesced:
            start = max(lot.page_start, sec.page_start)
            end = min(lot.page_end, sec.page_end)
            if start > end:
                continue
            lot_sections.append(
                ScoringSection(
                    title=getattr(sec, "title", "") or "评分表",
                    page_start=start,
                    page_end=end,
                    detection=getattr(sec, "detection", "structure"),
                )
            )

        if not lot_sections:
            continue  # 该 lot 无评分区段，不凭空装配

        # 在当前 lot 的交集范围内按模板边界切分（不跨 lot coalesce）
        if template_boundaries:
            lot_sections = split_sections_by_template_boundaries(lot_sections, template_boundaries)

        result.extend(lot_sections)

    return result
