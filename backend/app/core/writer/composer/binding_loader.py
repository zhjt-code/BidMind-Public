"""SectionComposer 绑定 / 章节 / 约束加载子模块（Task 6 Step 4）

承载 16 个加载方法：

目录加载：
- ``load_all_outline_nodes`` / ``load_outline_leaves``
- ``sort_by_reading_order``

行业画像 / 草稿：
- ``load_industry_profile`` / ``load_current_draft``
- ``build_previous_summaries``

绑定加载（含父节点继承）：
- ``load_bound_materials`` / ``load_bound_clauses``
- ``load_ancestor_node_ids``

绑定状态过滤（staticmethod / classmethod 等价物）：
- ``status_to_str`` / ``select_consumable_bindings`` / ``collect_binding_targets``

固定模板查找：
- ``find_bound_clause`` / ``find_fallback_template_clause``
- ``normalize_fixed_template_lookup`` / ``extract_fixed_template_keywords``

主类 thin forwarder 通过 ``composer._X`` 路径走回这些方法，保留：
- 测试 ``SectionComposer._collect_binding_targets`` 直接 classmethod 访问；
- 测试 ``composer._load_bound_materials = AsyncMock(...)`` 实例 monkey patch。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger
from sqlalchemy import select

from app.core.writer.binding_consumption_policy import (
    collect_confirmed_material_ids,
)
from app.core.writer.binding_consumption_policy import (
    select_consumable_bindings as _policy_select_consumable_bindings,
)
from app.core.writer.binding_consumption_policy import (
    status_to_str as _policy_status_to_str,
)
from app.core.writer.fixed_template import infer_fixed_template_key, match_template_title
from app.core.writer.fixed_template.features import is_generic_template_requirement
from app.core.writer.fixed_template.titles import is_custom_template_key
from app.core.writer.fixed_template_contract import resolve_fixed_template_source
from app.core.writer.numbering import strip_existing_number
from app.models.writing_binding import BindingStatus, BindingType, WritingBinding
from app.models.writing_clause import WritingClause
from app.models.writing_material import WritingMaterial
from app.models.writing_outline_node import WritingOutlineNode
from app.models.writing_section_draft import WritingSectionDraft

if TYPE_CHECKING:
    from app.core.writer.section_composer import SectionComposer


# ---------------------------------------------------------------------------
# 目录节点
# ---------------------------------------------------------------------------


async def load_all_outline_nodes(
    composer: "SectionComposer", project_id: int,
) -> list[WritingOutlineNode]:
    """加载项目的所有目录节点(用于编号计算等)"""
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
            )
        )
        return list(result.scalars().all())


async def load_outline_leaves(
    composer: "SectionComposer", project_id: int,
) -> list[WritingOutlineNode]:
    """加载叶子节点（无子节点的节点）"""
    all_nodes = await composer._load_all_outline_nodes(project_id)
    parent_ids = {n.parent_id for n in all_nodes if n.parent_id is not None}
    return [n for n in all_nodes if n.id not in parent_ids]


def sort_by_reading_order(
    nodes: list[WritingOutlineNode],
) -> list[WritingOutlineNode]:
    """按深度优先阅读顺序排序"""
    return sorted(nodes, key=lambda n: (n.depth, n.sort_order))


# ---------------------------------------------------------------------------
# 行业画像 / 草稿
# ---------------------------------------------------------------------------


async def load_industry_profile(
    composer: "SectionComposer", project_id: int,
) -> Optional[str]:
    """加载行业画像文本"""
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.sub_type == "industry_profile",
            ).limit(1)
        )
        clause = result.scalars().first()
    return clause.content if clause else None


async def load_current_draft(
    composer: "SectionComposer", node_id: int,
) -> Optional[WritingSectionDraft]:
    """加载当前版本草稿"""
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingSectionDraft).where(
                WritingSectionDraft.outline_node_id == node_id,
                WritingSectionDraft.is_current == 1,
            ).limit(1)
        )
        return result.scalars().first()


# ---------------------------------------------------------------------------
# 绑定状态过滤（无状态纯函数）
# ---------------------------------------------------------------------------


# 绑定消费策略单一真源: binding_consumption_policy。此处保留同名转发，
# 维持 SectionComposer._status_to_str / _select_consumable_bindings 契约与
# 本模块既有引用不变（: 生成与渲染共享同一策略）。
status_to_str = _policy_status_to_str
select_consumable_bindings = _policy_select_consumable_bindings


def collect_binding_targets(
    binding_groups: list[list[WritingBinding]],
    target_attr: str,
) -> list[int]:
    target_ids: list[int] = []
    seen: set[int] = set()
    for bindings in binding_groups:
        for binding in select_consumable_bindings(bindings):
            target_id = getattr(binding, target_attr, None)
            if target_id is None or target_id in seen:
                continue
            seen.add(target_id)
            target_ids.append(target_id)
    return target_ids


# ---------------------------------------------------------------------------
# 绑定加载（含祖先继承）
# ---------------------------------------------------------------------------


async def load_bound_materials(
    composer: "SectionComposer",
    node_id: int,
    *,
    node_title: str = "",
    chapter_profile: Optional[dict[str, Any]] = None,
) -> list[WritingMaterial]:
    """加载章节绑定的素材

    直接绑定始终可见；当目录被丰富拆成父子节点时，父节点上的
    material_to_node 绑定会按章节语义受控继承到叶子章节，避免
    “绑定存在但叶子章节生成时读不到素材”。
    """
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingBinding).where(
                WritingBinding.outline_node_id == node_id,
                WritingBinding.material_id.isnot(None),
                WritingBinding.binding_type == BindingType.material_to_node,
            )
        )
        bindings = list(result.scalars().all())
        # 素材绑定逐边只消费 confirmed，一条 rejected 边不影响同节点其他边。
        direct_groups: list[list[WritingBinding]] = [bindings]
        direct_material_ids = collect_confirmed_material_ids(direct_groups)

        inherited_groups: list[list[WritingBinding]] = []
        ancestor_node_ids = await composer._load_ancestor_node_ids(node_id)
        if ancestor_node_ids:
            inherited_result = await composer.db.execute(
                select(WritingBinding).where(
                    WritingBinding.outline_node_id.in_(ancestor_node_ids),
                    WritingBinding.material_id.isnot(None),
                    WritingBinding.binding_type == BindingType.material_to_node,
                )
            )
            inherited_bindings = list(inherited_result.scalars().all())
            inherited_by_node: dict[int, list[WritingBinding]] = defaultdict(list)
            for binding in inherited_bindings:
                inherited_by_node[binding.outline_node_id].append(binding)
            for ancestor_node_id in ancestor_node_ids:
                group = inherited_by_node.get(ancestor_node_id)
                if group:
                    inherited_groups.append(group)

        inherited_material_ids = collect_confirmed_material_ids(inherited_groups)
        material_ids = composer._dedupe_ids(direct_material_ids + inherited_material_ids)
        if not material_ids:
            return []
        confirmed_material_ids = {
            binding.material_id
            for group in direct_groups + inherited_groups
            for binding in group
            if binding.material_id is not None
            and composer._status_to_str(binding.status) == BindingStatus.confirmed.value
        }

        mat_result = await composer.db.execute(
            select(WritingMaterial).where(WritingMaterial.id.in_(material_ids))
        )
        materials = [
            material
            for material in mat_result.scalars().all()
            if composer._is_material_consumable(material)
            and (
                getattr(material, "kb_source_id", None) is None
                or material.id in confirmed_material_ids
            )
        ]
        material_map = {material.id: material for material in materials}

        inherited_materials = composer._filter_inherited_materials_for_node(
            [material_map[mid] for mid in inherited_material_ids if mid in material_map],
            node_title=node_title,
            chapter_profile=chapter_profile,
        )
        inherited_keep_ids = {material.id for material in inherited_materials}
        ordered_materials: list[WritingMaterial] = []
        seen: set[int] = set()
        for material_id in direct_material_ids:
            material = material_map.get(material_id)
            if material and material.id not in seen:
                ordered_materials.append(material)
                seen.add(material.id)
        for material_id in inherited_material_ids:
            material = material_map.get(material_id)
            if (
                material
                and material.id in inherited_keep_ids
                and material.id not in seen
            ):
                ordered_materials.append(material)
                seen.add(material.id)

        if inherited_keep_ids:
            logger.info(
                "章节继承父节点素材: node_id={}, title={!r}, direct={}, inherited={}",
                node_id,
                node_title,
                len(direct_material_ids),
                len(inherited_keep_ids),
            )
        return ordered_materials


async def load_bound_clauses(
    composer: "SectionComposer",
    node_id: int,
    *,
    include_inherited: bool = True,
) -> list[WritingClause]:
    """加载某章节内容生成时应消费的 clause_to_node 绑定约束。

 职责: 汇总该章节生成上下文与消费记录(source_clause_ids)应覆盖的全部约束。
 不做: 不加载素材、不做约束分类/作用域判定、不修改任何绑定状态。

 Args:
 include_inherited: True(默认)时在直接绑定之外并入
 (a) 祖先节点上 scope=global/volume_shared 的继承绑定;
 (b) 项目级 scope=global 无节点绑定(outline_node_id 为 NULL,
 matcher 落库的全局约束,)。
 False 时仅返回本节点直接绑定, 供 fixed_template 回填 source_clause_ids
 使用(占位/降级路径不应虚报继承约束消费)。

 注意: scope=orphan 的无节点绑定语义为"待人工分配", 任何情况下都不自动进入
 生成消费, 否则会把未确认归属的约束混入正文来源。
 """
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingBinding).where(
                WritingBinding.outline_node_id == node_id,
                WritingBinding.clause_id.isnot(None),
                WritingBinding.binding_type == BindingType.clause_to_node,
            )
        )
        direct_bindings = list(result.scalars().all())
        binding_groups: list[list[WritingBinding]] = [direct_bindings]

        if include_inherited:
            ancestor_node_ids = await composer._load_ancestor_node_ids(node_id)
            if ancestor_node_ids:
                inherited_result = await composer.db.execute(
                    select(WritingBinding).where(
                        WritingBinding.outline_node_id.in_(ancestor_node_ids),
                        WritingBinding.clause_id.isnot(None),
                        WritingBinding.binding_type == BindingType.clause_to_node,
                        WritingBinding.scope.in_(("global", "volume_shared")),
                    )
                )
                inherited_bindings = list(inherited_result.scalars().all())
                inherited_by_node: dict[int, list[WritingBinding]] = defaultdict(list)
                for binding in inherited_bindings:
                    inherited_by_node[binding.outline_node_id].append(binding)
                for ancestor_node_id in ancestor_node_ids:
                    group = inherited_by_node.get(ancestor_node_id)
                    if group:
                        binding_groups.append(group)

            # matcher 把全局约束以 outline_node_id=NULL, scope='global'
            # 落库并注释"内容生成阶段需消费", 但此前无任何查询能命中这类无节点
            # 绑定, 导致全局约束(含废标条款)从未进入生成上下文。此处按项目补加载,
            # 兑现"写入即消费"契约; orphan 无节点绑定不在此列(待人工分配)。
            node = await composer.db.get(WritingOutlineNode, node_id)
            if node is not None:
                global_result = await composer.db.execute(
                    select(WritingBinding).where(
                        WritingBinding.project_id == node.project_id,
                        WritingBinding.outline_node_id.is_(None),
                        WritingBinding.clause_id.isnot(None),
                        WritingBinding.binding_type == BindingType.clause_to_node,
                        WritingBinding.scope == "global",
                    )
                )
                global_bindings = list(global_result.scalars().all())
                if global_bindings:
                    binding_groups.append(global_bindings)

        clause_ids = composer._collect_binding_targets(binding_groups, "clause_id")
        if not clause_ids:
            return []

        cl_result = await composer.db.execute(
            select(WritingClause).where(WritingClause.id.in_(clause_ids))
        )
        return list(cl_result.scalars().all())


async def load_ancestor_node_ids(
    composer: "SectionComposer", node_id: int,
) -> list[int]:
    ancestor_ids: list[int] = []
    current_id = node_id
    while True:
        node = await composer.db.get(WritingOutlineNode, current_id)
        if not node or node.parent_id is None:
            break
        ancestor_ids.append(node.parent_id)
        current_id = node.parent_id
    return ancestor_ids


# ---------------------------------------------------------------------------
# 固定模板查找
# ---------------------------------------------------------------------------


async def find_bound_clause(
    composer: "SectionComposer",
    node_id: int,
    sub_type: str,
    node_title: str = "",
    node_volume_type: Optional[str] = None,
) -> Optional[WritingClause]:
    """查找绑定的特定子类型约束"""
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingClause, WritingBinding.confidence, WritingBinding.id).join(
                WritingBinding,
                WritingBinding.clause_id == WritingClause.id,
            ).where(
                WritingBinding.outline_node_id == node_id,
                WritingBinding.clause_id.isnot(None),
                WritingBinding.binding_type == BindingType.clause_to_node,
                WritingClause.sub_type == sub_type,
            ).order_by(
                WritingBinding.confidence.desc(),
                WritingBinding.id.asc(),
                WritingClause.sort_order.asc(),
                WritingClause.id.asc(),
            )
        )
        matches = result.all()
        filtered = [
            (clause, confidence, binding_id)
            for clause, confidence, binding_id in matches
            if _is_acceptable_template_clause(node_title, clause, node_volume_type)
        ]
        if not filtered:
            logger.warning(
                "fixed_template 绑定未命中: node_id={}, title={!r}, sub_type={}, reason=no_matching_concrete_template",
                node_id,
                node_title,
                sub_type,
            )
            return None

        clause, confidence, binding_id = filtered[0]
        logger.info(
            "fixed_template 绑定命中: node_id={}, title={!r}, clause_id={}, binding_id={}, matched_count={}, top_confidence={}",
            node_id,
            node_title,
            clause.id,
            binding_id,
            len(filtered),
            confidence,
        )
        return clause


async def find_fallback_template_clause(
    composer: "SectionComposer",
    project_id: int,
    node_title: str,
    node_volume_type: Optional[str] = None,
) -> Optional[WritingClause]:
    clean_title = strip_existing_number(node_title)
    normalized_title = composer._normalize_fixed_template_lookup(clean_title)
    title_keywords = composer._extract_fixed_template_keywords(clean_title)
    if not normalized_title and not title_keywords:
        return None

    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.sub_type == "fixed_template",
            ).order_by(
                WritingClause.sort_order.asc(),
                WritingClause.id.asc(),
            )
        )
        clauses = list(result.scalars().all())

    # 主路径按 template_key 推断命中, 解决 LLM/cleaner 已剥离首行标题
    # 后 raw_text 不含节点标题字符串导致 fallback 永久失败的问题。
    node_inferred_key = infer_fixed_template_key(node_title)
    if node_inferred_key:
        for clause in clauses:
            if not _is_acceptable_template_clause(node_title, clause, node_volume_type):
                continue
            metadata = getattr(clause, "metadata_json", None) or {}
            snapshot = metadata.get("fixed_template_snapshot") or {}
            clause_key = snapshot.get("template_key") or metadata.get("template_key")
            if clause_key == node_inferred_key:
                logger.warning(
                    "fixed_template 回退命中: title={!r}, clause_id={}, reason=template_key_match",
                    node_title,
                    clause.id,
                )
                return clause

    for clause in clauses:
        if not _is_acceptable_template_clause(node_title, clause, node_volume_type):
            continue
        haystack = composer._normalize_fixed_template_lookup(
            f"{clause.content or ''} {clause.raw_text or ''}"
        )
        if normalized_title and normalized_title in haystack:
            logger.warning(
                "fixed_template 回退命中: title={!r}, clause_id={}, reason=title_substring_match",
                node_title,
                clause.id,
            )
            return clause

    for clause in clauses:
        if not _is_acceptable_template_clause(node_title, clause, node_volume_type):
            continue
        haystack = composer._normalize_fixed_template_lookup(
            f"{clause.content or ''} {clause.raw_text or ''}"
        )
        if title_keywords and title_keywords & composer._extract_fixed_template_keywords(haystack):
            logger.warning(
                "fixed_template 回退命中: title={!r}, clause_id={}, reason=keyword_overlap",
                node_title,
                clause.id,
            )
            return clause
    return None


def _is_acceptable_template_clause(
    node_title: str,
    clause: WritingClause,
    node_volume_type: Optional[str] = None,
) -> bool:
    """校验 fixed_template 候选是否为当前节点的具体模板。: 候选必须满足
 - 不是泛化格式要求
 - snapshot.template_key 与节点标题映射一致
 - snapshot.is_complete_candidate == True (即 catalog/LLM 校验通过)
 旧的"按 content 关键词模糊兜底"通道一律拒绝, 防止伪标题混入。

 守门: 当 snapshot.volume_type 与 node_volume_type 都非空时,
 必须严格一致, 防止商务标节点拿到技术标投标函模板。
 """
    source_text, snapshot = resolve_fixed_template_source(clause)
    # clause.content 是 LLM 概括文案(可能含"全部格式"等泛化措辞),
    # 有快照时泛化检查只看真正会被消费的模板源文本, 避免措辞差异误杀具体模板;
    # 无快照时维持旧行为(content 是仅有的判断依据)。
    generic_probe = (
        source_text if snapshot else f"{clause.content or ''}\n{source_text}"
    )
    if is_generic_template_requirement(generic_probe):
        return False
    metadata = getattr(clause, "metadata_json", None)
    template_key = None
    title = ""
    snapshot_volume = None
    if isinstance(metadata, dict):
        template_key = metadata.get("template_key")
        title = str(metadata.get("target_section_hint") or "")
    if snapshot:
        template_key = snapshot.get("template_key") or template_key
        title = str(snapshot.get("title") or title)
        if not snapshot.get("is_complete_candidate"):
            # source=rule_only 的规则兜底 entry 走例外放行,
            # 由 template_route 自动打 fixed_template_review_gate 提示人工核对;
            # 词典外 custom key(置信度封顶)同走例外通道 + 人工 gate;
            # 其他 incomplete (例如 LLM 漏字段) 仍然拒绝, 避免幻觉模板透传。
            if snapshot.get("source") != "rule_only" and not is_custom_template_key(
                template_key or snapshot.get("template_key")
            ):
                return False
        snapshot_volume = snapshot.get("volume_type")
    else:
        # 没有 snapshot 视为非 catalog 命中, 旧的弱兜底通道一律拒绝
        return False
    if not template_key:
        return False
    if node_volume_type and snapshot_volume and node_volume_type != snapshot_volume:
        return False
    title = title or str(getattr(clause, "content", "") or "")
    return match_template_title(node_title, title, template_key).matched


def normalize_fixed_template_lookup(text: str) -> str:
    normalized = strip_existing_number((text or "").strip()).lower()
    normalized = re.sub(r"[（()）【】\[\]\s]", "", normalized)
    normalized = normalized.replace("及", "").replace("与", "").replace("和", "")
    for suffix in ("格式", "模板", "表格", "文件", "内容"):
        normalized = normalized.replace(suffix, "")
    return normalized


def extract_fixed_template_keywords(text: str) -> set[str]:
    normalized = normalize_fixed_template_lookup(text)
    keywords = set(re.findall(r"[\u4e00-\u9fff]{2,}", normalized))
    compound_terms = (
        "投标函", "投标函附录", "附录", "开标一览表",
        "报价书", "报价表", "授权委托书", "授权书",
        "声明函", "承诺书",
    )
    for term in compound_terms:
        if term in normalized:
            keywords.add(term)
    return keywords


# ---------------------------------------------------------------------------
# 前文摘要构建
# ---------------------------------------------------------------------------


async def build_previous_summaries(
    composer: "SectionComposer",
    project_id: int,
    current_node: WritingOutlineNode,
) -> list[dict[str, str]]:
    """构建当前节点之前所有章节的摘要列表"""
    async with composer._db_lock:
        result = await composer.db.execute(
            select(WritingSectionDraft).join(
                WritingOutlineNode,
                WritingSectionDraft.outline_node_id == WritingOutlineNode.id,
            ).where(
                WritingOutlineNode.project_id == project_id,
                WritingSectionDraft.is_current == 1,
                WritingSectionDraft.summary.isnot(None),
            ).order_by(WritingOutlineNode.depth, WritingOutlineNode.sort_order)
        )
        drafts = list(result.scalars().all())
    summaries: list[dict[str, str]] = []
    for d in drafts:
        if d.outline_node_id == current_node.id:
            break
        if d.summary:
            summaries.append({"title": f"node_{d.outline_node_id}", "summary": d.summary})
    return summaries
