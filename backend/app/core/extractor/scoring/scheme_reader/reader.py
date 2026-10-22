"""评分结构判读器(LLM 调用与产物核验)。

职责: 把信号快照压缩为判读输入, 调用 LLM 产出 SchemeMap, 经 Pydantic 校验与
      锚点核验后返回 SchemeMapReading; 任何失败均 fail-open(ok=False)。
不做: 不贴标(attribution 职责)、不对账(rejudge_loop 职责)、不落库。
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pydantic import ValidationError

from app.core.ai.prompts.scheme_reader import get_scheme_reader_prompt
from app.core.extractor.scoring.scheme_reader.anchor_verifier import (
    verify_scheme_map_anchors,
)
from app.core.extractor.scoring.scheme_reader.contracts import (
    SchemeMap,
    SchemeMapReading,
)

_MAX_TOKENS = 2500
_NODE_KEY = "bid_review.scheme_reader"
# 信号压缩上限: 骨架行超出后仅保留首尾(表头/合计/卷头信号已单列, 中段明细行对
# 结构判读增量有限)
_MAX_SKELETON_ROWS_IN_PROMPT = 40


async def read_scheme_map(
    signals: dict | None,
    llm_client: Any,
    *,
    feedback: str | None = None,
) -> SchemeMapReading:
    """判读评分结构; 信号缺失/调用失败/校验失败均返回 ok=False(消费方回退现行管线)。"""
    if not signals or not (signals.get("tables") or signals.get("declarations")):
        return SchemeMapReading(ok=False, diagnostics=["信号快照缺失, 判读跳过"])

    signals_block = format_signals_block(signals)
    system_msg, user_msg = get_scheme_reader_prompt(
        signals_block=signals_block, feedback=feedback
    )
    try:
        response = await _call_reader_llm(llm_client, system_msg, user_msg)
    except Exception as exc:
        logger.warning("[评分结构判读] LLM 调用失败, 回退现行管线: {}", exc)
        return SchemeMapReading(ok=False, diagnostics=[f"判读LLM调用失败: {exc}"])

    raw_map, dropped = _parse_scheme_map_lenient(_normalize_response(response))
    if raw_map is None:
        logger.warning("[评分结构判读] 产物结构校验失败(整体不可解析)")
        return SchemeMapReading(ok=False, diagnostics=["判读产物结构校验失败"])
    if dropped:
        logger.info("[评分结构判读] 逐条校验丢弃 {} 条无效产物", len(dropped))

    if not raw_map.schemes or not raw_map.assignments:
        return SchemeMapReading(
            ok=False,
            raw_map=raw_map,
            diagnostics=["判读产物为空(schemes/assignments 缺失)"] + dropped,
        )

    verification = verify_scheme_map_anchors(raw_map, signals)
    if not verification.verified_map.assignments:
        return SchemeMapReading(
            ok=False,
            raw_map=raw_map,
            rejected_assignments=verification.rejected_assignments,
            diagnostics=["全部归属锚点核验失败"] + verification.diagnostics,
        )

    reading = SchemeMapReading(
        ok=True,
        raw_map=raw_map,
        verified_map=verification.verified_map,
        effective_confidence=verification.effective_confidence,
        rejected_assignments=verification.rejected_assignments,
        diagnostics=verification.diagnostics,
        verified_volume_keys=verification.verified_volume_keys,
    )
    logger.info(
        "[评分结构判读] schemes={} assignments={}(拒收{}) confidence={}",
        len(reading.verified_map.schemes),
        len(reading.verified_map.assignments),
        reading.rejected_assignments,
        reading.effective_confidence,
    )
    return reading


async def _call_reader_llm(llm_client: Any, system_msg: str, user_msg: str) -> Any:
    from app.core.ai.prompt_registry import resolve_runtime_params

    temperature, max_tokens = resolve_runtime_params(_NODE_KEY, 0.0, _MAX_TOKENS)
    kwargs = {
        "user_message": user_msg,
        "system_message": system_msg,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "node_key": _NODE_KEY,
    }
    try:
        return await llm_client.chat_json(**kwargs)
    except TypeError:
        kwargs.pop("node_key", None)
        return await llm_client.chat_json(**kwargs)


def _normalize_response(response: Any) -> dict:
    if isinstance(response, dict):
        if "schemes" in response or "assignments" in response:
            return response
        for key in ("scheme_map", "result", "data"):
            value = response.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _parse_scheme_map_lenient(payload: dict) -> tuple[SchemeMap | None, list[str]]:
    """逐条宽松解析: 无效的单个 scheme/assignment 丢弃(记诊断), 不整体作废。

    真实失效模式(项目2250实跑): LLM 对资格评审表输出 scheme_index=null,
    整体 Pydantic 校验失败导致全部有效判读被连坐丢弃。
    """
    from app.core.extractor.scoring.scheme_reader.contracts import (
        SchemeDecl,
        SpanAssignment,
    )

    if not isinstance(payload, dict):
        return None, []
    dropped: list[str] = []
    schemes = []
    for raw in payload.get("schemes") or []:
        try:
            schemes.append(SchemeDecl(**raw) if isinstance(raw, dict) else None)
        except ValidationError:
            dropped.append(f"scheme 丢弃: {str(raw)[:60]}")
    schemes = [s for s in schemes if s is not None]
    assignments = []
    for raw in payload.get("assignments") or []:
        try:
            assignments.append(
                SpanAssignment(**raw) if isinstance(raw, dict) else None
            )
        except ValidationError:
            dropped.append(f"assignment 丢弃: {str(raw)[:60]}")
    assignments = [a for a in assignments if a is not None]
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    try:
        scheme_map = SchemeMap(
            schemes=schemes,
            assignments=assignments,
            confidence=max(0.0, min(1.0, confidence)),
            notes=str(payload.get("notes") or "")[:500],
        )
    except ValidationError:
        return None, dropped
    return scheme_map, dropped


def format_signals_block(signals: dict) -> str:
    """信号快照 → 判读输入文本块(逐字, 带锚点标注)。"""
    lines: list[str] = []
    sections = signals.get("sections") or []
    if sections:
        lines.append("### 评分区段")
        for sec in sections:
            lines.append(
                f"- {sec.get('title', '')} (第{sec.get('page_start')}-{sec.get('page_end')}页)"
            )
    declarations = signals.get("declarations") or []
    if declarations:
        lines.append("### 声明句")
        for d in declarations:
            lines.append(f"- [p{d.get('page')}] {d.get('text', '')}")
    headings = signals.get("headings") or []
    if headings:
        lines.append("### 标题样行")
        for h in headings:
            lines.append(f"- [p{h.get('page')}] {h.get('text', '')}")
    sparse_pages = [
        p.get("page") for p in (signals.get("pages") or []) if p.get("text_sparse")
    ]
    if sparse_pages:
        lines.append(f"### 文本稀疏页(疑似扫描件, 判读需降置信): {sparse_pages}")
    for table in signals.get("tables") or []:
        page = table.get("page")
        t_idx = table.get("table_index")
        lines.append(
            f"### 表 p{page}-t{t_idx} (共{table.get('row_count')}行"
            f"×{table.get('col_count')}列)"
        )
        for header in table.get("header_rows") or []:
            cells = " | ".join(c for c in (header.get("cells") or []) if c)
            lines.append(f"- 表头行 r{header.get('row_index')}: {cells}")
        for total in table.get("total_rows") or []:
            lines.append(f"- 合计行 r{total.get('row_index')}: {total.get('text', '')}")
        rows = table.get("rows") or []
        shown = rows
        omitted = 0
        if len(rows) > _MAX_SKELETON_ROWS_IN_PROMPT:
            half = _MAX_SKELETON_ROWS_IN_PROMPT // 2
            shown = rows[:half] + rows[-half:]
            omitted = len(rows) - len(shown)
        for row in shown:
            score = row.get("score") or "-"
            lines.append(f"- 行 r{row.get('row_index')}: {row.get('factor', '')} [{score}]")
        if omitted:
            lines.append(f"- (中段省略{omitted}行明细)")
    return "\n".join(lines)


__all__ = ["format_signals_block", "read_scheme_map"]
