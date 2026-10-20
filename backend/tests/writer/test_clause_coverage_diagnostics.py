"""覆盖诊断纯函数测试: 分母口径 + 未覆盖三类可行动原因分类。

风险映射: 覆盖面板由黑箱数字升级为原因分类, 分类错误会误导用户行动
(如把"未匹配"误报为"待生成")。锁定分类边界与分母口径。
"""

from types import SimpleNamespace


def _clause(clause_id, *, sub_type="content_required", confirmed=1, content="约束内容"):
    return SimpleNamespace(
        id=clause_id, sub_type=sub_type, confirmed=confirmed, content=content,
    )


def _draft(source_clause_ids):
    return SimpleNamespace(source_clause_ids=source_clause_ids)


def _binding(clause_id, *, outline_node_id=None, scope=None, status="suggested"):
    return SimpleNamespace(
        clause_id=clause_id, outline_node_id=outline_node_id, scope=scope, status=status,
    )


def test_denominator_excludes_hidden_sub_types() -> None:
    from app.core.writer.clause_coverage_diagnostics import (
        build_clause_coverage_diagnostics,
    )

    result = build_clause_coverage_diagnostics(
        clauses=[
            _clause(1, sub_type="scoring_criterion"),
            _clause(2, sub_type="submission_rule"),   # 旁路消费, 不计分母
            _clause(3, sub_type="content_required", confirmed=0),  # 未确认, 不计
        ],
        drafts=[],
        clause_node_bindings=[],
    )

    assert result.counted == 1
    assert result.covered == 0
    assert result.uncovered == 1


def test_denominator_excludes_reference_relevance() -> None:
    """: reference 约束不应被统计为未消费正文约束。"""
    from app.core.writer.clause_coverage_diagnostics import (
        build_clause_coverage_diagnostics,
    )

    reference_clause = _clause(1, sub_type="emphasis_point", content="参考提示")
    reference_clause.metadata_json = {
        "writing_relevance": "reference",
        "writing_relevance_source": "matcher",
    }

    result = build_clause_coverage_diagnostics(
        clauses=[reference_clause, _clause(2, sub_type="content_required")],
        drafts=[],
        clause_node_bindings=[],
    )

    assert result.counted == 1
    assert result.unmatched.count == 1
    assert result.unmatched.samples[0]["id"] == 2


def test_classifies_uncovered_into_three_reasons() -> None:
    from app.core.writer.clause_coverage_diagnostics import (
        build_clause_coverage_diagnostics,
    )

    result = build_clause_coverage_diagnostics(
        clauses=[
            _clause(1),  # 已覆盖
            _clause(2),  # 绑定到章节, 未生成 -> 待生成
            _clause(3),  # 仅 orphan -> 待人工分配
            _clause(4),  # 无绑定 -> 未匹配
            _clause(5),  # 仅 global 无节点 -> 待生成
        ],
        drafts=[_draft("[1]")],
        clause_node_bindings=[
            _binding(2, outline_node_id=100, scope="exclusive"),
            _binding(3, outline_node_id=None, scope="orphan"),
            _binding(5, outline_node_id=None, scope="global"),
        ],
    )

    assert result.covered == 1
    assert result.uncovered == 4
    assert result.pending_generation.count == 2   # clause 2(章节绑定) + 5(global)
    assert result.pending_assignment.count == 1   # clause 3(orphan)
    assert result.unmatched.count == 1            # clause 4(无绑定)


def test_pending_generation_precedence_over_orphan() -> None:
    """同一约束既有章节绑定又有 orphan 记录时, 应归"待生成"(章节绑定优先)。"""
    from app.core.writer.clause_coverage_diagnostics import (
        build_clause_coverage_diagnostics,
    )

    result = build_clause_coverage_diagnostics(
        clauses=[_clause(1)],
        drafts=[],
        clause_node_bindings=[
            _binding(1, outline_node_id=None, scope="orphan"),
            _binding(1, outline_node_id=200, scope="shared"),
        ],
    )

    assert result.pending_generation.count == 1
    assert result.pending_assignment.count == 0


def test_rejected_orphan_binding_is_treated_as_user_confirmed_not_consumed() -> None:
    """用户确认不消费的孤儿约束不应继续产生待分配/未匹配告警。"""
    from app.core.writer.clause_coverage_diagnostics import (
        build_clause_coverage_diagnostics,
    )

    result = build_clause_coverage_diagnostics(
        clauses=[_clause(1)],
        drafts=[],
        clause_node_bindings=[
            _binding(1, outline_node_id=None, scope="orphan", status="rejected"),
        ],
    )

    assert result.covered == 1
    assert result.uncovered == 0
    assert result.pending_assignment.count == 0
    assert result.unmatched.count == 0


def test_rejected_orphan_binding_accepts_binding_status_enum() -> None:
    """真实 ORM 返回 BindingStatus 枚举时, rejected orphan 也应视为已处理。"""
    from app.core.writer.clause_coverage_diagnostics import (
        build_clause_coverage_diagnostics,
    )
    from app.models.writing_binding import BindingStatus

    result = build_clause_coverage_diagnostics(
        clauses=[_clause(1)],
        drafts=[],
        clause_node_bindings=[
            _binding(1, outline_node_id=None, scope="orphan", status=BindingStatus.rejected),
        ],
    )

    assert result.covered == 1
    assert result.uncovered == 0
    assert result.pending_assignment.count == 0
    assert result.unmatched.count == 0


def test_samples_capped_and_carry_summary() -> None:
    from app.core.writer.clause_coverage_diagnostics import (
        build_clause_coverage_diagnostics,
    )

    clauses = [_clause(i, content=f"未匹配约束{i}") for i in range(1, 9)]
    result = build_clause_coverage_diagnostics(
        clauses=clauses, drafts=[], clause_node_bindings=[],
    )

    assert result.unmatched.count == 8
    assert len(result.unmatched.samples) == 5  # 采样上限
    assert result.unmatched.samples[0]["id"] == 1
    assert "未匹配约束1" in result.unmatched.samples[0]["summary"]


def test_by_sub_type_reports_counted_covered_and_excluded_relevance() -> None:
    from app.core.writer.clause_coverage_diagnostics import (
        build_clause_coverage_diagnostics,
    )

    reference = _clause(3, sub_type="emphasis_point", content="参考")
    reference.metadata_json = {"writing_relevance": "reference"}
    check_only = _clause(4, sub_type="disqualification", content="检查型")
    check_only.metadata_json = {"writing_relevance": "check_only"}

    result = build_clause_coverage_diagnostics(
        clauses=[
            _clause(1, sub_type="content_required"),
            _clause(2, sub_type="content_required"),
            reference,
            check_only,
        ],
        drafts=[_draft("[1]")],
        clause_node_bindings=[],
    )

    assert result.by_sub_type["content_required"].counted == 2
    assert result.by_sub_type["content_required"].covered == 1
    assert result.by_sub_type["content_required"].uncovered == 1
    assert result.by_sub_type["emphasis_point"].reference == 1
    assert result.by_sub_type["disqualification"].check_only == 1
