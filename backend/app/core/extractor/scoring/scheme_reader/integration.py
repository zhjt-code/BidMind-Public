"""SchemeReader 与评分提取管线的集成薄层。

职责: 按 SCORING_SCHEME_READER_MODE 读取信号快照、调用判读器、对最终评分项
      执行 shadow 分歧观察或 enforce 贴标, 返回判读上下文供对账/重判消费。
不做: 不解析表格、不做守恒裁决(scope_validator 职责)、不重判(rejudge_loop 职责)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.core.config import settings
from app.core.extractor.scoring.scheme_reader.attribution import (
    AttributionOutcome,
    stamp_scheme_attribution,
)
from app.core.extractor.scoring.scheme_reader.contracts import SchemeMapReading
from app.core.extractor.scoring.scheme_reader.reader import read_scheme_map

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"


@dataclass
class SchemeReaderContext:
    """判读上下文(供逐卷对账与重判环复用, 避免重复调用判读器)。"""

    mode: str
    signals: dict | None = None
    reading: SchemeMapReading | None = None
    attribution: AttributionOutcome | None = None

    @property
    def usable(self) -> bool:
        return bool(self.reading and self.reading.ok and self.reading.verified_map)


def resolve_scheme_reader_mode() -> str:
    mode = str(getattr(settings, "SCORING_SCHEME_READER_MODE", MODE_SHADOW) or "").lower()
    if mode not in (MODE_OFF, MODE_SHADOW, MODE_ENFORCE):
        return MODE_SHADOW
    return mode


async def load_scoring_signals(db: Any, document_id: int) -> dict | None:
    """读取 documents.scoring_signals(阶段0产物); 缺失返回 None。"""
    from app.models.document import Document

    try:
        row = await db.execute(
            select(Document.scoring_signals).where(Document.id == document_id)
        )
        signals = row.scalar_one_or_none()
    except Exception as exc:
        logger.warning("[评分结构判读] 信号快照读取失败(document_id={}): {}", document_id, exc)
        return None
    return signals if isinstance(signals, dict) else None


async def apply_scheme_reader_attribution(
    *,
    db: Any,
    document_id: int,
    llm_client: Any,
    items: list[Any],
) -> SchemeReaderContext:
    """判读 + 贴标单入口(scoring_extractor 薄委托点)。

    off: 直接返回空上下文; shadow: 判读并记录分歧不改值; enforce: 达信度阈值才贴标。
    任何失败 fail-open, 不影响现行管线产物。
    """
    mode = resolve_scheme_reader_mode()
    context = SchemeReaderContext(mode=mode)
    if mode == MODE_OFF or not items or db is None:
        return context

    context.signals = await load_scoring_signals(db, document_id)
    if not context.signals:
        return context

    context.reading = await read_scheme_map(context.signals, llm_client)
    if not context.usable:
        logger.info(
            "[评分结构判读] 不可用(document_id={}): {}",
            document_id,
            "; ".join((context.reading.diagnostics if context.reading else [])[:3]),
        )
        return context

    min_confidence = float(
        getattr(settings, "SCORING_SCHEME_READER_MIN_CONFIDENCE", 0.6) or 0.6
    )
    enforce = (
        mode == MODE_ENFORCE
        and context.reading.effective_confidence >= min_confidence
    )
    if mode == MODE_ENFORCE and not enforce:
        logger.warning(
            "[评分结构判读] enforce 降级为观察: 置信度 {} < 阈值 {} (document_id={})",
            context.reading.effective_confidence,
            min_confidence,
            document_id,
        )
    context.attribution = stamp_scheme_attribution(
        items,
        context.reading.verified_map,
        context.signals,
        enforce=enforce,
    )
    return context


async def attribute_with_judged_basis(
    *,
    db: Any,
    document_id: int,
    llm_client: Any,
    items: list[Any],
    volume_defs: dict[str, Any],
    scope_expectations: dict | None,
    inject_basis: bool,
) -> tuple[SchemeReaderContext, dict | None, bool, list[str]]:
    """判读贴标 + 判读基准合并的组合入口(rescue tail 单次薄委托,)。

 inject_basis=False 时只贴标不注入基准(L3 off 模式零差异约束)。
 返回 (判读上下文, 生效 scope_expectations, 是否需复验, 诊断列表)。
 """
    from app.core.extractor.scoring.scheme_reader.basis import merge_judged_basis

    diags: list[str] = []
    context = await apply_scheme_reader_attribution(
        db=db, document_id=document_id, llm_client=llm_client, items=items,
    )
    stamped = bool(context.attribution and context.attribution.stamped)
    if stamped:
        diags.append(
            f"评分结构判读贴标: 改标{context.attribution.stamped}项"
            f"(置信度{context.reading.effective_confidence})"
        )
    basis_changed = False
    if inject_basis:
        scope_expectations, basis_changed, basis_note = merge_judged_basis(
            context=context, volume_defs=volume_defs,
            scope_expectations=scope_expectations,
        )
        if basis_note:
            diags.append(basis_note)
    return context, scope_expectations, stamped or basis_changed, diags


__all__ = [
    "MODE_ENFORCE",
    "MODE_OFF",
    "MODE_SHADOW",
    "SchemeReaderContext",
    "apply_scheme_reader_attribution",
    "attribute_with_judged_basis",
    "load_scoring_signals",
    "resolve_scheme_reader_mode",
]
