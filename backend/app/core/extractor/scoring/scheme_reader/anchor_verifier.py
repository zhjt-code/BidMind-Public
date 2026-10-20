"""SchemeMap 锚点核验器。

职责: 把判读器 LLM 的每个边界判定锚点回到信号快照做 exact-match 核验,
 拒收无法锚定的归属、越界行区间与快照中不存在的表, 并折算有效置信度。
不做: 不调用 LLM、不贴标、不做守恒裁决。

设计(教训 + 2026 调研共识): 提取值必须能回溯到原文, 否则是 claim 不是
fact——判读器的输入若损坏, 核验必然失败并走人工路径, 不会静默传导。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.extractor.scoring.scheme_reader.contracts import (
    SchemeMap,
    SignalAnchor,
    SpanAssignment,
)


@dataclass
class AnchorVerificationResult:
    """核验产物: 仅保留可锚定归属的 SchemeMap + 拒收诊断。"""

    verified_map: SchemeMap
    rejected_assignments: int = 0
    effective_confidence: float = 0.0
    diagnostics: list[str] = field(default_factory=list)
    # 卷声明锚点核验通过的 (scheme_index, volume); 无锚/失锚的卷声明不在其中
    verified_volume_keys: set[tuple[int, str]] = field(default_factory=set)


def _normalize(text: object) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _index_tables(signals: dict) -> dict[tuple[int, int], dict]:
    return {
        (int(t.get("page", 0) or 0), int(t.get("table_index", 0) or 0)): t
        for t in (signals.get("tables") or [])
    }


def _table_row_texts(table: dict) -> dict[int, str]:
    """表内可核验行文本索引: row_index -> 归一文本(表头/合计/骨架三通道合并)。"""
    texts: dict[int, str] = {}
    for header in table.get("header_rows") or []:
        texts[int(header.get("row_index", -1))] = _normalize(
            "".join(header.get("cells") or [])
        )
    for total in table.get("total_rows") or []:
        texts[int(total.get("row_index", -1))] = _normalize(total.get("text"))
    for row in table.get("rows") or []:
        texts[int(row.get("row_index", -1))] = _normalize(
            f"{row.get('factor', '')}{row.get('score', '')}"
        )
    return texts


def verify_anchor(anchor: SignalAnchor | None, signals: dict) -> bool:
    """单锚点核验: quote 归一后必须出现在其声称的信号位置。"""
    if anchor is None:
        return False
    quote = _normalize(anchor.quote)
    if not quote:
        return False

    if anchor.kind == "declaration":
        return any(
            int(d.get("page", 0) or 0) == anchor.page
            and (quote in _normalize(d.get("text")) or _normalize(d.get("text")) in quote)
            for d in (signals.get("declarations") or [])
        )
    if anchor.kind == "heading":
        return any(
            int(h.get("page", 0) or 0) == anchor.page
            and (quote in _normalize(h.get("text")) or _normalize(h.get("text")) in quote)
            for h in (signals.get("headings") or [])
        )
    # table_row: 必须指向快照中存在的表和行
    if anchor.table_index is None or anchor.row_index is None:
        return False
    table = _index_tables(signals).get((anchor.page, anchor.table_index))
    if table is None:
        return False
    row_text = _table_row_texts(table).get(anchor.row_index)
    if not row_text:
        return False
    return quote in row_text or row_text in quote


# quote 引用失败的置信度罚权(②): 位置结构(表/行区间)已硬核验通过时,
# quote 不逐字只说明 LLM 引用不精确, 不足以否定整条归属——降置信而非拒收。
_QUOTE_MISMATCH_PENALTY_WEIGHT = 0.3


def _assignment_is_valid(
    assignment: SpanAssignment,
    tables: dict[tuple[int, int], dict],
    scheme_indexes: set[int],
    signals: dict,
    diagnostics: list[str],
) -> tuple[bool, bool]:
    """返回 (结构有效, quote失败)。结构无效=硬拒收; quote失败仅降置信。"""
    key = (assignment.page, assignment.table_index)
    table = tables.get(key)
    if table is None:
        diagnostics.append(
            f"归属拒收: 表(p{assignment.page},t{assignment.table_index})不在快照中"
        )
        return False, False
    row_count = int(table.get("row_count", 0) or 0)
    if assignment.row_start > assignment.row_end or assignment.row_end >= row_count:
        diagnostics.append(
            f"归属拒收: 行区间[{assignment.row_start},{assignment.row_end}]"
            f"越界(表p{assignment.page}共{row_count}行)"
        )
        return False, False
    if assignment.scheme_index not in scheme_indexes:
        diagnostics.append(
            f"归属拒收: scheme_index={assignment.scheme_index} 未在 schemes 中声明"
        )
        return False, False
    if assignment.boundary_anchor is not None and not verify_anchor(
        assignment.boundary_anchor, signals
    ):
        diagnostics.append(
            f"归属降置信: 边界引用非逐字但位置结构有效"
            f"(p{assignment.page},t{assignment.table_index},"
            f"quote={assignment.boundary_anchor.quote[:20]})"
        )
        return True, True
    return True, False


def verify_scheme_map_anchors(
    scheme_map: SchemeMap,
    signals: dict,
) -> AnchorVerificationResult:
    """核验整份 SchemeMap: 拒收失锚归属, 折算有效置信度。

    有效置信度 = LLM 自报置信度 × 归属核验通过率; 卷声明锚点核验失败仅降半权
    (卷基准可与逐卷对账再校验, 不因单锚失败整体作废)。
    """
    diagnostics: list[str] = []
    tables = _index_tables(signals)
    scheme_indexes = {s.scheme_index for s in scheme_map.schemes}

    surviving: list[SpanAssignment] = []
    quote_failed = 0
    for a in scheme_map.assignments:
        structurally_valid, quote_mismatch = _assignment_is_valid(
            a, tables, scheme_indexes, signals, diagnostics
        )
        if not structurally_valid:
            continue
        surviving.append(a)
        if quote_mismatch:
            quote_failed += 1
    rejected = len(scheme_map.assignments) - len(surviving)

    # 卷声明锚点核验(不拒收声明本体, 只记诊断供置信折算; 通过者进入
    # verified_volume_keys 供基准注入等高影响消费准入,)
    volume_anchor_total = 0
    volume_anchor_failed = 0
    verified_volume_keys: set[tuple[int, str]] = set()
    for scheme in scheme_map.schemes:
        for vol in scheme.volumes:
            if vol.anchor is None:
                continue
            volume_anchor_total += 1
            if not verify_anchor(vol.anchor, signals):
                volume_anchor_failed += 1
                diagnostics.append(
                    f"卷声明锚点核验失败: scheme{scheme.scheme_index}/{vol.volume}"
                    f"(quote={vol.anchor.quote[:20]})"
                )
            else:
                verified_volume_keys.add((scheme.scheme_index, vol.volume))

    assignment_ratio = (
        len(surviving) / len(scheme_map.assignments) if scheme_map.assignments else 0.0
    )
    volume_penalty = (
        0.5 * (volume_anchor_failed / volume_anchor_total) if volume_anchor_total else 0.0
    )
    quote_penalty = (
        _QUOTE_MISMATCH_PENALTY_WEIGHT * (quote_failed / len(surviving))
        if surviving else 0.0
    )
    effective = max(
        0.0,
        float(scheme_map.confidence)
        * assignment_ratio
        * (1.0 - volume_penalty)
        * (1.0 - quote_penalty),
    )

    verified_map = scheme_map.model_copy(update={"assignments": surviving})
    return AnchorVerificationResult(
        verified_map=verified_map,
        rejected_assignments=rejected,
        effective_confidence=round(effective, 4),
        diagnostics=diagnostics,
        verified_volume_keys=verified_volume_keys,
    )


__all__ = ["AnchorVerificationResult", "verify_anchor", "verify_scheme_map_anchors"]
