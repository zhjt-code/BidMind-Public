"""标书素材匹配 -- 语义命题构建。

职责: 把素材标签(metadata_tags/resolved_label)与章节画像(chapter_profile)组装为
 可比对的完整语义命题(proposition_text/demand_text)，供类型门禁与语义召回消费。
不做: 不打分、不召回、不调用 LLM、不查询数据库、不做过滤决策。

设计要点:
- 旧路径以 chunk 级最大余弦近邻直接绑定，通用证明极易误命中。新管线的比对基本单位
 是"完整语义命题"(业务类型+用途+领域+实体)，此模块只负责把既有字段拼装为该命题，
 不引入新的 LLM 打标。
- profile_complete 标记画像是否足以支撑"语义单路放行"；不完整画像必须依赖类型或
 标题旁证，防止仅凭一路相似度形成业务绑定。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.core.writer.material_matching.contracts import (
    ChapterDemandProfile,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.material_taxonomy import business_type_for_label
from app.core.writer.material_titles import get_effective_material_title


def _load_json_dict(text: Optional[str]) -> dict:
    """安全解析 JSON 文本为 dict，失败返回空 dict。"""
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _enum_or_str(value: Any) -> str:
    """兼容枚举与字符串，返回其字符串值。"""
    if value is None:
        return ""
    return value.value if hasattr(value, "value") else str(value)


def _as_str_list(value: Any, *, limit: Optional[int] = None) -> list[str]:
    """把任意值规整为去空白后的字符串列表。"""
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return items[:limit] if limit else items


def _is_image_label_verified(material: Any) -> bool:
    """图片素材仅在 label_conflict=='matched' 时视为已校验；非图片默认已校验。"""
    material_type = _enum_or_str(getattr(material, "material_type", ""))
    if material_type != "image":
        return True
    return str(getattr(material, "label_conflict", "") or "") == "matched"


def _consensus_business_type_from_labels(material: Any) -> Optional[str]:
    """细标签未决时，保留多来源一致的业务大类共识。"""
    label_sources = (
        getattr(material, "file_name_label", None),
        getattr(material, "content_label", None),
        getattr(material, "structure_hint_label", None),
    )
    business_types = {
        biz_type.value
        for label in label_sources
        if (biz_type := business_type_for_label(label)) is not None
    }
    return next(iter(business_types)) if len(business_types) == 1 else None


def _build_material_proposition_text(
    *,
    title: str,
    label: str,
    bid_purpose: str,
    domain: str,
    entities: list[str],
) -> str:
    """把素材关键语义拼装为供向量编码的完整命题文本。"""
    parts: list[str] = []
    if title:
        parts.append(f"素材：{title}")
    if label:
        parts.append(f"业务类型：{label}")
    if bid_purpose:
        parts.append(f"用途：{bid_purpose}")
    if domain:
        parts.append(f"领域：{domain}")
    if entities:
        parts.append(f"关键实体：{'、'.join(entities)}")
    return "。".join(parts)


def build_material_profiles(materials: list) -> list[MaterialSemanticProfile]:
    """为每个素材构建语义命题画像。

    Args:
        materials: 已加载并完成前置过滤(排除 split_parent/ignored)的素材 ORM 列表。

    Returns:
        与输入等长的语义命题画像列表(保持顺序)。
    """
    profiles: list[MaterialSemanticProfile] = []
    for material in materials:
        tags = _load_json_dict(getattr(material, "metadata_tags", None))
        resolved_label = getattr(material, "resolved_label", None) or None
        biz_type_enum = business_type_for_label(resolved_label)
        business_type = (
            biz_type_enum.value
            if biz_type_enum is not None
            else _consensus_business_type_from_labels(material)
        )

        domain = str(tags.get("domain", "") or "").strip()
        bid_purpose = str(tags.get("bid_purpose", "") or "").strip()
        entities = _as_str_list(tags.get("entities"), limit=10)
        applicable = _as_str_list(tags.get("applicable_sections"))
        inapplicable = _as_str_list(tags.get("inapplicable_sections"))
        exclusive = tags.get("exclusive_section") or None
        title = get_effective_material_title(material)

        label_for_text = resolved_label or (business_type or "")
        proposition_text = _build_material_proposition_text(
            title=title,
            label=label_for_text,
            bid_purpose=bid_purpose,
            domain=domain,
            entities=entities,
        )
        profile_complete = bool(resolved_label or business_type) and bool(
            bid_purpose or domain or applicable
        )

        profiles.append(
            MaterialSemanticProfile(
                material_id=material.id,
                title=title,
                material_type=_enum_or_str(getattr(material, "material_type", "")),
                resolved_label=resolved_label,
                business_type=business_type,
                domain=domain,
                bid_purpose=bid_purpose,
                entities=entities,
                applicable_sections=applicable,
                inapplicable_sections=inapplicable,
                exclusive_section=str(exclusive).strip() if exclusive else None,
                label_verified=_is_image_label_verified(material),
                proposition_text=proposition_text,
                profile_complete=profile_complete,
            )
        )
    return profiles


def _build_chapter_demand_text(
    *,
    title: str,
    semantic_role: str,
    expects: list[str],
    rejects: list[str],
) -> str:
    """把章节需求拼装为供向量编码的需求文本。"""
    parts: list[str] = []
    if title:
        parts.append(f"章节：{title}")
    if semantic_role:
        parts.append(f"功能定位：{semantic_role}")
    if expects:
        parts.append(f"期望内容：{'、'.join(expects)}")
    if rejects:
        parts.append(f"排除内容：{'、'.join(rejects)}")
    return "。".join(parts)


def _collect_leaf_nodes(nodes: list) -> list:
    """筛选叶子节点(不作为任何节点父节点的节点)。"""
    parent_ids = {
        getattr(node, "parent_id", None)
        for node in nodes
        if getattr(node, "parent_id", None) is not None
    }
    return [node for node in nodes if node.id not in parent_ids]


def build_chapter_demand_profiles(
    nodes: list,
    *,
    leaf_only: bool = True,
) -> list[ChapterDemandProfile]:
    """为叶子章节构建需求命题画像。

 Args:
 nodes: 项目全部目录节点 ORM 列表(需含父子关系以判定叶子)。
 leaf_only: 仅生成叶子章节画像(默认 True，与匹配阶段章节口径一致)。

 Returns:
 章节需求命题画像列表。: profile_complete 口径收敛到 quality SSOT(matchable)；
 degraded 章节脱离标题-only 静默（demand_text 置空，供下游旁证门禁）。
 """
    from app.core.writer.chapter_profiling.quality import (
        assess_profile_data,
        is_matchable,
    )

    target_nodes = _collect_leaf_nodes(nodes) if leaf_only else list(nodes)
    profiles: list[ChapterDemandProfile] = []
    for node in target_nodes:
        profile_json = _load_json_dict(getattr(node, "chapter_profile", None))
        semantic_role = str(profile_json.get("semantic_role", "") or "").strip()
        expects = _as_str_list(profile_json.get("expects"))
        rejects = _as_str_list(profile_json.get("rejects"))
        title = str(getattr(node, "title", "") or "").strip()
        quality = assess_profile_data(profile_json)
        complete = is_matchable(quality)

        # 宁缺毋滥: degraded 不注入标题-only demand_text，避免静默伪匹配
        if complete:
            demand_text = _build_chapter_demand_text(
                title=title,
                semantic_role=semantic_role,
                expects=expects,
                rejects=rejects,
            )
        else:
            demand_text = ""
            semantic_role = ""
            expects = []
            rejects = []

        profiles.append(
            ChapterDemandProfile(
                node_id=node.id,
                title=title,
                node_type=_enum_or_str(getattr(node, "node_type", "")),
                volume_type=(getattr(node, "volume_type", None) or None),
                semantic_role=semantic_role,
                expects=expects,
                rejects=rejects,
                demand_text=demand_text,
                profile_complete=complete,
            )
        )
    return profiles


__all__ = [
    "build_material_profiles",
    "build_chapter_demand_profiles",
]
