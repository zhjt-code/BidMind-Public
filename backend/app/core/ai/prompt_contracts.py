"""Prompt placeholder contracts.

职责: 声明高风险 LLM 节点的必填占位符集合。
不做: 不读取数据库、不发布 Prompt、不执行模板渲染。
"""

from __future__ import annotations

_REQUIRED_PLACEHOLDERS: dict[tuple[str, str], frozenset[str]] = {
    ("writing.section_plan", "user"): frozenset({
        "node_context_json",
        "chapter_profile_text",
        "industry_profile_text",
        "constraints_json",
        "scoring_points_json",
        "evidence_catalog_json",
    }),
}


def get_required_placeholders(node_key: str, role: str) -> set[str]:
    """Return required placeholders for a node/role pair."""

    return set(_REQUIRED_PLACEHOLDERS.get((node_key, role), frozenset()))


__all__ = ["get_required_placeholders"]
