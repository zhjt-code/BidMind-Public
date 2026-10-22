"""SectionComposer 素材上下文构建子模块（Task 6 Step 3）

本模块承载 25+ 个素材上下文方法 + 3 套常量：

常量（保留与主模块同名，便于 ``SectionComposer.X`` ClassVar re-export）：
- ``LONG_TEXT_SPECIALIZATION_RULES`` — 高价值文本场景规则
- ``DEFAULT_*`` / ``HIGH_VALUE_TEXT_*`` — 摘要/截断长度配置

方法分组（按依赖顺序）：

底层纯函数 / staticmethod 等价物：
- ``parse_material_tags`` / ``parse_profile`` / ``extract_keyword_terms``
- ``material_section_hint_matches`` / ``format_chapter_profile_text``
- ``dedupe_ids`` / ``serialize_source_ids`` / ``dedupe_preserve_order``
- ``is_material_consumable`` / ``count_priority_segments_in_entry``
- ``entry_contains_priority_segments`` / ``truncate_material_context_entry``
- ``format_image_metadata``

高价值文本场景判定（实例方法等价物，签名第一参数为 composer）：
- ``is_high_value_text_material`` / ``get_long_text_specialization``
- ``get_material_chunk_sample_limit`` / ``get_material_excerpt_chars``
- ``get_material_fallback_chars`` / ``build_priority_text_material_segments``

继承素材过滤：
- ``filter_inherited_materials_for_node`` / ``score_inherited_material_for_node``

LLM 上下文文本组装：
- ``load_material_images``（async, DB 读）
- ``load_material_chunk_samples``（async, DB 读）
- ``format_materials`` / ``build_material_context_entries``
- ``build_material_context_entry`` / ``build_material_usage_rules``

主类 thin forwarder 通过 ``composer._X`` 走回这些方法，保留测试
``composer._build_material_context_entry`` 等 monkey patch 契约。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import select

from app.core.writer.material_titles import get_effective_material_title
from app.models.writing_material import WritingMaterial
from app.models.writing_material_chunk import WritingMaterialChunk
from app.models.writing_material_image import WritingMaterialImage

if TYPE_CHECKING:
    from app.core.writer.section_composer import (
        MaterialContextBuildResult,
        SectionComposer,
    )
    from app.models.writing_outline_node import WritingOutlineNode


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_MATERIAL_CHUNK_SAMPLE_LIMIT = 2
DEFAULT_CHUNK_EXCERPT_CHARS = 280
DEFAULT_CONTENT_FALLBACK_CHARS = 300
HIGH_VALUE_TEXT_MATERIAL_CHUNK_SAMPLE_LIMIT = 5
HIGH_VALUE_TEXT_CHUNK_EXCERPT_CHARS = 650
HIGH_VALUE_TEXT_FALLBACK_CHARS = 1600
HIGH_VALUE_TEXT_DIRECT_PARAGRAPH_LIMIT = 3
HIGH_VALUE_TEXT_DIRECT_PARAGRAPH_CHARS = 420

LONG_TEXT_SPECIALIZATION_RULES: dict[str, dict[str, tuple[str, ...] | str]] = {
    "company_profile": {
        "labels": ("公司简介", "企业简介", "企业概况", "企业介绍", "单位简介"),
        "keywords": ("公司简介", "企业简介", "企业概况", "企业介绍", "投标人概况", "企业基本情况", "综合实力", "主营业务", "代表项目"),
        "node_keywords": ("公司", "企业", "投标人", "规模", "人员", "组织"),
        "base_terms": ("公司", "企业", "主营", "业务", "成立", "注册资本", "团队", "规模"),
        "usage_rule": "必须优先落地企业基础信息、主营业务、综合实力与代表项目等明确事实点。",
    },
    "performance_summary": {
        "labels": ("项目业绩综述", "项目业绩", "业绩综述", "业绩材料", "业绩证明", "项目合同", "项目经验"),
        "keywords": ("项目业绩综述", "项目业绩", "业绩概述", "类似项目", "项目经验", "业绩证明", "代表项目", "履约成果", "项目合同"),
        "node_keywords": ("业绩", "项目", "案例", "经验", "合同", "成果"),
        "base_terms": ("项目", "业绩", "案例", "合同", "金额", "客户", "实施", "履约", "成果"),
        "usage_rule": "必须优先落地代表项目、服务规模、履约成果、项目类型或合同关键信息，不得只写空泛经验表述。",
    },
    "service_commitment": {
        "labels": ("服务承诺", "售后服务承诺", "售后服务方案", "响应承诺", "保障措施", "服务方案"),
        "keywords": ("服务承诺", "售后服务承诺", "售后服务", "响应承诺", "响应机制", "保障措施", "服务保障", "承诺函"),
        "node_keywords": ("服务", "承诺", "售后", "响应", "保障", "时效"),
        "base_terms": ("服务", "承诺", "响应", "时效", "保障", "维护", "售后", "责任", "违约"),
        "usage_rule": "必须优先落地响应时效、服务机制、保障措施和违约责任等明确条款，不得泛化为空洞承诺。",
    },
    "management_system": {
        "labels": ("管理制度", "管理办法", "制度汇编", "质量管理制度", "安全管理制度", "HSE管理制度", "内部控制制度"),
        "keywords": ("管理制度", "管理办法", "制度汇编", "质量管理制度", "安全管理制度", "HSE管理", "内部控制", "流程制度"),
        "node_keywords": ("管理", "制度", "流程", "质量", "安全", "hse", "监督", "职责"),
        "base_terms": ("制度", "管理", "流程", "职责", "审批", "培训", "检查", "整改", "考核", "监督"),
        "usage_rule": "必须优先落地制度名称、关键流程、责任分工和执行监督机制，不得只写原则性套话。",
    },
    "technical_plan": {
        "labels": ("技术方案", "实施方案"),
        "keywords": ("技术方案", "实施方案", "组织设计", "实施计划", "工艺流程"),
        "node_keywords": ("技术", "方案", "实施", "工艺", "组织", "计划"),
        "base_terms": ("技术", "方案", "实施", "工艺", "流程", "计划", "保障"),
        "usage_rule": "必须优先落地可执行的方案步骤、工艺流程、资源配置和实施安排。",
    },
}


# ---------------------------------------------------------------------------
# 底层纯函数（无 composer 状态）
# ---------------------------------------------------------------------------


def parse_material_tags(material: WritingMaterial) -> dict[str, Any]:
    if not material.metadata_tags:
        return {}
    try:
        raw = json.loads(material.metadata_tags)
    except (json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def parse_profile(node: "WritingOutlineNode") -> Optional[dict[str, Any]]:
    if not node.chapter_profile:
        return None
    try:
        raw = json.loads(node.chapter_profile)
    except (json.JSONDecodeError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def extract_keyword_terms(text: str) -> set[str]:
    if not text:
        return set()
    terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", text))
    return {term.lower() for term in terms if term}


def material_section_hint_matches(tags: dict[str, Any], node_title: str) -> bool:
    applicable_sections = tags.get("applicable_sections", [])
    node_title_lower = (node_title or "").lower()
    for section_name in applicable_sections:
        section_lower = str(section_name).lower().strip()
        if section_lower and (
            section_lower in node_title_lower or node_title_lower in section_lower
        ):
            return True
    return False


def format_chapter_profile_text(
    chapter_profile: Optional[dict[str, Any]],
) -> str:
    if not chapter_profile:
        return "（无章节画像）"

    semantic_role = str(chapter_profile.get("semantic_role") or "").strip()
    expects = [
        str(item).strip()
        for item in chapter_profile.get("expects", [])
        if str(item).strip()
    ]
    rejects = [
        str(item).strip()
        for item in chapter_profile.get("rejects", [])
        if str(item).strip()
    ]

    lines: list[str] = []
    if semantic_role:
        lines.append(f"- 章节职责: {semantic_role}")
    if expects:
        lines.append("- 重点应覆盖: " + "；".join(expects[:5]))
    if rejects:
        lines.append("- 应避免混入: " + "；".join(rejects[:5]))
    return "\n".join(lines) if lines else "（无章节画像）"


def dedupe_ids(ids: list[Optional[int]]) -> list[int]:
    deduped: list[int] = []
    seen: set[int] = set()
    for item in ids:
        if item is None or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def serialize_source_ids(ids: Optional[list[int]]) -> Optional[str]:
    deduped = dedupe_ids(list(ids or []))
    if not deduped:
        return None
    return json.dumps(deduped, ensure_ascii=False)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def truncate_material_context_entry(entry: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    compact = entry.strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max(max_chars - 20, 0)].rstrip() + "\n[内容已截断]"


def count_priority_segments_in_entry(entry: str) -> int:
    return len(re.findall(r"重点基础信息\d+:", entry))


def is_material_consumable(material: WritingMaterial) -> bool:
    role = str(getattr(material, "material_role", "normal") or "normal")
    review_status = str(getattr(material, "split_review_status", "none") or "none")
    if role == "split_parent":
        return False
    if review_status == "ignored":
        return False
    return True


def entry_contains_priority_segments(entry: str) -> bool:
    return "重点基础信息" in entry


def format_image_metadata(images: list[WritingMaterialImage]) -> str:
    """将图片元信息格式化为文本,供 LLM 在生成内容时参考"""
    if not images:
        return ""
    parts = ["## 可用图片素材"]
    for img in images:
        img_type = img.image_type.value if img.image_type else "unknown"
        subtype = (getattr(img, "business_subtype", None) or "").strip()
        desc = img.caption or f"图片{img.image_index + 1}"
        line = f"- image_id={img.id}, 类型={img_type}, 描述={desc}"
        if subtype:
            line += f", 业务子类={subtype}"
        if img.structured_fields:
            compact_fields = compact_structured_fields(img.structured_fields)
            line += f", 结构化事实={compact_fields or '已提取'}"
        parts.append(line)
    parts.append(
        "\n如需在正文中插入图片,请在适当位置使用: "
        '<figure data-image-id="N"><figcaption>图X: 描述</figcaption></figure>'
    )
    return "\n".join(parts)


def compact_structured_fields(
    raw: object,
    *,
    max_items: int = 8,
    max_value_chars: int = 80,
) -> str:
    """压缩结构化字段，避免完整证照/表单内容撑爆 Prompt。"""
    if not raw:
        return ""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""

    parts: list[str] = []
    for key, value in data.items():
        if len(parts) >= max_items:
            break
        if value in (None, "", [], {}):
            continue
        key_text = str(key).strip()
        value_text = re.sub(r"\s+", " ", str(value)).strip()
        if not key_text or not value_text:
            continue
        if len(value_text) > max_value_chars:
            value_text = value_text[:max_value_chars].rstrip() + "..."
        parts.append(f"{key_text}={value_text}")
    return "；".join(parts)


# ---------------------------------------------------------------------------
# 高价值文本场景判定（接收 composer 引用以走 thin forwarder 兼容 monkey patch）
# ---------------------------------------------------------------------------


def is_high_value_text_material(
    composer: "SectionComposer", material: WritingMaterial,
) -> bool:
    material_type = (
        material.material_type.value
        if hasattr(material.material_type, "value")
        else str(material.material_type)
    )
    if material_type not in {"text", "old_bid_section", "template_fragment"}:
        return False

    tags = composer._parse_material_tags(material)
    keyword_signals = " ".join(
        [
            get_effective_material_title(material),
            material.content or "",
            " ".join(tags.get("keywords", [])),
            " ".join(tags.get("applicable_sections", [])),
        ]
    )
    specialization = composer._get_long_text_specialization(material, tags=tags)
    if specialization:
        return True
    high_value_keywords = (
        "公司简介", "企业简介", "企业概况", "企业介绍",
        "投标人概况", "企业基本情况", "综合实力",
        "业绩", "项目经验", "代表项目", "主营业务",
        "服务承诺", "售后服务", "响应承诺", "保障措施",
        "管理制度", "制度汇编", "管理办法", "质量管理制度", "安全管理制度",
    )
    return any(keyword in keyword_signals for keyword in high_value_keywords)


def get_long_text_specialization(
    composer: "SectionComposer",
    material: WritingMaterial,
    *,
    tags: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    tags = tags if tags is not None else composer._parse_material_tags(material)
    label_signals = {
        (getattr(material, "resolved_label", None) or "").strip(),
        (getattr(material, "structure_hint_label", None) or "").strip(),
        (getattr(material, "file_name_label", None) or "").strip(),
        str(tags.get("domain") or "").strip(),
    }
    keyword_signals = " ".join(
        [
            get_effective_material_title(material),
            material.content or "",
            " ".join(tags.get("keywords", [])),
            " ".join(tags.get("applicable_sections", [])),
        ]
    )
    for specialization, rule in LONG_TEXT_SPECIALIZATION_RULES.items():
        labels = set(rule.get("labels", ()))
        keywords = rule.get("keywords", ())
        if any(label in labels for label in label_signals if label):
            return specialization
        if any(keyword in keyword_signals for keyword in keywords):
            return specialization
    return None


def get_material_chunk_sample_limit(
    composer: "SectionComposer", material: WritingMaterial,
) -> int:
    if composer._is_high_value_text_material(material):
        return HIGH_VALUE_TEXT_MATERIAL_CHUNK_SAMPLE_LIMIT
    return DEFAULT_MATERIAL_CHUNK_SAMPLE_LIMIT


def get_material_excerpt_chars(
    composer: "SectionComposer", material: WritingMaterial,
) -> int:
    if composer._is_high_value_text_material(material):
        return HIGH_VALUE_TEXT_CHUNK_EXCERPT_CHARS
    return DEFAULT_CHUNK_EXCERPT_CHARS


def get_material_fallback_chars(
    composer: "SectionComposer", material: WritingMaterial,
) -> int:
    if composer._is_high_value_text_material(material):
        return HIGH_VALUE_TEXT_FALLBACK_CHARS
    return DEFAULT_CONTENT_FALLBACK_CHARS


def build_priority_text_material_segments(
    composer: "SectionComposer",
    material: WritingMaterial,
    *,
    node_title: str = "",
    chapter_profile: Optional[dict[str, Any]] = None,
) -> list[str]:
    if not composer._is_high_value_text_material(material):
        return []
    content = re.sub(r"\s+", " ", (material.content or "").strip())
    if not content:
        return []

    raw_paragraphs = [
        re.sub(r"\s+", " ", part.strip())
        for part in re.split(r"\n{2,}|\r?\n", material.content or "")
        if part and part.strip()
    ]
    paragraphs = [paragraph for paragraph in raw_paragraphs if len(paragraph) >= 30]
    if not paragraphs:
        paragraphs = [content[:HIGH_VALUE_TEXT_FALLBACK_CHARS]]

    query_terms = composer._extract_keyword_terms(
        " ".join(
            [
                node_title,
                (chapter_profile or {}).get("semantic_role", ""),
                " ".join((chapter_profile or {}).get("expects", [])),
                getattr(material, "resolved_label", None) or "",
                getattr(material, "structure_hint_label", None) or "",
                getattr(material, "file_name_label", None) or "",
                get_effective_material_title(material),
            ]
        )
    )
    specialization = composer._get_long_text_specialization(material)
    base_terms = set(
        LONG_TEXT_SPECIALIZATION_RULES.get(specialization or "", {}).get(
            "base_terms",
            ("公司", "企业", "主营", "业务", "成立", "注册资本", "资质", "项目", "业绩", "服务", "管理", "团队"),
        )
    )

    scored: list[tuple[int, int, str]] = []
    for index, paragraph in enumerate(paragraphs):
        excerpt = paragraph[:HIGH_VALUE_TEXT_DIRECT_PARAGRAPH_CHARS]
        para_terms = composer._extract_keyword_terms(excerpt)
        score = len(para_terms & query_terms)
        score += len(para_terms & base_terms)
        if index < 2:
            score += 1
        scored.append((score, index, excerpt))

    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [item[2] for item in scored if item[0] > 0][: HIGH_VALUE_TEXT_DIRECT_PARAGRAPH_LIMIT]
    if selected:
        return composer._dedupe_preserve_order(selected)
    fallback = [item[2] for item in scored[:HIGH_VALUE_TEXT_DIRECT_PARAGRAPH_LIMIT]]
    return composer._dedupe_preserve_order(fallback)


# ---------------------------------------------------------------------------
# 继承素材过滤
# ---------------------------------------------------------------------------


def filter_inherited_materials_for_node(
    composer: "SectionComposer",
    materials: list[WritingMaterial],
    *,
    node_title: str,
    chapter_profile: Optional[dict[str, Any]] = None,
) -> list[WritingMaterial]:
    """对父节点继承下来的素材做章节级二次过滤，避免全量污染子章节。"""
    if not materials:
        return []

    relevant: list[tuple[int, WritingMaterial]] = []
    for material in materials:
        score = composer._score_inherited_material_for_node(
            material,
            node_title=node_title,
            chapter_profile=chapter_profile,
        )
        if score > 0:
            relevant.append((score, material))

    relevant.sort(key=lambda item: (-item[0], item[1].id))
    return [material for _, material in relevant]


def score_inherited_material_for_node(
    composer: "SectionComposer",
    material: WritingMaterial,
    *,
    node_title: str,
    chapter_profile: Optional[dict[str, Any]] = None,
) -> int:
    tags = composer._parse_material_tags(material)
    score = 0
    node_title_lower = (node_title or "").lower().strip()

    if composer._material_section_hint_matches(tags, node_title):
        score += 4

    labels = [
        getattr(material, "resolved_label", None) or "",
        getattr(material, "structure_hint_label", None) or "",
        getattr(material, "file_name_label", None) or "",
    ]
    material_text = " ".join(
        [
            get_effective_material_title(material),
            " ".join(labels),
            str(tags.get("domain") or ""),
            " ".join(tags.get("applicable_sections", [])),
            " ".join(tags.get("keywords", [])),
            " ".join(tags.get("entities", [])),
            (material.content or "")[:600],
        ]
    )
    node_terms = composer._extract_keyword_terms(
        " ".join(
            [
                node_title,
                str((chapter_profile or {}).get("semantic_role") or ""),
                " ".join((chapter_profile or {}).get("expects", [])),
            ]
        )
    )
    material_terms = composer._extract_keyword_terms(material_text)
    score += len(node_terms & material_terms)

    specialization = composer._get_long_text_specialization(material, tags=tags)
    if specialization:
        node_keywords = LONG_TEXT_SPECIALIZATION_RULES.get(specialization, {}).get("node_keywords", ())
        if any(keyword in node_title_lower for keyword in node_keywords):
            score += 3

    if composer._is_high_value_text_material(material):
        expects_text = " ".join((chapter_profile or {}).get("expects", []))
        if expects_text and len(composer._extract_keyword_terms(expects_text) & material_terms) > 0:
            score += 2

    return score


# ---------------------------------------------------------------------------
# DB 加载（image / chunk samples）
# ---------------------------------------------------------------------------


async def load_material_images(
    composer: "SectionComposer", materials: list[WritingMaterial],
) -> list[WritingMaterialImage]:
    """加载素材关联的图片记录"""
    if not materials:
        return []
    material_ids = [m.id for m in materials]
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingMaterialImage).where(
                WritingMaterialImage.material_id.in_(material_ids),
            ).order_by(WritingMaterialImage.material_id, WritingMaterialImage.image_index)
        )
        return list(result.scalars().all())


async def load_material_chunk_samples(
    composer: "SectionComposer",
    materials: list[WritingMaterial],
    node_title: str,
    chapter_profile: Optional[dict[str, Any]] = None,
) -> dict[int, list[str]]:
    """按章节语义为每个素材挑选更相关的分块片段。

    `WritingMaterialChunk` 是标书编写生成阶段的主文本证据源，
    `chunk_ids` 仅保留为兼容字段，不再作为主消费入口。
    """
    if not materials:
        return {}

    material_ids = [material.id for material in materials]
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingMaterialChunk).where(
                WritingMaterialChunk.material_id.in_(material_ids),
            ).order_by(
                WritingMaterialChunk.material_id,
                WritingMaterialChunk.chunk_index,
            )
        )
        chunks = list(result.scalars().all())

    chunk_map: dict[int, list[WritingMaterialChunk]] = defaultdict(list)
    for chunk in chunks:
        chunk_map[chunk.material_id].append(chunk)

    chapter_terms = composer._extract_keyword_terms(
        " ".join(
            [
                node_title,
                (chapter_profile or {}).get("semantic_role", ""),
                " ".join((chapter_profile or {}).get("expects", [])),
            ]
        )
    )

    sampled_chunks: dict[int, list[str]] = {}
    for material in materials:
        material_tags = composer._parse_material_tags(material)
        tag_terms = composer._extract_keyword_terms(
            " ".join(
                [
                    material_tags.get("domain", ""),
                    " ".join(material_tags.get("applicable_sections", [])),
                    " ".join(material_tags.get("entities", [])),
                    " ".join(material_tags.get("keywords", [])),
                ]
            )
        )
        query_terms = chapter_terms | tag_terms
        material_chunks = chunk_map.get(material.id, [])
        if not material_chunks:
            continue

        chunk_excerpt_chars = composer._get_material_excerpt_chars(material)
        sample_limit = composer._get_material_chunk_sample_limit(material)
        scored_chunks: list[tuple[int, int, str]] = []
        for chunk in material_chunks:
            excerpt = (chunk.content or "").strip()
            if not excerpt:
                continue
            excerpt = excerpt[:chunk_excerpt_chars]
            chunk_terms = composer._extract_keyword_terms(excerpt)
            score = len(chunk_terms & query_terms)
            if composer._material_section_hint_matches(material_tags, node_title):
                score += 2
            scored_chunks.append((score, chunk.chunk_index, excerpt))

        if not scored_chunks:
            continue

        scored_chunks.sort(key=lambda item: (-item[0], item[1]))
        positives = [item[2] for item in scored_chunks if item[0] > 0]
        if positives:
            sampled_chunks[material.id] = positives[:sample_limit]
        else:
            sampled_chunks[material.id] = [item[2] for item in scored_chunks[:sample_limit]]

    return sampled_chunks


# ---------------------------------------------------------------------------
# LLM 上下文文本组装
# ---------------------------------------------------------------------------


def format_materials(
    composer: "SectionComposer",
    materials: list[WritingMaterial],
    *,
    chunk_samples: Optional[dict[int, list[str]]] = None,
    node_title: str = "",
    chapter_profile: Optional[dict[str, Any]] = None,
    max_total_chars: Optional[int] = None,
) -> "MaterialContextBuildResult":
    """将素材列表格式化为文本"""
    from app.core.writer.section_composer import MaterialContextBuildResult

    if not materials:
        return MaterialContextBuildResult("", [], 0, 0, 0, 0, 0)
    entries = composer._build_material_context_entries(
        materials,
        chunk_samples=chunk_samples,
        node_title=node_title,
        chapter_profile=chapter_profile,
    )
    used_material_ids: list[int] = []
    parts: list[str] = []
    consumed = 0
    priority_materials_used = 0
    priority_segments_used = 0
    truncated_materials = 0
    for material_id, entry, _ in entries:
        if max_total_chars is not None and max_total_chars > 0:
            if consumed + len(entry) > max_total_chars:
                if not parts:
                    truncated = composer._truncate_material_context_entry(entry, max_total_chars)
                    if truncated:
                        parts.append(truncated)
                        used_material_ids.append(material_id)
                        truncated_materials += 1
                        if composer._entry_contains_priority_segments(truncated):
                            priority_materials_used += 1
                            priority_segments_used += composer._count_priority_segments_in_entry(truncated)
                else:
                    truncated_materials += 1
                continue
        parts.append(entry)
        used_material_ids.append(material_id)
        consumed += len(entry)
        if composer._entry_contains_priority_segments(entry):
            priority_materials_used += 1
            priority_segments_used += composer._count_priority_segments_in_entry(entry)
    return MaterialContextBuildResult(
        text="\n\n".join(parts),
        used_material_ids=used_material_ids,
        bound_materials=len(materials),
        used_materials=len(used_material_ids),
        priority_materials_used=priority_materials_used,
        priority_segments_used=priority_segments_used,
        truncated_materials=truncated_materials,
    )


def build_material_context_entries(
    composer: "SectionComposer",
    materials: list[WritingMaterial],
    *,
    chunk_samples: Optional[dict[int, list[str]]] = None,
    node_title: str = "",
    chapter_profile: Optional[dict[str, Any]] = None,
) -> list[tuple[int, str, bool]]:
    priority_entries: list[tuple[int, str, bool]] = []
    regular_entries: list[tuple[int, str, bool]] = []
    for material in materials:
        is_priority = composer._is_high_value_text_material(material)
        entry = composer._build_material_context_entry(
            material,
            excerpts=(chunk_samples or {}).get(material.id),
            node_title=node_title,
            chapter_profile=chapter_profile,
        )
        bucket = priority_entries if is_priority else regular_entries
        bucket.append((material.id, entry, is_priority))
    return priority_entries + regular_entries


def build_material_context_entry(
    composer: "SectionComposer",
    material: WritingMaterial,
    excerpts: Optional[list[str]] = None,
    node_title: str = "",
    chapter_profile: Optional[dict[str, Any]] = None,
) -> str:
    material_type = (
        material.material_type.value
        if hasattr(material.material_type, "value")
        else str(material.material_type)
    )
    tags = composer._parse_material_tags(material)
    tag_parts: list[str] = []
    if tags.get("domain"):
        tag_parts.append(f"领域={tags['domain']}")
    if tags.get("applicable_sections"):
        tag_parts.append(
            "适用章节=" + "、".join(tags["applicable_sections"][:3])
        )
    if tags.get("entities"):
        tag_parts.append("关键实体=" + "、".join(tags["entities"][:3]))
    if tags.get("keywords"):
        tag_parts.append("关键词=" + "、".join(tags["keywords"][:5]))
    tag_status = str(tags.get("_tag_status") or "").strip()
    if tag_status:
        tag_parts.append(f"打标状态={tag_status}")
    confidence = tags.get("confidence")
    if confidence not in (None, ""):
        tag_parts.append(f"置信度={confidence}")

    provenance_parts: list[str] = []
    resolved_label = (getattr(material, "resolved_label", None) or "").strip()
    label_conflict = (getattr(material, "label_conflict", None) or "").strip()
    if resolved_label:
        provenance_parts.append(f"业务标签={resolved_label}")
    if label_conflict:
        provenance_parts.append(f"标签一致性={label_conflict}")
    page_start = getattr(material, "source_page_start", None)
    page_end = getattr(material, "source_page_end", None)
    source_page = getattr(material, "source_page", None)
    if page_start and page_end:
        page_text = f"来源页={page_start}" if page_start == page_end else f"来源页={page_start}-{page_end}"
        provenance_parts.append(page_text)
    elif source_page:
        provenance_parts.append(f"来源页={source_page}")

    lines = [f"### {get_effective_material_title(material)} ({material_type})"]
    if provenance_parts:
        lines.append("溯源: " + "；".join(provenance_parts))
    if tag_parts:
        lines.append("标签: " + "；".join(tag_parts))

    priority_segments = composer._build_priority_text_material_segments(
        material,
        node_title=node_title,
        chapter_profile=chapter_profile,
    )
    for idx, segment in enumerate(priority_segments, start=1):
        lines.append(f"重点基础信息{idx}: {segment}")

    fallback_chars = composer._get_material_fallback_chars(material)
    sample_limit = composer._get_material_chunk_sample_limit(material)
    chosen_excerpts = excerpts or [((material.content or "").strip()[:fallback_chars])]
    chosen_excerpts = [excerpt for excerpt in chosen_excerpts if excerpt]
    for idx, excerpt in enumerate(chosen_excerpts[:sample_limit], start=1):
        prefix = "相关片段" if excerpts else "内容摘要"
        lines.append(f"{prefix}{idx}: {excerpt}")

    if len(lines) == 1:
        lines.append("内容摘要1: （素材内容为空）")
    return "\n".join(lines)


def build_material_usage_rules(
    composer: "SectionComposer", materials: list[WritingMaterial],
) -> str:
    high_value_materials = [m for m in materials if composer._is_high_value_text_material(m)]
    if not high_value_materials:
        return "（无额外素材消费约束）"

    labels = composer._dedupe_preserve_order(
        [
            getattr(material, "resolved_label", None)
            or getattr(material, "structure_hint_label", None)
            or getattr(material, "file_name_label", None)
            or get_effective_material_title(material)
            for material in high_value_materials
        ]
    )
    label_text = "、".join(labels[:4])
    specialization_rules: list[str] = []
    seen_specializations: set[str] = set()
    for material in high_value_materials:
        specialization = composer._get_long_text_specialization(material)
        if not specialization or specialization in seen_specializations:
            continue
        seen_specializations.add(specialization)
        rule = LONG_TEXT_SPECIALIZATION_RULES.get(specialization, {}).get("usage_rule")
        if isinstance(rule, str) and rule:
            specialization_rules.append(f"- {rule}")
    generic_rules = [
        f"- 当前绑定中包含高价值文字素材：{label_text}",
        "- 必须优先把这些素材中的明确事实或条款作为正文依据，不得忽略，也不得用常识替代。",
        "- 若素材未提供足够事实，请使用 [待补充: 信息项] 标记，不得自行编造。",
    ]
    return "\n".join(generic_rules + specialization_rules)
