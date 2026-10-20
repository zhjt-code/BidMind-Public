"""生成图示适配策略。

职责: 根据章节身份与图类型过滤不适合进入标书正文的图示意图。
不做: 不调用 LLM、不编译 IR、不渲染 PNG、不访问数据库。
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.writer.visuals.construction_pack import (
    extra_type_terms,
    forbids_flowchart_on_layout_chapter,
    is_spatial_drawing_chapter,
    superseded_by_material,
)

# 仅匹配章节身份，禁止拿画像 rejects/expects 做子串误杀。
_TEXT_ONLY_TERMS = (
    "公司简介",
    "企业概况",
    "投标函",
    "报价",
)
_FLOWCHART_TERMS = (
    "流程",
    "工艺",
    "工序",
    "施工工艺",
    "应急",
    "验收",
    "三检",
    "实施",
    "质量",
    "安全",
    "步骤",
    "闭环",
)
_GANTT_TERMS = ("进度", "计划", "工期", "里程碑", "排期", "实施安排", "甘特")
_ORG_TERMS = ("组织架构", "组织机构", "项目经理部", "汇报关系")
_SWIMLANE_TERMS = ("甲方", "乙方", "跨部门", "职责协同", "泳道")
_ROADMAP_TERMS = ("阶段", "路线图", "建设期", "试运行", "roadmap")
_LAYERED_TERMS = ("架构", "平台层", "服务层", "数据层", "功能模块", "体系", "金字塔")


@dataclass(frozen=True)
class VisualEligibilityResult:
    """图示策略过滤结果。"""

    payload: dict[str, Any]
    patches: list[dict[str, str]]
    rejected: list[dict[str, Any]] = field(default_factory=list)


def _profile_identity(chapter_profile: dict[str, Any] | str | None) -> str:
    """只取身份描述，不读 expects/rejects。"""

    if isinstance(chapter_profile, str):
        try:
            parsed = json.loads(chapter_profile)
        except json.JSONDecodeError:
            return chapter_profile
        return _profile_identity(parsed)
    if not isinstance(chapter_profile, dict):
        return ""
    return str(chapter_profile.get("semantic_role", "") or "")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    compact = _compact(text)
    return any(term in compact for term in terms)


def _type_terms(diagram_type: str) -> tuple[str, ...]:
    if diagram_type == "gantt":
        return _GANTT_TERMS
    if diagram_type == "flowchart":
        return _FLOWCHART_TERMS
    if diagram_type == "org_chart":
        return _ORG_TERMS
    if diagram_type == "swimlane":
        return _SWIMLANE_TERMS
    if diagram_type == "roadmap":
        return _ROADMAP_TERMS
    if diagram_type == "layered":
        return _LAYERED_TERMS
    return extra_type_terms(diagram_type)


def _visual_reject_kind(
    visual: dict[str, Any],
    *,
    node_title: str,
    chapter_profile: dict[str, Any] | str | None,
    material_subtypes: set[str] | frozenset[str] | None = None,
) -> str | None:
    """返回拒绝 kind；None 表示放行。"""

    identity = " ".join([node_title or "", _profile_identity(chapter_profile)])
    if _has_any(identity, _TEXT_ONLY_TERMS):
        return "visual_policy_rejected"
    if is_spatial_drawing_chapter(identity):
        return "visual_policy_rejected"
    diagram_type = str(visual.get("diagram_type", "") or "")
    if diagram_type == "flowchart" and forbids_flowchart_on_layout_chapter(identity):
        return "visual_policy_rejected"
    match_text = " ".join([
        identity,
        str(visual.get("title", "") or ""),
        str(visual.get("purpose", "") or ""),
    ])
    if superseded_by_material(diagram_type, material_subtypes):
        return "visual_superseded_by_material"
    terms = _type_terms(diagram_type)
    if not terms:
        return "visual_policy_rejected"
    if not _has_any(match_text, terms):
        return "visual_policy_rejected"
    return None


def _is_visual_allowed(
    visual: dict[str, Any],
    *,
    node_title: str,
    chapter_profile: dict[str, Any] | str | None,
    material_subtypes: set[str] | frozenset[str] | None = None,
) -> bool:
    return _visual_reject_kind(
        visual,
        node_title=node_title,
        chapter_profile=chapter_profile,
        material_subtypes=material_subtypes,
    ) is None


def filter_visual_intents(
    plan_payload: dict[str, Any],
    *,
    node_title: str,
    chapter_profile: dict[str, Any] | str | None = None,
    material_subtypes: set[str] | frozenset[str] | None = None,
) -> VisualEligibilityResult:
    """按章节适配策略过滤 visuals, 并返回可审计补丁。"""

    payload = copy.deepcopy(plan_payload)
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    patches: list[dict[str, str]] = []
    for index, visual in enumerate(payload.get("visuals", []) or []):
        reject_kind = _visual_reject_kind(
            visual,
            node_title=node_title,
            chapter_profile=chapter_profile,
            material_subtypes=material_subtypes,
        )
        if reject_kind is None:
            kept.append(visual)
            continue
        rejected.append(visual)
        patches.append({
            "path": f"visuals[{index}]",
            "kind": reject_kind,
            "original": str(visual.get("visual_id", "")),
            "replacement": "removed",
        })
    payload["visuals"] = kept
    if patches:
        payload.setdefault("safety_patches", [])
        payload["safety_patches"].extend(patches)
    return VisualEligibilityResult(
        payload=payload, patches=patches, rejected=rejected,
    )


__all__ = ["VisualEligibilityResult", "filter_visual_intents"]
