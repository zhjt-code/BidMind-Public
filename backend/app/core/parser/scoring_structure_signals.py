"""评分结构边界信号快照（SchemeReader 阶段0）。

职责: 在装配/分块之前，从 ParsedDocument 评分区段逐字捕获结构边界信号
 (声明句/表头行/合计行/行骨架/标题样行/页来源标记)，带 (page, table_index,
 row_index) 锚点，持久化供评分结构判读器(SchemeReader)消费。
不做: 不解释语义(不判卷/不切方案/不定表类型)、不拼接表格、不产 chunk、
 不调用 LLM/DB(落库由 chunk_stage 负责)。

设计约束(教训):
- 取材点必须在装配之前——"合计N"行与表间章节标题在 ParsedPage.tables /
 ParsedPage.text 层完整存在，装配层拼接会丢弃这两类方案边界信号。
- 只做"逐字摘录 + 关键词选择"，禁止任何归属推断；判读器的每个边界结论
 都要能回到本快照做 exact-match 核验。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from app.core.extractor.scoring.score_column_semantics import (
    is_scoring_header_row,
    is_total_row,
    normalize_cell,
    parse_short_score,
    row_text,
)
from app.core.extractor.scoring.volume_parser import (
    _FLAT_COMPOSITION_TRIGGER_TERMS,
    extract_volume_composition_sentence,
)

if TYPE_CHECKING:
    from app.core.parser.pdf_parser import ParsedDocument
    from app.core.parser.scoring_section_locator import ScoringSection

SIGNALS_VERSION = 2  # v2: 新增 declaration_table_rows（声明样表行逐字保留）

# 区段外延页边距(与 scoring_chunk_builder._find_group_anchors 的 -1/+2 窗口对齐)
_SECTION_MARGIN_BEFORE = 1
_SECTION_MARGIN_AFTER = 1

# 逐字摘录截断上限(防止长规则文本撑爆快照)
_CELL_PREVIEW_LEN = 30
_FACTOR_PREVIEW_LEN = 20
_TOTAL_ROW_TEXT_LEN = 60
_DECLARATION_LINE_LEN = 120
_HEADING_LINE_LEN = 40

# 捕获数量上限(单表/单页/全文档)
_MAX_HEADER_ROWS_PER_TABLE = 4
_MAX_TOTAL_ROWS_PER_TABLE = 6
_MAX_SKELETON_ROWS_PER_TABLE = 80
_MAX_DECLARATION_LINES_PER_PAGE = 3
_MAX_DECLARATION_LINES_TOTAL = 30
_MAX_HEADING_LINES_PER_PAGE = 8
_MAX_TABLES_TOTAL = 40
# 声明样表行数上限(全文档): 实测语料最多 4 行/份, 此值仅作畸形文档的兜底
_MAX_DECLARATION_TABLE_ROWS = 20

# 声明样行关键词(选择器, 非解释器)
_DECLARATION_TERMS = ("分值构成", "总分", "满分", "百分制")
# 标题样行关键词与编号形态(捕获"5.2.1 商务评审"类表间章节标题)
_HEADING_TERMS = ("评审", "评分", "评标", "打分", "计分")
_HEADING_NUMBERING_RE = re.compile(
    r"^(\d+(?:\.\d+)+|第[一二三四五六七八九十百零\d]+[章节条款]|[（(]?[一二三四五六七八九十]+[)）、.．])"
)
# 文本稀疏页判定阈值(与 page_renderer.is_scan_heavy_page 的 50 字符判据同源)
_SPARSE_TEXT_THRESHOLD = 50
# 拆行标题合并(Task9): 编号行与标题词行之间允许的最大前瞻字符数
_HEADING_MERGE_LOOKAHEAD = 20


def build_scoring_structure_signals(
    parsed_doc: "ParsedDocument",
    sections: list["ScoringSection"] | None,
) -> dict | None:
    """从评分区段捕获结构信号快照, 返回 JSON 兼容 dict; 无可捕获信号返回 None。"""
    if not sections:
        return None
    scope_pages = resolve_scoring_scope_pages(parsed_doc, sections)
    if not scope_pages:
        return None

    tables: list[dict] = []
    page_flags: list[dict] = []
    declaration_table_rows: list[dict] = []
    declarations, headings = capture_heading_and_declaration_lines(scope_pages)

    for page in scope_pages:
        page_num = int(getattr(page, "page_num", 0) or 0)
        page_text = str(getattr(page, "text", "") or "")
        page_flags.append(
            {
                "page": page_num,
                "text_sparse": len(page_text.strip()) < _SPARSE_TEXT_THRESHOLD,
            }
        )
        for table in getattr(page, "tables", None) or []:
            declaration_table_rows.extend(
                _capture_declaration_table_rows(page_num, table, declaration_table_rows)
            )
            if len(tables) >= _MAX_TABLES_TOTAL:
                break
            signal = _capture_table_signal(page_num, table)
            if signal is not None:
                tables.append(signal)

    if not tables and not declarations:
        return None

    return {
        "version": SIGNALS_VERSION,
        "sections": [
            {
                "title": str(getattr(s, "title", "") or ""),
                "page_start": int(getattr(s, "page_start", 0) or 0),
                "page_end": int(getattr(s, "page_end", 0) or 0),
            }
            for s in sections
        ],
        "declarations": declarations[:_MAX_DECLARATION_LINES_TOTAL],
        "declaration_table_rows": declaration_table_rows,
        "headings": headings,
        "tables": tables,
        "pages": page_flags,
    }


def resolve_scoring_scope_pages(
    parsed_doc: "ParsedDocument",
    sections: list["ScoringSection"],
) -> list:
    """按区段(含外延边距)收集去重后的页对象, 保持页序。

    公共页选择helper(Task2 复用): 评分结构信号快照与原始表证据快照(装配前)
    共用同一页范围口径, 确保两条证据链锚定一致的物理页。
    """
    wanted: set[int] = set()
    for sec in sections:
        start = int(getattr(sec, "page_start", 0) or 0)
        end = int(getattr(sec, "page_end", 0) or 0)
        if end < start:
            continue
        wanted.update(
            range(max(1, start - _SECTION_MARGIN_BEFORE), end + _SECTION_MARGIN_AFTER + 1)
        )
    return [
        p
        for p in (getattr(parsed_doc, "pages", None) or [])
        if int(getattr(p, "page_num", 0) or 0) in wanted
    ]


def capture_heading_and_declaration_lines(pages: list) -> tuple[list[dict], list[dict]]:
    """逐页捕获标题样行(headings)与声明样行(declarations), 按传入页集决定范围。

    职责: 用同一套关键词/长度上限选择器扫描给定页集, 不自行决定扫描范围。

    历史: 本函数原为让证据层(scoring_table_evidence)传入全文档页集而抽出,
    与区段级的 `build_scoring_structure_signals` 共享实现避免漂移;
    证据层已随死数据处置删除(2026-08-03), 现仅剩区段级单一调用方,
    保留独立形态是因为"扫描范围由调用方决定"这一边界本身仍然成立。
    """
    declarations: list[dict] = []
    headings: list[dict] = []
    for page in pages:
        page_num = int(getattr(page, "page_num", 0) or 0)
        page_text = str(getattr(page, "text", "") or "")
        if len(declarations) < _MAX_DECLARATION_LINES_TOTAL:
            declarations.extend(_capture_declaration_lines(page_num, page_text))
        headings.extend(_capture_heading_lines(page_num, page_text))
    return declarations[:_MAX_DECLARATION_LINES_TOTAL], headings


def _capture_declaration_lines(page_num: int, page_text: str) -> list[dict]:
    """捕获声明样行: 卷定义声明句(权威提取器) + 关键词短行(逐字)。

    关键词通道要求行内含数字("技术评分(总分:53分)"), 排除"序号 评分项目 评分标准
    满分"类表头行文本(表头有 header_rows 独立通道, 混入声明通道会污染判读输入)。
    """
    captured: list[dict] = []
    seen: set[str] = set()

    sentence = extract_volume_composition_sentence(page_text)
    if sentence:
        compact = normalize_cell(sentence)
        seen.add(compact)
        captured.append(
            {"page": page_num, "text": sentence[:_DECLARATION_LINE_LEN], "kind": "composition"}
        )

    for line in page_text.split("\n"):
        if len(captured) >= _MAX_DECLARATION_LINES_PER_PAGE:
            break
        stripped = line.strip()
        if not stripped or len(stripped) > _DECLARATION_LINE_LEN:
            continue
        if not any(term in stripped for term in _DECLARATION_TERMS):
            continue
        if not any(ch.isdigit() for ch in stripped):
            continue
        compact = normalize_cell(stripped)
        if compact in seen:
            continue
        seen.add(compact)
        captured.append({"page": page_num, "text": stripped, "kind": "keyword"})
    return captured


def _capture_heading_lines(page_num: int, page_text: str) -> list[dict]:
    """捕获标题样行: 短行且(命中评审词 或 编号形态), 每页封顶。

 优先级(真实失效: 2250 页1 泛匹配行占满配额, "5.2.1 商务评审"被挤出):
 编号+评审词(方案边界一级证据) > 仅编号 > 仅评审词; 同级保持原文顺序。

 Task9(anchor诊断): 先对同页相邻行做确定性合并(见
 `_merge_adjacent_split_headings`), 再在合并后的行序列上做既有的
 候选筛选/优先级排序, 不改变筛选/排序逻辑本身。
 """
    lines = [line.strip() for line in page_text.split("\n")]
    merged_lines = _merge_adjacent_split_headings(lines)

    candidates: list[tuple[int, int, str]] = []
    for order, stripped in enumerate(merged_lines):
        if not stripped or len(stripped) > _HEADING_LINE_LEN:
            continue
        has_term = any(term in stripped for term in _HEADING_TERMS)
        has_numbering = bool(_HEADING_NUMBERING_RE.match(stripped))
        if not (has_term or has_numbering):
            continue
        # 纯编号行(如页码"12"或孤立"3.")无边界信息, 跳过
        if has_numbering and not has_term and len(normalize_cell(stripped)) <= 4:
            continue
        priority = 0 if (has_numbering and has_term) else (1 if has_numbering else 2)
        candidates.append((priority, order, stripped))
    selected = sorted(candidates)[:_MAX_HEADING_LINES_PER_PAGE]
    return [
        {"page": page_num, "text": text}
        for _, _, text in sorted(selected, key=lambda c: c[1])
    ]


def _merge_adjacent_split_headings(lines: list[str]) -> list[str]:
    """确定性合并被PDF文本流拆行的编号+标题(Task9, 不跨页——本函数只接收
    单页行数组)。合并后原两行位置分别替换为(合并文本, 空字符串), 空字符串
    会被下游"不含内容"过滤自然吞掉, 不重复输出该语义。
    """
    merged = list(lines)
    i = 0
    while i < len(merged) - 1:
        candidate = _merge_split_heading(merged[i], merged[i + 1])
        if candidate is not None:
            merged[i] = candidate
            merged[i + 1] = ""
            i += 2
            continue
        i += 1
    return merged


def _merge_split_heading(current: str, nxt: str) -> str | None:
    """current匹配编号但本身不含标题词, 且nxt在前_HEADING_MERGE_LOOKAHEAD
    字符内出现标题词时, 拼接current与nxt截至该标题词结束的前缀(不带后续
    正文); 合并后超出_HEADING_LINE_LEN则放弃合并(各自保留, 不新增无长度
    上限的正文)。任一条件不满足返回None。
    """
    if not current or not nxt:
        return None
    if not _HEADING_NUMBERING_RE.match(current):
        return None
    if any(term in current for term in _HEADING_TERMS):
        return None
    term_end = _find_earliest_term_end(nxt)
    if term_end is None or term_end > _HEADING_MERGE_LOOKAHEAD:
        return None
    merged = f"{current} {nxt[:term_end]}"
    if len(merged) > _HEADING_LINE_LEN:
        return None
    return merged


def _find_earliest_term_end(text: str) -> int | None:
    """标题词在text中首次出现的结束位置(未命中返回None); 多词命中取最早者。"""
    ends = [text.index(term) + len(term) for term in _HEADING_TERMS if term in text]
    return min(ends) if ends else None


def _capture_table_signal(page_num: int, table) -> dict | None:
    """捕获单张原始表的边界信号与行骨架(逐字, 带 row_index 锚点)。"""
    data = getattr(table, "data", None) or []
    if not data:
        return None

    header_rows: list[dict] = []
    total_rows: list[dict] = []
    skeleton: list[dict] = []
    for row_index, row in enumerate(data):
        cells = [str(c or "") for c in (row or [])]
        if not any(cell.strip() for cell in cells):
            continue
        if is_scoring_header_row(cells) and len(header_rows) < _MAX_HEADER_ROWS_PER_TABLE:
            header_rows.append(
                {
                    "row_index": row_index,
                    "cells": [normalize_cell(c)[:_CELL_PREVIEW_LEN] for c in cells],
                }
            )
            continue
        if is_total_row(cells) and len(total_rows) < _MAX_TOTAL_ROWS_PER_TABLE:
            total_rows.append(
                {
                    "row_index": row_index,
                    "text": row_text(cells)[:_TOTAL_ROW_TEXT_LEN],
                }
            )
            continue
        if len(skeleton) < _MAX_SKELETON_ROWS_PER_TABLE:
            skeleton.append(_build_row_skeleton(row_index, cells))

    return {
        "page": page_num,
        "table_index": int(getattr(table, "table_index", 0) or 0),
        "row_count": int(getattr(table, "row_count", len(data)) or len(data)),
        "col_count": int(getattr(table, "col_count", 0) or 0),
        "header_rows": header_rows,
        "total_rows": total_rows,
        "rows": skeleton,
    }


def _capture_declaration_table_rows(
    page_num: int, table, captured_so_far: list[dict]
) -> list[dict]:
    """逐字保留"分值构成"类声明所在的表行（保留原单元格切分）。

    职责: 只做关键词选择与逐字摘录, 不解析分值、不判卷。
    不做: 不改行骨架通道(_capture_table_signal 职责)、不做归属推断。

    为什么行骨架不够(实测):
      这类声明常是"标题格 + 内容格"两格一行——如
      `第一个信封评分分值构成（总分100.00分）` ‖ `施工组织设计:35.00分主要人员:25.00分…`。
      行骨架把整行压成"首格前20字 + 末个分值格", 内容格整体丢失;
      正文声明通道又按 120 字截断, 尾部若干"标签:分值"对被切掉。
      账本需要的是**整行原文**(拼接后触发构成词, 再逐对解析), 故单开一条逐字通道。

    体量: 58 份语料实测共 24 行、1354 字符(命中 15 份, 均 90 字符/份),
    相对整表快照可忽略, 故不设截断——截断正是本通道要修的缺陷。
    """
    rows: list[dict] = []
    for row_index, row in enumerate(getattr(table, "data", None) or []):
        if len(captured_so_far) + len(rows) >= _MAX_DECLARATION_TABLE_ROWS:
            break
        cells = [str(cell or "") for cell in (row or [])]
        joined = " ".join(cell for cell in cells if cell.strip())
        if not joined or not any(term in joined for term in _FLAT_COMPOSITION_TRIGGER_TERMS):
            continue
        rows.append(
            {
                "page": page_num,
                "table_index": int(getattr(table, "table_index", 0) or 0),
                "row_index": row_index,
                "cells": cells,
            }
        )
    return rows


def _build_row_skeleton(row_index: int, cells: list[str]) -> dict:
    """行骨架: 首个非空格(截断) + 末个可解析分值格原文。"""
    factor = ""
    for cell in cells:
        compact = normalize_cell(cell)
        if compact:
            factor = compact[:_FACTOR_PREVIEW_LEN]
            break
    score_text = ""
    for cell in reversed(cells):
        compact = normalize_cell(cell)
        if not compact:
            continue
        if parse_short_score(compact) is not None:
            score_text = compact[:_CELL_PREVIEW_LEN]
            break
    return {"row_index": row_index, "factor": factor, "score": score_text}


def summarize_signals_for_log(signals: dict | None) -> str:
    """一行日志摘要(表数/声明数/标题数), 不输出正文内容。"""
    if not signals:
        return "无信号"
    return (
        f"tables={len(signals.get('tables') or [])} "
        f"declarations={len(signals.get('declarations') or [])} "
        f"headings={len(signals.get('headings') or [])}"
    )


__all__ = [
    "SIGNALS_VERSION",
    "build_scoring_structure_signals",
    "capture_heading_and_declaration_lines",
    "resolve_scoring_scope_pages",
    "summarize_signals_for_log",
]
