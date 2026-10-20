"""评分区段孤表续接恢复。

职责: 把紧邻已装配逻辑表段结束页下一页、被候选闸门拒绝的退化小表(无表头/行少/
 单分值行)按段表头列角色投影为续接评分行, 就地并入段产物。
不做: 不判定整段评分性(scoring_table_detector 职责)、不解析评分项(parser 职责)、
 不处理失表页散文(scoring_text_tail_recovery 职责)、不调用 LLM/DB。

根因(2026-07-17 样本审计): 样本2第3页 `| |10分|信誉|规则...` 系第1-2页评分表的
物理续表——章节定位失败时表信号回扫不含弱信号小表(区段止于第2页), 且装配器宽度组
分布推断要求>=2个分值行, 单行退化组被整组丢弃, 信誉10分静默蒸发(核计90/100)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from app.core.extractor.scoring.score_column_semantics import (
    is_scoring_header_row,
    is_total_row,
)
from app.core.parser.scoring_header_role_binding import (
    roles_from_header as _roles_from_header,
)
from app.core.parser.scoring_table_assembler import (
    CanonicalScoringRow,
    LogicalScoringSegment,
)
from app.core.parser.scoring_row_projection import project_scoring_row
from app.core.parser.scoring_table_detector import analyze_scoring_table_rows

if TYPE_CHECKING:
    from app.core.parser.pdf_parser import ParsedDocument

# 判定阈值(从严 AND, 防误吞独立表/正文表)
_MAX_STUB_ROWS = 4
_MAX_WIDTH_DELTA = 2


def recover_stub_continuation(
    parsed_doc: "ParsedDocument",
    seg: LogicalScoringSegment,
    claimed: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    """扫描段结束页的下一页, 把符合续接特征的孤表行投影并入段; 返回新认领表键。

    续接判定(全部满足):
    - 紧邻页(seg.page_end + 1)且该表未被认领;
    - 行数 <= 4 且不含评分表头行(有表头即独立新表, 交既有路径);
    - 列数与段表头宽度差 <= 2;
    - 被候选闸门拒绝(is_scoring_candidate=False, 仅救"否则整表丢弃"的孤表);
    - 至少一行具备"恰一个短分值格 + 短中文名称格 + 长规则文本格"结构。
    """
    lookahead_page = seg.page_end + 1
    page = next(
        (p for p in parsed_doc.pages if p.page_num == lookahead_page), None
    )
    if page is None:
        return set()
    header_width = len(seg.header_row)
    newly_claimed: set[tuple[int, int]] = set()
    for pos, table in enumerate(page.tables or []):
        if (lookahead_page, pos) in claimed:
            continue
        data = getattr(table, "data", None) or []
        rows = [[str(c or "").strip() for c in row] for row in data if row is not None]
        if not rows or len(rows) > _MAX_STUB_ROWS:
            continue
        if any(is_scoring_header_row(row) for row in rows):
            continue
        width = max(len(r) for r in rows)
        if abs(width - header_width) > _MAX_WIDTH_DELTA:
            continue
        if analyze_scoring_table_rows(rows).is_scoring_candidate:
            continue  # 能独立成表的交既有 legacy/装配路径, 不做续接抢占
        projected = [
            fields for row in rows
            if not is_total_row(row) and (fields := _project_stub_row(row)) is not None
        ]
        if not projected:
            continue
        for name, score_text, score, rule in projected:
            _append_projected_row(seg, lookahead_page, name, score_text, score, rule)
        newly_claimed.add((lookahead_page, pos))
        seg.page_end = max(seg.page_end, lookahead_page)
        logger.info(
            "[文档处理] 孤表续接恢复: page={} 并入{}行评分续接 (段 pages={}-{})",
            lookahead_page,
            len(projected),
            seg.page_start,
            seg.page_end,
        )
    return newly_claimed


def _project_stub_row(row: list[str]) -> tuple[str, str, float, str] | None:
    """把退化行投影为(名称, 分值原文, 分值, 规则); 结构不符返回 None。"""
    projected = project_scoring_row(row)
    if projected is None:
        return None
    return projected.name, projected.score_text, projected.score, projected.rule


def _append_projected_row(
    seg: LogicalScoringSegment,
    page: int,
    name: str,
    score_text: str,
    score: float,
    rule: str,
) -> None:
    """按段的两种渲染形态同步追加投影行(不切换 repair 渲染模式)。"""
    group = seg.rows[-1].group if seg.rows else ""
    seg.rows.append(
        CanonicalScoringRow(
            group=group,
            factor=name,
            rule=rule,
            score_text=score_text,
            score=score,
            page=page,
        )
    )
    roles, _weight, _clause = _roles_from_header(seg.header_row)
    raw = [""] * len(seg.header_row)
    name_idx = roles.name if roles.name is not None else 0
    score_idx = roles.score if roles.score is not None else len(raw) - 2
    rule_idx = roles.rule if roles.rule is not None else len(raw) - 1
    raw[name_idx] = name
    raw[score_idx] = score_text
    raw[rule_idx] = rule
    seg.raw_rows.append(raw)


__all__ = ["recover_stub_continuation"]
