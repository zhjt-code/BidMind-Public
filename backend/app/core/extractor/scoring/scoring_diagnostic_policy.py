"""评分结构丢失诊断策略。

职责: 定义稳定结构丢失信号常量与 needs_review 强制门禁函数；
 将"哪类诊断触发强制 needs_review"的判定收口到单处，
 防止各消费方（score_totals / scoring_scheme_totals 等）硬编码信号字符串。
不做: 不做分值校验、不读取 ORM 实体、不访问 API 层、不构建 payload。
"""

from __future__ import annotations

# 稳定信号: 物理表装配层部分段验收失败，评分表可能存在结构丢失，需人工对照原文核对。
# 生产来源: scoring_segment_acceptance.build_partial_rejection_diagnostics
SIGNAL_PARTIAL_REJECTION = "物理表部分段验收失败"

# 触发强制 needs_review 的稳定结构丢失信号集合。
# 维护规则: 仅收录"明确结构丢失"信号，普通数值诊断（如分值缺失/权重偏差）不得加入此集合。
_STRUCTURAL_LOSS_SIGNALS: frozenset[str] = frozenset({
    SIGNAL_PARTIAL_REJECTION,
})


def diagnostics_require_review(extra_diagnostics: list[str] | None) -> bool:
    """当 extra_diagnostics 含明确结构丢失信号时返回 True，否则返回 False。

    True → 调用方应强制 needs_review=True，使用中性 note，并 merge diagnostics。
    False → 不干预既有数值校验结论（保持现有降噪语义：普通诊断在通过时不显示）。

    参数:
        extra_diagnostics: 来自上游（如装配层）的结构化诊断串列表，可为 None。
    返回:
        bool: 是否包含至少一条结构丢失信号。
    """
    if not extra_diagnostics:
        return False
    return any(
        signal in diag
        for diag in extra_diagnostics
        for signal in _STRUCTURAL_LOSS_SIGNALS
    )


__all__ = [
    "SIGNAL_PARTIAL_REJECTION",
    "diagnostics_require_review",
]
