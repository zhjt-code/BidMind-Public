"""招标要求确认页展示分组。

职责: 把"同分类 + 同描述"的多行招标要求(主要来自 _split_compound_requirements
 的复合拆分子面)以及与 scoring_item `equivalent` 关联的 normal 改写行
 折叠为单一展示代表项，消除人工确认页的视觉重复。
不做: 不修改/删除任何数据库行；不跨分类或跨描述合并；不改变下游检索/审查语义
 （底层子面行原样保留，确认/删除由调用方按 member_ids 整组 fan-out）。

为什么在展示层而非合并管线收口:
 复合拆分行的 description / source_pages 对用户完全相同，仅 sub_requirements /
 subject_scope 不同。这些差异是为下游分面检索与审查服务的，
 不能在持久化层合并；但对人工确认者，它们就是同一条款，应折叠为一张卡片。
"""

from __future__ import annotations

from typing import Any

from app.core.review_contracts import parse_json_list
from app.models.requirement import Requirement


def _normalize_description(description: str | None) -> str:
    """规范化描述用于分组键：去除所有空白并小写。"""
    if not description:
        return ""
    return "".join((description or "").split()).lower()


def _category_value(req: Requirement) -> str:
    """取分类的稳定字符串值（枚举或字符串均兼容）。"""
    category = req.category
    return getattr(category, "value", category)


def _group_review_relevance(members: list[Requirement]) -> str:
    """组内评审意义取"最可见"级别，避免折叠隐藏真实审查项。

    actionable > informational > procedural（任一成员为 actionable 则整组 actionable）。
    """
    levels = {getattr(m, "review_relevance", "actionable") or "actionable" for m in members}
    if "actionable" in levels:
        return "actionable"
    if "informational" in levels:
        return "informational"
    if "procedural" in levels:
        return "procedural"
    return "actionable"


def _equivalent_scoring_targets(rows: list[Requirement]) -> dict[int, Requirement]:
    """normal 行 -> 与其 `equivalent` 关联的 scoring_item 行（展示折叠）。

 关联由 scoring_reconcile 写入 normal.scoring_metadata（linked_scoring_fact_keys /
 scoring_relation_type），此前无消费者，等价双行仍并排展示。仅折叠单一 equivalent
 关联；covers/ambiguous 保持各自成组。
 """
    by_id = {row.id: row for row in rows}
    targets: dict[int, Requirement] = {}
    for row in rows:
        if getattr(row, "requirement_kind", "normal") == "scoring_item":
            continue
        meta = getattr(row, "scoring_metadata", None) or {}
        if meta.get("scoring_relation_type") != "equivalent":
            continue
        keys = meta.get("linked_scoring_fact_keys") or []
        if len(keys) != 1 or not str(keys[0]).startswith("requirement:"):
            continue
        try:
            scoring_id = int(str(keys[0]).split(":", 1)[1])
        except ValueError:
            continue
        target = by_id.get(scoring_id)
        if target is not None and getattr(target, "requirement_kind", "normal") == "scoring_item":
            targets[row.id] = target
    return targets


def group_requirements_for_display(rows: list[Requirement]) -> list[dict[str, Any]]:
    """将招标要求行按 (分类, 规范化描述) 折叠为展示代表项。

    Args:
        rows: 已按 id 排序的 Requirement ORM 行。

    Returns:
        list[dict]: 每个分组一个代表项 dict，附带:
          - member_ids: 该组全部底层行 id（升序），供调用方整组操作
          - source_pages: 全组来源页码并集（升序）
          - merged_count: 组内行数（>1 时前端展示"多处合并"提示）
          - is_mandatory: 组内任一行为否决项即为 True
          - confirmed: 组内全部行已确认才为 True
        代表项按组内最小 id 升序排列，保持与原列表稳定一致的顺序。
    """
    groups: dict[tuple[str, str], list[Requirement]] = {}
    order: list[tuple[str, str]] = []
    equivalent_target = _equivalent_scoring_targets(rows)

    for req in rows:
        anchor = equivalent_target.get(req.id, req)
        key = (_category_value(anchor), _normalize_description(anchor.description))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(req)

    items: list[dict[str, Any]] = []
    for key in order:
        members = sorted(groups[key], key=lambda r: r.id)
        # 评分表原文行(scoring_item)优先作代表项, 折叠掉与其等价的 normal 改写句
        representative = next(
            (m for m in members if getattr(m, "requirement_kind", "normal") == "scoring_item"),
            members[0],
        )

        member_ids = [m.id for m in members]

        # 组级归因(P2.1): 组折叠时取"与组级别一致且带归因"的成员归因;
        # 组为 actionable 时不展示归因
        group_relevance = _group_review_relevance(members)
        group_reason = None
        if group_relevance != "actionable":
            for member in members:
                if (
                    getattr(member, "review_relevance", None) == group_relevance
                    and getattr(member, "relevance_reason", None)
                ):
                    group_reason = member.relevance_reason
                    break

        merged_pages: list[int] = []
        seen_pages: set[int] = set()
        for member in members:
            for raw in parse_json_list(member.source_pages):
                try:
                    page = int(raw)
                except (TypeError, ValueError):
                    continue
                if page >= 1 and page not in seen_pages:
                    seen_pages.add(page)
                    merged_pages.append(page)
        merged_pages.sort()

        merged_keywords: list[str] = []
        seen_kw: set[str] = set()
        for member in members:
            for kw in parse_json_list(member.keywords):
                if not isinstance(kw, str):
                    continue
                norm = kw.strip().lower()
                if norm and norm not in seen_kw:
                    seen_kw.add(norm)
                    merged_keywords.append(kw)

        merged_sub: list[str] = []
        seen_sub: set[str] = set()
        for member in members:
            for sub in parse_json_list(member.sub_requirements):
                if not isinstance(sub, str):
                    continue
                norm = sub.strip()
                if norm and norm not in seen_sub:
                    seen_sub.add(norm)
                    merged_sub.append(sub)

        member_count = len(members)
        existing_merged = representative.merged_count or 1
        display_merged_count = max(existing_merged, member_count) if member_count > 1 else representative.merged_count

        items.append(
            {
                "id": representative.id,
                "project_id": representative.project_id,
                "document_id": representative.document_id,
                "category": _category_value(representative),
                "description": representative.description,
                "is_mandatory": any(bool(m.is_mandatory) for m in members),
                "source_page": (merged_pages[0] if merged_pages else representative.source_page),
                "source_pages": merged_pages,
                "keywords": merged_keywords,
                "sub_requirements": merged_sub,
                "merged_count": display_merged_count,
                "topic": representative.topic,
                "subject_scope": representative.subject_scope,
                "canonical_key": representative.canonical_key,
                "main_requirement_id": representative.main_requirement_id,
                "review_relevance": group_relevance,
                "relevance_reason": group_reason,
                "requirement_kind": getattr(representative, "requirement_kind", "normal"),
                "score_weight": getattr(representative, "score_weight", None),
                "is_key_review": any(bool(getattr(m, "is_key_review", 0)) for m in members),
                "scoring_metadata": getattr(representative, "scoring_metadata", None),
                "confirmed": all(bool(m.confirmed) for m in members),
                "member_ids": member_ids,
            }
        )

    return items
