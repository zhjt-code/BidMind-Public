"""写标正文对已确认约束的来源覆盖检查。"""

from __future__ import annotations

import json
from typing import Any

from app.core.writer.clause_scope_policy import (
    HIDDEN_COVERAGE_SUB_TYPES,
    is_body_coverage_counted,
)


def parse_source_clause_ids(raw: str | None) -> set[int]:
    """解析草稿 source_clause_ids，异常数据按空集合处理。"""
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()

    ids: set[int] = set()
    for item in data:
        try:
            ids.add(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _is_visible_confirmed_clause(clause: Any) -> bool:
    return is_body_coverage_counted(clause)


def _short_clause_text(content: str) -> str:
    text = " ".join((content or "").split())
    if len(text) <= 40:
        return text
    return f"{text[:40]}..."


def build_clause_coverage_issues(
    clauses: list[Any],
    drafts: list[Any],
    *,
    exclude_sub_types: set[str] | None = None,
) -> list[dict]:
    """返回未被当前草稿 source_clause_ids 覆盖的 confirmed 可见约束。

    exclude_sub_types: 由专项检查(如评分项响应)独立承接的 sub_type，从本通道排除，
                       避免同一约束在覆盖通道与专项通道双报。
    """
    excluded = exclude_sub_types or set()
    covered_ids: set[int] = set()
    for draft in drafts:
        covered_ids.update(parse_source_clause_ids(getattr(draft, "source_clause_ids", None)))

    issues: list[dict] = []
    for clause in clauses:
        clause_id = getattr(clause, "id", None)
        if clause_id is None or not _is_visible_confirmed_clause(clause):
            continue
        if getattr(clause, "sub_type", "") in excluded:
            continue
        if int(clause_id) in covered_ids:
            continue

        summary = _short_clause_text(str(getattr(clause, "content", "") or ""))
        issues.append({
            "type": "clause_coverage",
            "severity": "warning",
            "node_id": None,
            "message": f"已确认约束未被正文来源覆盖: {summary}",
            # 用户可见 detail 禁止拼接 schema 字段名(如 source_clause_ids)
            "detail": (
                f"约束编号 {clause_id} 尚未被任何章节草稿记为正文来源。"
                "请在绑定阶段确认关联章节，或在对应章节内容中补充响应。"
            ),
        })
    return issues
