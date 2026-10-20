"""结构重判环(SchemeReader 阶段3)。

职责: 对账不符时把逐卷差异明细作为反馈重调判读器(封顶2轮), 每轮贴标后重建
 事实集复验; 仍不符则产出结构化警告(人工确认页 payload)。
不做: 不做初始判读(integration 职责)、不改分值、不调 L2/L3 救援、不落库。

设计(教训): 重判反馈必须是具体差异("commercial 0/32 零覆盖; technical
85/53 盈余32"), 不是笼统"重试"——显式错误反馈是修复环收敛的前提(2026 调研共识)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.core.extractor.scoring.scheme_reader.attribution import (
    stamp_scheme_attribution,
)
from app.core.extractor.scoring.scheme_reader.integration import SchemeReaderContext
from app.core.extractor.scoring.scheme_reader.reader import read_scheme_map
from app.core.extractor.scoring.scoring_scope_contracts import (
    ScoringScopeValidation,
    ScoringValidationKind,
)

MAX_REJUDGE_ROUNDS = 2

_VOLUME_LABELS = {
    "technical": "技术卷",
    "commercial": "商务卷",
    "price": "价格卷",
    "qualification": "资信卷",
    "other": "其他卷",
    "unspecified": "未指明卷",
}


@dataclass
class RejudgeOutcome:
    """重判环产物。

    resolved=True: 重判贴标后对账闭合, items 已就地更新;
    resolved=False: items 保持进入时状态, warning 携带人工确认页结构化警告。
    """

    resolved: bool
    rounds: int = 0
    validation: ScoringScopeValidation | None = None
    warning: dict | None = None
    diagnostics: list[str] = field(default_factory=list)


def format_delta_feedback(validation: ScoringScopeValidation) -> str:
    """逐卷差异 → 重判反馈/人工警告文本(确定性, 不依赖 LLM)。"""
    lines: list[str] = []
    for delta in validation.scope_deltas:
        direction = delta.direction
        if direction in ("balanced", "unknown"):
            continue
        scope_label = "/".join(p for p in delta.scope if p and p != "__single__")
        vol_label = _VOLUME_LABELS.get(delta.volume, delta.volume) if delta.volume else "总集"
        if direction == "surplus":
            desc = f"盈余{abs(delta.delta):g}分(疑似归属错误或多方案混算)"
        elif delta.actual == 0:
            desc = f"零覆盖(缺{delta.delta:g}分, 疑似整卷被错标到其他卷)"
        else:
            desc = f"缺口{delta.delta:g}分"
        lines.append(
            f"- [{scope_label}] {vol_label}: 实际{delta.actual:g}/基准{delta.expected:g}, {desc}"
        )
    return "\n".join(lines)


def build_structure_warning(
    validation: ScoringScopeValidation,
    *,
    rounds: int,
    document_id: int,
) -> dict:
    """人工确认页结构化警告 payload(前端复用现有横幅, 补逐卷差异文本)。"""
    return {
        "type": "scoring_structure_warning",
        "document_id": document_id,
        "rejudge_rounds": rounds,
        "delta_text": format_delta_feedback(validation),
        "note": (
            "评分结构存在盈余/零覆盖形态, 自动重判未能闭合, "
            "请人工核对评分表的卷归属与方案划分"
        ),
    }


async def rejudge_scheme_map(
    *,
    context: SchemeReaderContext,
    items: list[Any],
    validation: ScoringScopeValidation,
    volume_defs: dict,
    scope_expectations: dict | None,
    document_id: int,
    llm_client: Any,
    max_rounds: int = MAX_REJUDGE_ROUNDS,
) -> RejudgeOutcome:
    """带差异反馈的结构重判环: 重判→贴标→复验, 闭合即止, 封顶 max_rounds 轮。

    仅在 enforce 模式调用(shadow 观察由 attribution 分歧日志承担); items 就地改标,
    未闭合时恢复进入前的归属快照(原子性)。
    """
    from app.core.extractor.scoring.scoring_facts import build_scoring_fact_set
    from app.core.extractor.scoring.scoring_scope_validator import (
        validate_scoring_fact_set,
    )

    outcome = RejudgeOutcome(resolved=False, validation=validation)
    if not context.signals:
        outcome.diagnostics.append("重判跳过: 无信号快照")
        outcome.warning = build_structure_warning(
            validation, rounds=0, document_id=document_id
        )
        return outcome

    snapshot = [
        (getattr(i, "volume", None), getattr(i, "volume_label", None), getattr(i, "scheme", None))
        for i in items
    ]
    current_val = validation
    for round_no in range(1, max_rounds + 1):
        feedback = format_delta_feedback(current_val) or current_val.note
        reading = await read_scheme_map(context.signals, llm_client, feedback=feedback)
        outcome.rounds = round_no
        if not (reading.ok and reading.verified_map):
            outcome.diagnostics.append(
                f"重判第{round_no}轮判读不可用: " + "; ".join(reading.diagnostics[:2])
            )
            break
        stamp_scheme_attribution(
            items, reading.verified_map, context.signals, enforce=True
        )
        fact_set = build_scoring_fact_set(items, source_id=f"document:{document_id}")
        current_val = validate_scoring_fact_set(
            fact_set,
            declared_volume_defs=volume_defs or None,
            declared_scope_expectations=scope_expectations or None,
        )
        outcome.validation = current_val
        if current_val.kind == ScoringValidationKind.COMPLETE:
            outcome.resolved = True
            outcome.diagnostics.append(f"结构重判第{round_no}轮后对账闭合")
            logger.info(
                "[结构重判] document_id={} 第{}轮闭合", document_id, round_no
            )
            return outcome
        outcome.diagnostics.append(
            f"重判第{round_no}轮后仍不符: {current_val.note[:80]}"
        )

    # 未闭合: 恢复归属快照(原子性), 产出人工警告
    for item, (vol, vol_label, scheme) in zip(items, snapshot):
        try:
            item.volume = vol
            item.volume_label = vol_label
            item.scheme = scheme
        except (AttributeError, ValueError):
            pass
    outcome.validation = validation
    outcome.warning = build_structure_warning(
        validation, rounds=outcome.rounds, document_id=document_id
    )
    logger.warning(
        "[结构重判] document_id={} {}轮未闭合, 升级人工确认", document_id, outcome.rounds
    )
    return outcome


@dataclass
class StructuralRouteOutcome:
    """结构失真通路裁决摘要(供 rescue tail 组装最终产物)。"""

    resolved: bool
    validation: ScoringScopeValidation | None = None
    warning: dict | None = None
    diagnostics: list[str] = field(default_factory=list)


async def route_structural_distortion(
    *,
    scheme_context: SchemeReaderContext | None,
    items: list[Any],
    validation: ScoringScopeValidation,
    volume_defs: dict,
    scope_expectations: dict | None,
    document_id: int,
    llm_client: Any,
) -> StructuralRouteOutcome:
    """结构失真独立通路: 差异上横幅 → (enforce)重判环 → 未闭合升级人工警告。

 触发依据是 scope_deltas 盈余方向(纯确定性), 与 failed_scopes/expected_by_scope
 解耦(红色清单强制约束)。shadow/off 模式不重判, 直接产出确定性警告。
 """
    from app.core.extractor.scoring.scheme_reader.integration import MODE_ENFORCE

    outcome = StructuralRouteOutcome(resolved=False, validation=validation)
    delta_text = format_delta_feedback(validation)
    outcome.diagnostics.append(
        "评分结构性失真(双向对账): "
        + (delta_text.replace("\n", "；") if delta_text else validation.note)
    )

    if (
        scheme_context is not None
        and scheme_context.usable
        and scheme_context.mode == MODE_ENFORCE
    ):
        rejudge = await rejudge_scheme_map(
            context=scheme_context, items=items, validation=validation,
            volume_defs=volume_defs, scope_expectations=scope_expectations,
            document_id=document_id, llm_client=llm_client,
        )
        outcome.diagnostics.extend(rejudge.diagnostics)
        if rejudge.resolved:
            outcome.resolved = True
            outcome.validation = rejudge.validation
            return outcome
        outcome.warning = rejudge.warning

    if outcome.warning is None:
        outcome.warning = build_structure_warning(
            validation, rounds=0, document_id=document_id
        )
    outcome.diagnostics.append(str(outcome.warning.get("note", "")))
    return outcome


__all__ = [
    "MAX_REJUDGE_ROUNDS",
    "RejudgeOutcome",
    "StructuralRouteOutcome",
    "build_structure_warning",
    "format_delta_feedback",
    "rejudge_scheme_map",
    "route_structural_distortion",
]
