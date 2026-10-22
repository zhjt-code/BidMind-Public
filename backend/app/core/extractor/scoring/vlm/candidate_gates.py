"""VLM 候选双门禁: 证据锚定(硬) + 业务闭合(软)。

职责: 采纳前校验——①候选项名称与分值必须逐字锚定于渲染页文本层
 (拦截分值虚构); ②候选事实集经 validate_scoring_fact_set 评估闭合,
 闭合结果供分级采纳, 不再单独否决整批。
 受控放宽: 跨页折行使正确全名无法在页文本连续命中时, 名称轴
 允许回退到缝合后的网格叶子清单名(确定性来源); 分值轴永不放宽。
不做: 不调用 LLM、不落库、不放宽分值锚定语义。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GateOutcome:
    """双门禁裁决结果。"""

    passed: bool
    rejection_codes: tuple[str, ...] = ()
    feedback: str = ""
    validation: Any = None
    unanchored_names: tuple[str, ...] = field(default_factory=tuple)


def _compact(text: str) -> str:
    """去空白 + NFKC 全半角归一 + 小写（审查V4: 与锚定文本层同口径）。

    PDF 文本层常见全角数字/字母/括号("（Ｆ１）""１５分"), VLM 输出为半角,
    仅去空白会系统性误拒真实项——锚定误杀是 run-to-run 方差的主源之一。
    """
    import unicodedata

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).lower()


def _score_representations(weight: float) -> list[str]:
    reps = []
    if float(weight).is_integer():
        reps.append(str(int(weight)))
        reps.append(f"{int(weight)}.0")  # 审查V4: 原文"15.0分"写法
    reps.append(f"{weight:g}")
    return list(dict.fromkeys(reps))


# 回退锚定的最短名长(compact 后), 防短名与清单碎片误配
_FALLBACK_NAME_MIN_LEN = 4


def _name_in_fallback(compact_name: str, fallback_names: tuple[str, ...]) -> bool:
    """名称轴回退源: 与缝合后网格叶子清单名 compact 双向包含。

 回退源是确定性网格而非模型输出, 不引入虚构风险; 仅名称轴可用。
 """
    if len(compact_name) < _FALLBACK_NAME_MIN_LEN:
        return False
    for fallback in fallback_names or ():
        compact_fb = _compact(fallback)
        if len(compact_fb) < _FALLBACK_NAME_MIN_LEN:
            continue
        if compact_name in compact_fb or compact_fb in compact_name:
            return True
    return False


def _name_anchored(
    name: str, anchor_text: str, fallback_names: tuple[str, ...] = ()
) -> bool:
    """名称锚定: 全名子串命中, 或复合名"父-子"各段(≥2字)分别命中,
 或命中缝合网格叶子清单名(跨页折行回退,)。"""
    compact_name = _compact(name)
    if not compact_name:
        return False
    if compact_name in anchor_text:
        return True
    segments = [seg for seg in re.split(r"[-－]", compact_name) if len(seg) >= 2]
    if segments and all(seg in anchor_text for seg in segments):
        return True
    return _name_in_fallback(compact_name, fallback_names)


# 规则原文锚定的最短长度与前缀窗口(紧凑字符)
_RULE_ANCHOR_MIN_LEN = 10
_RULE_ANCHOR_PREFIX = 40


def _composite_child_evidence_anchored(item: Any, anchor_text: str) -> bool:
    """: 合成子行名的名称轴放行证据——父段逐字锚定 + 规则原文锚定。

 提示词要求合并单元格子行按"父项名称-子行名称"命名, 子行无干净名称格时
 模型必然概括改写, 逐字锚定会拒收真实行(3479 实测缺31分即此形态)。
 放行条件(全部满足): 名称为复合形态且父段逐字命中原文; 任一规则原文
 (整条或前40紧凑字符)逐字命中原文。分值轴仍由调用方独立锚定, 全改写
 (名称+规则均未见于原文)的幻觉行照拒。
 """
    name = _compact(str(getattr(item, "score_item_name", "") or ""))
    segments = [seg for seg in re.split(r"[-－]", name) if len(seg) >= 2]
    if len(segments) < 2 or segments[0] not in anchor_text:
        return False
    rules = [
        *(getattr(item, "score_rules", None) or []),
        *(getattr(item, "deduction_rules", None) or []),
    ]
    for rule in rules:
        compact_rule = _compact(str(rule or ""))
        if len(compact_rule) < _RULE_ANCHOR_MIN_LEN:
            continue
        if (
            compact_rule in anchor_text
            or compact_rule[:_RULE_ANCHOR_PREFIX] in anchor_text
        ):
            return True
    return False


def _item_anchored(
    item: Any, anchor_text: str, fallback_names: tuple[str, ...] = ()
) -> bool:
    name = str(getattr(item, "score_item_name", "") or "")
    if not _name_anchored(name, anchor_text, fallback_names):
        # 合成子行名按"父段+规则原文"双证据放行, 防概括命名误杀
        if not _composite_child_evidence_anchored(item, anchor_text):
            return False
    weight = float(getattr(item, "score_weight", 0.0) or 0.0)
    if weight > 0 and not any(
        rep in anchor_text for rep in _score_representations(weight)
    ):
        return False
    return True


def _unanchored_reason(
    item: Any, anchor_text: str, fallback_names: tuple[str, ...] = ()
) -> str:
    name = str(getattr(item, "score_item_name", "") or "")
    if not _name_anchored(name, anchor_text, fallback_names):
        return f"{name}(名称未见于原文)"
    weight = float(getattr(item, "score_weight", 0.0) or 0.0)
    return f"{name}(分值{weight:g}未见于原文)"


def filter_anchored_items(
    items: list[Any], anchor_text: str, fallback_names: tuple[str, ...] = ()
) -> tuple[list[Any], list[str]]:
    """逐项锚定过滤: 返回(保留项, 丢弃原因列表)。空文本层视为全部丢弃。

 fallback_names: 缝合后网格叶子清单名, 仅放宽名称轴; 分值仍须
 独立锚定于页文本, 防编分值红线不动。
 """
    if not anchor_text:
        dropped = [_unanchored_reason(item, "") for item in items]
        return [], dropped
    kept: list[Any] = []
    dropped: list[str] = []
    for item in items:
        if _item_anchored(item, anchor_text, fallback_names):
            kept.append(item)
        else:
            dropped.append(_unanchored_reason(item, anchor_text, fallback_names))
    return kept, dropped


def sanitize_unanchored_group_labels(
    items: list[Any], anchor_text: str
) -> tuple[list[Any], list[str]]:
    """分组标签锚定净化: volume_label 未命中渲染页文本层即置空降级。

 职责: 剥除模型发明的分组标签(如照抄提示词示例的"技术卷"), 返回(净化项, 剥除记录)。
 不做: 不整项拒收(名称/分值轴仍由锚定门禁裁决)、不校验标签归属是否正确
 (无声明组基准时结构性不可校验)、不改动分值。

 背景: 锚定门禁只管名称/分值两轴, 分组标签此前原样透传落库——伪造组名使
 unlabeled_sum=0 驱动假绿, 并覆盖诚实留空的候选(Goodhart 逆向激励)。
 剥除后该项诚实回到"未归组", 由 verdict 非绿与人工核对接管。
 """
    if not anchor_text:
        return list(items), []
    compact_anchor = _compact(anchor_text)
    sanitized: list[Any] = []
    stripped: list[str] = []
    for item in items:
        label = str(getattr(item, "volume_label", "") or "")
        compact_label = _compact(label)
        if not compact_label or compact_label in compact_anchor:
            sanitized.append(item)
            continue
        name = str(getattr(item, "score_item_name", "") or "")
        stripped.append(f"{name}: {label}")
        try:
            sanitized.append(item.model_copy(update={"volume_label": None}))
        except (AttributeError, TypeError, ValueError):
            cloned = copy.copy(item)
            setattr(cloned, "volume_label", None)
            sanitized.append(cloned)
    return sanitized, stripped


def check_evidence_anchoring(items: list[Any], anchor_text: str) -> GateOutcome:
    """整批锚定门禁(兼容旧测试): 空文本层或过滤后为空则拒。"""
    if not anchor_text:
        return GateOutcome(
            passed=False,
            rejection_codes=("no_anchor_text",),
            feedback="评分区无可锚定文本层(疑扫描件), 无法核验证据。",
        )
    kept, dropped = filter_anchored_items(items, anchor_text)
    if dropped and not kept:
        preview = "; ".join(dropped[:8])
        return GateOutcome(
            passed=False,
            rejection_codes=("unanchored",),
            feedback=(
                f"以下{len(dropped)}项未通过原文锚定校验, 名称与分值必须逐字来自"
                f"图片原文, 禁止改写或推算: {preview}"
            ),
            unanchored_names=tuple(dropped),
        )
    if dropped:
        preview = "; ".join(dropped[:8])
        return GateOutcome(
            passed=True,
            rejection_codes=(),
            feedback=(
                f"已丢弃{len(dropped)}项未锚定条目, 保留{len(kept)}项: {preview}"
            ),
            unanchored_names=tuple(dropped),
        )
    return GateOutcome(passed=True)


def check_business_closure(
    items: list[Any],
    volume_defs: dict,
    document_id: int,
    *,
    expected_per_lot: float | None = None,
) -> GateOutcome:
    """闭合评估: 返回 validation 供分级采纳, 不再单独否决。

    expected_per_lot (审查V2): 账本高置信文档级基准。None 时 validate 回退
    默认 100——有权威基准(如150分制)的文档必须传入, 否则"漏提凑满默认100"
    会被误判 COMPLETE 静默转绿。
    """
    from app.core.extractor.scoring.scoring_facts import build_scoring_fact_set
    from app.core.extractor.scoring.scoring_scope_contracts import (
        ScoringValidationKind,
    )
    from app.core.extractor.scoring.scoring_scope_validator import (
        validate_scoring_fact_set,
    )

    fact_set = build_scoring_fact_set(items, source_id=f"document:{document_id}")
    validation = validate_scoring_fact_set(
        fact_set,
        expected_per_lot=expected_per_lot,
        declared_volume_defs=volume_defs,
    )
    if validation.kind == ScoringValidationKind.COMPLETE:
        return GateOutcome(passed=True, validation=validation)
    return GateOutcome(
        passed=True,
        rejection_codes=("not_closed",),
        feedback=(
            f"提取结果未通过分值闭合校验({validation.kind.value}): "
            f"{validation.note or ''} 请核对是否有漏提、重复计分或分组归属错误。"
        ),
        validation=validation,
    )


def evaluate_candidate_gates(
    items: list[Any],
    *,
    anchor_text: str,
    volume_defs: dict,
    document_id: int,
    expected_per_lot: float | None = None,
) -> GateOutcome:
    """锚定硬门禁(逐项过滤)先于闭合软评估。"""
    if not items:
        return GateOutcome(
            passed=False,
            rejection_codes=("empty_candidate",),
            feedback="未解析出任何评分细项。",
        )
    if not anchor_text:
        return GateOutcome(
            passed=False,
            rejection_codes=("no_anchor_text",),
            feedback="评分区无可锚定文本层(疑扫描件), 无法核验证据。",
        )
    kept, dropped = filter_anchored_items(items, anchor_text)
    if not kept:
        preview = "; ".join(dropped[:8])
        return GateOutcome(
            passed=False,
            rejection_codes=("unanchored",),
            feedback=(
                f"以下{len(dropped)}项未通过原文锚定校验, 名称与分值必须逐字来自"
                f"图片原文, 禁止改写或推算: {preview}"
            ),
            unanchored_names=tuple(dropped),
        )
    closure = check_business_closure(
        kept, volume_defs, document_id, expected_per_lot=expected_per_lot
    )
    feedback = closure.feedback
    if dropped:
        drop_preview = "; ".join(dropped[:8])
        drop_note = f"已丢弃{len(dropped)}项未锚定条目: {drop_preview}"
        feedback = f"{drop_note} {feedback}".strip() if feedback else drop_note
    return GateOutcome(
        passed=True,
        rejection_codes=closure.rejection_codes,
        feedback=feedback,
        validation=closure.validation,
        unanchored_names=tuple(dropped),
    )


__all__ = [
    "GateOutcome",
    "check_business_closure",
    "check_evidence_anchoring",
    "evaluate_candidate_gates",
    "filter_anchored_items",
    "sanitize_unanchored_group_labels",
]
