"""目录树生成器

三级优先级路径:
1. 照搬模式: 招标文件有明确目录 -> 骨架锁定 + 子节点LLM丰富
2. 自拟模式: 无明确目录但有结构分析 -> 只生成技术标+商务标两卷
3. unified 兜底: 原始一次性LLM生成
"""

import asyncio
import json
import re
import time
from typing import Awaitable, Callable, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import LLMClient, get_review_client
from app.core.ai.input_guard import retry_on_input_too_long_400
from app.core.ai.prompt_registry import call_with_fallback, resolve_runtime_params
from app.core.writer.chapter_profiling.batching import (
    compute_coverage,
    split_leaves_into_batches,
)
from app.core.writer.chapter_profiling.quality import (
    ProfileQuality,
    assess_profile_data,
    is_matchable,
)
from app.core.writer.directory_spec_context import is_usable_directory_spec
from app.core.writer.fixed_template import infer_fixed_template_key
from app.core.writer.fixed_template.titles import custom_template_key
from app.core.writer.industry_profile_view import (
    PURPOSE_CHAPTER_PROFILE,
    build_profile_excerpt,
)
from app.core.writer.locator_volume import (
    dedup_catchall_among_siblings,
    filter_locator_toc_items,
    should_omit_volume_wrapper,
)
from app.core.writer.numbering import strip_existing_number
from app.core.writer.outline_scope import attach_scope_titles_to_outline
from app.core.writer.prompts.outline_generate import (
    get_outline_generate_prompt as _code_outline,
)
from app.core.writer.prompts.outline_generate import (
    get_section_fill_prompt as _code_section_fill,
)
from app.core.writer.prompts.outline_generate import (
    get_skeleton_enrich_prompt as _code_skeleton_enrich,
)
from app.core.writer.scoring_volume_affinity import volume_category_from_metadata
from app.core.writer.text_budget import make_priority_shrink_reducer
from app.models.writing_clause import WritingClause
from app.models.writing_material import WritingMaterial
from app.models.writing_outline_node import (
    OutlineNodeStatus,
    OutlineNodeType,
    WritingOutlineNode,
)
from app.schemas.writing import (
    BidDirectoryItem,
    BidDirectorySpec,
    BidStructureAnalysis,
    MaterialChecklistItem,
    OutlineNodeGenerated,
)

# NOTE: 以下 wrapper 直接走 prompt_registry.call_with_fallback,
# 见 section_composer 同名注释。


def get_outline_generate_prompt(*a, **kw):
    return call_with_fallback("writing.skeleton_generate", _code_outline, *a, **kw)

def get_section_fill_prompt(*a, **kw):
    return call_with_fallback("writing.section_fill", _code_section_fill, *a, **kw)

def get_skeleton_enrich_prompt(*a, **kw):
    return call_with_fallback("writing.skeleton_enrich", _code_skeleton_enrich, *a, **kw)


# 章节画像 prompt 的行业画像预算。实测画像正文 1781~4331 字符且评标关注点位于
# 1610 字符之后，原头部截断会整段丢弃该节，改由视图层按优先级选段。
_CHAPTER_PROFILE_INDUSTRY_CHARS = 1500

_DEFAULT_TOTAL_BUDGET = 30000
_DATA_FILL_WORD_COUNT = 500
_DATA_FILL_MIN_WORD_COUNT = 300
_DATA_FILL_MAX_WORD_COUNT = 3000
ProgressCallback = Callable[[int, int, str], None]
_FREE_WRITING_MIN_WORD_COUNT = 500
_FREE_WRITING_MAX_WORD_COUNT = 5000

_FIXED_TEMPLATE_KEYWORDS = [
    "投标函", "承诺书", "授权书", "授权委托书",
    "法定代表人声明", "法定代表人证明书", "法定代表人身份证明",
    "声明函", "投标保证金", "开标一览表", "报价书",
]
_DATA_FILL_KEYWORDS = [
    "人员配置表", "业绩汇总表", "设备清单", "报价表", "资质文件",
    "证书", "营业执照", "组织机构代码", "税务登记", "人员简历",
    "项目业绩", "类似项目", "准入证", "开户许可证", "执照",
    "复印件", "缴纳证明", "身份证明",
]
_FREE_WRITING_KEYWORDS = [
    "技术方案", "实施方案", "服务方案", "项目理解", "管理方案",
    "质量保证", "售后服务", "应急预案", "进度计划", "安全管理",
    "培训方案", "评分有关资料", "其他资料",
]


def _clause_metadata_suffix(clause: WritingClause) -> str:
    metadata = getattr(clause, "metadata_json", None)
    if not metadata:
        return ""
    keys = [
        "score_item_name", "max_score", "target_section_hint",
        "evidence_hint", "material_kind", "required_form",
        "applies_to", "is_mandatory",
    ]
    compact = {
        key: metadata.get(key)
        for key in keys
        if isinstance(metadata, dict) and metadata.get(key) not in (None, "", [], {})
    }
    if not compact:
        return ""
    return f" | metadata={json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}"


def _format_clause_for_prompt(clause: WritingClause) -> str:
    weight = f" ({clause.score_weight}分)" if clause.score_weight else ""
    return f"- {clause.content}{weight}{_clause_metadata_suffix(clause)}"


def _parse_nested_requirements(content: str) -> tuple[str, list[str]]:
    """解析包含嵌套列表的需求文本"""
    sub_items: list[str] = []
    content = content.strip()
    main_text = content

    colon_pattern = r'^(.+?[：:])(\s*\d+[.、．]\s*.+)$'
    match = re.match(colon_pattern, content, re.DOTALL)
    if match:
        main_text = match.group(1)
        list_part = match.group(2)
        sub_items = re.split(r'\s*\d+[.、．]\s*', list_part)
        sub_items = [s.strip().rstrip('；;，,。') for s in sub_items if s.strip()]
        return main_text, sub_items

    paren_pattern = r'[(\（](\d+)[)\）]\s*'
    if re.search(paren_pattern, content):
        first_match = re.search(paren_pattern, content)
        if first_match and first_match.start() > 5:
            main_text = content[:first_match.start()].strip()
            list_part = content[first_match.start():]
            sub_items = re.split(paren_pattern, list_part)
            sub_items = [
                s.strip().rstrip('；;，,。')
                for s in sub_items
                if s.strip() and not s.isdigit()
            ]
            return main_text, sub_items

    if '、' in content and content.count('、') >= 2:
        colon_pos = max(content.find('：'), content.find(':'))
        if colon_pos > 0:
            main_text = content[:colon_pos + 1]
            list_part = content[colon_pos + 1:]
            sub_items = [s.strip() for s in list_part.split('、') if s.strip()]
            if len(sub_items) >= 2:
                return main_text, sub_items

    return main_text, sub_items


class OutlineGenerator:
    """目录树生成器"""

    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        project_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self.llm_client = llm_client or get_review_client(project_id=project_id)
        # 当且仅当 fixed_template catalog 命中过该 template_key 时,
        # 才允许把节点升级为 fixed_template; 该集合在 generate() 入口加载。
        self._catalog_template_keys: set[str] = set()

    async def _chat_json_with_input_guard(
        self,
        *,
        operation_name: str,
        initial_payload: dict[str, str],
        render_prompt: Callable[[dict[str, str]], tuple[str, str]],
        reduce_payload_semantically: Callable[[dict[str, str], int], tuple[dict[str, str], dict[str, object] | None]],
        max_tokens: int,
        temperature: float,
    ):
        async def _send_request(payload: dict[str, str]):
            system_msg, user_msg = render_prompt(payload)
            return await self.llm_client.chat_json(
                user_message=user_msg,
                system_message=system_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        return await retry_on_input_too_long_400(
            operation_name=operation_name,
            initial_payload=initial_payload,
            send_request=_send_request,
            reduce_payload_semantically=reduce_payload_semantically,
            max_retries=1,
            describe_payload=lambda payload: {
                key: len(value)
                for key, value in payload.items()
                if isinstance(value, str)
            },
        )

    # ==================== 主入口 ====================

    async def generate(
        self,
        project_id: int,
        total_word_budget: int = _DEFAULT_TOTAL_BUDGET,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[WritingOutlineNode]:
        """生成目录树 -- 三级优先级路径

        P1: 照搬模式 -- 招标文件有明确目录
        P2: 自拟模式 -- 无明确目录但有结构分析
        P3: unified 兜底
        """
        logger.info(f"目录树生成启动: project={project_id}, 字数预算={total_word_budget}")

        await self._load_catalog_template_keys(project_id)
        dir_spec = await self._load_bid_directory_spec(project_id)
        if dir_spec and is_usable_directory_spec(dir_spec):
            logger.info(
                f"目录生成模式: 照搬 (volumes={len(dir_spec.volumes)}, "
                f"types={[v.volume_type for v in dir_spec.volumes]})"
            )
            return await self._generate_from_spec(
                project_id, dir_spec, total_word_budget, progress_callback,
            )
        if dir_spec and dir_spec.has_explicit_directory and dir_spec.volumes:
            logger.warning(
                "目录规格不可用, 跳过照搬模式: "
                f"project={project_id}, volumes={len(dir_spec.volumes)}"
            )

        structure = await self._load_bid_structure(project_id)
        if structure and structure.structure_mode != "unified":
            logger.info(
                f"目录生成模式: 自拟 ({structure.structure_mode})"
            )
            return await self._generate_self_composed(
                project_id, structure, total_word_budget, progress_callback,
            )

        logger.info("目录生成模式: unified (兜底)")
        return await self._generate_unified(project_id, total_word_budget, progress_callback)

    @staticmethod
    def _report_progress(
        progress_callback: Optional[ProgressCallback],
        current: int,
        total: int,
        message: str,
    ) -> None:
        """安全上报目录生成进度，回调异常不影响业务流程。"""

        if not progress_callback:
            return
        try:
            progress_callback(current, total, message)
        except Exception as e:
            logger.warning(f"目录生成进度回调失败: {e}")

    # ==================== P1: 照搬模式 ====================

    async def _generate_from_spec(
        self,
        project_id: int,
        spec: BidDirectorySpec,
        total_word_budget: int,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[WritingOutlineNode]:
        """照搬模式: 骨架锁定 + 叶子节点LLM丰富

        阶段1(纯算法): 按 spec 递归创建分卷顶层节点和全深度骨架子节点。
        格式章定位标题（如「投标文件格式」）不落成根节点，其下条目提升为 depth=0。
        阶段2(LLM丰富): 对没有 children 的叶子骨架节点并行丰富子目录
        """
        t0 = time.time()
        all_clauses = await self._load_all_confirmed_clauses(project_id)

        all_nodes: list[OutlineNodeGenerated] = []
        enrichable: list[tuple[OutlineNodeGenerated, int, str, list[str]]] = []
        catchall_groups: list[list[OutlineNodeGenerated]] = []

        for vol_idx, volume in enumerate(spec.volumes):
            items = filter_locator_toc_items(volume.items)
            sibling_titles = [it.title for it in items]
            # 空/空白卷名不得落成 OutlineNodeGenerated，省略包装保业务连续
            vol_title = (volume.volume_title or "").strip()
            omit_wrapper = should_omit_volume_wrapper(vol_title) or not vol_title
            if omit_wrapper:
                group: list[OutlineNodeGenerated] = []
                item_depth = 0
                item_volume_type = None
            else:
                vol_node = OutlineNodeGenerated(
                    title=vol_title,
                    depth=0,
                    sort_order=vol_idx,
                    node_type="free_writing",
                    is_required=1,
                    score_weight=None,
                    children=[],
                    is_skeleton=True,
                    volume_type=volume.volume_type,
                )
                group = vol_node.children
                item_depth = 1
                item_volume_type = volume.volume_type
                all_nodes.append(vol_node)

            for item_idx, item in enumerate(items):
                child = self._build_skeleton_recursive(
                    item, current_depth=item_depth, volume_type=item_volume_type,
                )
                child.sort_order = item_idx
                group.append(child)

                # 红线: 丰富仅限自由论述叶子, 产出子节点为非骨架可删;
                # fixed_template/data_fill 骨架及兜底容器一律不丰富
                is_leaf = not item.children
                is_catchall = any(
                    kw in item.title for kw in self._CATCHALL_KEYWORDS
                )
                if is_leaf and not is_catchall and child.node_type == "free_writing":
                    enrichable.append(
                        (child, item.order, vol_title, sibling_titles)
                    )

            if omit_wrapper:
                all_nodes.extend(group)
            catchall_groups.append(group)

        def _count_nodes(nodes: list[OutlineNodeGenerated]) -> int:
            return sum(1 + _count_nodes(n.children) for n in nodes)

        skeleton_count = _count_nodes(all_nodes)
        max_depth = max(
            (n.depth for n in self._collect_leaves(all_nodes)),
            default=0,
        )
        logger.info(
            f"照搬骨架: {skeleton_count}个节点, "
            f"最大深度={max_depth}, 待丰富={len(enrichable)}个"
        )

        if enrichable:
            enrich_total = len(enrichable)
            gen_total = enrich_total + 1
            self._report_progress(progress_callback, 0, gen_total, "骨架丰富启动")

            from app.core.config import get_review_concurrency

            enrich_sema = asyncio.Semaphore(max(int(get_review_concurrency()), 1))

            async def _run_enrich(
                node: OutlineNodeGenerated,
                order: int,
                vol_title: str,
                sibling_titles: list[str],
            ) -> str:
                async with enrich_sema:
                    try:
                        await self._enrich_skeleton_node(
                            node, order, vol_title, all_clauses, project_id,
                            sibling_titles,
                        )
                    except Exception as exc:
                        logger.warning(f"骨架丰富任务异常({node.title}): {exc}")
                    return node.title

            enrich_tasks = [
                _run_enrich(node, order, vol_title, sibling_titles)
                for node, order, vol_title, sibling_titles in enrichable
            ]
            completed = 0
            for future in asyncio.as_completed(enrich_tasks):
                title = await future
                completed += 1
                self._report_progress(
                    progress_callback,
                    completed,
                    gen_total,
                    f"骨架丰富 {completed}/{enrich_total}: {title}",
                )
        else:
            gen_total = 2
            self._report_progress(progress_callback, 1, gen_total, "生成目录结构")

        # 同组兜底节点子章节去重：标题已在非兜底兄弟中出现的非骨架子节点移除
        for group in catchall_groups:
            dedup_count = dedup_catchall_among_siblings(
                group, self._CATCHALL_KEYWORDS,
            )
            if dedup_count > 0:
                logger.info(f"目录兜底节点去重: 移除{dedup_count}个与其他章节重复的子节点")

        self._report_progress(
            progress_callback, gen_total - 1, gen_total, "实质内容标题挂载",
        )
        try:
            attached = await attach_scope_titles_to_outline(
                tree=all_nodes,
                db=self.db,
                llm_client=self.llm_client,
                project_id=project_id,
            )
            if attached:
                logger.info(f"实质内容标题挂载(照搬): {attached}条")
        except Exception as e:
            logger.warning(f"实质内容标题挂载失败(照搬): {e}")

        for node in all_nodes:
            self._override_node_type(node)

        self._assign_word_counts_tree(all_nodes, total_word_budget)

        self._report_progress(progress_callback, gen_total, gen_total, "写入目录")
        saved = await self._save_tree(project_id, all_nodes)
        elapsed = time.time() - t0
        logger.info(
            f"目录树生成完成(照搬模式): project={project_id}, "
            f"nodes={len(saved)}, 耗时={elapsed:.1f}s"
        )
        return saved

    def _build_skeleton_recursive(
        self,
        item: BidDirectoryItem,
        current_depth: int,
        volume_type: Optional[str] = None,
    ) -> OutlineNodeGenerated:
        """递归展开 BidDirectoryItem 为 OutlineNodeGenerated 树。

        对每个 item 创建骨架节点，如果 item.children 非空则递归构建子节点。
        """
        nt = self._infer_node_type(item.title)
        if item.node_type != "free_writing" and item.node_type_source != "fallback":
            nt = item.node_type

        node = OutlineNodeGenerated(
            title=item.title,
            depth=current_depth,
            sort_order=max(0, item.order - 1),
            node_type=nt,
            is_required=1,
            score_weight=None,
            children=[],
            is_skeleton=True,
            volume_type=volume_type,
        )

        for child_idx, child_item in enumerate(item.children or []):
            child_node = self._build_skeleton_recursive(
                child_item,
                current_depth=current_depth + 1,
                volume_type=volume_type,
            )
            child_node.sort_order = child_idx
            node.children.append(child_node)

        return node

    _CATCHALL_KEYWORDS = ["其他资料", "其他证明", "认为必要"]

    async def _enrich_skeleton_node(
        self,
        node: OutlineNodeGenerated,
        node_order: int,
        volume_title: str,
        all_clauses: list[WritingClause],
        project_id: int,
        sibling_titles: Optional[list[str]] = None,
    ) -> None:
        """LLM丰富单个骨架节点的子目录"""

        def _filter(sub_types: list[str]) -> str:
            items = [
                c for c in all_clauses
                if c.sub_type in sub_types
            ]
            if not items:
                return ""
            return "\n".join(_format_clause_for_prompt(c) for c in items)

        sibling_text = ""
        if sibling_titles:
            lines = []
            for idx, title in enumerate(sibling_titles, 1):
                marker = "【当前】" if title == node.title else ""
                lines.append(f"- 第{idx}项: {title} {marker}".rstrip())
            sibling_text = "\n".join(lines)

        initial_payload = {
            "relevant_scoring": _filter(["scoring_criterion"]),
            "relevant_content_reqs": _filter(["content_required"]),
            "relevant_materials": _filter(["material_required"]),
            "project_context": _filter(["project_context"]),
            "sibling_titles_text": sibling_text,
        }

        def _render_prompt(payload: dict[str, str]) -> tuple[str, str]:
            return get_skeleton_enrich_prompt(
                node_title=node.title,
                node_order=node_order,
                volume_title=volume_title,
                relevant_scoring=payload["relevant_scoring"],
                relevant_content_reqs=payload["relevant_content_reqs"],
                relevant_materials=payload["relevant_materials"],
                project_context=payload["project_context"],
                sibling_titles_text=payload["sibling_titles_text"],
            )

        _reduce_payload = make_priority_shrink_reducer(
            [
                ("relevant_materials", 1),
                ("relevant_content_reqs", 1),
                ("relevant_scoring", 1),
                ("project_context", 1),
                ("sibling_titles_text", 1),
            ],
            kept_segments=["node_title", "volume_title"],
        )

        t0 = time.time()
        try:
            temperature, max_tokens = resolve_runtime_params(
                "writing.skeleton_enrich", 0.1, 6000,
            )
            raw_result = await self._chat_json_with_input_guard(
                operation_name=f"目录骨架丰富({node.title})",
                initial_payload=initial_payload,
                render_prompt=_render_prompt,
                reduce_payload_semantically=_reduce_payload,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            logger.warning(f"骨架丰富失败({node.title}): {e}")
            return

        elapsed = time.time() - t0
        raw_list = raw_result if isinstance(raw_result, list) else []
        children = self._parse_nodes(raw_list)

        for child in children:
            child.is_skeleton = False
            child.volume_type = node.volume_type

        node.children = children
        logger.info(
            f"骨架丰富: {node.title} -> {len(children)}个子节点, "
            f"耗时={elapsed:.1f}s"
        )

    # ==================== P2: 自拟模式 ====================

    async def _generate_self_composed(
        self,
        project_id: int,
        structure: BidStructureAnalysis,
        total_word_budget: int,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[WritingOutlineNode]:
        """自拟模式: 按结构分析结果生成分卷目录"""
        t0 = time.time()
        context = await self._build_context(project_id)
        all_clauses = await self._load_all_confirmed_clauses(project_id)

        volumes: list[tuple[str, str, str]] = []
        if structure.has_qualification:
            volumes.append(("qualification", "资格标", "qualification"))
        if structure.has_technical:
            volumes.append(("technical", "技术标", "technical"))
        if structure.has_commercial:
            volumes.append(("commercial", "商务标", "commercial"))
        if not volumes:
            volumes.append(("technical", "技术标", "technical"))

        all_nodes: list[OutlineNodeGenerated] = []
        fill_tasks = []

        for idx, (vol_type, vol_title, category) in enumerate(volumes):
            vol_node = OutlineNodeGenerated(
                title=vol_title,
                depth=0,
                sort_order=idx,
                node_type="free_writing",
                is_required=1,
                score_weight=None,
                children=[],
                is_skeleton=False,
                volume_type=vol_type,
            )
            all_nodes.append(vol_node)

            section_clauses = self._classify_clauses_for_volume(
                all_clauses, category,
            )
            async def _run_fill(
                node: OutlineNodeGenerated = vol_node,
                volume_category: str = category,
                volume_clauses: list[WritingClause] = section_clauses,
                structure_mode: str = structure.structure_mode,
            ) -> str:
                try:
                    await self._fill_volume(
                        node, volume_category, volume_clauses,
                        context.get("industry_profile", ""),
                        structure_mode=structure_mode,
                    )
                except Exception as exc:
                    logger.warning(f"分卷填充任务异常({node.title}): {exc}")
                return node.title

            fill_tasks.append(_run_fill())

        fill_total = len(fill_tasks)
        gen_total = fill_total + 1
        self._report_progress(progress_callback, 0, gen_total, "分卷填充启动")
        completed = 0
        for future in asyncio.as_completed(fill_tasks):
            title = await future
            completed += 1
            self._report_progress(
                progress_callback,
                completed,
                gen_total,
                f"分卷填充 {completed}/{fill_total}: {title}",
            )

        self._report_progress(
            progress_callback, fill_total, gen_total, "实质内容标题挂载",
        )
        try:
            attached = await attach_scope_titles_to_outline(
                tree=all_nodes,
                db=self.db,
                llm_client=self.llm_client,
                project_id=project_id,
            )
            if attached:
                logger.info(f"实质内容标题挂载(自拟): {attached}条")
        except Exception as e:
            logger.warning(f"实质内容标题挂载失败(自拟): {e}")

        for node in all_nodes:
            self._override_node_type(node)

        self._assign_word_counts_tree(all_nodes, total_word_budget)

        self._report_progress(progress_callback, gen_total, gen_total, "写入目录")
        saved = await self._save_tree(project_id, all_nodes)
        elapsed = time.time() - t0
        logger.info(
            f"目录树生成完成(自拟模式): project={project_id}, "
            f"nodes={len(saved)}, 耗时={elapsed:.1f}s"
        )
        return saved

    _TECHNICAL_KEYWORDS = frozenset([
        "技术", "方案", "实施", "管理方案", "进度", "质量", "安全",
        "HSE", "售后", "培训", "应急", "工期", "保障措施",
    ])
    _COMMERCIAL_KEYWORDS = frozenset([
        "商务", "报价", "价格", "财务", "开标",
    ])
    _QUALIFICATION_KEYWORDS = frozenset([
        "资格", "资质", "证明", "营业执照", "授权", "承诺", "资格审查",
    ])

    def _classify_clauses_for_volume(
        self,
        clauses: list[WritingClause],
        target_category: str,
    ) -> list[WritingClause]:
        """为指定分卷筛选相关约束"""
        relevant_types = {
            "directory_rule", "content_required",
            "material_required", "scoring_criterion",
            "project_context",
        }
        result: list[WritingClause] = []
        for clause in clauses:
            if clause.sub_type not in relevant_types:
                continue
            if clause.sub_type == "scoring_criterion":
                metadata_category = volume_category_from_metadata(clause)
                if metadata_category is not None:
                    if metadata_category == target_category:
                        result.append(clause)
                    continue
            text = clause.content or ""
            has_tech = any(kw in text for kw in self._TECHNICAL_KEYWORDS)
            has_comm = any(kw in text for kw in self._COMMERCIAL_KEYWORDS)

            if target_category == "technical":
                if has_comm and not has_tech:
                    continue
                result.append(clause)
            elif target_category == "commercial":
                if has_tech and not has_comm:
                    continue
                result.append(clause)
            elif target_category == "qualification":
                has_qual = any(kw in text for kw in self._QUALIFICATION_KEYWORDS)
                if (has_tech or has_comm) and not has_qual:
                    continue
                result.append(clause)
            else:
                result.append(clause)
        return result

    async def _fill_volume(
        self,
        vol_node: OutlineNodeGenerated,
        category: str,
        clauses: list[WritingClause],
        industry_profile: str,
        structure_mode: str = "tech_comm",
    ) -> None:
        """调用LLM为单个分卷生成完整子目录"""

        def _format(sub_type: str) -> str:
            items = [c for c in clauses if c.sub_type == sub_type]
            if not items:
                return ""
            return "\n".join(_format_clause_for_prompt(c) for c in items)

        initial_payload = {
            "relevant_dir_rules": _format("directory_rule"),
            "relevant_content_reqs": _format("content_required"),
            "relevant_scoring": _format("scoring_criterion"),
            "relevant_materials": _format("material_required"),
            "industry_profile": industry_profile,
        }

        def _render_prompt(payload: dict[str, str]) -> tuple[str, str]:
            return get_section_fill_prompt(
                section_title=vol_node.title,
                section_category=category,
                relevant_dir_rules=payload["relevant_dir_rules"],
                relevant_content_reqs=payload["relevant_content_reqs"],
                relevant_scoring=payload["relevant_scoring"],
                relevant_materials=payload["relevant_materials"],
                industry_profile=payload["industry_profile"],
                structure_mode=structure_mode,
            )

        _reduce_payload = make_priority_shrink_reducer(
            [
                ("relevant_materials", 1),
                ("relevant_content_reqs", 1),
                ("relevant_scoring", 1),
                ("industry_profile", 2),
            ],
            kept_segments=["section_title", "category", "directory_rules"],
        )

        t0 = time.time()
        try:
            temperature, max_tokens = resolve_runtime_params(
                "writing.section_fill", 0.1, 5000,
            )
            raw_result = await self._chat_json_with_input_guard(
                operation_name=f"目录分卷填充({vol_node.title})",
                initial_payload=initial_payload,
                render_prompt=_render_prompt,
                reduce_payload_semantically=_reduce_payload,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            logger.warning(f"分卷填充失败({vol_node.title}): {e}")
            return

        elapsed = time.time() - t0
        raw_list = raw_result if isinstance(raw_result, list) else []
        children = self._parse_nodes(raw_list)

        for child in children:
            child.volume_type = vol_node.volume_type

        vol_node.children = children
        logger.info(
            f"分卷填充: {vol_node.title} -> {len(children)}个子节点, "
            f"耗时={elapsed:.1f}s"
        )

    # ==================== P3: Unified 兜底 ====================

    async def _generate_unified(
        self,
        project_id: int,
        total_word_budget: int,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[WritingOutlineNode]:
        """原始一次性LLM生成模式"""
        context = await self._build_context(project_id)
        self._report_progress(progress_callback, 0, 2, "统一目录生成中")

        _reduce_payload = make_priority_shrink_reducer(
            [
                ("material_requirements", 1),
                ("content_requirements", 1),
                ("scoring_criteria", 1),
                ("industry_profile", 2),
            ],
            kept_segments=["directory_rules"],
        )

        t0 = time.time()
        temperature, max_tokens = resolve_runtime_params(
            "writing.skeleton_generate", 0.1, 8000,
        )
        raw_result = await self._chat_json_with_input_guard(
            operation_name=f"目录统一生成(project={project_id})",
            initial_payload=context,
            render_prompt=lambda payload: get_outline_generate_prompt(**payload),
            reduce_payload_semantically=_reduce_payload,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        llm_elapsed = time.time() - t0

        raw_list = raw_result if isinstance(raw_result, list) else []
        logger.info(f"LLM返回: {len(raw_list)}个顶层节点, 耗时={llm_elapsed:.1f}s")

        generated_nodes = self._parse_nodes(raw_list)
        self._report_progress(progress_callback, 1, 2, "实质内容标题挂载")
        try:
            attached = await attach_scope_titles_to_outline(
                tree=generated_nodes,
                db=self.db,
                llm_client=self.llm_client,
                project_id=project_id,
            )
            if attached:
                logger.info(f"实质内容标题挂载(unified): {attached}条")
        except Exception as e:
            logger.warning(f"实质内容标题挂载失败(unified): {e}")

        for node in generated_nodes:
            self._override_node_type(node)

        self._assign_word_counts_tree(generated_nodes, total_word_budget)

        self._report_progress(progress_callback, 2, 2, "统一目录生成完成")
        saved = await self._save_tree(project_id, generated_nodes)
        logger.info(
            f"目录树生成完成(unified): project={project_id}, nodes={len(saved)}"
        )
        return saved

    # ==================== 素材缺失清单 ====================

    async def generate_material_checklist(
        self, project_id: int
    ) -> list[MaterialChecklistItem]:
        """对比 material_required / 提交注意事项约束和已上传素材"""
        material_reqs = await self._load_clauses(
            project_id, sub_type="material_required"
        )
        notice_reqs = list(await self._load_clauses(
            project_id, sub_type="submission_notice"
        ))
        notice_reqs.extend(await self._load_clauses(
            project_id, sub_type="submission_rule"
        ))
        uploaded = await self._load_materials(project_id)

        checklist: list[MaterialChecklistItem] = []

        for req in material_reqs:
            matched = self._find_matching_material(req, uploaded)
            main_text, sub_items = _parse_nested_requirements(req.content)
            checklist.append(MaterialChecklistItem(
                clause_id=req.id,
                requirement=main_text,
                sub_requirements=sub_items,
                category="material",
                status="fulfilled" if matched else "missing",
                matched_material_id=matched.id if matched else None,
            ))

        for req in notice_reqs:
            main_text, sub_items = _parse_nested_requirements(req.content)
            checklist.append(MaterialChecklistItem(
                clause_id=req.id,
                requirement=main_text,
                sub_requirements=sub_items,
                category="notice",
                status="notice",
                matched_material_id=None,
            ))

        return self._dedup_checklist(checklist)

    # ==================== 上下文构建 ====================

    async def _build_context(self, project_id: int) -> dict[str, str]:
        """从已确认约束构建 LLM 输入上下文"""
        dir_rules = await self._load_clauses(project_id, sub_type="directory_rule")
        content_reqs = await self._load_clauses(project_id, sub_type="content_required")
        material_reqs = await self._load_clauses(project_id, sub_type="material_required")
        scoring = await self._load_clauses(project_id, sub_type="scoring_criterion")
        profile = await self._load_clause(project_id, sub_type="industry_profile")

        def _format(clauses: list[WritingClause]) -> str:
            if not clauses:
                return ""
            return "\n".join(_format_clause_for_prompt(c) for c in clauses)

        return {
            "directory_rules": _format(dir_rules),
            "content_requirements": _format(content_reqs),
            "material_requirements": _format(material_reqs),
            "scoring_criteria": _format(scoring),
            "industry_profile": profile.content if profile else "",
        }

    # ==================== 数据库读取 ====================

    async def _load_bid_directory_spec(
        self, project_id: int,
    ) -> Optional[BidDirectorySpec]:
        """加载招标文件目录规格"""
        clause = await self._load_clause(
            project_id, sub_type="bid_directory_spec",
        )
        if not clause:
            return None
        try:
            data = json.loads(clause.content)
            return BidDirectorySpec(**data)
        except Exception as e:
            logger.warning(f"解析目录规格失败: {e}")
            return None

    async def _load_bid_structure(
        self, project_id: int,
    ) -> Optional[BidStructureAnalysis]:
        """加载标书结构分析结果"""
        clause = await self._load_clause(
            project_id, sub_type="bid_structure_analysis",
        )
        if not clause:
            return None
        try:
            data = json.loads(clause.content)
            return BidStructureAnalysis(**data)
        except Exception as e:
            logger.warning(f"解析标书结构分析失败: {e}")
            return None

    async def _load_all_confirmed_clauses(
        self, project_id: int,
    ) -> list[WritingClause]:
        """加载项目所有已确认的约束"""
        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.confirmed == 1,
            ).order_by(WritingClause.sort_order)
        )
        return list(result.scalars().all())

    async def _load_clauses(
        self, project_id: int, sub_type: str
    ) -> list[WritingClause]:
        """加载指定子类型的已确认约束"""
        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.sub_type == sub_type,
                WritingClause.confirmed == 1,
            ).order_by(WritingClause.sort_order)
        )
        return list(result.scalars().all())

    async def _load_clause(
        self, project_id: int, sub_type: str
    ) -> Optional[WritingClause]:
        """加载单条约束"""
        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.sub_type == sub_type,
            ).limit(1)
        )
        return result.scalars().first()

    async def _load_materials(self, project_id: int) -> list[WritingMaterial]:
        """加载项目已上传的素材"""
        result = await self.db.execute(
            select(WritingMaterial).where(
                WritingMaterial.project_id == project_id
            )
        )
        return list(result.scalars().all())

    # ==================== 解析与转换 ====================

    def _parse_nodes(
        self, raw_list: list[dict]
    ) -> list[OutlineNodeGenerated]:
        """将 LLM 输出的 JSON 解析为 Pydantic 对象"""
        nodes: list[OutlineNodeGenerated] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            try:
                node = OutlineNodeGenerated(**item)
                nodes.append(node)
            except Exception as e:
                logger.warning(f"节点解析失败: item={item}, err={e}")
        return nodes

    async def _load_catalog_template_keys(self, project_id: int) -> None:
        """加载本项目 catalog 已生成的 fixed_template template_key 集合。"""
        from app.core.writer.fixed_template_contract import (
            FIXED_TEMPLATE_SNAPSHOT_KEY,
        )

        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.sub_type == "fixed_template",
            )
        )
        keys: set[str] = set()
        for clause in result.scalars().all():
            metadata = clause.metadata_json if isinstance(clause.metadata_json, dict) else None
            if not metadata:
                continue
            tkey = metadata.get("template_key")
            snap = metadata.get(FIXED_TEMPLATE_SNAPSHOT_KEY) or {}
            tkey = tkey or snap.get("template_key")
            if tkey:
                keys.add(str(tkey))
        self._catalog_template_keys = keys
        if keys:
            logger.info(
                "OutlineGenerator: 项目{}固定模板 catalog template_keys={}",
                project_id,
                sorted(keys),
            )

    def _catalog_allows_fixed_template(self, title: str) -> bool:
        """仅当标题映射到的 template_key 在 catalog 中真实存在时, 才允许升级。: 词典外模板经 catalog 以 ``custom:<归一标题>`` key 收录,
 节点标题按同一归一规则映射后命中即允许升级。
 """
        if not self._catalog_template_keys:
            return False
        tkey = infer_fixed_template_key(title)
        if tkey and tkey in self._catalog_template_keys:
            return True
        custom = custom_template_key(title)
        return bool(custom and custom in self._catalog_template_keys)

    def _override_node_type(self, node: OutlineNodeGenerated) -> None:
        """用关键词规则覆盖 LLM 的 node_type 判定（递归）。

 P2.2: 在 override 发生时记录 `node_classification_conflict` metric,
 便于后续决策是否改成"LLM 优先、关键词仅作 hint"。: fixed_template 升级仅在 catalog 命中具体 template_key 时允许;
 否则即使标题含投标函/承诺书等关键词, 也不升级。
 """
        title = node.title
        original_type = node.node_type
        new_type: str | None = None
        if self._catalog_allows_fixed_template(title):
            new_type = "fixed_template"
        if new_type is None:
            for kw in _DATA_FILL_KEYWORDS:
                if kw in title:
                    new_type = "data_fill"
                    break
        if new_type is None:
            for kw in _FREE_WRITING_KEYWORDS:
                if kw in title:
                    new_type = "free_writing"
                    break

        if new_type is not None:
            if original_type and original_type != new_type:
                logger.warning(
                    "node_classification_conflict: title={!r}, llm={!r}, keyword={!r} (keyword overrides)",
                    title, original_type, new_type,
                )
                try:
                    from app.core.observability.metrics import (
                        writer_node_type_conflict_total as _conflict_metric,
                    )
                    _conflict_metric.labels(
                        llm_type=str(original_type),
                        keyword_type=new_type,
                    ).inc()
                except Exception:
                    pass
            node.node_type = new_type

        for child in node.children:
            self._override_node_type(child)

    @staticmethod
    def _infer_node_type(title: str) -> str:
        """根据标题关键词推断 node_type (粗略 hint, 最终由 _override_node_type 与 catalog 校验确定)。"""
        if infer_fixed_template_key(title):
            return "fixed_template"
        for kw in _FIXED_TEMPLATE_KEYWORDS:
            if kw in title:
                return "fixed_template"
        for kw in _DATA_FILL_KEYWORDS:
            if kw in title:
                return "data_fill"
        return "free_writing"

    # ==================== 字数分配 ====================

    def _assign_word_counts_tree(
        self,
        nodes: list[OutlineNodeGenerated],
        total_budget: int,
    ) -> None:
        """在树结构上分配 target_word_count"""
        all_leaves = self._collect_leaves(nodes)

        data_fill_leaves = [n for n in all_leaves if n.node_type == "data_fill"]
        free_leaves = [n for n in all_leaves if n.node_type == "free_writing"]

        data_fill_total = len(data_fill_leaves) * _DATA_FILL_WORD_COUNT
        remaining = max(0, total_budget - data_fill_total)

        total_weight = sum(n.score_weight or 1.0 for n in free_leaves)
        if total_weight > 0 and free_leaves:
            for n in free_leaves:
                weight = n.score_weight or 1.0
                n.score_weight = weight

        fallback_word_count_map: dict[str, Optional[int]] = {}
        for n in all_leaves:
            key = f"{n.title}_{n.depth}_{n.sort_order}"
            if n.node_type == "fixed_template":
                fallback_word_count_map[key] = None
            elif n.node_type == "data_fill":
                fallback_word_count_map[key] = _DATA_FILL_WORD_COUNT
            elif n.node_type == "free_writing" and total_weight > 0:
                weight = n.score_weight or 1.0
                fallback_word_count_map[key] = max(
                    500, int(weight / total_weight * remaining)
                )

        self._word_count_map: dict[str, Optional[int]] = {}
        for n in all_leaves:
            key = f"{n.title}_{n.depth}_{n.sort_order}"
            fallback_word_count = fallback_word_count_map.get(key)
            self._word_count_map[key] = self._normalize_leaf_target_word_count(
                n.node_type,
                getattr(n, "target_word_count", None),
                fallback_word_count,
            )

    def _collect_leaves(
        self, nodes: list[OutlineNodeGenerated]
    ) -> list[OutlineNodeGenerated]:
        """收集所有叶子节点"""
        leaves: list[OutlineNodeGenerated] = []
        for n in nodes:
            if not n.children:
                leaves.append(n)
            else:
                leaves.extend(self._collect_leaves(n.children))
        return leaves

    def _get_word_count(self, node: OutlineNodeGenerated) -> Optional[int]:
        """从预计算映射中获取字数"""
        key = f"{node.title}_{node.depth}_{node.sort_order}"
        if hasattr(self, "_word_count_map"):
            return self._word_count_map.get(key)
        if node.node_type == "data_fill":
            return self._normalize_leaf_target_word_count(
                node.node_type,
                getattr(node, "target_word_count", None),
                _DATA_FILL_WORD_COUNT,
            )
        return None

    @staticmethod
    def _coerce_word_count(value: object) -> Optional[int]:
        """将目标字数建议转为整数，无法解析时返回 None。"""
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return int(text)
        return None

    @classmethod
    def _normalize_leaf_target_word_count(
        cls,
        node_type: str,
        suggested_word_count: object,
        fallback_word_count: Optional[int],
    ) -> Optional[int]:
        """按章节类型对建议字数限幅；缺失时退回算法分配值。"""
        suggested = cls._coerce_word_count(suggested_word_count)
        fallback = cls._coerce_word_count(fallback_word_count)

        if node_type == "fixed_template":
            return None

        if node_type == "data_fill":
            base = suggested if suggested is not None else fallback or _DATA_FILL_WORD_COUNT
            return max(_DATA_FILL_MIN_WORD_COUNT, min(_DATA_FILL_MAX_WORD_COUNT, base))

        if node_type == "free_writing":
            base = suggested if suggested is not None else fallback
            if base is None:
                return _FREE_WRITING_MIN_WORD_COUNT
            return max(_FREE_WRITING_MIN_WORD_COUNT, min(_FREE_WRITING_MAX_WORD_COUNT, base))

        return fallback

    # ==================== DB 写入 ====================

    async def _save_tree(
        self,
        project_id: int,
        nodes: list[OutlineNodeGenerated],
    ) -> list[WritingOutlineNode]:
        """递归写入目录树到 DB"""
        saved: list[WritingOutlineNode] = []

        async def _recurse(
            gen_nodes: list[OutlineNodeGenerated],
            parent_id: Optional[int],
            depth: int,
            parent_volume_type: Optional[str] = None,
        ) -> None:
            for idx, gen in enumerate(gen_nodes):
                vol_type = gen.volume_type or parent_volume_type
                orm_node = WritingOutlineNode(
                    project_id=project_id,
                    parent_id=parent_id,
                    title=strip_existing_number(gen.title),
                    depth=depth,
                    sort_order=idx,
                    node_type=OutlineNodeType(gen.node_type),
                    is_required=gen.is_required,
                    score_weight=gen.score_weight,
                    target_word_count=self._get_word_count(gen),
                    status=OutlineNodeStatus.pending,
                    volume_type=vol_type,
                    is_skeleton=1 if gen.is_skeleton else 0,
                )
                self.db.add(orm_node)
                await self.db.flush()
                saved.append(orm_node)

                if gen.children:
                    await _recurse(gen.children, orm_node.id, depth + 1, vol_type)

        await _recurse(nodes, None, 0)
        await self.db.commit()

        for node in saved:
            await self.db.refresh(node)

        return saved

    # ==================== 清单去重 ====================

    @staticmethod
    def _dedup_checklist(
        items: list[MaterialChecklistItem],
    ) -> list[MaterialChecklistItem]:
        """清单输出去重"""
        if len(items) <= 1:
            return items

        seen_norms: list[str] = []
        result: list[MaterialChecklistItem] = []

        sorted_items = sorted(items, key=lambda x: 0 if x.category == "notice" else 1)

        for item in sorted_items:
            norm = re.sub(r'[\s\W]', '', item.requirement.lower())
            if not norm:
                result.append(item)
                continue
            is_dup = False
            for s in seen_norms:
                chars_a, chars_b = set(norm), set(s)
                union = len(chars_a | chars_b)
                if union > 0 and len(chars_a & chars_b) / union >= 0.65:
                    is_dup = True
                    break
                if len(norm) > 10 and len(s) > 10 and (norm in s or s in norm):
                    is_dup = True
                    break
            if not is_dup:
                seen_norms.append(norm)
                result.append(item)

        removed = len(items) - len(result)
        if removed > 0:
            logger.debug(
                f"素材缺失清单去重: raw={len(items)}, deduped={len(result)}"
            )
        return result

    # ==================== 素材匹配 ====================

    def _find_matching_material(
        self,
        req: WritingClause,
        materials: list[WritingMaterial],
    ) -> Optional[WritingMaterial]:
        """简单关键词匹配素材"""
        req_keywords = set(re.findall(r"[\u4e00-\u9fff]{2,}", req.content))
        if not req_keywords:
            return None

        best_match: Optional[WritingMaterial] = None
        best_overlap = 0

        for mat in materials:
            mat_text = (mat.title or "") + " " + (mat.content or "")[:200]
            mat_keywords = set(re.findall(r"[\u4e00-\u9fff]{2,}", mat_text))
            overlap = len(req_keywords & mat_keywords)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = mat

        return best_match if best_overlap >= 2 else None


    # ==================== 章节画像 ====================

    async def generate_chapter_profiles(
        self,
        project_id: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> int:
        """为叶子章节批量生成语义画像

        按 volume_type 分组，每组一次 AI 调用。
        画像写入 WritingOutlineNode.chapter_profile (JSON Text)。

        Returns:
            int: 成功生成画像的节点数量
        """
        from collections import Counter as _Counter

        from app.core.writer.prompts.chapter_profile import get_chapter_profile_prompt

        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
            ).order_by(WritingOutlineNode.depth, WritingOutlineNode.sort_order)
        )
        all_nodes = list(result.scalars().all())

        if not all_nodes:
            return 0

        child_ids = {n.parent_id for n in all_nodes if n.parent_id is not None}
        leaf_nodes = [n for n in all_nodes if n.id not in child_ids]

        if not leaf_nodes:
            return 0

        node_map = {n.id: n for n in all_nodes}

        # P2.3: 载入行业画像摘要 + 本项目约束 sub_type 分布, 供画像 prompt 消费
        industry_profile_excerpt = ""
        try:
            profile_clause = await self._load_clause(
                project_id, sub_type="industry_profile",
            )
            if profile_clause and profile_clause.content:
                industry_profile_excerpt = build_profile_excerpt(
                    profile_clause.content,
                    purpose=PURPOSE_CHAPTER_PROFILE,
                    max_chars=_CHAPTER_PROFILE_INDUSTRY_CHARS,
                )
        except Exception as exc:
            logger.debug(f"chapter_profile: 加载行业画像降级: {exc}")

        clause_domain_summary = ""
        clause_examples_summary = ""
        try:
            cls_result = await self.db.execute(
                select(WritingClause).where(WritingClause.project_id == project_id)
            )
            all_clauses = list(cls_result.scalars().all())
            if all_clauses:
                counter = _Counter(
                    (c.sub_type or "unknown") for c in all_clauses
                )
                clause_domain_summary = ", ".join(
                    f"{k}={v}" for k, v in counter.most_common()
                )
                clause_examples_summary = self._build_clause_examples_summary(
                    all_clauses
                )
        except Exception as exc:
            logger.debug(f"chapter_profile: 聚合 clause 分布降级: {exc}")

        def _ancestor_path(node: WritingOutlineNode) -> str:
            parts = []
            cur = node
            while cur:
                parts.append(cur.title)
                cur = node_map.get(cur.parent_id) if cur.parent_id else None
            return " > ".join(reversed(parts))

        def _build_tree_text() -> str:
            lines = []
            for n in all_nodes:
                indent = "  " * n.depth
                ntype = n.node_type.value if hasattr(n.node_type, 'value') else str(n.node_type)
                lines.append(f"{indent}- [{ntype}] {n.title}")
            return "\n".join(lines)

        tree_text = _build_tree_text()

        volume_groups: dict[str | None, list[WritingOutlineNode]] = {}
        for n in leaf_nodes:
            volume_groups.setdefault(n.volume_type, []).append(n)
        total_groups = len(volume_groups)

        def _report(current: int, message: str) -> None:
            if not progress_callback:
                return
            try:
                progress_callback(current, total_groups, message)
            except Exception as e:
                logger.warning(f"章节画像进度回调失败: {e}")

        def _build_leaf_list(group_nodes: list[WritingOutlineNode]) -> str:
            """职责: 把节点集合渲染为 LLM 可读的叶子列表文本
            不做: 不修改 group_nodes、不发起 LLM 调用
            """
            lines: list[str] = []
            for n in group_nodes:
                ntype = n.node_type.value if hasattr(n.node_type, 'value') else str(n.node_type)
                path = _ancestor_path(n)
                siblings = [
                    sib.title for sib in all_nodes
                    if sib.parent_id == n.parent_id and sib.id != n.id
                ]
                sib_text = ", ".join(siblings[:5]) if siblings else "无"
                lines.append(
                    f"- ID={n.id}, 标题={n.title}, 类型={ntype}, "
                    f"路径={path}, 兄弟节点=[{sib_text}]"
                )
            return "\n".join(lines)

        async def _profile_one_volume(
            volume_key: str | None,
            group_nodes: list[WritingOutlineNode],
        ) -> list[tuple[int, dict]]:
            """职责: 单卷章节画像产出（分批 + 有缺即补齐）
            不做: 不写库、不跨卷协调、不合成假画像
            返回 [(node_id, profile_data), ...]
            """
            from app.core.config import settings as _settings

            volume_label = volume_key or "默认分卷"
            batch_size = int(getattr(_settings, "WRITING_CHAPTER_PROFILE_BATCH_SIZE", 12))
            enforce = bool(
                getattr(_settings, "WRITING_CHAPTER_PROFILE_ENFORCE_COVERAGE", True)
            )
            max_rounds = int(
                getattr(_settings, "WRITING_CHAPTER_PROFILE_MAX_SUPPLEMENT_ROUNDS", 2)
            )

            _report(0, f"整理{volume_label}章节画像输入")

            def _render_prompt(payload: dict[str, str]) -> tuple[str, str]:
                return get_chapter_profile_prompt(
                    payload["tree_text"],
                    payload["leaf_list"],
                    industry_profile_excerpt=payload.get(
                        "industry_profile_excerpt", ""
                    ),
                    clause_domain_summary=payload.get(
                        "clause_domain_summary", ""
                    ),
                    clause_examples_summary=payload.get(
                        "clause_examples_summary", ""
                    ),
                )

            # 超长优先裁剪 industry_profile_excerpt, 其次 tree_text, 最后 leaf_list
            _reduce_payload = make_priority_shrink_reducer(
                [
                    ("industry_profile_excerpt", 1),
                    ("clause_examples_summary", 3),
                    ("tree_text", 3),
                    ("leaf_list", 3),
                ],
                kept_segments=[volume_label, "chapter_profile_contract"],
            )

            async def _call_profile_llm(
                payload: dict[str, str],
                op_label: str,
            ) -> list:
                temperature, max_tokens = resolve_runtime_params(
                    "writing.chapter_profile", 0.1, 8000,
                )
                raw = await self._chat_json_with_input_guard(
                    operation_name=op_label,
                    initial_payload=payload,
                    render_prompt=_render_prompt,
                    reduce_payload_semantically=_reduce_payload,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return self._normalize_chapter_profiles_response(
                    raw, volume_key=volume_key,
                )

            batches = split_leaves_into_batches(group_nodes, batch_size)
            all_results: list[tuple[int, dict]] = []
            seen_ids: set[int] = set()

            for batch_idx, batch_nodes in enumerate(batches):
                expected = len(batch_nodes)
                node_id_set = {n.id for n in batch_nodes}
                batch_payload = {
                    "tree_text": tree_text,
                    "leaf_list": _build_leaf_list(batch_nodes),
                    "industry_profile_excerpt": industry_profile_excerpt,
                    "clause_domain_summary": clause_domain_summary,
                    "clause_examples_summary": clause_examples_summary,
                }

                try:
                    _report(
                        0,
                        f"等待{volume_label}章节画像模型返回"
                        f"（批次 {batch_idx + 1}/{len(batches)}）",
                    )
                    profiles = await _call_profile_llm(
                        batch_payload,
                        f"章节画像({volume_label}#{batch_idx + 1})",
                    )
                except Exception as e:
                    logger.warning(
                        f"章节画像生成失败: volume={volume_key}, "
                        f"batch={batch_idx + 1}, err={e}"
                    )
                    profiles = []

                batch_results = self._accept_profile_items(profiles, node_id_set)

                # 有缺即补齐（不再使用 coverage>=0.5 死区）
                # enforce=False 时仍走一次旧门控补救（_supplement 内部按 50% 判定）
                rounds = 0
                while rounds < (max_rounds if enforce else 1):
                    accepted_ids = {nid for nid, _ in batch_results}
                    missing_nodes = [
                        n for n in batch_nodes if n.id not in accepted_ids
                    ]
                    if not missing_nodes:
                        break
                    if not enforce and not batch_results:
                        break
                    supplement_results = await self._supplement_missing_profiles(
                        volume_key=volume_key,
                        volume_label=volume_label,
                        expected=expected,
                        missing_nodes=missing_nodes,
                        base_payload=batch_payload,
                        build_leaf_list=_build_leaf_list,
                        call_llm=_call_profile_llm,
                    )
                    rounds += 1
                    if not supplement_results:
                        break
                    for nid, pdata in supplement_results:
                        if nid not in accepted_ids:
                            batch_results.append((nid, pdata))
                            accepted_ids.add(nid)

                for nid, pdata in batch_results:
                    if nid not in seen_ids:
                        all_results.append((nid, pdata))
                        seen_ids.add(nid)

            expected_all = len(group_nodes)
            complete_n = sum(
                1
                for _, pdata in all_results
                if assess_profile_data(pdata) == ProfileQuality.COMPLETE
            )
            matchable_n = sum(
                1
                for _, pdata in all_results
                if assess_profile_data(pdata) == ProfileQuality.MATCHABLE
            )
            degraded_n = expected_all - len(all_results)
            self._log_volume_profile_coverage(
                volume_key=volume_key,
                expected=expected_all,
                accepted=len(all_results),
                complete=complete_n,
                matchable=matchable_n,
                degraded=degraded_n,
            )
            return all_results

        tasks = [
            asyncio.create_task(_profile_one_volume(k, g))
            for k, g in volume_groups.items()
        ]

        total_profiled = 0
        completed_groups = 0
        for future in asyncio.as_completed(tasks):
            try:
                result = await future
            except Exception as exc:
                completed_groups += 1
                _report(completed_groups, f"章节画像第 {completed_groups}/{total_groups} 组异常，继续处理")
                logger.warning(f"章节画像并行任务异常: {exc}")
                continue
            completed_groups += 1
            _report(completed_groups, f"章节画像已完成 {completed_groups}/{total_groups} 组")
            for nid, profile_data in result:
                node = node_map.get(nid)
                if node:
                    node.chapter_profile = json.dumps(
                        profile_data, ensure_ascii=False
                    )
                    total_profiled += 1

        if total_profiled > 0:
            _report(total_groups, "保存章节画像结果")
            await self.db.commit()

        logger.info(
            f"章节画像生成完成: project={project_id}, "
            f"profiled={total_profiled}/{len(leaf_nodes)}"
        )
        return total_profiled

    @staticmethod
    def _normalize_chapter_profiles_response(
        raw_profiles: object,
        *,
        volume_key: str | None = None,
    ) -> list:
        """归一化章节画像 LLM 返回为画像项数组。

 职责: 把 LLM 三种已观测形态归一为统一的画像项数组
 不做: 字段补全、画像质量校验、画像内容修正

 兼容形态（按优先级）:
 1. 顶层数组 -> 直接返回
 2. 包裹对象 {"profiles"|"items"|"chapters"|"data"|"result": [...]} -> 解包
 3. 单条画像对象（含 node_id/semantic_role/expects/rejects 中至少 2 项）
 -> 包装为单元素数组
 """
        if isinstance(raw_profiles, list):
            return raw_profiles
        if isinstance(raw_profiles, dict):
            for key in ("profiles", "items", "chapters", "data", "result"):
                value = raw_profiles.get(key)
                if isinstance(value, list):
                    logger.info(
                        f"章节画像返回包裹对象，已解包字段: volume={volume_key}, key={key}"
                    )
                    return value
            profile_item_keys = {"node_id", "semantic_role", "expects", "rejects"}
            matched_keys = profile_item_keys & set(raw_profiles.keys())
            if len(matched_keys) >= 2:
                logger.info(
                    f"章节画像返回单条对象，已包装为单元素数组: "
                    f"volume={volume_key}, matched_keys={sorted(matched_keys)}"
                )
                return [raw_profiles]
            logger.warning(
                f"章节画像返回对象但未包含数组字段: volume={volume_key}, "
                f"keys={list(raw_profiles.keys())[:8]}"
            )
            return []
        logger.warning(
            f"章节画像返回非数组: volume={volume_key}, type={type(raw_profiles).__name__}"
        )
        return []

    @staticmethod
    def _accept_profile_items(
        profiles: list,
        node_id_set: set,
    ) -> list[tuple[int, dict]]:
        """职责: 把归一化后的画像项按 node_id_set 过滤并做质量门控
 不做: 不发起 LLM 调用、不修改入参、不补救缺漏
 返回 [(node_id, profile_data), ...]: 接受门改为 matchable（role + expects>=1）；rejects<2 不再丢弃。
 """
        results: list[tuple[int, dict]] = []
        for item in profiles:
            if not isinstance(item, dict):
                continue
            nid = item.get("node_id")
            if nid not in node_id_set:
                continue
            profile_data = {
                "semantic_role": item.get("semantic_role", ""),
                "expects": item.get("expects", []) if isinstance(item.get("expects"), list) else [],
                "rejects": item.get("rejects", []) if isinstance(item.get("rejects"), list) else [],
            }
            quality = assess_profile_data(profile_data)
            if not is_matchable(quality):
                logger.warning(
                    f"画像质量不达标(跳过): node_id={nid}, "
                    f"role={bool(str(profile_data['semantic_role'] or '').strip())}, "
                    f"expects={len(profile_data['expects'])}, "
                    f"rejects={len(profile_data['rejects'])}"
                )
                continue
            if quality == ProfileQuality.MATCHABLE:
                logger.info(
                    f"画像 matchable 但非 complete(保留): node_id={nid}, "
                    f"rejects={len(profile_data['rejects'])}"
                )
            results.append((nid, profile_data))
        return results

    async def _supplement_missing_profiles(
        self,
        *,
        volume_key: str | None,
        volume_label: str,
        expected: int,
        missing_nodes: list,
        base_payload: dict,
        build_leaf_list: Callable[[list], str],
        call_llm: Callable[[dict, str], Awaitable[list]],
    ) -> list[tuple[int, dict]]:
        """职责: 对遗漏的叶子节点发起一次补充 LLM 调用
 不做: 不递归、不修改 base_payload、不合成假画像
 返回 [(node_id, profile_data), ...]，仅包含补救成功的画像: 移除 coverage>=0.5 死区；有缺漏且 enforce 开启时一律补齐。
 兼容回退: WRITING_CHAPTER_PROFILE_ENFORCE_COVERAGE=False 时恢复旧 50% 门控。
 """
        if not missing_nodes or expected <= 0:
            return []

        from app.core.config import settings as _settings

        enforce = bool(
            getattr(_settings, "WRITING_CHAPTER_PROFILE_ENFORCE_COVERAGE", True)
        )
        accepted_before = expected - len(missing_nodes)
        coverage = compute_coverage(expected=expected, accepted=accepted_before)

        if not enforce and coverage >= 0.5:
            logger.info(
                f"章节画像有少量缺漏但未触发补救(兼容旧门控): volume={volume_key}, "
                f"expected={expected}, accepted={accepted_before}, "
                f"missing={len(missing_nodes)}"
            )
            return []

        logger.warning(
            f"章节画像产出缺漏，触发补救调用: volume={volume_key}, "
            f"expected={expected}, accepted={accepted_before}, "
            f"missing={len(missing_nodes)}, coverage={coverage:.0%}"
        )

        supplement_payload = dict(base_payload)
        supplement_payload["leaf_list"] = build_leaf_list(missing_nodes)
        try:
            supplement_profiles = await call_llm(
                supplement_payload,
                f"章节画像补救({volume_label})",
            )
        except Exception as exc:
            logger.warning(
                f"章节画像补救调用失败: volume={volume_key}, err={exc}"
            )
            return []

        missing_id_set = {n.id for n in missing_nodes}
        return self._accept_profile_items(
            supplement_profiles, missing_id_set,
        )

    @staticmethod
    def _log_volume_profile_coverage(
        *,
        volume_key: str | None,
        expected: int,
        accepted: int,
        complete: int = 0,
        matchable: int = 0,
        degraded: int = 0,
    ) -> None:
        """职责: 输出单卷画像覆盖率统计日志，区分 LLM 真实覆盖与 degraded
        不做: 不抛异常、不修改业务状态
        """
        if expected <= 0:
            return
        coverage = compute_coverage(expected=expected, accepted=accepted)
        msg = (
            f"单卷章节画像完成: volume={volume_key}, "
            f"expected={expected}, accepted={accepted}, "
            f"complete={complete}, matchable={matchable}, degraded={degraded}, "
            f"coverage={coverage:.0%}"
        )
        # 补救后仍缺漏 >50% 视为严重故障，升级到 ERROR 便于运维感知
        if coverage < 0.5:
            logger.error(msg + " (严重缺漏，已触发补救但仍未恢复)")
        elif coverage < 1.0:
            logger.warning(msg + " (存在少量缺漏/degraded)")
        else:
            logger.info(msg)

    @staticmethod
    def _build_clause_examples_summary(clauses: list[WritingClause]) -> str:
        """为章节画像提供少量高价值约束示例，避免画像只看类型计数。"""
        preferred = [
            "fixed_template",
            "material_required",
            "scoring_criterion",
            "content_required",
            "table_format",
        ]
        grouped: dict[str, list[WritingClause]] = {key: [] for key in preferred}
        for clause in clauses:
            if clause.sub_type in grouped and len(grouped[clause.sub_type]) < 3:
                grouped[clause.sub_type].append(clause)

        lines: list[str] = []
        for sub_type in preferred:
            for clause in grouped[sub_type]:
                lines.append(f"- {sub_type}: {_format_clause_for_prompt(clause)}")
        return "\n".join(lines)


def get_outline_generator(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    project_id: Optional[int] = None,
) -> OutlineGenerator:
    """工厂函数"""
    return OutlineGenerator(db=db, llm_client=llm_client, project_id=project_id)
