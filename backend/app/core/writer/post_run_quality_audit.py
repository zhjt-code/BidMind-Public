"""写标运行后质量审计。

职责: 聚合目录、草稿、绑定和一致性问题的交付风险指标。
不做: 不访问数据库、不调用 LLM、不判定业务是否最终可投。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.writer.binding_consumption_policy import status_to_str

_PLACEHOLDER_RE = re.compile(r"\[待补充[:：]?.*?\]")


def _parse_ids(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value if isinstance(item, int)]
    try:
        raw = json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    result: list[int] = []
    for item in raw:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def build_post_run_quality_audit(
    *,
    nodes: list[Any],
    drafts: list[Any],
    consistency_issues: list[dict[str, Any]],
    bindings: list[Any],
    industry_profile_clause_ids: list[int] | None = None,
) -> dict[str, int]:
    """构建运行后质量审计指标。"""
    profile_ids = set(industry_profile_clause_ids or [])
    node_ids_with_children = {
        getattr(node, "parent_id", None)
        for node in nodes
        if getattr(node, "parent_id", None) is not None
    }
    draft_node_ids = [getattr(draft, "outline_node_id", None) for draft in drafts]

    placeholder_count = 0
    drafts_without_material_sources = 0
    drafts_with_profile_lineage = 0
    needs_review_drafts = 0
    failed_drafts = 0

    for draft in drafts:
        content = str(getattr(draft, "content", "") or "")
        placeholder_count += len(_PLACEHOLDER_RE.findall(content))
        material_ids = _parse_ids(getattr(draft, "source_material_ids", None))
        if not material_ids:
            drafts_without_material_sources += 1
        clause_ids = set(_parse_ids(getattr(draft, "source_clause_ids", None)))
        if profile_ids and clause_ids & profile_ids:
            drafts_with_profile_lineage += 1
        failure_state = str(getattr(draft, "failure_state", "") or "")
        if failure_state == "needs_review" or int(getattr(draft, "review_required", 0) or 0) == 1:
            needs_review_drafts += 1
        if failure_state == "failed":
            failed_drafts += 1

    high_issues = [
        issue for issue in consistency_issues
        if isinstance(issue, dict) and issue.get("severity") == "high"
    ]
    fixed_template_high_issues = [
        issue for issue in high_issues
        if str(issue.get("type") or "").startswith("fixed_template_")
    ]
    rejected_orphan_bindings = [
        binding for binding in bindings
        if str(getattr(binding, "scope", "") or "") == "orphan"
        and status_to_str(getattr(binding, "status", "")) == "rejected"
    ]

    return {
        "current_drafts": len(drafts),
        "non_leaf_current_drafts": sum(
            1 for node_id in draft_node_ids if node_id in node_ids_with_children
        ),
        "placeholder_count": placeholder_count,
        "drafts_without_material_sources": drafts_without_material_sources,
        "high_issue_count": len(high_issues),
        "fixed_template_high_issue_count": len(fixed_template_high_issues),
        "rejected_orphan_bindings": len(rejected_orphan_bindings),
        "drafts_with_profile_lineage": drafts_with_profile_lineage,
        "needs_review_drafts": needs_review_drafts,
        "failed_drafts": failed_drafts,
    }


__all__ = ["build_post_run_quality_audit"]
