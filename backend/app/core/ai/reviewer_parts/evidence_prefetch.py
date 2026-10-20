"""证据预取阶段（R2-3 Step 6 抽出）

涵盖以下原 `ReviewOrchestrator` 方法：

- `prefetch_evidence`（async, 160 行）: Phase1 短 session 内一次性预取证据 +
 知识库上下文，与 LLM 调用解耦（设计原则）。
- `build_evidence_index`（25 行）: 构建跨 requirement 的关键词倒排索引，
 在 `_review_project_impl` 启动时一次性构建并写入 `self._evidence_index`。
- `lookup_cross_category_evidence`（48 行）: 按 `_DOMAIN_KEYWORDS` 查找跨类别证据。
- `get_project_owner_id`（27 行）: 项目 owner_id 缓存查询，避免连接池竞争。

模块级常量（与 ReviewOrchestrator._DOMAIN_KEYWORDS byte-equal，主类通过 re-export
ClassVar 引用）:
- DOMAIN_KEYWORDS

签名规范: 子模块函数去前导下划线, 第一参数保留 `self`。
跨方法调用通过 `self.xxx` 触发主类 thin forwarder。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional, Sequence

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.reviewer_parts.evidence_pack_budget import (
    SECTION_CROSS,
    SECTION_IMAGE,
    SECTION_KNOWLEDGE,
    SECTION_LEDGER,
    SECTION_PROHIBITION,
    SECTION_RAW,
    EvidenceItem,
    assemble_evidence_pack,
    resolve_pack_budget,
)
from app.core.ai.reviewer_parts.evidence_policy import (
    EvidenceAdequacy,
    facet_missing_keys_for_rows,
    select_facet_covered_rows,
    should_search_independently,
)
from app.core.matcher.requirement_matcher import MatchedPair
from app.core.retriever.evidence_coverage import EvidenceFacet, build_evidence_facets
from app.core.retriever.evidence_priors import is_starvation_note
from app.core.review_contracts import (
    CrossEvidence,
    ReviewEvidenceBundle,
    is_reviewable_requirement,
    parse_json_list,
    parse_page_numbers,
)
from app.core.ai.reviewer_parts.evidence_ledger.contracts import EvidenceLedger
from app.core.config import settings
from app.models.project import Project

if TYPE_CHECKING:
    from app.core.ai.reviewer import ReviewOrchestrator


# 跨类别证据关键词（与 ReviewOrchestrator._DOMAIN_KEYWORDS byte-equal）
DOMAIN_KEYWORDS: list[str] = [
    "业绩", "合同", "证书", "社保", "培训", "体检", "保险",
    "发票", "审计", "资质", "执照", "施工", "项目经理",
    "技术负责人", "安全员", "建造师", "验收", "竣工",
    # 信用合规
    "失信", "信用", "信用中国", "重大违法", "黑名单", "不良行为",
    # 主体链条
    "法定代表人", "单位负责人", "授权代表", "委托代理人", "授权委托书",
    # 共享财务资信材料
    "纳税证明", "资信证明", "审计报告", "财务报表",
]


# ---------------------------------------------------------------------------
# 跨 requirement 关键词倒排索引
# ---------------------------------------------------------------------------


def build_evidence_index(
    self: "ReviewOrchestrator", matched_pairs: list[MatchedPair]
) -> dict[str, list[tuple[str, str, str, str | None]]]:
    """构建跨 requirement 的证据关键词索引。

    Fix-08: tuple 第四位为 evidence_anchor（可选），供 Prompt 层做锚定引用。

    Returns:
        dict: keyword -> [(content_snippet, source_pages, category, evidence_anchor)]
    """
    index: dict[str, list[tuple[str, str, str, str | None]]] = {}
    for pair in matched_pairs:
        if not pair.response or not pair.response.content:
            continue
        content = pair.response.content
        pages = pair.response.source_pages or ""
        category = pair.category or ""
        anchor = getattr(pair.response, "evidence_anchor", None)
        for kw in self._DOMAIN_KEYWORDS:
            if kw in content:
                index.setdefault(kw, []).append(
                    (content[:300], str(pages), category, anchor)
                )
    return index


def lookup_cross_category_evidence(
    self: "ReviewOrchestrator", pair: MatchedPair
) -> tuple[str, list[CrossEvidence]]:
    """Fix-08: 查找跨类别证据，返回 (prompt_text, 结构化命中列表)。

    - prompt_text 向后兼容 ``ReviewEvidenceBundle.cross_category_evidence: str``
    - 结构化列表用于 post-pipeline 决定是否预置 ``needs_review``
    """
    empty_text = ""
    if not hasattr(self, "_evidence_index") or not self._evidence_index:
        return empty_text, []
    req_desc = pair.requirement.description if pair.requirement else ""
    if not req_desc:
        return empty_text, []
    current_category = pair.category or ""

    sources: list[CrossEvidence] = []
    seen: set[tuple[str, str]] = set()
    for kw in self._DOMAIN_KEYWORDS:
        if kw not in req_desc:
            continue
        for snippet, pages, cat, anchor in self._evidence_index.get(kw, []):
            if cat == current_category:
                continue
            key = (snippet[:100], pages)
            if key in seen:
                continue
            seen.add(key)
            sources.append(CrossEvidence(
                category=cat,
                snippet=snippet[:100],
                pages=str(pages or ""),
                anchor=anchor,
            ))

    if not sources:
        return empty_text, []

    # Fix-08: Prompt 文本格式化带显式来源类别 / 页码 / 锚点，避免 LLM 误把跨证据当本条响应。
    lines = [
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "【以下为其他审查类别中找到的相关证据摘要 -- 请结合判断本要求是否已被实质性响应；"
        "务必在 comment 中注明来源类别与页码，不得把跨证据页码直接写入本条 source_pages】",
    ]
    for ce in sources[:5]:
        lines.append(f"▶ {ce.to_prompt_line()}")
    return "\n".join(lines), sources[:5]


# ---------------------------------------------------------------------------
# 项目 owner 缓存查询（连接池保护）
# ---------------------------------------------------------------------------


async def get_project_owner_id(
    self: "ReviewOrchestrator",
    project_id: int,
    session: Optional[AsyncSession] = None,
) -> Optional[int]:
    """读取项目所有者，用于知识库用户级隔离。: 同一审查任务 project_id 不变, 缓存命中后直接返回, 避免每个
 并发协程都触发 self.db.execute (历史上是连接池耗尽的源头之一)。

 Args:
 project_id: 项目ID
 session: 可选 DB 会话; 显式传入时不触碰共享 self.db,
 未传入时回退到 self.db (向后兼容; 单例上下文用)。
 """
    if self._project_owner_id_loaded:
        return self._project_owner_id_cache
    target_session = session if session is not None else self.db
    result = await target_session.execute(
        select(Project.owner_id).where(Project.id == project_id)
    )
    owner_id = result.scalar_one_or_none()
    normalized = int(owner_id) if owner_id is not None else None
    self._project_owner_id_cache = normalized
    self._project_owner_id_loaded = True
    return normalized


# ---------------------------------------------------------------------------
# Phase1 证据预取主入口（短 session）
# ---------------------------------------------------------------------------


async def collect_prohibition_items(
    pair: MatchedPair,
    session: AsyncSession,
    response: Any,
    evidence_items: list[EvidenceItem],
) -> Any:
    """禁止项反向核验: 收集违禁摘录条目并挂载闸门命中, 返回解析出的契约。

    职责: 只做禁区检出与条目登记; 是否进入 Prompt 由证据包预算器决定。
    不做: 不改符合度、不决定补搜是否触发（归 evidence_policy）。
    """
    from app.core.ai.gates.review.prohibition_policy import (
        format_prohibition_excerpt,
        parse_prohibition,
    )

    req_text = pair.requirement.description if pair.requirement else ""
    prohibition_spec = parse_prohibition(req_text)
    prohibition_leak_hits: list[dict] = []
    if prohibition_spec and response and getattr(response, "document_id", None):
        try:
            from app.core.retriever.prohibition_prefetch import collect_prohibition_leaks

            leak_hits = await collect_prohibition_leaks(
                session, int(response.document_id), prohibition_spec
            )
            prohibition_leak_hits = [hit.to_dict() for hit in leak_hits]
            leak_excerpt = format_prohibition_excerpt(leak_hits)
            if leak_excerpt:
                # 禁止项反向核验是放行红线证据: clippable=False, 预算内优先保留
                evidence_items.append(EvidenceItem(
                    section=SECTION_PROHIBITION,
                    text=leak_excerpt,
                    content=leak_excerpt,
                    label="prohibition_leak",
                    clippable=False,
                ))
        except Exception as leak_err:
            logger.debug(
                f"[AI审查] 禁止项反向核验降级: requirement_id={pair.requirement_id}, err={leak_err}"
            )
    try:
        pair._gate_prohibition_leak_hits = prohibition_leak_hits  # type: ignore[attr-defined]
    except Exception:
        pass
    return prohibition_spec


async def build_knowledge_context(
    self: "ReviewOrchestrator",
    pair: MatchedPair,
    session: AsyncSession,
    response: Any,
    project_id: Optional[int],
) -> tuple[str, list[dict[str, Any]]]:
    """在短 session 内构建知识库辅助上下文。

 显式授权门控: 仅当项目级 knowledge_usage_config 已启用且 flow=bid_review、
 confirmed_by_user=True 且选择了资料时才允许构建; 否则严禁注入。
 构建失败一律降级为空, 不得阻断审查。
 """
    if project_id is None:
        return "", []

    try:
        from app.core.knowledge import (
            KnowledgeContextBuilder,
            load_usage_for_project,
        )

        usage = await load_usage_for_project(session, project_id)
        if not usage.is_active_for_flow("bid_review"):
            return "", []

        response_snippet = ""
        if response and response.content:
            response_snippet = response.content[:1000]
        kb_query = (
            f"{pair.category}\n{pair.requirement.description}\n{response_snippet}"
        )
        owner_id = await self._get_project_owner_id(project_id, session=session)
        built_context = await KnowledgeContextBuilder(session).build(
            query=kb_query,
            flow="bid_review",
            owner_id=owner_id,
            primary_text=pair.requirement.description,
            allowed_document_ids=list(usage.selected_kb_document_ids),
            top_k=3,
        )
        refs = [
            {
                "kb_doc_id": ref.kb_doc_id,
                "kb_chunk_id": ref.kb_chunk_id,
                "page_num": ref.page_num,
                "title": ref.title,
                "authority": ref.authority,
                "version": ref.version,
                "similarity": ref.similarity,
            }
            for ref in (built_context.references or [])
        ]
        if refs:
            logger.info(
                "[知识库辅助] flow=bid_review project={} requirement={} "
                "refs={} policy={}",
                project_id,
                pair.requirement_id,
                len(refs),
                usage.usage_policy or "auxiliary_only",
            )
        return built_context.text or "", refs
    except Exception as kb_err:
        logger.debug(f"知识库参考构建失败(忽略): {kb_err}")
        return "", []


async def prefetch_evidence(
    self: "ReviewOrchestrator",
    pair: MatchedPair,
    session: AsyncSession,
    project_id: Optional[int] = None,
) -> ReviewEvidenceBundle:
    """在短生命周期 session 内预取所有审查所需的证据数据

 将 DB 读取与 LLM 调用解耦：本方法在 session 活跃期内完成全部 DB 查询，
 返回纯文本结果，之后 session 即可释放。: 知识库上下文（KnowledgeContextBuilder）也在本阶段构建,
 让 _review_single_pair 在 LLM 调用期间不再需要任何 DB session,
 彻底落实 的"Phase 2 无 session"原则。

 Args:
 pair: 匹配对
 session: 短生命周期 DB 会话
 project_id: 项目ID, 用于知识库上下文 owner 隔离 (None 时跳过知识库构建)

 Returns:
 ReviewEvidenceBundle: 统一证据包
 """
    prefetch_started = time.perf_counter()
    # 局部图片证据依旧先取, 但不再作为独立补搜的抑制条件
    # (补搜触发判定统一收口到 evidence_policy.should_search_independently)
    # 收集的是结构化候选条目, 是否进入 Prompt 由证据包预算器统一决定
    evidence_items: list[EvidenceItem] = list(
        await self._fetch_image_evidence_for_pair(pair, session=session)
    )

    cross_evidence, cross_evidence_sources = self._lookup_cross_category_evidence(pair)
    if cross_evidence:
        evidence_items.append(EvidenceItem(
            section=SECTION_CROSS,
            text=cross_evidence,
            content=cross_evidence,
            label="cross_category",
        ))
    response = pair.response
    completeness = response.completeness if response else None
    source_pages = parse_page_numbers(response.source_pages if response else None)
    missing_elements = [
        str(item)
        for item in parse_json_list(response.missing_elements if response else None)
        if item is not None
    ]
    quality_flags: list[str] = []
    if pair.has_response and not source_pages:
        quality_flags.append("missing_source_pages")
    if pair.has_response and not (response.evidence_anchor if response else None):
        quality_flags.append("missing_evidence_anchor")
    if pair.has_response and completeness in {None, "", "partial"} and not missing_elements:
        quality_flags.append("missing_gap_details")
    # 提取阶段标记的检索饥饿注入审查Prompt(经"系统质量提示"区),
    # 使未响应审查意见携带具体未覆盖页码转人工复核
    # (判定走 is_starvation_note 单源 helper, 新旧前缀兼容存量数据)
    starvation_note = (response.relevance_note or "") if response else ""
    if is_starvation_note(starvation_note):
        quality_flags.append(starvation_note)

    if quality_flags:
        logger.warning(
            "[AI审查] 证据包完整性告警: requirement_id={}, flags={}",
            pair.requirement_id,
            quality_flags,
        )

    # Fix-02: 从 bid_response_chunks 装配真实检索命中证据，供下游 Gate 使用
    # R1: 同次查询补取 chunk_id/similarity 并 join Chunk.content，作为原文佐证
    # 注入 Prompt，消除"审查 LLM 不回看原文"的证据链断点。
    # 描述+人工确认子要求 → 可检索分面, 供缺口评估与原文保底选择共同消费
    # LLM 关键词并入分面来源, 与提取侧同口径
    facets = build_evidence_facets(
        pair.requirement.description if pair.requirement else "",
        parse_json_list(getattr(pair.requirement, "sub_requirements", None)),
        keywords=parse_json_list(getattr(pair.requirement, "keywords", None)),
    )
    facet_missing: tuple[str, ...] = ()

    retrieval_chunk_pages: set[int] = set()
    retrieval_primary_pages: set[int] = set()
    retrieval_hit_sub_queries: list[str] = []
    raw_evidence_rows: list[dict[str, Any]] = []
    facet_probe_rows: list[dict[str, Any]] = []
    if response and response.id is not None:
        try:
            from sqlalchemy import select as _select

            from app.models.bid_response_chunk import BidResponseChunk
            from app.models.chunk import Chunk
            stmt = (
                _select(
                    BidResponseChunk.page_num,
                    BidResponseChunk.matched_sub_query,
                    BidResponseChunk.role,
                    BidResponseChunk.similarity,
                    BidResponseChunk.chunk_id,
                    BidResponseChunk.matched_keywords,
                    Chunk.section_path,
                    Chunk.content,
                )
                .outerjoin(Chunk, BidResponseChunk.chunk_id == Chunk.id)
                .where(BidResponseChunk.bid_response_id == response.id)
            )
            rows = (await session.execute(stmt)).all()
            seen_sq: set[str] = set()
            for (
                page_num, matched_sq, role, similarity,
                chunk_id, matched_keywords, section_path, chunk_content,
            ) in rows:
                if page_num is not None:
                    retrieval_chunk_pages.add(int(page_num))
                    if role == "primary":
                        retrieval_primary_pages.add(int(page_num))
                if matched_sq and matched_sq not in seen_sq:
                    retrieval_hit_sub_queries.append(matched_sq)
                    seen_sq.add(matched_sq)
                # 分面覆盖探针含无正文行: matched_sub_query 命中同样构成覆盖
                facet_probe_rows.append(
                    {"content": chunk_content or "", "matched_sub_query": matched_sq}
                )
                if chunk_content:
                    raw_evidence_rows.append({
                        "role": role or "primary",
                        "page": int(page_num) if page_num is not None else None,
                        "similarity": float(similarity) if similarity is not None else 0.0,
                        "section": str(section_path or ""),
                        "content": str(chunk_content),
                        "matched_sub_query": matched_sq,
                        "matched_keywords": matched_keywords,
                        "chunk_id": chunk_id,
                    })
            facet_missing = facet_missing_keys_for_rows(facets, facet_probe_rows)
        except Exception as _err:
            logger.debug(
                f"[AI审查] 检索证据装配降级（degraded=missing_retrieval_evidence）: "
                f"requirement_id={pair.requirement_id}, err={_err}"
            )
            quality_flags.append("missing_retrieval_evidence")

    inventory_facts = getattr(self, "_material_inventory", None)
    if inventory_facts is None and project_id is not None:
        try:
            from app.core.retriever.material_inventory import load_material_facts

            inventory_facts = await load_material_facts(session, int(project_id))
        except Exception as _inv_err:
            logger.debug("[AI审查] 材料库存读取降级: {}", _inv_err)
            inventory_facts = ()
    elif inventory_facts is None:
        inventory_facts = ()
    if inventory_facts:
        from app.core.extractor.facet_closure import reconcile_review_gaps_with_inventory
        from app.core.retriever.material_inventory import inventory_strong_hits

        display_missing, remaining_facets, presence_flags = (
            reconcile_review_gaps_with_inventory(
                missing_elements, facet_missing, facets, inventory_facts
            )
        )
        missing_elements = display_missing
        facet_missing = remaining_facets
        quality_flags.extend(presence_flags)
        adequacy_missing = [
            item for item in parse_json_list(response.missing_elements if response else None)
            if item is not None
            and not inventory_strong_hits(inventory_facts, str(item), material_grade_only=True)
        ]
    else:
        adequacy_missing = list(missing_elements)
    if facet_missing:
        # deterministic_no_response 对未知 flag 自动弃权——分面缺口项
        # 交回 LLM 审查属设计意图
        gap_flag = "facet_gap:" + ",".join(facet_missing[:3])
        quality_flags.append(gap_flag[:120])

    # 独立补搜触发收口到 evidence_policy——证据不充分即全局补搜,
    # 邻页无关图片不再抑制, 非否决项同样有补救通道
    prohibition_spec = await collect_prohibition_items(
        pair, session, response, evidence_items
    )

    adequacy = EvidenceAdequacy(
        has_response=bool(pair.has_response),
        is_reviewable=is_reviewable_requirement(pair.requirement),
        is_mandatory=bool(pair.is_mandatory),
        has_source_pages=bool(source_pages),
        completeness=completeness,
        missing_elements=tuple(str(item) for item in adequacy_missing if item is not None),
        evidence_anchor=(response.evidence_anchor if response else None),
        facet_missing_keys=facet_missing,
        has_local_image_evidence=any(
            item.section == SECTION_IMAGE for item in evidence_items
        ),
        is_prohibition=prohibition_spec is not None,
    )
    if should_search_independently(adequacy):
        evidence_items.extend(
            await self._independent_evidence_search(pair, session=session)
        )

    evidence_items.extend(_raw_rows_to_items(raw_evidence_rows))

    knowledge_context_text, knowledge_context_refs = await build_knowledge_context(
        self, pair, session, response, project_id
    )
    if knowledge_context_text:
        evidence_items.append(EvidenceItem(
            section=SECTION_KNOWLEDGE,
            text=knowledge_context_text,
            content=knowledge_context_text,
            label="knowledge_context",
        ))

    bundle = ReviewEvidenceBundle(
        response_content=response.content if response and response.content else "",
        source_pages=source_pages,
        completeness=completeness,
        missing_elements=missing_elements,
        linked_to=response.linked_to if response else None,
        evidence_type=(
            response.evidence_type.value
            if response and response.evidence_type
            else None
        ),
        evidence_anchor=response.evidence_anchor if response else None,
        relevance_score=response.relevance_score if response else None,
        relevance_note=response.relevance_note if response else None,
        image_evidence="",
        cross_category_evidence="",
        quality_flags=quality_flags,
        retrieval_chunk_pages=retrieval_chunk_pages,
        retrieval_primary_pages=retrieval_primary_pages,
        retrieval_hit_sub_queries=retrieval_hit_sub_queries,
        cross_category_evidence_sources=cross_evidence_sources,
        knowledge_context_text="",
        knowledge_context_refs=knowledge_context_refs,
        raw_evidence_excerpts="",
        evidence_pack_text="",
        evidence_items=evidence_items,
    )
    # 账本块也是可裁证据, 必须与其余证据共享同一总预算(只封顶了账本自身)
    bundle = attach_ledger_evidence(self, pair, facets, bundle)
    return apply_round0_evidence_pack(
        bundle,
        facets,
        pair.requirement_id,
        prefetch_ms=(time.perf_counter() - prefetch_started) * 1000,
    )


def attach_ledger_evidence(
    self: "ReviewOrchestrator",
    pair: MatchedPair,
    facets: Sequence[EvidenceFacet],
    bundle: ReviewEvidenceBundle,
) -> ReviewEvidenceBundle:
    """
    职责: 把项目已核实材料（材料级/提及级）注入证据包并标注等级与(卷,页)
    不做: 构建账本、改符合度、二次检索
    """
    ledger = getattr(self, "_evidence_ledger", None)
    if not isinstance(ledger, EvidenceLedger):
        ledger = EvidenceLedger()
    req_id = pair.requirement_id
    own_pages = set(bundle.retrieval_chunk_pages or ())
    injected: list = []
    for atom in ledger.atoms_for_facets(facets):
        sources = set(atom.source_requirement_ids)
        if sources and sources <= {req_id}:
            continue
        if atom.page is not None and atom.page in own_pages:
            continue
        injected.append(atom)
    bundle.ledger_atoms = injected
    bundle.ledger_evidence = EvidenceLedger.to_budgeted_prompt_block(
        injected,
        facets,
        char_budget=int(settings.REVIEW_LEDGER_CHAR_BUDGET),
        per_kind_limit=int(settings.REVIEW_LEDGER_ATOMS_PER_KIND),
    )
    # 账本块进入统一证据包预算, 不再与其余证据区各自累加
    if bundle.ledger_evidence:
        bundle.evidence_items.append(EvidenceItem(
            section=SECTION_LEDGER,
            text=bundle.ledger_evidence,
            content=bundle.ledger_evidence,
            label="project_ledger",
        ))
    try:
        pair._gate_ledger_atoms = [atom.to_dict() for atom in injected]  # type: ignore[attr-defined]
    except Exception:
        pass
    return bundle


# ---------------------------------------------------------------------------
# R1: 投标原文佐证摘录格式化
# ---------------------------------------------------------------------------

def _format_raw_evidence_excerpts(
    rows: list[dict[str, Any]],
    facets: Sequence[EvidenceFacet] = (),
) -> str:
    """把 bid_response_chunks join Chunk 的查询结果格式化为 Prompt 注入段。

 职责: 从命中 chunk 集合中筛选 top N（: 传入 facets 时分面保底,
 否则按 role/similarity 全局排序）、按字符上限截断，产出带页码与
 判读规则的"【投标原文佐证】"文本段。
 不做: SQL 查询、LLM 调用、token 预算计算、选择/判定逻辑（归 evidence_policy）。

 Args:
 rows: 每行 dict 含 role/page/similarity/content 字段（content 必非空）
 facets: 可选分面序列; 为空时保持历史 (role_rank, -similarity) 排序

 Returns:
 Prompt 注入文本；rows 为空或 settings 关闭时返回空串。
 """
    from app.core.config import settings
    from app.core.retriever.evidence_ranker import is_low_information_content

    top_n = getattr(settings, "REVIEW_RAW_EVIDENCE_CHUNKS", 0)
    char_limit = getattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 500)
    if top_n <= 0 or not rows:
        return ""

    usable = [
        row
        for row in rows
        if not is_low_information_content(str(row.get("content") or ""))
    ]
    if not usable:
        return ""
    selected = select_facet_covered_rows(usable, facets, top_n)

    lines: list[str] = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "【投标原文佐证（来自检索命中片段，按命中类型与相似度排序）】",
        (
            "审查规则：若上方投标响应摘要与下方原文佐证在事实/数值/范围上不一致，"
            "以原文为准；并在 comment 中明确指出差异。原文佐证不可作为证明本要求被响应的"
            "唯一依据——仍需结合招标要求语义判断。"
        ),
    ]
    for row in selected:
        rendered = _render_raw_row(row, char_limit)
        if rendered:
            lines.append(rendered)
    return "\n".join(lines)


def _render_raw_row(row: dict[str, Any], char_limit: int) -> str:
    """渲染单条原文佐证行（供整段格式化与证据条目共用同一口径）。"""
    content = str(row.get("content") or "").strip().replace("\n", " ")
    if not content:
        return ""
    if len(content) > char_limit:
        content = content[:char_limit] + "…"
    page = row.get("page")
    page_label = f"第{page}页" if page is not None else "页码未知"
    role = row.get("role") or "primary"
    role_label = {
        "primary": "正文命中",
        "deep": "深搜补充",
        "toc": "目录导航",
        "image": "图片证据",
    }.get(role, role)
    sim = row.get("similarity") or 0.0
    section = str(row.get("section") or "").strip()
    section_label = f"·章节={section}" if section else ""
    return f"▶ [{role_label}·{page_label}{section_label}·相似度={sim:.2f}] {content}"


def _raw_rows_to_items(rows: list[dict[str, Any]]) -> list[EvidenceItem]:
    """把 bid_response_chunks 命中行转成原文佐证候选条目。

 职责: 只做行→条目的结构转换与低信息过滤; 取多少条、占多少预算由
 ``evidence_pack_budget`` 统一决定, 本函数不再自行截断 top_n。
 """
    from app.core.retriever.evidence_ranker import is_low_information_content

    char_limit = int(getattr(settings, "REVIEW_RAW_EVIDENCE_CHARS", 500))
    items: list[EvidenceItem] = []
    for row in rows:
        content = str(row.get("content") or "")
        if not content.strip() or is_low_information_content(content):
            continue
        rendered = _render_raw_row(row, char_limit)
        if not rendered:
            continue
        items.append(EvidenceItem(
            section=SECTION_RAW,
            text=rendered,
            content=content,
            label=f"raw:{row.get('chunk_id')}",
            page=row.get("page"),
            chunk_id=row.get("chunk_id"),
            role=str(row.get("role") or "primary"),
            similarity=float(row.get("similarity") or 0.0),
            matched_sub_query=row.get("matched_sub_query"),
        ))
    return items


def apply_round0_evidence_pack(
    bundle: ReviewEvidenceBundle,
    facets: Sequence[EvidenceFacet],
    requirement_id: Optional[int] = None,
    prefetch_ms: Optional[float] = None,
) -> ReviewEvidenceBundle:
    """按 round 0 全局预算组包并回填 bundle 各证据区。

 职责: 把候选条目收敛为"模型可见"的最小充分集, 回填既有 *_evidence 字段,
 并在候选已覆盖但可见集缺分面时写入 ``visible_facet_gap`` 质量旗标。
 不做: 不检索、不改符合度、不动骨架字段。
 """
    result = assemble_evidence_pack(
        bundle.evidence_items, facets, resolve_pack_budget(0)
    )
    bundle.image_evidence = result.image_evidence
    bundle.raw_evidence_excerpts = result.raw_evidence_excerpts
    bundle.ledger_evidence = result.ledger_evidence
    bundle.cross_category_evidence = result.cross_category_evidence
    bundle.knowledge_context_text = result.knowledge_context_text
    # 闸门证据口径保持不变: 仍以模型可见的原文佐证为准(不扩大触发面)
    bundle.evidence_pack_text = result.raw_evidence_excerpts.strip()

    if result.visible_facet_missing:
        gap_flag = "visible_facet_gap:" + ",".join(result.visible_facet_missing[:3])
        bundle.quality_flags.append(gap_flag[:120])

    if result.candidate_count:
        meta = result.as_log_meta()
        if prefetch_ms is not None:
            meta["prefetch_ms"] = round(prefetch_ms, 1)
        logger.info(
            "[AI审查] 证据包预算: requirement_id={}, {}",
            requirement_id,
            meta,
        )
    return bundle
