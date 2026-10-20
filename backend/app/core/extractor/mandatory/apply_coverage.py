"""
职责: 在分块提取之后、合并之前，对内存中的提取结果执行高风险原子条件召回：
 抬标否决、补提原子条件、产出未认领信号告警。
不做: 不落库、不替代分块提取、不抽取评分项、不做等价合并（补提项与普通提取项
 一起进入同一条候选门 + LLM 合并 + 评审意义收敛栈，见）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from loguru import logger

from app.core.extractor.mandatory.atomic_extract import extract_atomic_conditions
from app.core.extractor.mandatory.contracts import (
    AtomicCondition,
    CoverageReport,
    ExistingRequirementView,
    PageText,
)
from app.core.extractor.mandatory.coverage import decide_mandatory_coverage
from app.core.extractor.mandatory.signals import (
    extend_windows_with_scattered_signals,
    harvest_mandatory_signals,
    locate_high_stakes_windows,
    pages_from_chunks,
)
from app.schemas.requirement import ExtractedRequirement

_VALID_CATEGORIES = frozenset({"QUAL", "TECH", "COMM", "PERF", "STAFF", "FORMAT"})


_FAILURE_WARNING = "高风险否决/资格条款自动核验未完成，请对照原文检查否决项是否完整"


async def recall_mandatory_conditions(
    *,
    chunks: Sequence[Any],
    extracted: list[ExtractedRequirement],
    llm_client: Any = None,
    progress_callback: Callable[..., None] | None = None,
) -> tuple[list[ExtractedRequirement], CoverageReport]:
    """对分块提取结果做否决抬标与原子补提，返回 (更新后的列表, 报告)。

    召回失败不阻断提取主流程：吞掉异常并以 fail-visible 告警回报；项目取消异常照常上抛。
    """
    try:
        return await _recall_mandatory_conditions(
            chunks=chunks,
            extracted=extracted,
            llm_client=llm_client,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        from app.core.arq_runtime import ProjectCancelledError

        if isinstance(exc, ProjectCancelledError):
            raise
        logger.warning("高风险条款核验失败(忽略): {}", exc, exc_info=True)
        return extracted, CoverageReport(warnings=[_FAILURE_WARNING])


async def _recall_mandatory_conditions(
    *,
    chunks: Sequence[Any],
    extracted: list[ExtractedRequirement],
    llm_client: Any,
    progress_callback: Callable[..., None] | None,
) -> tuple[list[ExtractedRequirement], CoverageReport]:
    _emit(progress_callback, "核验否决与资格条款", current=0, total=1)
    pages: list[PageText] = pages_from_chunks(chunks)
    if not pages:
        return extracted, CoverageReport()

    signals = harvest_mandatory_signals(pages)
    windows = extend_windows_with_scattered_signals(
        locate_high_stakes_windows(pages),
        signals,
    )
    existing_views = [
        ExistingRequirementView(
            index=idx,
            requirement_id=None,
            description=item.description or "",
            is_mandatory=bool(item.is_mandatory),
            requirement_kind=str(item.requirement_kind or "normal"),
            source_pages=tuple(p for p in (item.source_pages or []) if isinstance(p, int)),
        )
        for idx, item in enumerate(extracted)
    ]
    hint_snips = [signal.snippet for signal in signals if signal.kind != "consequence_lead"]
    atomics = await extract_atomic_conditions(
        llm_client,
        pages=pages,
        windows=windows,
        hints=hint_snips[:40],
        progress_callback=progress_callback,
    )
    decision = decide_mandatory_coverage(
        pages=pages,
        windows=windows,
        signals=signals,
        existing=existing_views,
        extracted_atomics=atomics,
    )

    updated = list(extracted)
    promoted = 0
    for index in decision.promote_indices:
        item = updated[index]
        if (item.requirement_kind or "normal") != "normal" or item.is_mandatory:
            continue
        updated[index] = item.model_copy(
            update={"is_mandatory": True, "review_relevance": "actionable"}
        )
        promoted += 1

    inserted = 0
    for atomic in decision.inserts:
        candidate = _requirement_from_atomic(atomic)
        if candidate is None:
            continue
        updated.append(candidate)
        inserted += 1

    logger.info(
        "高风险条款核验 windows={} signals={} promoted={} inserted={} unclaimed={}",
        len(windows),
        len(signals),
        promoted,
        inserted,
        len(decision.unclaimed),
    )
    return updated, CoverageReport(
        promoted_count=promoted,
        inserted_count=inserted,
        unclaimed_count=len(decision.unclaimed),
        warnings=list(decision.warnings),
    )


def _requirement_from_atomic(atomic: AtomicCondition) -> ExtractedRequirement | None:
    """原子条件转为待合并的提取项；字段不合法时丢弃而不中断。"""
    category = atomic.category if atomic.category in _VALID_CATEGORIES else "TECH"
    try:
        return ExtractedRequirement(
            category=category,
            description=atomic.description,
            is_mandatory=True,
            keywords=list(atomic.keywords or []),
            source_pages=[p for p in atomic.source_pages if isinstance(p, int) and p >= 1],
            merged_count=1,
            review_relevance="actionable",
        )
    except Exception as exc:
        logger.debug("原子条件无法转为提取项(跳过): {} / {}", atomic.description[:60], exc)
        return None


def _emit(
    progress_callback: Callable[..., None] | None,
    message: str,
    current: int = 0,
    total: int = 1,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback("mandatory_coverage", current, total, message)
    except TypeError:
        progress_callback("mandatory_coverage", current, total, message, None)
    except Exception as exc:
        logger.warning("高风险核验进度回调失败: {}", exc)
