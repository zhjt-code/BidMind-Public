"""标书编写 -- 素材真实消费追踪。

职责: 区分已绑定、已进入上下文、已真实落入正文的素材 ID 与证据。
不做: 不查询数据库、不判断素材语义相关性、不修改草稿或绑定。
"""

from __future__ import annotations

from html import escape
from typing import Any


def _dedupe_ids(values: list[int | None] | None) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        if value is None:
            continue
        try:
            material_id = int(value)
        except (TypeError, ValueError):
            continue
        if material_id in seen:
            continue
        seen.add(material_id)
        result.append(material_id)
    return result


def build_consumption_trace(
    *,
    bound_material_ids: list[int | None] | None = None,
    selected_material_ids: list[int | None] | None = None,
    consumed_material_ids: list[int | None] | None = None,
    evidence: dict[int, str] | None = None,
) -> dict[str, Any]:
    """构建素材消费追踪结构。"""
    normalized_evidence: dict[str, str] = {}
    for material_id, snippet in (evidence or {}).items():
        if snippet:
            normalized_evidence[str(int(material_id))] = str(snippet)[:500]
    return {
        "bound_material_ids": _dedupe_ids(bound_material_ids),
        "selected_material_ids": _dedupe_ids(selected_material_ids),
        "consumed_material_ids": _dedupe_ids(consumed_material_ids),
        "evidence": normalized_evidence,
    }


def select_consumed_material_ids(trace: dict[str, Any] | None) -> list[int]:
    """从 trace 中读取真实消费素材 ID。"""
    if not isinstance(trace, dict):
        return []
    raw = trace.get("consumed_material_ids")
    return _dedupe_ids(raw if isinstance(raw, list) else [])


def has_consumption_evidence(
    material_id: int,
    content: str,
    trace: dict[str, Any] | None,
) -> bool:
    """判断 material_id 是否有可回查正文消费证据。"""
    if material_id not in select_consumed_material_ids(trace):
        return False
    evidence = trace.get("evidence") if isinstance(trace, dict) else None
    if not isinstance(evidence, dict):
        return False
    snippet = str(evidence.get(str(material_id)) or "").strip()
    if not snippet:
        return False
    return snippet in (content or "") or escape(snippet) in (content or "")


__all__ = [
    "build_consumption_trace",
    "select_consumed_material_ids",
    "has_consumption_evidence",
]
