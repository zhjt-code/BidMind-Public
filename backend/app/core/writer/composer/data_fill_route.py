"""SectionComposer data_fill 路由子模块（Task 6 Step 6）

承载 data_fill 路径完整流程：

- ``try_data_fill_template``  B5.7 Jinja2 模板替代 LLM 生成表格
- ``extract_template_items``  从 materials + chunk_samples 抽取模板 items
- ``fill_data``  data_fill 主入口（资料填充 + 轻量 AI + 图片元信息）

设计要点：
- 接收 ``composer`` 引用以复用 ``_load_*`` / ``_format_*`` /
  ``_chat_with_input_guard`` 等 helper（保留 monkey patch 兼容）。
- ``settings.WRITING_CONTEXT_BUDGET_TOKENS`` 与 ``_CHARS_PER_TOKEN`` 仍走主
  模块 import：测试通过 ``monkeypatch.setattr("app.core.writer.section_composer.settings.WRITING_CONTEXT_BUDGET_TOKENS", ...)``
  替换 settings 引用。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final, Optional

from loguru import logger

from app.core.ai.prompt_registry import resolve_runtime_params
from app.core.writer.composer.clause_directive import build_clause_directives
from app.core.writer.image_node_affinity import filter_images_for_node
from app.core.writer.industry_profile_view import (
    PURPOSE_DATA_FILL,
    build_profile_excerpt,
)
from app.core.writer.material_consumption_trace import build_consumption_trace
from app.core.writer.numbering import get_sub_numbering_hint, strip_existing_number
from app.core.writer.section_structure import normalize_section_html
from app.core.writer.text_budget import make_priority_shrink_reducer

if TYPE_CHECKING:
    from app.core.writer.section_composer import ComposePayload, SectionComposer
    from app.models.writing_outline_node import WritingOutlineNode

# data_fill 产出以表格与短段落为主，画像只用于术语与语言风格对齐，
# 预算低于 free_writing，避免挤压素材与约束的上下文份额。
_DATA_FILL_PROFILE_CHARS: Final = 1200


def try_data_fill_template(
    composer: "SectionComposer",
    node: "WritingOutlineNode",
    project_id: Optional[int],
    materials: list,
    chunk_samples: Any,
    clauses: list,
    images: Optional[list] = None,
) -> Optional["ComposePayload"]:
    """B5.7: 尝试用 Jinja2 模板替代 LLM 生成表格内容.

    仅在:
    - project_id 命中 WRITER_DATA_FILL_TEMPLATE_RATIO
    - 节点标题匹配四类模板 (personnel/performance/qualification/equipment)
    时触发; 其他情况返回 None 让调用方走 LLM.
    """
    from app.core.writer.section_composer import ComposePayload

    if project_id is None:
        return None

    try:
        from app.core.writer.template_router import (
            match_template,
            render_template,
            should_use_template,
        )
    except Exception:
        return None

    try:
        if not should_use_template(project_id):
            return None
        _tpl_name = match_template(node.title or "", None)
        if not _tpl_name:
            return None

        _items = composer._extract_template_items(materials, chunk_samples, _tpl_name, images=images)
        if not _items:
            # 素材不足以生成表格条目, 回退 LLM 路径
            logger.info(
                f"template_router: node={node.title} matched {_tpl_name} "
                f"but no items extractable, falling back to LLM"
            )
            return None

        _html = render_template(_tpl_name, _items, title=node.title or "")
        if images:
            _html = composer._append_figure_tags(_html, images)
        logger.info(
            f"template_router: node={node.title} rendered via {_tpl_name} "
            f"with {len(_items)} items (LLM skipped)"
        )
        _used_material_ids = composer._dedupe_ids([m.id for m in materials])
        _clause_ids = composer._dedupe_ids([c.id for c in clauses])
        _bound_material_ids = composer._dedupe_ids([m.id for m in materials])
        return ComposePayload(
            content=_html,
            source_material_ids=_used_material_ids,
            source_clause_ids=_clause_ids,
            bound_material_ids=_bound_material_ids,
            selected_material_ids=_used_material_ids,
            consumption_trace=build_consumption_trace(
                bound_material_ids=_bound_material_ids,
                selected_material_ids=_used_material_ids,
                consumed_material_ids=_used_material_ids,
            ),
        )
    except Exception as e:
        logger.warning(f"template_router degraded, falling back to LLM: {e}")
        return None


def extract_template_items(
    materials: list,
    chunk_samples: Any,
    template_name: str,
    *,
    images: Optional[list] = None,
) -> list[dict[str, Any]]:
    """从 materials + chunk_samples 抽取 Jinja2 模板所需的 items.

    优先消费 WritingMaterialImage.structured_fields，缺失字段由模板 default 兜底。
    """
    images_by_material: dict[int, list] = {}
    for img in images or []:
        material_id = getattr(img, "material_id", None)
        if material_id is not None:
            images_by_material.setdefault(int(material_id), []).append(img)

    items: list[dict[str, Any]] = []
    for m in materials or []:
        _title = getattr(m, "display_title", None) or getattr(m, "title", "") or ""
        _type = getattr(m, "material_type", "") or ""
        item: dict[str, Any] = {
            "name": _title,
            "title": _title,
            "type": str(_type),
            "description": _title,
        }
        item.update(
            _structured_template_fields(
                images_by_material.get(getattr(m, "id", 0), []),
                template_name,
            )
        )
        items.append(item)
    return items


def _load_structured_fields(image: Any) -> dict[str, Any]:
    """读取图片结构化字段，兼容JSON字符串与dict。"""
    raw = getattr(image, "structured_fields", None)
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _structured_template_fields(images: list, template_name: str) -> dict[str, Any]:
    """把结构化字段映射为模板字段名。"""
    if template_name != "qualification.j2":
        return {}
    for image in images:
        fields = _load_structured_fields(image)
        if not fields:
            continue
        return {
            "cert_name": fields.get("资质名称") or fields.get("证件类型") or "",
            "cert_level": fields.get("资质等级") or fields.get("等级") or "",
            "cert_number": fields.get("证书编号") or fields.get("编号") or "",
            "issuer": fields.get("发证机关") or fields.get("认证机构") or "",
            "valid_until": fields.get("有效期止") or fields.get("有效期至") or "",
        }
    return {}


async def fill_data(
    composer: "SectionComposer",
    node: "WritingOutlineNode",
    industry_profile: Optional[str],
    number_map: Optional[dict[int, str]] = None,
    project_id: Optional[int] = None,
) -> "ComposePayload":
    """资料填充(轻量AI + 图片元信息)

    B5.7: 若 project 命中 WRITER_DATA_FILL_TEMPLATE_RATIO 且节点标题匹配
    人员/业绩/资质/设备模板, 优先走 Jinja2 模板化 (避免 LLM 调用).
    """
    # 函数级 import 避免循环依赖；同时保留测试 monkeypatch 主模块 settings 引用
    from app.core.writer.section_composer import (
        _CHARS_PER_TOKEN,
        ComposePayload,
        get_data_fill_prompt,
        settings,
    )

    chapter_profile = composer._parse_profile(node)
    materials = await composer._load_bound_materials(
        node.id,
        node_title=node.title,
        chapter_profile=chapter_profile,
    )
    clauses = await composer._load_bound_clauses(node.id)
    chunk_samples = await composer._load_material_chunk_samples(
        materials,
        node.title,
        chapter_profile,
    )
    all_images = await composer._load_material_images(materials)
    images = filter_images_for_node(all_images, node.title)

    # B5.7: 模板化路径 - 命中关键词则跳过 LLM
    _template_payload = composer._try_data_fill_template(
        node=node,
        project_id=project_id,
        materials=materials,
        chunk_samples=chunk_samples,
        clauses=clauses,
        images=images,
    )
    if _template_payload is not None:
        return _template_payload

    clauses_text = build_clause_directives(clauses, max_chars=1500) if clauses else ""
    material_context_result = composer._format_materials(
        materials,
        chunk_samples=chunk_samples,
        node_title=node.title,
        chapter_profile=chapter_profile,
        max_total_chars=min(
            6000,
            settings.WRITING_CONTEXT_BUDGET_TOKENS * _CHARS_PER_TOKEN // 2,
        ),
    )
    materials_text = material_context_result.text
    used_material_ids = material_context_result.used_material_ids
    chapter_profile_text = composer._format_chapter_profile_text(chapter_profile)
    material_usage_rules = composer._build_material_usage_rules(materials)
    profile_excerpt = build_profile_excerpt(
        industry_profile,
        purpose=PURPOSE_DATA_FILL,
        max_chars=_DATA_FILL_PROFILE_CHARS,
    )
    logger.info(
        "长文本消费[data_fill]: node={!r}, bound={}, used={}, priority_used={}, priority_segments={}, truncated={}",
        node.title,
        material_context_result.bound_materials,
        material_context_result.used_materials,
        material_context_result.priority_materials_used,
        material_context_result.priority_segments_used,
        material_context_result.truncated_materials,
    )

    image_meta_text = composer._format_image_metadata(images)
    if image_meta_text:
        materials_text = materials_text + "\n\n" + image_meta_text

    chapter_number = (number_map or {}).get(node.id, "")
    sub_hint = get_sub_numbering_hint(node.depth)
    clean_title = strip_existing_number(node.title)

    def _build_payload(content: str, *, failure_code: Optional[str] = None) -> "ComposePayload":
        """成功/失败双出口共用的 ComposePayload 构造（原两处仅 content 不同）。"""
        return ComposePayload(
            content=content,
            source_material_ids=used_material_ids,
            source_clause_ids=composer._dedupe_ids([c.id for c in clauses]),
            bound_material_ids=composer._dedupe_ids([m.id for m in materials]),
            selected_material_ids=used_material_ids,
            consumption_trace=build_consumption_trace(
                bound_material_ids=composer._dedupe_ids([m.id for m in materials]),
                selected_material_ids=used_material_ids,
                consumed_material_ids=used_material_ids,
            ),
            planning_failure_code=failure_code,
        )

    try:
        initial_payload: dict[str, object] = {
            "clauses_text": clauses_text,
            "materials_text": materials_text,
            "chapter_profile_text": chapter_profile_text,
            "profile_excerpt": profile_excerpt,
            "material_usage_rules": material_usage_rules,
        }

        def _render_prompt(payload: dict[str, object]) -> tuple[str, str]:
            return get_data_fill_prompt(
                node_title=clean_title,
                target_word_count=getattr(node, "target_word_count", None) or 500,
                clauses_text=str(payload["clauses_text"]),
                materials_text=str(payload["materials_text"]),
                chapter_profile_text=str(payload["chapter_profile_text"]),
                chapter_number=chapter_number,
                sub_numbering_hint=sub_hint,
                material_usage_rules=str(payload["material_usage_rules"]),
                industry_profile_excerpt=str(payload["profile_excerpt"]),
            )

        _reduce_payload = make_priority_shrink_reducer(
            [
                ("materials_text", 1),
                ("clauses_text", 1),
                ("chapter_profile_text", 1),
                ("profile_excerpt", 1),
            ],
            kept_segments=["node_title", "material_usage_rules"],
            coerce_str=True,
        )

        temperature, max_tokens = resolve_runtime_params(
            "writing.data_fill", 0.1, 2000,
        )
        content = await composer._chat_with_input_guard(
            operation_name=f"章节data_fill({node.title})",
            initial_payload=initial_payload,
            render_prompt=_render_prompt,
            reduce_payload_semantically=_reduce_payload,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = normalize_section_html(content.strip(), clean_title)
        if images:
            content = composer._append_figure_tags(content, images)
        return _build_payload(content)
    except Exception as e:
        logger.error(f"data_fill 生成失败: node={node.title}, err={e}", exc_info=True)
        try:
            from app.core.observability.degradation_aggregator import (
                record_degradation as _record_degradation,
            )
            _record_degradation(
                kind="compose_failed",
                ref=f"node_{node.id}:data_fill",
                reason=f"{type(e).__name__}: {str(e)[:120]}",
            )
        except Exception:
            pass
        return _build_payload(f"<p>[{node.title} 内容生成失败，请手动补充]</p>")
