"""写标运行后质量审计纯函数测试。"""

from __future__ import annotations

from types import SimpleNamespace


def test_post_run_quality_audit_flags_delivery_risks() -> None:
    from app.core.writer.post_run_quality_audit import build_post_run_quality_audit
    from app.models.writing_binding import BindingStatus

    nodes = [
        SimpleNamespace(id=1, parent_id=None, node_type="free_writing"),
        SimpleNamespace(id=2, parent_id=1, node_type="free_writing"),
    ]
    drafts = [
        SimpleNamespace(
            outline_node_id=1,
            content="父节点草稿",
            source_clause_ids="[99]",
            source_material_ids="[]",
            failure_state="completed",
            review_required=0,
        ),
        SimpleNamespace(
            outline_node_id=2,
            content="正文 [待补充:设备型号]",
            source_clause_ids="[10]",
            source_material_ids="[]",
            failure_state="completed",
            review_required=0,
        ),
    ]
    issues = [{"type": "fixed_template_field_missing", "severity": "high"}]
    bindings = [
        SimpleNamespace(
            scope="orphan",
            status=BindingStatus.rejected,
            binding_type="clause_to_node",
        )
    ]

    audit = build_post_run_quality_audit(
        nodes=nodes,
        drafts=drafts,
        consistency_issues=issues,
        bindings=bindings,
        industry_profile_clause_ids=[99],
    )

    assert audit["current_drafts"] == 2
    assert audit["non_leaf_current_drafts"] == 1
    assert audit["placeholder_count"] == 1
    assert audit["drafts_without_material_sources"] == 2
    assert audit["high_issue_count"] == 1
    assert audit["fixed_template_high_issue_count"] == 1
    assert audit["rejected_orphan_bindings"] == 1
    assert audit["drafts_with_profile_lineage"] == 1
