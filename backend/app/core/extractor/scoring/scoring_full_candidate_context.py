"""完整候选 scope trace 与证据包——ScoringChunkScopeTrace / Bundle / Builder。

职责: 构建评分 scope 的内存 trace 和不可跨 scope 的证据包，供 L3 完整候选提取使用；
      超预算时 fail-closed 保留目标表，不截断、不装可选，diagnostics 仅记录元数据。
不做: 不调用 LLM、不访问数据库、不记录文档原文、不修改任何已有模块。
"""

from __future__ import annotations

import hashlib
import types
from dataclasses import dataclass
from typing import Any, Literal

from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk
from app.core.extractor.scoring.scoring_scope_contracts import ScopeIdentity

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_UNASSIGNED_LOT = "__unassigned__"
_SINGLE_LOT = "__single__"

# 内容信号词（用于分类 optional 证据）
_FORMULA_TERMS = frozenset({"得分=", "基准价", "评标价", "价格满分", "评分公式"})
_DECLARATION_TERMS = frozenset({"满分", "总分", "小计", "得分：", "合计"})


@dataclass(frozen=True)
class ScoringChunkScopeTrace:
    """chunk 内存 scope trace（单次提取生命周期有效，不改 DB Schema）。

    职责: 承载 chunk 物理元数据与显式 scope 归属；scheme 由调用方传入。
    不做: 不猜测 scheme，不访问数据库，不持久化。
    """

    chunk_id: int
    document_id: int
    chunk_index: int
    chunk_type: str
    scope: ScopeIdentity
    page_start: int
    page_end: int
    parent_block_id: str | None
    table_fingerprint: str | None
    source: str


def build_scoring_chunk_scope_trace(
    chunk: Any, scope: ScopeIdentity
) -> ScoringChunkScopeTrace:
    """从 chunk 与 scoring_payload 读取物理元数据，构建 ScoringChunkScopeTrace。

    scheme 由调用方显式传入；payload.lot 与目标 lot 冲突时抛 ValueError；
    无有效 chunk_id / UNASSIGNED scope / 无有效页码时拒绝。
    """
    scheme, lot = scope

    # 1. 校验 UNASSIGNED scope
    if lot == _UNASSIGNED_LOT:
        raise ValueError(
            f"scope lot 为 UNASSIGNED（{_UNASSIGNED_LOT!r}），禁止构建 trace"
        )

    # 2. 校验 chunk_id
    chunk_id = _get_int_attr(chunk, "id")
    if chunk_id <= 0:
        raise ValueError(f"chunk 缺少有效 chunk_id，实际: {chunk_id}")

    # 3. 读取 payload
    payload = payload_from_chunk(chunk)

    # 4. lot 冲突检测（payload 有明确 lot 且与目标 lot 不同，非 SINGLE）
    if payload is not None:
        payload_lot = payload.get("lot")
        if (
            payload_lot is not None
            and payload_lot != lot
            and lot != _SINGLE_LOT
        ):
            raise ValueError(
                f"payload lot {payload_lot!r} 与目标 lot {lot!r} 冲突"
            )

    # 5. 读取页范围
    if payload is not None:
        page_start = _coerce_page(payload.get("page_start")) or _coerce_page(
            getattr(chunk, "page_num", None)
        )
        page_end = _coerce_page(payload.get("page_end")) or page_start
        source = str(payload.get("source") or "payload")
        table_fp = payload.get("table_fingerprint") or None
    else:
        page_start = _coerce_page(getattr(chunk, "page_num", None))
        page_end = page_start
        source = "chunk"
        table_fp = None

    if page_start <= 0:
        raise ValueError(
            f"chunk {chunk_id} 缺少有效页码信息（page_start={page_start}）"
        )

    return ScoringChunkScopeTrace(
        chunk_id=chunk_id,
        document_id=_get_int_attr(chunk, "document_id"),
        chunk_index=_get_int_attr(chunk, "chunk_index"),
        chunk_type=str(getattr(chunk, "chunk_type", None) or "text"),
        scope=scope,
        page_start=page_start,
        page_end=page_end,
        parent_block_id=getattr(chunk, "parent_block_id", None) or None,
        table_fingerprint=table_fp,
        source=source,
    )


# ---------------------------------------------------------------------------
# ScoringScopeEvidenceUnit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringScopeEvidenceUnit:
    """单条 scope 证据单元，含元数据头与原文。

    职责: 承载 kind/元数据/原文，提供元数据头 + 原文渲染输出。
    不做: 不截断内容，不过滤原文，不访问外部资源。
    """

    kind: Literal["target_table", "score_declaration", "price_formula", "neighbor"]
    chunk_id: int
    page_start: int
    page_end: int
    parent_block_id: str | None
    table_fingerprint: str | None
    source: str
    scope: ScopeIdentity
    content: str

    def render_for_prompt(self) -> str:
        """输出元数据头与原文，元数据头包含 chunk_id/page/parent_block/source。"""
        parent = f" parent={self.parent_block_id}" if self.parent_block_id else ""
        fp = f" fp={self.table_fingerprint[:8]}" if self.table_fingerprint else ""
        header = (
            f"[chunk_id={self.chunk_id} "
            f"page={self.page_start}-{self.page_end} "
            f"kind={self.kind}"
            f"{parent}{fp} "
            f"source={self.source}]"
        )
        return f"{header}\n{self.content}"


# ---------------------------------------------------------------------------
# ScoringScopeEvidenceBundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringScopeEvidenceBundle:
    """单个 scope 证据包（不可跨 scope 复用）。

    职责: 承载经过 scope 隔离、预算控制的证据单元与结构化诊断元数据。
    不做: 不记录原文到 diagnostics，不调用 LLM，不访问数据库。
    """

    target_scope: ScopeIdentity
    units: tuple[ScoringScopeEvidenceUnit, ...]
    context_char_budget: int
    context_char_count: int
    reserved_char_count: int
    context_overflow: bool
    omitted_chunk_ids: tuple[int, ...]
    diagnostics: tuple[str, ...]  # 仅计数/字符数/chunk id，无原文

    @property
    def allowed_chunk_ids(self) -> tuple[int, ...]:
        return tuple(u.chunk_id for u in self.units)

    @property
    def allowed_pages(self) -> tuple[int, ...]:
        seen: set[int] = set()
        result: list[int] = []
        for u in self.units:
            for p in range(u.page_start, u.page_end + 1):
                if p not in seen:
                    seen.add(p)
                    result.append(p)
        return tuple(sorted(result))

    @property
    def prompt_context(self) -> str:
        return "\n\n".join(u.render_for_prompt() for u in self.units)

    @property
    def has_target_tables(self) -> bool:
        return any(u.kind == "target_table" for u in self.units)


# ---------------------------------------------------------------------------
# ScoringScopeEvidenceRequest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringScopeEvidenceRequest:
    """build_scoring_scope_evidence 输入容器（避免函数参数 >6）。

    职责: 汇聚目标 scope、chunks、traces、items、诊断与预算配置；构造即快照。
    不做: 不执行证据筛选逻辑。
    """

    target_scope: ScopeIdentity
    scoring_chunks: tuple  # chunk_type=="scoring_table"
    context_chunks: tuple  # 其他上下文 chunk
    traces: types.MappingProxyType  # chunk_id → ScoringChunkScopeTrace
    current_items: tuple  # 计入保留预算
    diagnostics: tuple   # 计入保留预算
    context_char_budget: int
    neighbor_limit: int  # 前 N + 后 N

def build_scoring_scope_evidence(
    request: ScoringScopeEvidenceRequest,
) -> ScoringScopeEvidenceBundle:
    """按 scope 隔离规则构建证据包；超预算 fail-closed 保留目标表。

    规则: 只消费有 trace 且 scope 匹配的 chunk；scoring_table 为必需证据不截断；
    必需+保留超限 → overflow、不装可选；可选按优先级整体装入；去重稳定排序。
    """
    target_scope = request.target_scope
    traces = request.traces

    # 过滤：有 trace 且 scope 匹配
    all_chunks = list(request.scoring_chunks) + list(request.context_chunks)
    valid_pairs: list[tuple[Any, ScoringChunkScopeTrace]] = []
    for chunk in all_chunks:
        cid = _get_int_attr(chunk, "id")
        trace = traces.get(cid)
        if trace is None or trace.scope != target_scope:
            continue
        valid_pairs.append((chunk, trace))

    # 去重（id + 内容哈希）并分类
    seen_ids: set[int] = set()
    seen_hashes: set[str] = set()
    required_units: list[ScoringScopeEvidenceUnit] = []
    optional_units: list[ScoringScopeEvidenceUnit] = []

    for chunk, trace in valid_pairs:
        content = str(getattr(chunk, "content", "") or "")
        if not content.strip():
            continue
        if trace.chunk_id in seen_ids:
            continue
        content_hash = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]
        if content_hash in seen_hashes:
            continue
        seen_ids.add(trace.chunk_id)
        seen_hashes.add(content_hash)

        kind = _classify_kind(chunk, trace)
        unit = ScoringScopeEvidenceUnit(
            kind=kind,
            chunk_id=trace.chunk_id,
            page_start=trace.page_start,
            page_end=trace.page_end,
            parent_block_id=trace.parent_block_id,
            table_fingerprint=trace.table_fingerprint,
            source=trace.source,
            scope=trace.scope,
            content=content,
        )
        if kind == "target_table":
            required_units.append(unit)
        else:
            optional_units.append(unit)

    reserved_char_count = len(_render_reserved(request.current_items, request.diagnostics))
    required_char_count = sum(len(u.render_for_prompt()) for u in required_units)
    context_overflow = (required_char_count + reserved_char_count) > request.context_char_budget

    if context_overflow:
        overflow_diags = (
            f"预算不足: 必需={required_char_count}字符, "
            f"保留={reserved_char_count}字符, "
            f"预算={request.context_char_budget}字符",
            f"省略可选证据单元数: {len(optional_units)}",
            f"目标表chunk_ids: {[u.chunk_id for u in required_units]}",
        )
        return ScoringScopeEvidenceBundle(
            target_scope=target_scope,
            units=tuple(required_units),
            context_char_budget=request.context_char_budget,
            context_char_count=required_char_count,
            reserved_char_count=reserved_char_count,
            context_overflow=True,
            omitted_chunk_ids=tuple(u.chunk_id for u in optional_units),
            diagnostics=overflow_diags,
        )

    available = request.context_char_budget - reserved_char_count - required_char_count
    packed_optional, omitted_ids = _pack_optional(
        optional_units, available, request.neighbor_limit
    )
    bundle_units = tuple(required_units) + tuple(packed_optional)
    context_char_count = sum(len(u.render_for_prompt()) for u in bundle_units)

    return ScoringScopeEvidenceBundle(
        target_scope=target_scope,
        units=bundle_units,
        context_char_budget=request.context_char_budget,
        context_char_count=context_char_count,
        reserved_char_count=reserved_char_count,
        context_overflow=False,
        omitted_chunk_ids=tuple(omitted_ids),
        diagnostics=(),
    )


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _classify_kind(
    chunk: Any, trace: ScoringChunkScopeTrace
) -> Literal["target_table", "score_declaration", "price_formula", "neighbor"]:
    if trace.chunk_type == "scoring_table":
        return "target_table"
    content_clean = str(getattr(chunk, "content", "") or "").replace(" ", "")
    if any(t in content_clean for t in _FORMULA_TERMS):
        return "price_formula"
    if any(t in content_clean for t in _DECLARATION_TERMS):
        return "score_declaration"
    return "neighbor"


def _pack_optional(
    optional_units: list[ScoringScopeEvidenceUnit],
    available: int,
    neighbor_limit: int,
) -> tuple[list[ScoringScopeEvidenceUnit], list[int]]:
    """按优先级装入可选证据；超预算整体跳过；neighbor 总量限制为 2*N。"""
    _PRIORITY = {"score_declaration": 0, "price_formula": 1, "neighbor": 2}
    sorted_units = sorted(
        optional_units,
        key=lambda u: (_PRIORITY.get(u.kind, 3), u.page_start, u.chunk_id),
    )
    packed: list[ScoringScopeEvidenceUnit] = []
    omitted: list[int] = []
    neighbor_count = 0
    max_neighbors = max(0, neighbor_limit) * 2  # 前 N + 后 N
    remaining = available
    for unit in sorted_units:
        if unit.kind == "neighbor" and neighbor_count >= max_neighbors:
            omitted.append(unit.chunk_id)
            continue
        rendered = unit.render_for_prompt()
        if len(rendered) <= remaining:
            packed.append(unit)
            remaining -= len(rendered)
            if unit.kind == "neighbor":
                neighbor_count += 1
        else:
            omitted.append(unit.chunk_id)
    return packed, omitted


def _render_reserved(current_items: tuple, diagnostics: tuple) -> str:
    parts: list[str] = []
    if current_items:
        parts.append(f"[当前评分项数量: {len(current_items)}]")
    for diag in diagnostics:
        parts.append(str(diag))
    return "\n".join(parts)


def _get_int_attr(obj: Any, attr: str) -> int:
    try:
        return int(getattr(obj, attr, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_page(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0

__all__ = [
    "ScoringScopeEvidenceBundle",
    "ScoringScopeEvidenceRequest",
    "ScoringScopeEvidenceUnit",
    "ScoringChunkScopeTrace",
    "build_scoring_chunk_scope_trace",
    "build_scoring_scope_evidence",
]
