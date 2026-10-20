"""单对审查（R2-3 Step 7 抽出，最大子模块）

涵盖以下原 `ReviewOrchestrator` 私有方法（8 个）：

- `review_single_pair`（107 行）：Phase 2 LLM 调用入口
- `try_pre_llm_skip`：B4 纯文件形态 skip（按响应给符合/不符合）
- `fetch_image_evidence_for_pair`（52 行）：source_pages 图像证据
- `independent_evidence_search`（68 行）：否决项 fallback 关键词搜索
- `collect_chunk_pages_for_pair`（58 行）：PageAnchorVerifyGate chunk_pages 提供方
- `build_review_prompt`（60 行）：对比审查 Prompt 构造
- `build_no_response_prompt`（34 行）：未响应审查 Prompt 构造
- `resolve_cd_schema`（14 行）：json_object 灰度判定

外部测试侧已知调用路径（拆分后必须仍 work）:
- `orchestrator._build_review_prompt(...)`（test_review_contracts 5 用例）
- `orchestrator._resolve_cd_schema = lambda: None`（实例替换）
- `await orch._collect_chunk_pages_for_pair(pair, session=...)` （test_reviewer_session_decouple）

`call_llm_review` 已迁出至 `review_llm_invoke.py`（LLM 调用协议与本模块的单对编排
职责独立演化）。其 `_call_llm_review` forwarder 及 `inspect.getsource` 必须含
`max_retries` / `temperature=0.0` 的静态字面量契约仍由 `reviewer.py` 承担。

签名规范: 子模块函数去前导下划线, 第一参数保留 `self`。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.input_budget import clip_text_keep_ends
from app.core.ai.reviewer_parts.evidence_pack_budget import (
    assemble_evidence_pack,
    resolve_pack_budget,
)
from app.core.ai.reviewer_parts.review_input_gate import (
    build_oversize_pending_result,
    fit_prompt_within_hard_cap,
)
from app.core.config import settings
from app.core.matcher.requirement_matcher import MatchedPair
from app.core.review_contracts import (
    ReviewEvidenceBundle,
    parse_json_list,
    parse_page_numbers,
)
from app.models.review_result import ComplianceLevel, ReviewResult
from app.schemas.review import ExtractedReviewResult

if TYPE_CHECKING:
    from app.core.ai.llm_retry_resume import LlmRetryResumeState
    from app.core.ai.reviewer import ReviewOrchestrator
    from app.core.ai.reviewer_parts.evidence_pack_budget import EvidenceItem


# ---------------------------------------------------------------------------
# 评分项·重点评审补充（P2）—— 实现已迁至 scoring_rubric（本文件贴近 800 行硬阈值），
# 此处 re-export 兼容既有 import 契约（test_pair_review_input_guard 直接引用）。
# ---------------------------------------------------------------------------
from app.core.ai.reviewer_parts.scoring_rubric import (  # noqa: E402
    expects_rule_assessments,
)
from app.core.ai.reviewer_parts.scoring_rubric import (
    scoring_supplement as _scoring_supplement,
)

# ---------------------------------------------------------------------------
# Prompt 构造
# ---------------------------------------------------------------------------


def _review_evidence_budgets(reduction_round: int) -> tuple[int, int]:
    """按收缩轮次返回 (知识库预算, 证据预算) 字符数。

 知识库参考衰减更快(辅助, 优先牺牲), 投标原文/图片佐证衰减较慢并保底,
 保证招标要求与投标响应摘要等骨架不受影响。: 预算口径迁入 ``evidence_pack_budget.resolve_pack_budget`` 单点,
 本函数保留为兼容 API（证据预算即证据包总帽）。
 """
    budget = resolve_pack_budget(reduction_round)
    return budget.knowledge_chars, budget.pack_chars


def _shrink_review_evidence(
    merged_evidence: str,
    raw_evidence: str,
    knowledge_context: str,
    reduction_round: int,
) -> tuple[str, str, str]:
    """按收缩轮次对可裁证据块做首尾保留式压缩(骨架不在此处)。

    优先级: 知识库参考(最先丢) > 投标原文/图片佐证。均用 clip_text_keep_ends
    保留首尾关键信息, 避免中段语义被腰斩, 确保 LLM 仍能理解。
    """
    knowledge_budget, evidence_budget = _review_evidence_budgets(reduction_round)
    shrunk_knowledge = (
        clip_text_keep_ends(knowledge_context, knowledge_budget)
        if knowledge_context else ""
    )
    shrunk_merged = (
        clip_text_keep_ends(merged_evidence, evidence_budget)
        if merged_evidence else ""
    )
    shrunk_raw = (
        clip_text_keep_ends(raw_evidence, evidence_budget)
        if raw_evidence else ""
    )
    return shrunk_merged, shrunk_raw, shrunk_knowledge


def _shrink_bundle_sections(
    pair: MatchedPair,
    evidence_bundle: ReviewEvidenceBundle,
    knowledge_context: str,
    reduction_round: int,
) -> tuple[str, str, str, str]:
    """收缩轮次的证据重组: 返回 (合并证据, 原文佐证, 账本, 知识库)。: 携带候选条目的证据包按同一全局预算重新组包（每分面仍保底一条）;
 旧式 bundle（无 ``evidence_items``，历史调用方与单测路径）退化为按段截断。
 """
    merged_evidence = evidence_bundle.merged_evidence_text or ""
    raw_evidence = evidence_bundle.raw_evidence_excerpts or ""
    ledger_evidence = getattr(evidence_bundle, "ledger_evidence", "") or ""
    knowledge_block = knowledge_context or ""

    items = list(getattr(evidence_bundle, "evidence_items", None) or [])
    if items:
        from app.core.retriever.evidence_coverage import build_evidence_facets

        requirement = getattr(pair, "requirement", None)
        facets = build_evidence_facets(
            getattr(requirement, "description", "") or "",
            parse_json_list(getattr(requirement, "sub_requirements", None)),
            keywords=parse_json_list(getattr(requirement, "keywords", None)),
        )
        result = assemble_evidence_pack(
            items, facets, resolve_pack_budget(reduction_round)
        )
        merged_parts = [result.image_evidence, result.cross_category_evidence]
        return (
            "\n".join(part for part in merged_parts if part).strip(),
            result.raw_evidence_excerpts,
            result.ledger_evidence,
            result.knowledge_context_text,
        )

    merged_evidence, raw_evidence, knowledge_block = _shrink_review_evidence(
        merged_evidence, raw_evidence, knowledge_block, reduction_round,
    )
    _, evidence_budget = _review_evidence_budgets(reduction_round)
    if ledger_evidence:
        ledger_evidence = clip_text_keep_ends(ledger_evidence, evidence_budget)
    return merged_evidence, raw_evidence, ledger_evidence, knowledge_block


def build_review_prompt(
    self: "ReviewOrchestrator",
    pair: MatchedPair,
    evidence_bundle: ReviewEvidenceBundle,
    knowledge_context: str = "",
    *,
    reduction_round: int = 0,
) -> str:
    """构建对比审查 Prompt

 reduction_round>0 时: 仅对知识库参考与证据佐证按递减预算做首尾保留式
 压缩, 招标要求/投标响应摘要/输出 schema/评分规则等骨架始终完整; round=0 行为不变。
 """
    # 函数内 import 避免 reviewer 主模块循环 import + 兼容测试 monkeypatch
    from app.core.ai.reviewer import _get_review_prompts

    merged_evidence = evidence_bundle.merged_evidence_text or ""
    raw_evidence = evidence_bundle.raw_evidence_excerpts or ""
    knowledge_block = knowledge_context or ""
    ledger_evidence = getattr(evidence_bundle, "ledger_evidence", "") or ""
    if reduction_round > 0:
        (
            merged_evidence,
            raw_evidence,
            ledger_evidence,
            knowledge_block,
        ) = _shrink_bundle_sections(
            pair, evidence_bundle, knowledge_block, reduction_round,
        )

    response_content = evidence_bundle.response_content
    if merged_evidence:
        response_content = (
            response_content + "\n\n" + merged_evidence
            if response_content
            else merged_evidence
        )
    if ledger_evidence:
        response_content = (
            response_content + "\n\n" + ledger_evidence
            if response_content
            else ledger_evidence
        )

    # R1: 注入投标原文佐证。摘要与原文不一致时 Prompt 明确要求以原文为准。
    if raw_evidence:
        response_content = (
            response_content + "\n" + raw_evidence
            if response_content
            else raw_evidence
        )

    if evidence_bundle.quality_flags:
        response_content = (
            response_content
            + "\n\n【证据完整性提示】\n- "
            + "\n- ".join(evidence_bundle.quality_flags)
        )

    missing_elements = (
        "、".join(evidence_bundle.missing_elements)
        if evidence_bundle.missing_elements else "无"
    )
    linked_to = (
        str(evidence_bundle.linked_to)
        if evidence_bundle.linked_to is not None
        else "null"
    )
    completeness = evidence_bundle.completeness or "unknown"
    source_pages = ",".join(str(page) for page in evidence_bundle.source_pages)

    _, user_tpl, _ = _get_review_prompts()
    from app.core.i18n import translate_token

    category_raw = pair.category or ""
    prompt = user_tpl.format(
        requirement_id=pair.requirement_id,
        category=category_raw,
        category_label=translate_token(category_raw) if category_raw else "未知",
        requirement_description=pair.requirement.description,
        is_mandatory="是" if pair.is_mandatory else "否",
        response_id=pair.response_id or "null",
        response_content=response_content,
        evidence_type=evidence_bundle.evidence_type or "无",
        source_pages=source_pages,
        completeness=completeness,
        linked_to=linked_to,
        missing_elements=missing_elements,
        evidence_anchor=evidence_bundle.evidence_anchor or "无",
        relevance_score=str(evidence_bundle.relevance_score) if evidence_bundle.relevance_score is not None else "未知",
        relevance_note=evidence_bundle.relevance_note or "无",
    )
    if knowledge_block:
        prompt += (
            "\n\n"
            + knowledge_block
            + "\n\n请注意：知识库参考不得覆盖招标要求和投标文件事实；"
            "审查结论必须优先依据招标文件与投标响应。"
        )
    prompt += _scoring_supplement(pair)
    from app.core.ai.gates.review.prohibition_policy import (
        prohibition_supplement as _prohibition_supplement,
    )
    prompt += _prohibition_supplement(pair)
    return prompt


def build_no_response_prompt(
    self: "ReviewOrchestrator",
    pair: MatchedPair,
    evidence_bundle: ReviewEvidenceBundle,
    knowledge_context: str = "",
    *,
    reduction_round: int = 0,
) -> str:
    """构建未响应审查 Prompt

 reduction_round>0 时: 按递减预算压缩图片证据摘要与知识库参考,
 招标要求等骨架不动; round=0 行为不变。
 """
    from app.core.ai.reviewer import _get_review_prompts

    merged_evidence = evidence_bundle.merged_evidence_text or ""
    knowledge_block = knowledge_context or ""
    ledger_evidence = getattr(evidence_bundle, "ledger_evidence", "") or ""
    raw_excerpts_shrunk: str | None = None
    if reduction_round > 0:
        (
            merged_evidence,
            raw_excerpts_shrunk,
            ledger_evidence,
            knowledge_block,
        ) = _shrink_bundle_sections(
            pair, evidence_bundle, knowledge_block, reduction_round,
        )

    _, _, no_resp_tpl = _get_review_prompts()
    from app.core.i18n import translate_token

    category_raw = pair.category or ""
    base = no_resp_tpl.format(
        requirement_id=pair.requirement_id,
        category=category_raw,
        category_label=translate_token(category_raw) if category_raw else "未知",
        requirement_description=pair.requirement.description,
        is_mandatory="是" if pair.is_mandatory else "否"
    )
    if merged_evidence:
        base += (
            "\n\n## 补充信息：投标文件中可能存在以扫描件形式提供的相关材料"
            + merged_evidence
            + "\n请结合上述图片附件信息重新判断投标人是否实际提供了相关材料。"
        )
    if ledger_evidence:
        base += "\n\n" + ledger_evidence
    raw_excerpts = (
        raw_excerpts_shrunk
        if raw_excerpts_shrunk is not None
        else (evidence_bundle.raw_evidence_excerpts or "")
    ).strip()
    if raw_excerpts:
        base += (
            "\n\n## 检索命中原文\n"
            + raw_excerpts
            + "\n证据原文已给出时禁止写未提供，应判定为检索已命中待复核。"
        )
    if evidence_bundle.quality_flags:
        base += (
            "\n\n## 系统质量提示\n- "
            + "\n- ".join(evidence_bundle.quality_flags)
        )
    if knowledge_block:
        base += (
            "\n\n"
            + knowledge_block
            + "\n\n请注意：知识库参考只能帮助判断常规规范和证据类型，"
            "不能替代投标文件实际响应。"
        )
    base += _scoring_supplement(pair)
    from app.core.ai.gates.review.prohibition_policy import (
        prohibition_supplement as _prohibition_supplement,
    )
    base += _prohibition_supplement(pair)
    return base


def resolve_cd_schema(self: "ReviewOrchestrator") -> dict | None:
    """B2.8 — 按灰度比例决定是否启用 json_object 约束（review 流程）。

    reviewer 使用 chat + OutputValidator（非 chat_with_validation），
    此处使用 json_object 模式保证 provider 返回合法 JSON。
    """
    from app.core.ai.constrained_decoding import should_use_constrained_decoding
    if should_use_constrained_decoding(
        self._project_id,
        settings.CONSTRAINED_DECODING_RATIO_REVIEW,
    ):
        return {"type": "json_object"}
    return None


# ---------------------------------------------------------------------------
# 图像证据 / 独立证据搜索 / 检索 chunk 页
# ---------------------------------------------------------------------------


async def fetch_image_evidence_for_pair(
    self: "ReviewOrchestrator",
    pair: MatchedPair,
    session: Optional[AsyncSession] = None,
) -> list["EvidenceItem"]:
    """获取匹配对来源页面的图片证据条目

 从投标响应的 source_pages 解析页码, 查询这些页面(+-2页)中包含图片标注的
 chunk。: 返回结构化 ``EvidenceItem`` 而非拼接字符串——是否进入
 Prompt、占多少预算由 ``evidence_pack_budget`` 统一决定, 本函数只负责取数。
 """
    from app.core.ai.reviewer_parts.evidence_pack_budget import (
        SECTION_IMAGE,
        EvidenceItem,
    )

    if not pair.response or not pair.response.source_pages:
        return []

    try:
        page_nums = {
            int(p.strip())
            for p in str(pair.response.source_pages).split(",")
            if p.strip().isdigit()
        }
    except (ValueError, AttributeError):
        return []

    if not page_nums or not pair.response.document_id:
        return []

    from app.core.retriever.semantic_retriever import SemanticRetriever
    retriever = SemanticRetriever(session or self.db)
    image_chunks = await retriever.fetch_image_chunks_by_pages(
        document_id=pair.response.document_id,
        page_nums=page_nums,
        expand_range=2,
    )

    items: list[EvidenceItem] = []
    for chunk in image_chunks:
        page_info = f"第{chunk.page_num}页" if chunk.page_num else "未知页"
        items.append(EvidenceItem(
            section=SECTION_IMAGE,
            text=f"▶ ({page_info})\n{chunk.content}\n",
            content=str(chunk.content or ""),
            label=f"image:{chunk.chunk_id}",
            page=chunk.page_num,
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            role="image",
            similarity=float(chunk.similarity or 0.0),
        ))
    return items


async def independent_evidence_search(
    self: "ReviewOrchestrator",
    pair: MatchedPair,
    session: Optional[AsyncSession] = None,
) -> list["EvidenceItem"]:
    """对证据不充分的可审项做独立的全文档证据补搜

 当 source_pages 为空(级联失败场景)或分面存在缺口时, 绕过提取阶段的结果,
 直接用招标要求关键词在全文档中搜索图片与结构化证据 chunk。: 召回口径不变(图片/结构化各 15 条), 但返回结构化 ``EvidenceItem``
 而非拼接字符串——广召回留在候选层, 是否进入 Prompt 由证据包预算器决定。
 """
    from app.core.ai.reviewer_parts.evidence_pack_budget import (
        SECTION_INDEPENDENT,
        EvidenceItem,
    )

    if not pair.response or not pair.response.document_id:
        return []

    from app.core.retriever.evidence_priors import augment_keywords_with_priors
    from app.core.retriever.query_decomposer import get_query_decomposer
    from app.core.retriever.semantic_retriever import SemanticRetriever

    retriever = SemanticRetriever(session or self.db)
    decomposer = get_query_decomposer()
    # 关键词经类别证据先验增强,全局图片检索按要求相关性截断
    keywords = augment_keywords_with_priors(
        decomposer.extract_keywords(pair.requirement.description),
        getattr(pair.requirement, "category", None),
    )

    # 分面查询并入关键词来源——多实体/并列子面(人工确认的子要求)
    # 的补搜不再只依赖整句关键词分词。去重保序, 分面数受 MAX_FACETS 上限约束。
    # LLM 关键词并入分面来源; 追加 facet.key(干净短语)而非 facet.query
    # ("要求前缀+词"含空格无法字面命中全局图片/结构化 chunk)。
    from app.core.retriever.evidence_coverage import build_evidence_facets
    facets = build_evidence_facets(
        pair.requirement.description,
        parse_json_list(getattr(pair.requirement, "sub_requirements", None)),
        keywords=parse_json_list(getattr(pair.requirement, "keywords", None)),
    )
    seen_keywords = set(keywords)
    for facet in facets:
        if facet.key not in seen_keywords:
            keywords.append(facet.key)
            seen_keywords.add(facet.key)

    from app.core.ai.gates.review.prohibition_policy import prohibition_search_terms
    for term in prohibition_search_terms(
        pair.requirement.description if pair.requirement else ""
    ):
        if term not in seen_keywords:
            keywords.append(term)
            seen_keywords.add(term)

    if not keywords:
        return []

    image_chunks = await retriever.fetch_image_chunks_globally(
        document_id=pair.response.document_id,
        keywords=keywords,
        query=pair.requirement.description,
    )
    structured_chunks = await retriever.fetch_keyword_chunks_globally(
        document_id=pair.response.document_id,
        keywords=keywords,
        query=pair.requirement.description,
    )

    merged_chunks = []
    seen_chunk_ids: set[int] = set()
    for chunk in [*image_chunks, *structured_chunks]:
        if chunk.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk.chunk_id)
        merged_chunks.append(chunk)

    items: list[EvidenceItem] = []
    for chunk in merged_chunks:
        page_info = f"第{chunk.page_num}页" if chunk.page_num else "未知页"
        items.append(EvidenceItem(
            section=SECTION_INDEPENDENT,
            text=f"▶ ({page_info})\n{chunk.content}\n",
            content=str(chunk.content or ""),
            label=f"independent:{chunk.chunk_id}",
            page=chunk.page_num,
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            role="deep",
            similarity=float(chunk.similarity or 0.0),
        ))

    if items:
        logger.debug(
            f"独立证据搜索: requirement={pair.requirement_id}, "
            f"keywords={keywords[:5]}, 命中{len(items)}个chunk"
        )
        project_id = getattr(getattr(pair, "requirement", None), "project_id", None)
        target = session if session is not None else getattr(self, "db", None)
        if project_id is not None and target is not None:
            try:
                from app.core.ai.reviewer_parts.evidence_ledger.builder import (
                    merge_inventory_atoms,
                )
                from app.core.ai.reviewer_parts.evidence_ledger.contracts import (
                    EvidenceLedger,
                )
                from app.core.retriever.material_inventory import (
                    SOURCE_REVIEW_SEARCH,
                    merge_search_hits_into_inventory,
                )
                from app.models.document import Document

                document_id = getattr(pair.response, "document_id", None)
                bid_part_by_document: dict[int, Optional[str]] = {}
                if document_id is not None:
                    part = (
                        await target.execute(
                            select(Document.bid_part).where(
                                Document.id == int(document_id)
                            )
                        )
                    ).scalar_one_or_none()
                    bid_part_by_document[int(document_id)] = part
                facts = await merge_search_hits_into_inventory(
                    target,
                    int(project_id),
                    merged_chunks,
                    keywords,
                    bid_part_by_document=bid_part_by_document,
                    source_channel=SOURCE_REVIEW_SEARCH,
                )
                await target.commit()
                ledger = getattr(self, "_evidence_ledger", None)
                if facts and isinstance(ledger, EvidenceLedger):
                    self._evidence_ledger = merge_inventory_atoms(ledger, facts)
                from app.core.retriever.material_inventory import MaterialInventory

                inventory = getattr(self, "_material_inventory", None)
                if facts and isinstance(inventory, MaterialInventory):
                    self._material_inventory = inventory.with_facts(facts)
            except Exception as err:
                logger.debug("审查补搜库存回写降级: {}", err)
                try:
                    await target.rollback()
                except Exception:
                    pass
    return items


async def collect_chunk_pages_for_pair(
    self: "ReviewOrchestrator",
    pair: MatchedPair,
    session: Optional[AsyncSession] = None,
) -> set[int]:
    """S.2.4 / Fix-02: 提供 PageAnchorVerifyGate 所需的 chunk_pages 参照集。

 优先级:
 1. bid_response_chunks（本条响应的真实检索命中页，精确且小集合）
 2. 文档全部 chunk 页码集合（历史数据未写入 chunks 表时的兜底）: 新增 session 参数, 并发协程显式传入短 session 即可避免
 碰共享 self.db 触发连接池竞争。未传入时回退到 self.db (向后兼容)。
 """
    if not pair.response or not getattr(pair.response, "document_id", None):
        return set()

    target_session = session if session is not None else self.db

    # Fix-02: 优先按 bid_response 维度读精确命中集（无缓存，粒度 per-response）
    if getattr(pair.response, "id", None) is not None:
        try:
            from app.models.bid_response_chunk import BidResponseChunk
            result = await target_session.execute(
                select(BidResponseChunk.page_num)
                .where(BidResponseChunk.bid_response_id == pair.response.id)
                .distinct()
            )
            precise_pages = {int(p) for (p,) in result.all() if p is not None}
            if precise_pages:
                return precise_pages
        except Exception as err:
            logger.debug(
                f"_collect_chunk_pages_for_pair: bid_response_chunks 读取降级, "
                f"回退文档全集: resp={pair.response.id}, err={err}"
            )

    # 兜底：文档全部 chunk 页集合（历史数据或读取失败时），缓存 per-document
    doc_id = pair.response.document_id
    cached = self._chunk_pages_cache.get(doc_id)
    if cached is not None:
        return cached
    try:
        from app.models.chunk import Chunk
        result = await target_session.execute(
            select(Chunk.page_num).where(Chunk.document_id == doc_id).distinct()
        )
        pages = {int(p) for (p,) in result.all() if p is not None}
    except Exception as e:
        logger.debug(
            f"_collect_chunk_pages_for_pair degraded: doc={doc_id}, err={e}"
        )
        pages = set()
    self._chunk_pages_cache[doc_id] = pages
    return pages


# ---------------------------------------------------------------------------
# B4 pre_llm Gate / LLM 主调用
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# B4 pre_llm skip（纯文件形态 + 按响应给意见）
# ---------------------------------------------------------------------------


def _requirement_forbids_algorithm_skip(pair: "MatchedPair") -> bool:
    """评分项与重点评审项必须走 LLM，禁止算法 skip。"""
    req = getattr(pair, "requirement", None)
    if req is None:
        return False
    kind = getattr(req, "requirement_kind", "normal") or "normal"
    if kind == "scoring_item":
        return True
    return bool(getattr(req, "is_key_review", 0))


def _pair_response_present(pair: "MatchedPair") -> bool:
    """投标响应是否有可审内容。空串与未确认占位视为无响应。"""
    from app.core.extractor.evidence_verdict import is_unconfirmed_response_content

    resp = getattr(pair, "response", None)
    if resp is not None:
        content = (getattr(resp, "content", None) or "").strip()
        if not content or is_unconfirmed_response_content(content):
            return False
        return True
    return bool(getattr(pair, "has_response", False))


async def try_pre_llm_skip(
    self: "ReviewOrchestrator",
    project_id: int,
    pair: MatchedPair,
) -> Optional[ExtractedReviewResult]:
    """B4 pre_llm: 纯文件形态要求 skip 判定。

    仅在 INVESTIGATE_ALGORITHM_FIRST_ENABLED 且项目命中 RATIO 时，
    命中 enforce_patch + severity 为 enforce/reject + _skip_llm_call 才真正 skip LLM。
    评分项/重点评审即使 Gate 给出补丁也不得 skip。
    Gate 被降级为 advisory 或 off 时只执行度量，不跳过 LLM。
    """
    from app.core.ai.gates import (
        build_pipeline_for,
        should_use_algorithm_first,
    )
    from app.core.ai.gates.base import GateContext
    from app.core.ai.gates.review.physical_form_skip import SKIP_COMMENT_MATCHED

    algo_first = should_use_algorithm_first(project_id)
    req = pair.requirement

    _evidence = {
        "requirement_text": (
            req.description if req and req.description else ""
        ),
        "is_mandatory": pair.is_mandatory,
        "has_response": _pair_response_present(pair),
        "category": pair.category,
        "requirement_kind": getattr(req, "requirement_kind", "normal") if req else "normal",
        "is_key_review": bool(getattr(req, "is_key_review", 0)) if req else False,
    }
    _ctx = GateContext(
        flow="investigate", stage="pre_llm",
        target=pair, evidence=_evidence,
        config={"project_id": project_id},
    )
    pipeline = build_pipeline_for("investigate")
    verdicts = pipeline.run(_ctx)

    if not algo_first:
        return None

    if _requirement_forbids_algorithm_skip(pair):
        return None

    for v in verdicts:
        if (
            v.verdict == "enforce_patch"
            and v.severity in ("enforce", "reject")
            and v.suggested_patch
            and v.suggested_patch.get("_skip_llm_call")
        ):
            patch = v.suggested_patch
            compliance = patch.get("compliance_level", "fully")
            return ExtractedReviewResult(
                requirement_id=pair.requirement_id,
                bid_response_id=pair.response_id,
                category=pair.category,
                requirement_summary=req.description[:50] if req and req.description else "",
                response_summary=(patch.get("comment") or "")[:100],
                is_compliant=compliance == "fully",
                compliance_level=compliance,
                score=patch.get("score", 95),
                comment=patch.get("comment") or SKIP_COMMENT_MATCHED,
                risk_level=patch.get("risk_level", "low"),
            )

    return None


def _short_circuit_unreviewable(
    self: "ReviewOrchestrator",
    project_id: int,
    pair: MatchedPair,
) -> Optional[ReviewResult]:
    """提取失败 / 证据不足项的前置短路（P0-D、）。

 这两类项既不按正常响应审查(内容是占位符), 也不进入"未响应=可能不符合"
 推定, 直接产出人工复核结论; 不满足短路条件时返回 None。
 """
    from app.core.extractor.evidence_verdict import is_evidence_insufficient_content
    from app.core.extractor.extraction_failure import is_extraction_failed_content

    if pair.response is None:
        return None

    if is_extraction_failed_content(pair.response.content):
        from app.core.ai.reviewer_parts.result_builder import FAILURE_COMMENT_MARKER

        result = self._create_failure_result(project_id, pair)
        result.comment = (
            f"审查失败[{FAILURE_COMMENT_MARKER}]：提取阶段模型调用失败"
            f"（超时/配额/重试耗尽），未获取响应内容，请人工复核"
        )
        result.reconcile_note = "提取失败项，未执行 AI 审查，不构成未响应推定"
        result.consistency_flag = "needs_review"
        logger.warning(
            f"要求 {pair.requirement_id}: 提取失败项直接转人工复核(跳过 LLM 审查)"
        )
        return result

    if is_evidence_insufficient_content(pair.response.content):
        result = self._create_failure_result(project_id, pair)
        result.comment = (
            "检索证据不足，未能覆盖要求分面或存在检索饥饿，已跳过自动审查，请人工复核"
        )
        result.reconcile_note = "证据不足待复核，未执行 AI 审查，不构成未响应推定"
        result.consistency_flag = "needs_review"
        logger.warning(
            f"要求 {pair.requirement_id}: 证据不足项直接转人工复核(跳过 LLM 审查)"
        )
        return result

    return None


# ---------------------------------------------------------------------------
# 单对审查主入口（Phase 2 LLM 调用 + Gate Pipeline + CoVe/Judge advisory）
# ---------------------------------------------------------------------------


async def review_single_pair(
    self: "ReviewOrchestrator",
    project_id: int,
    pair: MatchedPair,
    evidence_bundle: Optional[ReviewEvidenceBundle] = None,
    resume_state: Optional["LlmRetryResumeState"] = None,
) -> Optional[ReviewResult]:
    """审查单个匹配对（无 DB 依赖，证据已预取）: 整个方法不再持有 DB session - 知识库上下文已经在 Phase1
 预取阶段(_prefetch_evidence)写入 evidence_bundle.knowledge_context_text。
 Gate Pipeline 阶段所需的 chunk_pages 查询通过 _create_review_result 内部
 自建短 session 完成, 彻底落实 设计原则。
 """
    short_circuit = _short_circuit_unreviewable(self, project_id, pair)
    if short_circuit is not None:
        return short_circuit

    evidence_bundle = evidence_bundle or ReviewEvidenceBundle(
        response_content=pair.response.content if pair.response and pair.response.content else "",
        source_pages=parse_page_numbers(pair.response.source_pages if pair.response else None),
        completeness=pair.response.completeness if pair.response else None,
        missing_elements=[
            str(item)
            for item in parse_json_list(pair.response.missing_elements if pair.response else None)
            if item is not None
        ],
        linked_to=pair.response.linked_to if pair.response else None,
        evidence_type=(
            pair.response.evidence_type.value
            if pair.response and pair.response.evidence_type
            else None
        ),
        evidence_anchor=pair.response.evidence_anchor if pair.response else None,
        relevance_score=pair.response.relevance_score if pair.response else None,
        relevance_note=pair.response.relevance_note if pair.response else None,
        image_evidence="",
        cross_category_evidence="",
        quality_flags=[],
    )
    pack_text = (
        getattr(evidence_bundle, "evidence_pack_text", "")
        or evidence_bundle.raw_evidence_excerpts
        or evidence_bundle.merged_evidence_text
        or ""
    )
    try:
        pair._gate_evidence_pack_text = pack_text  # type: ignore[attr-defined]
    except Exception:
        pass
    knowledge_context = evidence_bundle.knowledge_context_text or ""

    if pair.has_response:
        prompt = self._build_review_prompt(pair, evidence_bundle, knowledge_context)
    else:
        prompt = self._build_no_response_prompt(pair, evidence_bundle, knowledge_context)

    def _rebuild_prompt(reduction_round: int) -> str:
        # 输入过长时按 reduction_round 语义收缩重建(骨架不动)
        if pair.has_response:
            return self._build_review_prompt(
                pair, evidence_bundle, knowledge_context,
                reduction_round=reduction_round,
            )
        return self._build_no_response_prompt(
            pair, evidence_bundle, knowledge_context,
            reduction_round=reduction_round,
        )

    # 发送前硬上限预检。禁止先发超限请求再等 provider 400/超时——
    # 后者会把单条款的证据过载放大成整批审查的超时与业务中断。
    prompt, oversize_reason = fit_prompt_within_hard_cap(prompt, _rebuild_prompt)
    if oversize_reason is not None:
        return build_oversize_pending_result(self, project_id, pair, oversize_reason)

    # B4: pre_llm Gate Pipeline — 物理形态 skip 判定
    # 任何 B4 工具层异常不得影响主审查流程
    try:
        extracted = await self._try_pre_llm_skip(project_id, pair)
    except Exception as _b4_err:
        logger.debug(f"B4 pre_llm skip 降级: {_b4_err}")
        extracted = None

    # 阶段8a: 未响应确定性判定。默认 shadow —— 只计算建议并记录影子对比,
    # extracted 保持 None, LLM 全量路径逐字段不变; 仅 enforce 模式才接管结论。
    _det_outcome = None
    if extracted is None and not pair.has_response:
        try:
            from app.core.ai.reviewer_parts.deterministic_no_response import (
                build_enforced_result,
                evaluate_no_response_pair,
            )
            _det_outcome = evaluate_no_response_pair(pair, evidence_bundle)
            extracted = build_enforced_result(pair, _det_outcome)
        except Exception as _det_err:
            logger.debug(f"确定性未响应判定降级: {_det_err}")
            _det_outcome = None
    _det_enforced = extracted is not None

    if extracted is None:
        try:
            extracted = await asyncio.wait_for(
                self._call_llm_review(
                    prompt,
                    prompt_rebuilder=_rebuild_prompt,
                    operation_label=f"要求 {pair.requirement_id}",
                    # rule_assessments 嵌套数组形态禁用 json_object 模式
                    expects_nested_array_output=expects_rule_assessments(pair),
                    # 供最小契约装配服务端权威字段
                    pair=pair,
                    # 归因到实际使用的 Prompt 分支，而非最后读取的节点
                    node_key=(
                        "bid_review.review_item" if pair.has_response
                        else "bid_review.review_no_response"
                    ),
                    resume_state=resume_state,
                ),
                timeout=self.item_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"要求 {pair.requirement_id} 审查超时（>{self.item_timeout}s）")
            extracted = None

    # 影子对比只在算法未接管时采集: enforce 接管后 extracted 即算法自身产物,
    # 与之对比等于自证, 不构成有效证据。
    if _det_outcome is not None and not _det_enforced:
        try:
            from app.core.ai.reviewer_parts.deterministic_no_response import (
                record_shadow_comparison,
            )
            record_shadow_comparison(pair, _det_outcome, extracted)
        except Exception as _shadow_err:
            logger.debug(f"确定性未响应判定影子采集降级: {_shadow_err}")

    if not extracted:
        logger.warning(f"要求 {pair.requirement_id} 审查失败，生成缺失结果")
        return self._create_failure_result(project_id, pair)

    try:
        from app.core.ai.reviewer_parts.existence_rereview import (
            maybe_rereview_existence_denial,
        )
        extracted = await maybe_rereview_existence_denial(
            self,
            pair,
            extracted,
            evidence_bundle,
            prompt_rebuilder=_rebuild_prompt,
            resume_state=resume_state,
        )
    except Exception as _exist_err:
        logger.debug(f"存在性重审降级: {_exist_err}")

    # Phase 2.5 -- _create_review_result 内 _collect_chunk_pages_for_pair
    # 需要 DB 查询, 在此显式开短 session, 不复用共享 self.db。
    from app.core.database import async_session_factory as _phase25_factory
    async with _phase25_factory() as _phase25_session:
        review_result = await self._create_review_result(
            project_id, pair, extracted, session=_phase25_session,
        )

    # Fix-08 / 问题 8: 注入过跨类别证据且最终判 fully -> 预置 needs_review
    # 动机: 跨类别证据属于间接援引，单凭 LLM 判 fully 风险较高，统一推入人工复核队列。
    try:
        if (
            evidence_bundle
            and evidence_bundle.has_cross_category_injection
            and review_result.compliance_level == ComplianceLevel.FULLY
        ):
            review_result.consistency_flag = "needs_review"
            note = "跨类别证据已被采纳，建议人工确认引用是否构成实质响应"
            if not review_result.reconcile_note:
                review_result.reconcile_note = note
            elif "跨类别证据" not in review_result.reconcile_note:
                review_result.reconcile_note = (
                    f"{review_result.reconcile_note}；{note}"
                )
    except Exception as _cross_err:
        logger.debug(f"Fix-08 cross-category needs_review 预置降级: {_cross_err}")

    # B5.10: CoVe on high-risk (not + mandatory), fail-safe
    await self._maybe_run_cove_investigate(project_id, pair, extracted, review_result)

    # B5.12: Judge sampling per-pair, fail-safe
    await self._maybe_run_judge_investigate(project_id, pair, extracted, review_result)

    return review_result
