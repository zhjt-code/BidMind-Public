"""约束消费覆盖诊断。

职责: 依据已确认约束 / 当前草稿 source_clause_ids / clause_to_node 绑定, 计算约束消费
      覆盖诊断——分母、已覆盖数, 以及未覆盖约束按"可行动原因"三分类
      (待人工分配 / 未匹配 / 待生成)。
不做: 不访问 DB、不做 LLM、不修改数据、不做约束分类或作用域判定(仅消费绑定既有 scope)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.writer.clause_coverage_checker import (
    parse_source_clause_ids,
)
from app.core.writer.binding_consumption_policy import status_to_str
from app.core.writer.clause_scope_policy import (
    HIDDEN_COVERAGE_SUB_TYPES,
    WRITING_RELEVANCE_CHECK_ONLY,
    WRITING_RELEVANCE_REFERENCE,
    is_body_coverage_counted,
    read_writing_relevance,
)

# 未覆盖原因分类 key(前端 constants 需与之对齐)
REASON_PENDING_ASSIGNMENT = "pending_assignment"  # 待人工分配: 仅有 orphan 无节点绑定
REASON_UNMATCHED = "unmatched"                     # 未匹配: 无任何 clause_to_node 绑定
REASON_PENDING_GENERATION = "pending_generation"   # 待生成: 已绑定章节/全局, 生成即覆盖

_SAMPLE_LIMIT = 5
_SUMMARY_MAX = 40


@dataclass
class CoverageReasonGroup:
    """单一未覆盖原因的聚合结果。"""
    count: int = 0
    samples: list[dict] = field(default_factory=list)  # [{"id", "summary"}]


@dataclass
class ClauseCoverageDiagnostics:
    """约束消费覆盖诊断结果。"""
    counted: int
    covered: int
    uncovered: int
    pending_assignment: CoverageReasonGroup
    unmatched: CoverageReasonGroup
    pending_generation: CoverageReasonGroup
    by_sub_type: dict[str, "CoverageSubTypeStats"] = field(default_factory=dict)


@dataclass
class CoverageSubTypeStats:
    """单个 sub_type 的覆盖与折叠统计。"""
    counted: int = 0
    covered: int = 0
    uncovered: int = 0
    reference: int = 0
    check_only: int = 0


def _summary(content: str) -> str:
    text = " ".join((content or "").split())
    return text if len(text) <= _SUMMARY_MAX else f"{text[:_SUMMARY_MAX]}..."


def _is_counted(clause: Any) -> bool:
    """是否计入覆盖分母(已确认 + 非旁路消费类型), 与 clause_coverage_checker 同源。"""
    return is_body_coverage_counted(clause)


def _classify_uncovered(bindings: list[Any]) -> str:
    """对单条未覆盖约束按 clause_to_node 绑定形态分类为可行动原因。

    - 绑定到具体章节, 或全局约束(生成任意章节即覆盖) -> 待生成;
    - 仅有 orphan 无节点绑定(matcher 未找到归属) -> 待人工分配;
    - 无任何 clause_to_node 绑定 -> 未匹配。
    """
    node_bound = False
    has_global = False
    has_orphan = False
    for binding in bindings:
        node_id = getattr(binding, "outline_node_id", None)
        scope = str(getattr(binding, "scope", "") or "")
        if node_id is not None:
            node_bound = True
        elif scope == "global":
            has_global = True
        elif scope == "orphan":
            has_orphan = True
    if node_bound or has_global:
        return REASON_PENDING_GENERATION
    if has_orphan:
        return REASON_PENDING_ASSIGNMENT
    return REASON_UNMATCHED


def build_clause_coverage_diagnostics(
    clauses: list[Any],
    drafts: list[Any],
    clause_node_bindings: list[Any],
) -> ClauseCoverageDiagnostics:
    """计算约束消费覆盖诊断。

    Args:
        clauses: 项目全部约束(含 id/sub_type/confirmed/content)。
        drafts: 当前版本草稿(含 source_clause_ids)。
        clause_node_bindings: clause_to_node 绑定(含 clause_id/outline_node_id/scope);
            调用方须已按 binding_type == clause_to_node 过滤。
    """
    covered_ids: set[int] = set()
    for draft in drafts:
        covered_ids.update(
            parse_source_clause_ids(getattr(draft, "source_clause_ids", None))
        )

    bindings_by_clause: dict[int, list[Any]] = {}
    ignored_orphan_ids: set[int] = set()
    for binding in clause_node_bindings:
        clause_id = getattr(binding, "clause_id", None)
        if clause_id is None:
            continue
        if (
            str(getattr(binding, "scope", "") or "") == "orphan"
            and status_to_str(getattr(binding, "status", "")) == "rejected"
        ):
            ignored_orphan_ids.add(int(clause_id))
        bindings_by_clause.setdefault(int(clause_id), []).append(binding)

    counted = [
        clause for clause in clauses
        if getattr(clause, "id", None) is not None and _is_counted(clause)
    ]

    groups = {
        REASON_PENDING_ASSIGNMENT: CoverageReasonGroup(),
        REASON_UNMATCHED: CoverageReasonGroup(),
        REASON_PENDING_GENERATION: CoverageReasonGroup(),
    }
    by_sub_type: dict[str, CoverageSubTypeStats] = {}

    for clause in clauses:
        if int(getattr(clause, "confirmed", 0) or 0) != 1:
            continue
        sub_type = str(getattr(clause, "sub_type", "") or "")
        if not sub_type or sub_type in HIDDEN_COVERAGE_SUB_TYPES:
            continue
        stats = by_sub_type.setdefault(sub_type, CoverageSubTypeStats())
        relevance = read_writing_relevance(clause)
        if relevance == WRITING_RELEVANCE_REFERENCE:
            stats.reference += 1
        elif relevance == WRITING_RELEVANCE_CHECK_ONLY:
            stats.check_only += 1
        elif getattr(clause, "id", None) is not None:
            stats.counted += 1
            if int(clause.id) in covered_ids:
                stats.covered += 1
            elif int(clause.id) in ignored_orphan_ids:
                stats.covered += 1
            else:
                stats.uncovered += 1

    covered = 0
    for clause in counted:
        clause_id = int(clause.id)
        if clause_id in covered_ids:
            covered += 1
            continue
        if clause_id in ignored_orphan_ids:
            covered += 1
            continue
        reason = _classify_uncovered(bindings_by_clause.get(clause_id, []))
        group = groups[reason]
        group.count += 1
        if len(group.samples) < _SAMPLE_LIMIT:
            group.samples.append({
                "id": clause_id,
                "summary": _summary(str(getattr(clause, "content", "") or "")),
            })

    return ClauseCoverageDiagnostics(
        counted=len(counted),
        covered=covered,
        uncovered=len(counted) - covered,
        pending_assignment=groups[REASON_PENDING_ASSIGNMENT],
        unmatched=groups[REASON_UNMATCHED],
        pending_generation=groups[REASON_PENDING_GENERATION],
        by_sub_type=by_sub_type,
    )
