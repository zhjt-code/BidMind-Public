"""SectionComposer 固定模板路由子模块（Task 6 Step 5）

承载 fixed_template 整套：

主入口：
- ``fill_template``  固定模板填充（纯算法 + 结构化数据自动回填 + 图片插入）

占位符 / 图片：
- ``replace_placeholders``  委托给 ``sanitize_residual_placeholders``
- ``autofill_structured_fields`` / ``is_autofill_compatible``
- ``append_figure_tags``

模板净化（招标 PDF 复读断裂模板的修复路径）：
- ``normalize_fixed_template_text`` / ``should_join_template_line``
- ``normalize_template_parenthesized_item`` / ``inject_template_suffix_value``
- ``normalize_template_stub_spacing``

表格化检测与重建：
- ``is_tabular_fixed_template`` / ``rebuild_tabular_fixed_template``
- ``rebuild_bid_opening_table_html`` / ``rebuild_generic_table_html``
- ``split_template_lines`` / ``normalize_table_note``
- ``looks_like_numbered_line`` / ``looks_like_bid_opening_table_fragment``
- ``looks_like_generic_table_fragment`` / ``normalize_placeholder_text``

模块级常量在主类通过 ClassVar re-export，被外部测试访问。
``test_writer_numbering`` 通过 ``SectionComposer._normalize_fixed_template_text`` /
``SectionComposer._rebuild_tabular_fixed_template`` 直接调用，主类保留 thin
forwarder。
"""

from __future__ import annotations

import json
import re
from html import escape
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from app.core.writer.composer.fixed_template_material_fill import (
    append_fixed_template_material_facts,
)
from app.core.writer.fixed_template import match_template_title
from app.core.writer.fixed_template.features import (
    extract_field_slots,
    extract_signature_slots,
    is_directory_like,
    is_generic_template_requirement,
)
from app.core.writer.fixed_template.ownership import select_owner_node
from app.core.writer.fixed_template_contract import (
    fixed_template_review_gate,
    has_verbatim_source,
    resolve_fixed_template_source,
)
from app.core.writer.image_node_affinity import filter_images_for_node
from app.core.writer.material_consumption_trace import build_consumption_trace
from app.core.writer.numbering import format_number_for_depth, strip_existing_number
from app.core.writer.section_structure import normalize_section_html
from app.models.writing_material_image import WritingMaterialImage

if TYPE_CHECKING:
    from app.core.writer.section_composer import ComposePayload, SectionComposer
    from app.models.writing_outline_node import WritingOutlineNode


# ---------------------------------------------------------------------------
# 常量（与原 SectionComposer ClassVar 字节级一致；主类通过 re-export 保留）
# ---------------------------------------------------------------------------

TEMPLATE_SYMBOL_ONLY_RE = re.compile(r"^[¥￥、.．:：;；,，()\-\s]+$")
TEMPLATE_NUMBER_STUB_RE = re.compile(r"^(\d+)\s*[.．、]?\s*(\d+)?$")
TABULAR_TEMPLATE_TITLE_KEYWORDS = ("开标一览表", "报价书", "报价表", "投标函附录")

AUTOFILL_TYPE_WHITELIST: dict[str, list[str]] = {
    "license": ["营业执照", "企业", "证件", "资质"],
    "personnel_cert": ["身份证", "人员", "法定代表人", "法人", "项目经理"],
    "qualification": ["资质", "认证", "体系", "许可"],
    "nameplate": ["设备", "铭牌", "仪器"],
}


# ---------------------------------------------------------------------------
# 主入口：fill_template
# ---------------------------------------------------------------------------


async def fill_template(
    composer: "SectionComposer", node: "WritingOutlineNode",
) -> "ComposePayload":
    """固定模板填充(纯算法 + 结构化数据自动回填 + 图片插入)"""
    from app.core.writer.section_composer import ComposePayload

    node_volume_type = getattr(node, "volume_type", None)
    clause = await composer._find_bound_clause(
        node.id,
        sub_type="fixed_template",
        node_title=node.title,
        node_volume_type=node_volume_type,
    )
    if clause is None:
        clause = await composer._find_fallback_template_clause(
            project_id=node.project_id,
            node_title=node.title,
            node_volume_type=node_volume_type,
        )
    template, snapshot = resolve_fixed_template_source(clause) if clause else ("", None)
    gate_flags = None
    if snapshot and snapshot.get("is_complete_candidate") is False:
        gate_flags = fixed_template_review_gate(
            "固定模板来源快照可能不完整，请人工核对招标原模板后再导出。"
        )

    if not template:
        logger.warning(
            "fixed_template 模板缺失: node_id={}, title={!r}, clause_id={}",
            node.id,
            node.title,
            getattr(clause, "id", None),
        )
        return ComposePayload(
            content="<p>[固定模板内容待补充]</p>",
            source_material_ids=[],
            source_clause_ids=composer._dedupe_ids([getattr(clause, "id", None)]),
            gate_flags=fixed_template_review_gate("固定模板缺少可回查原文，必须人工补充。"),
        )
    if not _is_template_source_usable(node.title, template, snapshot):
        logger.warning(
            "fixed_template 模板源不匹配: node_id={}, title={!r}, clause_id={}",
            node.id,
            node.title,
            getattr(clause, "id", None),
        )
        return ComposePayload(
            content="<p>[固定模板内容待确认]</p>",
            source_material_ids=[],
            source_clause_ids=composer._dedupe_ids([getattr(clause, "id", None)]),
            gate_flags=fixed_template_review_gate("固定模板来源与章节标题不匹配或缺少模板槽位，必须人工核对。"),
        )

    # 同卷内一条模板约束只允许亲和度最高的节点消费,
    # 防止复合标题拆分后同一模板表被多个章节复读。
    owner = await _resolve_owner_among_siblings(composer, node, snapshot)
    if owner is not None and owner.id != node.id:
        logger.info(
            "fixed_template 归属让渡: node_id={} title={!r} -> owner_id={} title={!r}",
            node.id, node.title, owner.id, owner.title,
        )
        owner_title = strip_existing_number(owner.title)
        return ComposePayload(
            content=f"<p>本节内容与「{owner_title}」共用同一招标模板，请参见「{owner_title}」章节。</p>",
            source_material_ids=[],
            source_clause_ids=composer._dedupe_ids([getattr(clause, "id", None)]),
            gate_flags=fixed_template_review_gate(
                f"该章节与「{owner_title}」命中同一招标模板，已让渡给亲和度更高的章节，请人工确认本节内容安排。",
                severity="medium",
            ),
        )

    template = composer._replace_placeholders(template)

    materials = await composer._load_bound_materials(
        node.id,
        node_title=node.title,
        chapter_profile=None,
    )
    all_images = await composer._load_material_images(materials)
    images = filter_images_for_node(all_images, node.title)
    if len(images) != len(all_images):
        logger.info(
            f"图片过滤: node={node.title!r}, "
            f"before={len(all_images)}, after={len(images)}"
        )

    if images:
        template = composer._autofill_structured_fields(template, images, node.title)
    clean_title = strip_existing_number(node.title)
    if has_verbatim_source(snapshot):
        # 布局保真来源已是无损视觉行, 直通输出;
        # 断裂修补(normalize)与表格重建仅服务 chunk 碎文本, 会引入错误编号归属。
        template = normalize_section_html(template.strip(), clean_title)
    else:
        template = composer._normalize_fixed_template_text(template)
        if composer._is_tabular_fixed_template(clean_title, template):
            template = composer._rebuild_tabular_fixed_template(clean_title, template)
        template = normalize_section_html(template.strip(), clean_title)

    if images:
        template = composer._append_figure_tags(template, images)
    bound_material_ids = composer._dedupe_ids([m.id for m in materials])
    image_material_ids = composer._dedupe_ids([
        getattr(image, "material_id", None) for image in images
    ])
    template, consumed_material_ids, consumption_evidence = append_fixed_template_material_facts(
        template,
        materials,
        node_title=node.title,
        template_text=f"{getattr(clause, 'content', '') or ''}\n{getattr(clause, 'raw_text', '') or ''}",
    )
    consumed_material_ids = composer._dedupe_ids(image_material_ids + consumed_material_ids)

    # 成功生成的固定模板章节已按其全部直接绑定约束生成, source_clause_ids
    # 必须如实回填"模板约束 + 本节点直接绑定约束", 与 free_writing/data_fill 的
    # [c.id for c in clauses] 契约对齐; 否则覆盖统计会误判这些约束"未进入正文来源"。
    # 只取直接绑定(include_inherited=False): 继承/全局约束由各自章节负责记录消费,
    # 且降级占位路径(上方 return)不走此处, 不虚报覆盖。
    direct_clauses = await composer._load_bound_clauses(
        node.id, include_inherited=False,
    )

    # D3: fixed_template 不调用 build_clause_directives，在此把绑定评分项的提示
    # 写入 consumption_trace.scoring_hints，供下游检查器/报告消费（最小实现）。
    scoring_hints = [
        hint
        for c in direct_clauses
        if getattr(c, "sub_type", "") == "scoring_criterion"
        for hint in [_extract_scoring_hint(c)]
        if hint
    ]
    base_trace = build_consumption_trace(
        bound_material_ids=bound_material_ids,
        selected_material_ids=bound_material_ids,
        consumed_material_ids=consumed_material_ids,
        evidence=consumption_evidence,
    )
    if scoring_hints:
        base_trace["scoring_hints"] = scoring_hints

    return ComposePayload(
        content=template,
        source_material_ids=composer._dedupe_ids(consumed_material_ids),
        source_clause_ids=composer._dedupe_ids(
            [getattr(clause, "id", None)] + [c.id for c in direct_clauses]
        ),
        gate_flags=gate_flags,
        bound_material_ids=bound_material_ids,
        selected_material_ids=bound_material_ids,
        consumption_trace=base_trace,
    )


async def _resolve_owner_among_siblings(
    composer: "SectionComposer",
    node: "WritingOutlineNode",
    snapshot: Optional[dict],
) -> Optional["WritingOutlineNode"]:
    """加载同卷 fixed_template 节点并仲裁模板归属; 失败时返回 None(按现状放行)。"""
    if not snapshot:
        return None
    try:
        all_nodes = await composer._load_all_outline_nodes(node.project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fixed_template 归属仲裁加载节点失败, 放行: {}", exc)
        return None
    node_volume = str(getattr(node.volume_type, "value", node.volume_type) or "")
    candidates = [
        item for item in all_nodes
        if str(getattr(item.node_type, "value", item.node_type) or "") == "fixed_template"
        and str(getattr(item.volume_type, "value", item.volume_type) or "") == node_volume
    ]
    return select_owner_node(candidates, snapshot)


def _extract_scoring_hint(clause: Any) -> Optional[str]:
    """从 scoring_criterion 约束提取简短提示文本，供 fixed_template trace 消费。

    不调用 LLM / build_clause_directives；仅做纯字符串组合，保持 fixed_template 路径轻量。
    返回 None 表示该约束不含有效评分提示。
    """
    meta = getattr(clause, "metadata_json", None)
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("score_item_name") or getattr(clause, "content", "") or "").strip()
    if not name:
        return None
    rules = meta.get("score_rules")
    if isinstance(rules, list):
        rules_text = "；".join(str(r) for r in rules if r)
    else:
        rules_text = str(rules or "").strip()
    return f"{name}：{rules_text}" if rules_text else name


def _is_template_source_usable(
    node_title: str,
    template: str,
    snapshot: Optional[dict],
) -> bool:
    """校验模板源是否可被当前 fixed_template 节点消费。: catalog 出口已经做了规则+LLM 校验; 这里仅承担最后兜底
 - 拒绝目录/泛化格式要求
 - 校验 snapshot.template_key 与节点标题一致
 - snapshot.source ∈ {rule_then_llm, llm_only, rule_only} 均允许进入,
 但 source=rule_only 时调用方应额外打 needs_review gate flag
 """
    if is_directory_like(template) or is_generic_template_requirement(template):
        return False
    snap = snapshot or {}
    title = str(snap.get("title") or "")
    template_key = snap.get("template_key")
    if title and not match_template_title(node_title, title, template_key).matched:
        return False
    source = str(snap.get("source") or "")
    if source and source not in {"rule_then_llm", "llm_only", "rule_only", "chunk_range"}:
        return False
    slots = len(extract_field_slots(template)) + len(extract_signature_slots(template))
    return slots >= 2


def replace_placeholders(template: str) -> str:
    """替换模板占位符（私有方法保留以兼容内部调用，逻辑委托给模块级函数）"""
    from app.core.writer.section_composer import sanitize_residual_placeholders
    return sanitize_residual_placeholders(template)


# ---------------------------------------------------------------------------
# 图片结构化字段回填
# ---------------------------------------------------------------------------


def autofill_structured_fields(
    template: str,
    images: list[WritingMaterialImage],
    node_title: str = "",
) -> str:
    """用图片的结构化字段自动替换模板中的 [待补充:xxx] 占位符

    白名单控制: 只有当图片的 image_type 与章节标题语义兼容时,
    才允许结构化字段回填, 避免跨类型污染(如营业执照字段填入身份证模板)。
    """
    title_lower = (node_title or "").lower()

    for img in images:
        if not img.structured_fields:
            continue

        img_type = img.image_type.value if img.image_type else ""
        if img_type and title_lower and not is_autofill_compatible(
            img_type, title_lower,
        ):
            logger.debug(
                f"结构化字段跳过: image_type={img_type} 与章节 {node_title!r} 不兼容"
            )
            continue

        try:
            fields = json.loads(img.structured_fields)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(fields, dict):
            continue

        for key, value in fields.items():
            if not value or not isinstance(value, str):
                continue
            template = template.replace(f"[待补充:{key}]", value)
            template = template.replace(f"[待补充: {key}]", value)

    return template


def is_autofill_compatible(image_type: str, node_title_lower: str) -> bool:
    """检查 image_type 的结构化字段是否允许回填到当前章节"""
    allowed_kws = AUTOFILL_TYPE_WHITELIST.get(image_type, [])
    if not allowed_kws:
        return True
    return any(kw in node_title_lower for kw in allowed_kws)


def append_figure_tags(
    html: str, images: list[WritingMaterialImage],
) -> str:
    """在内容末尾附加图片 <figure> 标记"""
    from app.core.writer.identity_image_layout import build_identity_image_layout

    identity_layout = build_identity_image_layout(images)
    identity_ids = {str(image_id) for image_id in identity_layout.ordered_image_ids}
    if identity_ids:
        html = re.sub(
            r'<figure\s+[^>]*data-image-id=["\'](\d+)["\'][^>]*>.*?</figure>\s*',
            lambda match: "" if match.group(1) in identity_ids else match.group(0),
            html or "",
            flags=re.DOTALL,
        )

    existing_image_ids = set(re.findall(r'data-image-id=["\'](\d+)["\']', html or ""))
    figure_parts: list[str] = []
    identity_images = list(identity_layout.ordered_images)
    identity_image_ids = {getattr(img, "id", None) for img in identity_images}
    ordered_images = [
        img for img in images if getattr(img, "id", None) not in identity_image_ids
    ] + identity_images
    for img in ordered_images:
        img_id = str(img.id)
        if img_id in existing_image_ids and img_id not in identity_ids:
            continue
        caption = img.caption or f"附件图片{img.image_index + 1}"
        figure_parts.append(
            f'<figure data-image-id="{img.id}">'
            f'<figcaption>{caption}</figcaption>'
            f'</figure>'
        )
    if figure_parts:
        html = html + "\n" + "\n".join(figure_parts)
    return html


# ---------------------------------------------------------------------------
# 模板净化（招标 PDF 复读断裂模板的修复路径）
# ---------------------------------------------------------------------------


def normalize_fixed_template_text(template: str) -> str:
    """净化招标固定模板中的断裂编号、占位符和跨行噪音。"""
    lines = [re.sub(r"\s+", " ", (line or "").strip()) for line in template.splitlines()]
    lines = [line for line in lines if line]
    normalized: list[str] = []
    i = 0

    while i < len(lines):
        line = normalize_template_stub_spacing(lines[i])
        next_line = normalize_template_stub_spacing(lines[i + 1]) if i + 1 < len(lines) else ""

        if line in {"( )", "()", "", ""}:
            i += 1
            continue
        if re.fullmatch(r"\d+\s*[¥￥]", line):
            i += 1
            continue
        if line.isdigit() and next_line.startswith("）"):
            i += 1
            continue

        if "第 、 项资料" in line and re.fullmatch(r"\d+\s+\d+", next_line):
            left, right = next_line.split()
            line = line.replace("第 、 项资料", f"第{left}、{right}项资料")
            i += 1
            next_line = normalize_template_stub_spacing(lines[i + 1]) if i + 1 < len(lines) else ""

        if "第 项资料" in line and next_line.isdigit():
            line = line.replace("第 项资料", f"第{next_line}项资料")
            i += 1
            next_line = normalize_template_stub_spacing(lines[i + 1]) if i + 1 < len(lines) else ""

        if line.startswith("附："):
            item_parts = [re.sub(r"附：\s*、", "1、", line).replace("附：", "", 1).strip()]
            j = i + 1
            while j < len(lines):
                candidate = normalize_template_stub_spacing(lines[j])
                candidate_next = normalize_template_stub_spacing(lines[j + 1]) if j + 1 < len(lines) else ""
                if candidate.isdigit() and candidate_next.startswith("、"):
                    item_no = len(item_parts) + 1
                    item_parts.append(f"{item_no}、{candidate_next.lstrip('、').strip()}")
                    j += 2
                    continue
                if candidate.startswith("、"):
                    item_parts.append(candidate.lstrip("、").strip())
                    j += 1
                    continue
                if candidate.isdigit():
                    j += 1
                    continue
                break
            normalized.append(f"附：{'；'.join(part for part in item_parts if part)}")
            i = j
            continue

        if line.startswith("）"):
            line, consumed_next = normalize_template_parenthesized_item(line, next_line)
            if consumed_next:
                i += 1
            if not line:
                i += 1
                continue
            normalized.append(line)
            i += 1
            continue

        if TEMPLATE_NUMBER_STUB_RE.fullmatch(line) and next_line:
            normalized.append(f"{line} {next_line}")
            i += 2
            continue

        if normalized and should_join_template_line(normalized[-1], line):
            normalized[-1] = f"{normalized[-1]}{line}"
        elif not TEMPLATE_SYMBOL_ONLY_RE.fullmatch(line):
            normalized.append(line)
        i += 1

    return "\n".join(normalized)


def normalize_template_parenthesized_item(
    line: str, next_line: str,
) -> tuple[str, bool]:
    """修复断裂的模板子项，只恢复结构，不在此处拍板中文层级。"""
    text = re.sub(r"^[）)]\s*", "", line).strip()
    consumed_next = False
    stub_match = TEMPLATE_NUMBER_STUB_RE.fullmatch(next_line)
    if stub_match:
        item_no = int(stub_match.group(1))
        suffix_value = (stub_match.group(2) or "").strip()
        text = inject_template_suffix_value(text, suffix_value)
        if item_no > 0:
            text = f"{format_number_for_depth(3, item_no - 1)}{text}"
        consumed_next = True
    return text, consumed_next


def inject_template_suffix_value(text: str, suffix_value: str) -> str:
    if not suffix_value:
        return text
    if "为 个" in text:
        return text.replace("为 个", f"为{suffix_value}个", 1)
    if "为  个" in text:
        return text.replace("为  个", f"为{suffix_value}个", 1)
    if " 个" in text:
        return text.replace(" 个", f"{suffix_value}个", 1)
    return f"{text}{suffix_value}"


def normalize_template_stub_spacing(line: str) -> str:
    line = re.sub(r"(?<=\d)\s+[.．、]", lambda m: m.group(0).strip(), line)
    line = re.sub(r"([.．、])\s+", r"\1 ", line)
    return line.strip()


def should_join_template_line(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if previous.endswith(("。", "；", "：", ":", "）", ")", "？", "！")):
        return False
    if re.match(r"^(致：|注：|附：|地址：|电子邮箱：|电话：|传真：|投 标 人：|法定代表人)", current):
        return False
    if current.startswith(("一、", "二、", "三、", "四、", "五、", "（", "1", "2", "3", "4", "5", "6")):
        return False
    if TEMPLATE_SYMBOL_ONLY_RE.fullmatch(current):
        return False
    return True


# ---------------------------------------------------------------------------
# 表格化检测与重建
# ---------------------------------------------------------------------------


def is_tabular_fixed_template(title: str, template: str) -> bool:
    clean_title = strip_existing_number(title or "")
    if any(keyword in clean_title for keyword in TABULAR_TEMPLATE_TITLE_KEYWORDS):
        return True
    normalized = re.sub(r"\s+", " ", template or "")
    return (
        "项目名称" in normalized
        and "报价" in normalized
        and "备注" in normalized
    )


def rebuild_tabular_fixed_template(title: str, template: str) -> str:
    clean_title = strip_existing_number(title or "")
    if "开标一览表" in clean_title:
        return rebuild_bid_opening_table_html(template)
    if "投标函附录" in clean_title:
        return rebuild_generic_table_html(
            template,
            headers=("序号", "条款名称", "投标响应", "备注"),
            row_placeholders=(
                "[待补充:序号]",
                "[待补充:条款名称]",
                "[待补充:投标响应]",
                "[待补充:备注]",
            ),
        )
    return rebuild_generic_table_html(
        template,
        headers=("项目名称", "报价", "备注"),
        row_placeholders=(
            "[待补充:项目名称]",
            "[待补充:报价]",
            "[待补充:备注]",
        ),
    )


def rebuild_bid_opening_table_html(template: str) -> str:
    lines = split_template_lines(template)
    preface_lines: list[str] = []
    note_lines: list[str] = []
    numbered_lines: list[str] = []
    residual_lines: list[str] = []
    table_hints: list[str] = []

    for line in lines:
        if line.startswith("注："):
            note_lines.append(normalize_table_note(line))
            continue
        if looks_like_bid_opening_table_fragment(line):
            table_hints.append(line)
            continue
        if looks_like_numbered_line(line):
            numbered_lines.append(line)
            continue
        if (
            not note_lines
            and not numbered_lines
            and ("招标人名称" in line or "报价如下" in line)
        ):
            preface_lines.append(line)
            continue
        residual_lines.append(line)

    quote_placeholder = "[待补充:报价]"
    if any("%" in line or "税率" in line for line in table_hints):
        quote_placeholder = "[待补充:报价(%)]"

    remark_text = next(
        (
            line for line in table_hints
            if any(keyword in line for keyword in ("最高限价", "税率", "不含税"))
        ),
        "[待补充:备注]",
    )
    remark_text = normalize_placeholder_text(remark_text)

    parts = [f"<p>{escape(line)}</p>" for line in preface_lines]
    parts.append(
        "<table>"
        "<thead><tr><th>项目名称</th><th>报价</th><th>备注</th></tr></thead>"
        "<tbody><tr>"
        "<td>[待补充:项目名称]</td>"
        f"<td>{escape(quote_placeholder)}</td>"
        f"<td>{escape(remark_text)}</td>"
        "</tr></tbody>"
        "</table>"
    )
    for line in note_lines + numbered_lines + residual_lines:
        parts.append(f"<p>{escape(line)}</p>")
    return "\n".join(parts)


def rebuild_generic_table_html(
    template: str,
    *,
    headers: tuple[str, ...],
    row_placeholders: tuple[str, ...],
) -> str:
    lines = split_template_lines(template)
    preface_lines: list[str] = []
    note_lines: list[str] = []
    numbered_lines: list[str] = []
    residual_lines: list[str] = []

    for line in lines:
        if line.startswith("注："):
            note_lines.append(normalize_table_note(line))
            continue
        if looks_like_numbered_line(line):
            numbered_lines.append(line)
            continue
        if looks_like_generic_table_fragment(line, headers):
            continue
        if not note_lines and not numbered_lines and ("：") in line:
            preface_lines.append(line)
            continue
        residual_lines.append(line)

    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    row_html = "".join(
        f"<td>{escape(normalize_placeholder_text(cell))}</td>"
        for cell in row_placeholders
    )
    parts = [f"<p>{escape(line)}</p>" for line in preface_lines]
    parts.append(
        "<table>"
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody><tr>{row_html}</tr></tbody>"
        "</table>"
    )
    for line in note_lines + numbered_lines + residual_lines:
        parts.append(f"<p>{escape(line)}</p>")
    return "\n".join(parts)


def split_template_lines(template: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", (line or "").strip())
        for line in (template or "").splitlines()
        if (line or "").strip()
    ]


def normalize_table_note(line: str) -> str:
    normalized = re.sub(r"^注[:：]\s*[、，,.;；]*\s*", "注：", line)
    return normalized if normalized != "注：" else "注：[待补充:说明]"


def looks_like_numbered_line(line: str) -> bool:
    return bool(re.match(r"^[（(]?[一二三四五六七八九十\d]+[)）]?[、.]?\s*", line))


def looks_like_bid_opening_table_fragment(line: str) -> bool:
    normalized = re.sub(r"\s+", "", line)
    if normalized in {"项目%", "项目"}:
        return True
    return (
        ("项目名称" in line and "报价" in line)
        or ("报价" in line and "备注" in line)
        or any(keyword in line for keyword in ("最高限价", "税率", "不含税"))
    )


def looks_like_generic_table_fragment(
    line: str,
    headers: tuple[str, ...],
) -> bool:
    matched_headers = sum(1 for header in headers if header in line)
    return matched_headers >= max(2, len(headers) // 2)


def normalize_placeholder_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    return cleaned or "[待补充]"
