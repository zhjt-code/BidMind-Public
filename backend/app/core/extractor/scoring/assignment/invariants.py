"""P3-3 不变量校验器——对 Assignment 快照执行七项不变量校验。

职责: 接受 Assignment、CandidateFact 字典、EvidenceAtom 字典、Declaration 列表，
      输出 InvariantReport；覆盖证据覆盖/分值守恒/唯一消费/scope隔离/
      层级守恒/条件闭合/实质唯一性七项校验，汇总残差与冲突。
不做: 不执行指派算法、不调用 LLM/DB、不修改任何入参对象、不写 Requirement、
      不导入 app.api.*。
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from app.core.extractor.scoring.assignment.contracts import (
    Assignment,
    CandidateFact,
    Declaration,
    EvidenceAtom,
    InvariantCheck,
    InvariantReport,
)

# 分值守恒与条件闭合的容差（0.01 分以内视为闭合）
_TOLERANCE = Decimal("0.01")
# Decimal 精度缩放因子（与 kernel 一致，避免浮点漂移）
_SCALE = Decimal("10000")

# 七项不变量规范名称（固定顺序，供下游按名索引）
CHECK_NAMES: tuple[str, ...] = (
    "evidence_coverage",
    "score_conservation",
    "unique_consumption",
    "scope_isolation",
    "hierarchy_conservation",
    "conditional_closure",
    "substantial_uniqueness",
)

# 硬失败不变量：任一失败直接拉低到 FAILED 状态
_HARD_FAIL_CHECKS: frozenset[str] = frozenset({"evidence_coverage", "scope_isolation"})


def check_invariants(
    assignment: Assignment,
    facts_by_key: dict[str, CandidateFact],
    atoms_by_key: dict[str, EvidenceAtom],
    declarations: Sequence[Declaration],
) -> InvariantReport:
    """执行 P3-3 七项不变量校验，返回不可变 InvariantReport 快照。

    职责: 独立重校验 Assignment 的内部一致性，输出三态结论与逐项明细。
    不做: 不重跑指派算法；不调用 LLM/DB；入参均视为只读。

    参数:
        assignment:   内核产出的 Assignment 快照（不可变）
        facts_by_key: {candidate_key → CandidateFact} 全局查找表
        atoms_by_key: {physical_key → EvidenceAtom} 全局查找表
        declarations: 本批次所有 Declaration 列表（含跨 scope）
    返回:
        InvariantReport：VERIFIED/PROVISIONAL/FAILED 三态结论 + 七项明细
    """
    scope_decls = [
        d for d in declarations
        if d.lot == assignment.lot and d.scheme == assignment.scheme
    ]

    checks: tuple[InvariantCheck, ...] = (
        _check_evidence_coverage(assignment, facts_by_key, atoms_by_key),
        _check_score_conservation(assignment, facts_by_key, scope_decls),
        _check_unique_consumption(assignment, facts_by_key),
        _check_scope_isolation(assignment, facts_by_key),
        _check_hierarchy_conservation(assignment, facts_by_key),
        _check_conditional_closure(assignment, facts_by_key, scope_decls),
        _check_substantial_uniqueness(assignment),
    )

    failed_checks = [c for c in checks if not c.passed]
    has_hard_fail = any(c.check_name in _HARD_FAIL_CHECKS for c in failed_checks)

    # 继承 kernel 已标记的残差与冲突键
    residual_keys = assignment.residual_atom_keys
    conflict_keys = assignment.conflict_atom_keys

    # 状态决策：优先 hard fail，其次软失败/残差，最后 VERIFIED
    if has_hard_fail or assignment.status == "FAILED":
        status: str = "FAILED"
    elif failed_checks or residual_keys:
        status = "PROVISIONAL"
    else:
        status = "VERIFIED"

    # VERIFIED 时契约要求 violations/residuals/conflicts 均为空
    violations: tuple[str, ...]
    if status == "VERIFIED":
        violations = ()
        residual_keys = ()
        conflict_keys = ()
    else:
        violations = tuple(
            f"[{c.check_name}] {c.note}" for c in failed_checks
        )

    note = _build_note(status, failed_checks, residual_keys, conflict_keys)

    return InvariantReport(
        assignment_id=assignment.assignment_id,
        status=status,
        checks=checks,
        violations=violations,
        residual_atom_keys=residual_keys,
        conflict_atom_keys=conflict_keys,
        note=note,
    )


# ---------------------------------------------------------------------------
# 七项不变量检查函数（私有，每函数单一职责）
# ---------------------------------------------------------------------------


def _check_evidence_coverage(
    assignment: Assignment,
    facts_by_key: dict[str, CandidateFact],
    atoms_by_key: dict[str, EvidenceAtom],
) -> InvariantCheck:
    """每条已采纳事实至少有一条已知 EvidenceAtom 支撑（硬约束）。"""
    uncovered: list[str] = []
    for fk in assignment.assigned_fact_keys:
        fact = facts_by_key.get(fk)
        if fact is None:
            uncovered.append(f"fact_key={fk!r} 不在 facts_by_key 中")
            continue
        valid = [k for k in fact.evidence_atom_keys if k in atoms_by_key]
        if not valid:
            uncovered.append(
                f"fact={fk!r} 的 {len(fact.evidence_atom_keys)} 条原子键均未在 atoms_by_key 中找到"
            )

    if uncovered:
        return InvariantCheck(
            check_name="evidence_coverage",
            passed=False,
            note=f"{len(uncovered)} 条事实缺少有效物理证据: {'; '.join(uncovered[:3])}",
        )
    return InvariantCheck(check_name="evidence_coverage", passed=True)


def _check_score_conservation(
    assignment: Assignment,
    facts_by_key: dict[str, CandidateFact],
    scope_decls: list[Declaration],
) -> InvariantCheck:
    """已采纳事实分值之和须满足可信声明约束（条件化：仅声明有 declared_total 时校验）。"""
    # 优先取 deterministic 权威，其次取任意有总分的声明
    decl = next(
        (d for d in scope_decls if d.authority == "deterministic" and d.declared_total is not None),
        next((d for d in scope_decls if d.declared_total is not None), None),
    )
    if decl is None:
        return InvariantCheck(
            check_name="score_conservation",
            passed=True,
            note="无声明总分约束，跳过守恒校验",
        )

    target = _dec(decl.declared_total)
    actual = sum(
        _dec(facts_by_key[k].score_value)
        for k in assignment.assigned_fact_keys
        if k in facts_by_key
    )
    diff = abs(actual - target)

    if diff > _TOLERANCE:
        return InvariantCheck(
            check_name="score_conservation",
            passed=False,
            note=(
                f"分值不守恒: 已采纳合计={float(actual):.4f} "
                f"声明={float(target):.4f} 差值={float(diff):.4f}"
            ),
        )
    return InvariantCheck(check_name="score_conservation", passed=True)


def _check_unique_consumption(
    assignment: Assignment,
    facts_by_key: dict[str, CandidateFact],
) -> InvariantCheck:
    """每条物理证据原子最多被一条已采纳事实消费（软约束）。"""
    atom_to_facts: dict[str, list[str]] = {}
    for fk in assignment.assigned_fact_keys:
        fact = facts_by_key.get(fk)
        if fact is None:
            continue
        for ak in fact.evidence_atom_keys:
            atom_to_facts.setdefault(ak, []).append(fk)

    duplicates = {ak: fks for ak, fks in atom_to_facts.items() if len(fks) > 1}
    if duplicates:
        sample = list(duplicates.items())[:3]
        note = "; ".join(
            f"atom={ak!r} 被 {len(fks)} 条事实共同消费" for ak, fks in sample
        )
        return InvariantCheck(check_name="unique_consumption", passed=False, note=note)

    return InvariantCheck(check_name="unique_consumption", passed=True)


def _check_scope_isolation(
    assignment: Assignment,
    facts_by_key: dict[str, CandidateFact],
) -> InvariantCheck:
    """已采纳事实的 lot/scheme 必须与 Assignment scope 完全一致（硬约束）。"""
    violators: list[str] = []
    for fk in assignment.assigned_fact_keys:
        fact = facts_by_key.get(fk)
        if fact is None:
            continue
        if fact.lot != assignment.lot or fact.scheme != assignment.scheme:
            violators.append(
                f"fact={fk!r} scope=({fact.lot},{fact.scheme}) "
                f"≠ 期望=({assignment.lot},{assignment.scheme})"
            )

    if violators:
        return InvariantCheck(
            check_name="scope_isolation",
            passed=False,
            note=f"{len(violators)} 条事实 scope 越界: {'; '.join(violators[:3])}",
        )
    return InvariantCheck(check_name="scope_isolation", passed=True)


def _check_hierarchy_conservation(
    assignment: Assignment,
    facts_by_key: dict[str, CandidateFact],
) -> InvariantCheck:
    """group_total 类型事实不得与叶节点事实共享 EvidenceAtom（防父子双计，软约束）。"""
    group_atoms: set[str] = set()
    leaf_atoms: set[str] = set()

    for fk in assignment.assigned_fact_keys:
        fact = facts_by_key.get(fk)
        if fact is None:
            continue
        if fact.fact_type == "group_total":
            group_atoms.update(fact.evidence_atom_keys)
        else:
            leaf_atoms.update(fact.evidence_atom_keys)

    overlap = group_atoms & leaf_atoms
    if overlap:
        sample = sorted(overlap)[:3]
        return InvariantCheck(
            check_name="hierarchy_conservation",
            passed=False,
            note=f"{len(overlap)} 条原子键被父/子项双计: {sample}",
        )
    return InvariantCheck(check_name="hierarchy_conservation", passed=True)


def _check_conditional_closure(
    assignment: Assignment,
    facts_by_key: dict[str, CandidateFact],
    scope_decls: list[Declaration],
) -> InvariantCheck:
    """分卷闭合校验：仅当声明为 deterministic 且有 declared_by_volume 时执行（软约束）。"""
    trusted = [
        d for d in scope_decls
        if d.authority == "deterministic" and d.declared_by_volume
    ]
    if not trusted:
        return InvariantCheck(
            check_name="conditional_closure",
            passed=True,
            note="无分卷声明，跳过闭合校验",
        )

    violations: list[str] = []
    for decl in trusted:
        for vol_name, vol_score in decl.declared_by_volume:
            vol_facts = [
                facts_by_key[k]
                for k in assignment.assigned_fact_keys
                if k in facts_by_key and facts_by_key[k].volume == vol_name
            ]
            actual = sum(_dec(f.score_value) for f in vol_facts)
            target = _dec(vol_score)
            diff = abs(actual - target)
            if diff > _TOLERANCE:
                violations.append(
                    f"卷={vol_name!r} 实际={float(actual):.2f} "
                    f"声明={float(target):.2f} 差={float(diff):.2f}"
                )

    if violations:
        return InvariantCheck(
            check_name="conditional_closure",
            passed=False,
            note="; ".join(violations[:5]),
        )
    return InvariantCheck(check_name="conditional_closure", passed=True)


def _check_substantial_uniqueness(
    assignment: Assignment,
) -> InvariantCheck:
    """无实质等价第二可行解（依赖 kernel 在 conflict_atom_keys 注入的 "multiple_solutions" 标记）。"""
    if "multiple_solutions" in assignment.conflict_atom_keys:
        return InvariantCheck(
            check_name="substantial_uniqueness",
            passed=False,
            note="内核检测到多个等价可行解，指派结果不唯一",
        )
    return InvariantCheck(check_name="substantial_uniqueness", passed=True)


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _dec(value: float | None) -> Decimal:
    """安全转换为 Decimal（None → 0），与 kernel 精度保持一致。"""
    if value is None:
        return Decimal("0")
    return (
        Decimal(str(value)) * _SCALE
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / _SCALE


def _build_note(
    status: str,
    failed_checks: list[InvariantCheck],
    residual_keys: tuple[str, ...],
    conflict_keys: tuple[str, ...],
) -> str:
    """生成人可读摘要 note（供 Banner/API 展示）。"""
    if status == "VERIFIED":
        return "全部不变量通过，零残差零冲突"

    parts: list[str] = []
    if failed_checks:
        names = "、".join(c.check_name for c in failed_checks)
        parts.append(f"{len(failed_checks)} 项不变量失败（{names}）")
    if residual_keys:
        parts.append(f"残差 {len(residual_keys)} 条")
    if "multiple_solutions" in conflict_keys:
        parts.append("存在多解冲突")
    elif conflict_keys:
        parts.append(f"冲突键 {len(conflict_keys)} 条")

    return "；".join(parts) if parts else f"状态={status}"


# ---------------------------------------------------------------------------
# 公开导出
# ---------------------------------------------------------------------------

__all__ = [
    "CHECK_NAMES",
    "check_invariants",
]
