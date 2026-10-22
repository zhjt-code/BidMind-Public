"""
职责: 按规则的 required_facts 判定每条串标情形在本对文件上的可核查性。
不做: 命中判定、评分、风险等级推导。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.collusion.rules.contracts import FACT_FIELDS, LedgerStatus, fact_label
from app.core.collusion.rules.facts import DocumentFacts
from app.core.collusion.rules.registry import RuleRegistry, RuleSpec


@dataclass(frozen=True)
class ScenarioEntry:
    """单条规则在一对文件上的可核查性条目。"""

    rule_id: str
    dimension: str
    status: LedgerStatus
    # 每个组用 tuple[str, ...] 表示（单事实组也包成单元素 tuple）
    missing_a: tuple[tuple[str, ...], ...]
    missing_b: tuple[tuple[str, ...], ...]
    reason: str


@dataclass(frozen=True)
class ScenarioLedger:
    """一对文件的串标情形账本（不可变快照）。"""

    entries: tuple[ScenarioEntry, ...]

    def by_status(self, status: LedgerStatus) -> tuple[ScenarioEntry, ...]:
        """按状态筛选条目。"""
        return tuple(e for e in self.entries if e.status == status)

    def counts(self) -> dict[str, int]:
        """全量各状态计数（包含 checkable 条目）。"""
        result: dict[str, int] = {"checkable": 0, "not_checkable": 0, "unimplemented": 0}
        for entry in self.entries:
            result[entry.status] = result.get(entry.status, 0) + 1
        return result

    def to_dict(self) -> dict:
        """序列化为可落库的 dict。

        entries 只收录 status != 'checkable' 的条目（控制落库体积），
        counts 仍是全量计数。
        """
        return {
            "version": 1,
            "counts": self.counts(),
            "entries": [
                {
                    "rule_id": e.rule_id,
                    "status": e.status,
                    "missing_a": [list(g) for g in e.missing_a],
                    "missing_b": [list(g) for g in e.missing_b],
                }
                for e in self.entries
                if e.status != "checkable"
            ],
        }


def _rule_has_non_collectable_fact(rule: RuleSpec) -> bool:
    """判断规则的 required_facts 中是否含 collectable=False 的事实。"""
    for side in ("a", "b"):
        for fname in rule.iter_required_fact_names(side):
            ff = FACT_FIELDS.get(fname)
            if ff is not None and not ff.collectable:
                return True
    return False


def _build_entry(
    rule: RuleSpec,
    facts_a: DocumentFacts,
    facts_b: DocumentFacts,
) -> ScenarioEntry:
    """为单条规则构建 ScenarioEntry。

    状态优先级（从高到低）：
    1. unimplemented：feasibility=='manual' 或含不可采集事实
    2. not_checkable：存在未满足的事实组
    3. checkable：全部事实组已满足
    """
    # 优先级 1：能力未上线
    if rule.feasibility == "manual" or _rule_has_non_collectable_fact(rule):
        return ScenarioEntry(
            rule_id=rule.rule_id,
            dimension=rule.dimension,
            status="unimplemented",
            missing_a=(),
            missing_b=(),
            reason="检测能力尚未上线（含不可采集字段或需人工核查）",
        )

    # 优先级 2：事实组缺失
    missing_a_groups = rule.missing_fact_groups(facts_a.present_facts, "a")
    missing_b_groups = rule.missing_fact_groups(facts_b.present_facts, "b")
    if missing_a_groups or missing_b_groups:
        return ScenarioEntry(
            rule_id=rule.rule_id,
            dimension=rule.dimension,
            status="not_checkable",
            missing_a=tuple(tuple(g) for g in missing_a_groups),
            missing_b=tuple(tuple(g) for g in missing_b_groups),
            reason="所需事实字段缺失，无法核查",
        )

    # 优先级 3：可核查
    return ScenarioEntry(
        rule_id=rule.rule_id,
        dimension=rule.dimension,
        status="checkable",
        missing_a=(),
        missing_b=(),
        reason="",
    )


def build_pair_scenario_ledger(
    registry: RuleRegistry,
    facts_a: DocumentFacts,
    facts_b: DocumentFacts,
) -> ScenarioLedger:
    """按注册表全量规则构建 pair 级情形账本。

    Args:
        registry: 规则注册表
        facts_a: 文件 A 的事实快照
        facts_b: 文件 B 的事实快照
    """
    entries = tuple(_build_entry(rule, facts_a, facts_b) for rule in registry.rules)
    return ScenarioLedger(entries=entries)


def summarize_ledger_gate(
    ledger: ScenarioLedger,
    registry: RuleRegistry,
) -> dict:
    """汇总 gate 规则的可核查性状态，供上层决定是否阻断对比流程。

    blocked=True 当且仅当存在 gate 规则状态为 not_checkable；
    unimplemented 状态不阻断（检测能力待上线，不应阻塞已有能力的审查）。

    Returns:
        {
            "blocked": bool,
            "blocked_rule_ids": list[str]（已排序），
            "missing_fact_labels": list[str]（去重保序的中文标签），
        }
    """
    gate_rule_ids: frozenset[str] = frozenset(r.rule_id for r in registry.gate_rules())
    entry_map: dict[str, ScenarioEntry] = {e.rule_id: e for e in ledger.entries}

    blocked_rule_ids: list[str] = []
    seen_labels: set[str] = set()
    ordered_labels: list[str] = []

    for rule_id in sorted(gate_rule_ids):  # 排序保证输出确定性
        entry = entry_map.get(rule_id)
        if entry is None or entry.status != "not_checkable":
            continue
        blocked_rule_ids.append(rule_id)
        # 收集缺失事实的中文标签（去重保序）
        for groups in (entry.missing_a, entry.missing_b):
            for group in groups:
                for fname in group:
                    label = fact_label(fname)
                    if label not in seen_labels:
                        seen_labels.add(label)
                        ordered_labels.append(label)

    return {
        "blocked": bool(blocked_rule_ids),
        "blocked_rule_ids": sorted(blocked_rule_ids),
        "missing_fact_labels": ordered_labels,
    }
