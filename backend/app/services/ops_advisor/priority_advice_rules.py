"""
职责: 纯函数规则引擎, 根据用途池内各 config 的周窗可靠性指标产出调度建议
不做: 不访问 DB、不写回 priority、不生成自然语言文案
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.ai_settings import (
    PRIORITIES_SINGLETON,
    PRIORITY_BACKUP,
    PRIORITY_LAST_RESORT,
    PRIORITY_NORMAL,
    PRIORITY_PREFERRED,
    PRIORITY_SECOND,
)

# 最小样本量：低于此值标 insufficient_data
MIN_SAMPLE_ATTEMPTS = 30

# 提升门槛：最终有效率
PROMOTE_MIN_VALID_RATE = 0.95
# 降权门槛
DEMOTE_MAX_VALID_RATE = 0.85
DEMOTE_MIN_EMPTY_RATE = 0.05
DEMOTE_MIN_STRUCTURED_FAIL_RATE = 0.05


@dataclass
class ConfigReliabilityStats:
    """单一 config 在某用途池内的周窗统计。"""

    config_id: str
    model_name: str = ""
    attempts: int = 0
    valid_rate: float = 0.0
    empty_rate: float = 0.0
    structured_fail_rate: float = 0.0
    latency_p95_ms: float = 0.0
    current_priority: int = PRIORITY_NORMAL


@dataclass
class AdviceDecision:
    action: str  # promote / demote / keep / insufficient_data
    rule_id: str
    recommended_config_id: Optional[str] = None
    demote_config_id: Optional[str] = None
    proposed_priority: Optional[dict[str, int]] = None
    evidence: dict[str, Any] = field(default_factory=dict)


def _rank_key(stats: ConfigReliabilityStats) -> tuple:
    """排序：有效率高优先，其次空响应率低、结构化失败低、P95 低、样本多。"""
    return (
        -stats.valid_rate,
        stats.empty_rate,
        stats.structured_fail_rate,
        stats.latency_p95_ms,
        -stats.attempts,
        stats.config_id,
    )


def _ensure_singleton_ok(priority: dict[str, int]) -> dict[str, int]:
    """若单点档位冲突，把多余者降为 NORMAL。"""
    result = dict(priority)
    for level in PRIORITIES_SINGLETON:
        members = [cid for cid, val in result.items() if val == level]
        if len(members) <= 1:
            continue
        # 保留第一个，其余降为普通
        for cid in members[1:]:
            result[cid] = PRIORITY_NORMAL
    return result


def build_purpose_advice(
    *,
    purpose: str,
    candidates: list[ConfigReliabilityStats],
    current_priority: dict[str, int],
) -> AdviceDecision:
    """对单一用途池生成建议。

    规则优先级:
    1. 全部样本不足 -> insufficient_data
    2. 最优且达标 -> promote（设为 PREFERRED）
    3. 最差且劣化明显 -> demote（设为 BACKUP 或 LAST_RESORT）
    4. 否则 keep
    """
    evidence_base = {
        "purpose": purpose,
        "candidates": [
            {
                "config_id": c.config_id,
                "model_name": c.model_name,
                "attempts": c.attempts,
                "valid_rate": c.valid_rate,
                "empty_rate": c.empty_rate,
                "structured_fail_rate": c.structured_fail_rate,
                "latency_p95_ms": c.latency_p95_ms,
                "current_priority": c.current_priority,
            }
            for c in candidates
        ],
        "min_sample_attempts": MIN_SAMPLE_ATTEMPTS,
    }

    if not candidates:
        return AdviceDecision(
            action="insufficient_data",
            rule_id="no_candidates",
            evidence=evidence_base,
        )

    sufficient = [c for c in candidates if c.attempts >= MIN_SAMPLE_ATTEMPTS]
    if not sufficient:
        return AdviceDecision(
            action="insufficient_data",
            rule_id="all_insufficient_sample",
            evidence=evidence_base,
        )

    ranked = sorted(sufficient, key=_rank_key)
    best = ranked[0]
    worst = ranked[-1]

    # promote: 最优达标且当前不是首选
    if best.valid_rate >= PROMOTE_MIN_VALID_RATE:
        if current_priority.get(best.config_id, PRIORITY_NORMAL) != PRIORITY_PREFERRED:
            proposed = dict(current_priority)
            # 原首选降为次选或普通
            for cid, val in list(proposed.items()):
                if val == PRIORITY_PREFERRED and cid != best.config_id:
                    proposed[cid] = PRIORITY_SECOND
            proposed[best.config_id] = PRIORITY_PREFERRED
            # 确保池内所有候选都有条目
            for c in candidates:
                proposed.setdefault(c.config_id, PRIORITY_NORMAL)
            proposed = _ensure_singleton_ok(proposed)
            return AdviceDecision(
                action="promote",
                rule_id="promote_best_valid_rate",
                recommended_config_id=best.config_id,
                proposed_priority=proposed,
                evidence={
                    **evidence_base,
                    "best_config_id": best.config_id,
                    "best_valid_rate": best.valid_rate,
                    "threshold": PROMOTE_MIN_VALID_RATE,
                },
            )
        return AdviceDecision(
            action="keep",
            rule_id="best_already_preferred",
            recommended_config_id=best.config_id,
            evidence={
                **evidence_base,
                "best_config_id": best.config_id,
                "best_valid_rate": best.valid_rate,
            },
        )

    # demote: 最差劣化且不是唯一候选
    should_demote = (
        len(sufficient) >= 2
        and (
            worst.valid_rate <= DEMOTE_MAX_VALID_RATE
            or worst.empty_rate >= DEMOTE_MIN_EMPTY_RATE
            or worst.structured_fail_rate >= DEMOTE_MIN_STRUCTURED_FAIL_RATE
        )
        and worst.config_id != best.config_id
    )
    if should_demote:
        current = current_priority.get(worst.config_id, PRIORITY_NORMAL)
        if current < PRIORITY_BACKUP:  # 数值越小越优先，尚未降到备用
            proposed = dict(current_priority)
            for c in candidates:
                proposed.setdefault(c.config_id, PRIORITY_NORMAL)
            target_level = (
                PRIORITY_LAST_RESORT
                if worst.valid_rate < 0.7
                else PRIORITY_BACKUP
            )
            proposed[worst.config_id] = target_level
            proposed = _ensure_singleton_ok(proposed)
            return AdviceDecision(
                action="demote",
                rule_id="demote_worst_degraded",
                demote_config_id=worst.config_id,
                recommended_config_id=best.config_id,
                proposed_priority=proposed,
                evidence={
                    **evidence_base,
                    "worst_config_id": worst.config_id,
                    "worst_valid_rate": worst.valid_rate,
                    "worst_empty_rate": worst.empty_rate,
                    "worst_structured_fail_rate": worst.structured_fail_rate,
                    "target_priority": target_level,
                },
            )

    return AdviceDecision(
        action="keep",
        rule_id="no_action_needed",
        recommended_config_id=best.config_id,
        evidence={
            **evidence_base,
            "best_config_id": best.config_id,
            "best_valid_rate": best.valid_rate,
        },
    )


def build_node_readonly_note(
    *,
    node_key: str,
    candidates: list[ConfigReliabilityStats],
) -> AdviceDecision:
    """节点级只出建议、不可写回。"""
    evidence = {
        "node_key": node_key,
        "writable": False,
        "candidates": [
            {
                "config_id": c.config_id,
                "model_name": c.model_name,
                "attempts": c.attempts,
                "valid_rate": c.valid_rate,
                "latency_p95_ms": c.latency_p95_ms,
            }
            for c in candidates
        ],
    }
    sufficient = [c for c in candidates if c.attempts >= MIN_SAMPLE_ATTEMPTS]
    if not sufficient:
        return AdviceDecision(
            action="insufficient_data",
            rule_id="node_insufficient_sample",
            evidence=evidence,
        )
    best = sorted(sufficient, key=_rank_key)[0]
    return AdviceDecision(
        action="keep",
        rule_id="node_readonly_best",
        recommended_config_id=best.config_id,
        evidence={**evidence, "best_config_id": best.config_id},
    )
