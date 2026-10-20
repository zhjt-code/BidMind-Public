"""投标响应提取器

根据已确认的招标要求，从投标文件中提取对应的响应内容。

流程：
1. 获取已确认的招标要求列表
2. 并行对每条要求，语义检索投标文件中的相关内容（提高速度）
3. 调用 LLM 提取该要求对应的响应
4. 存入数据库

设计说明：
- 投标响应由AI自动提取，无需人工确认
- 这是本系统的核心价值所在
"""

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import LLMClient, get_review_client, retry_on_input_too_long_400
from app.core.ai.json_response import require_dict_response
from app.core.ai.prompts.bid_extract import get_bid_extract_prompt
from app.core.bid_envelope import BidEnvelope, load_bid_envelope
from app.core.config import get_ai_mode, get_review_concurrency, settings
from app.core.extractor.bid_envelope_retrieval import retrieve_first_round_evidence
from app.core.extractor.bid_extract_prompt_budget import (
    OVERSIZE_REASON,
    fit_extract_reduction_round,
    order_results_facet_first,
)
from app.core.extractor.bid_response_recovery import (
    BidRecoveryRequest,
    build_second_round_callback,
    detect_starvation_for_request,
    recover_bid_response,
    should_attempt_recovery,
)
from app.core.extractor.evidence_verdict import (
    EVIDENCE_INSUFFICIENT_CONTENT,
    HIT_UNCONFIRMED_NOTE,
    build_pack_partial_content,
    decide_bid_response_persist,
    pack_has_substantial_hits,
)
from app.core.extractor.extraction_failure import (
    ExtractionFailedSignal,
    build_extraction_failed_response,
)
from app.core.extractor.scoring.scoring_prompt_block import (
    build_query_extension,
    build_scoring_context,
)
from app.core.observability.guard_instrument import guard_observe
from app.core.retriever.context_reassembler import ContextReassembler
from app.core.retriever.evidence_coverage import (
    build_evidence_facets,
)
from app.core.retriever.evidence_pipeline.assemble_package import assemble_evidence_package
from app.core.retriever.evidence_pipeline.contracts import RetrievalQuery
from app.core.retriever.evidence_priors import format_starvation_note
from app.core.retriever.semantic_retriever import SearchResult, SemanticRetriever
from app.core.review_contracts import (
    parse_json_list,
    reviewable_requirement_conditions,
)
from app.models.bid_response import BidResponse, EvidenceType
from app.models.requirement import Requirement
from app.schemas.bid_response import ExtractedBidResponse

# 证明材料类型映射
EVIDENCE_TYPE_MAP = {
    "certificate": EvidenceType.CERTIFICATE,
    "proposal": EvidenceType.PROPOSAL,
    "commitment": EvidenceType.COMMITMENT,
    "reference": EvidenceType.REFERENCE,
    "other": EvidenceType.OTHER,
}


@dataclass(frozen=True)
class _BidExtractRetryPayload:
    """投标响应提取的兜底重试状态。"""

    reduction_round: int = 0


class BidExtractor:
    """投标响应提取器
    
    根据已确认的招标要求，从投标文件中自动提取响应内容。
    
    示例:
        >>> extractor = BidExtractor(db, llm_client)
        >>> responses = await extractor.extract_responses(project_id)
        >>> print(f"提取了 {len(responses)} 条响应")
    """
    
    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        max_retries: int = 3,
        top_k: int = 8,
        concurrency: Optional[int] = None,
        item_timeout: Optional[float] = None,
        project_id: Optional[int] = None
    ):
        """初始化投标响应提取器
        
        Args:
            db: 数据库会话
            llm_client: LLM客户端（可选，默认根据AI模式配置自动选择）
            max_retries: LLM 调用最大重试次数（默认3，与 tender_extract 对齐，
                覆盖 R3 max_tokens=1500 + JSON 多策略修复后罕见的截断/格式错误）
            top_k: 语义检索返回的分块数量（默认8，需覆盖图片标注chunk）
            concurrency: 并行调用LLM的并发数
            item_timeout: 单项提取超时时间（秒），不传则按模式自动设置
        """
        self.db = db
        self._project_id = project_id
        self.llm_client = llm_client or get_review_client(project_id=project_id)
        self.max_retries = max_retries
        self.top_k = top_k
        self.concurrency = concurrency if concurrency is not None else get_review_concurrency()
        self.item_timeout = item_timeout or self._get_default_timeout()
        self.retriever = SemanticRetriever(db)

    def _resolve_cd_schema(self) -> dict | None:
        """B2.8 — 按灰度比例决定是否启用 json_object 约束（extractor 流程）。

        extractor 使用 chat_json（非 chat_with_validation），输出为自由 dict，
        无 Pydantic model 可用于 strict json_schema。此处使用基础 json_object 模式
        确保 provider 返回合法 JSON，减少 jsonrepair 触发率。
        """
        from app.core.ai.constrained_decoding import should_use_constrained_decoding
        from app.core.config import settings as _settings
        if should_use_constrained_decoding(
            getattr(self, "_project_id", None),
            _settings.CONSTRAINED_DECODING_RATIO_EXTRACTOR,
        ):
            return {"type": "json_object"}
        return None
    
    async def extract_for_project(
        self,
        project_id: int,
        save_to_db: bool = True,
        skip_existing: bool = True,
        heartbeat_callback: Optional[Callable[..., None]] = None,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> list[BidResponse]:
        """为项目提取所有投标响应
        
        根据已确认的招标要求，从投标文件中提取对应响应。
        
        Args:
            project_id: 项目ID
            save_to_db: 是否保存到数据库
            skip_existing: 是否跳过已有响应的要求
            heartbeat_callback: 心跳回调 callback(done, total, inflight=0)
            
        Returns:
            list[BidResponse]: 提取的投标响应列表
            
        Raises:
            ValueError: 项目没有已确认的招标要求或投标文件
        """
        from app.core.tasks.cancellation import invoke_cancel_check
        from app.core.tasks.parallel_unit_progress import invoke_unit_progress

        invoke_cancel_check(cancel_check)
        logger.info(f"[投标提取] 开始提取: project_id={project_id}")
        
        # 1. 获取已确认的招标要求
        requirements = await self._get_confirmed_requirements(project_id)
        invoke_cancel_check(cancel_check)
        if not requirements:
            raise ValueError(f"项目 {project_id} 没有已确认的招标要求")
        
        all_confirmed = list(requirements)
        total_requirements = len(requirements)
        logger.info(f"[投标提取] 共 {total_requirements} 条已确认的招标要求")

        # 1.1 跳过已有响应的要求（防止重跑时重复写入）
        existing_requirement_ids: set[int] = set()
        if skip_existing and save_to_db:
            invoke_cancel_check(cancel_check)
            existing_requirement_ids = await self._get_existing_response_requirement_ids(project_id)
            invoke_cancel_check(cancel_check)
            if existing_requirement_ids:
                logger.info(f"[投标提取] 已存在 {len(existing_requirement_ids)} 条投标响应，跳过已处理要求")
                requirements = [
                    req for req in requirements if req.id not in existing_requirement_ids
                ]
        
        # 2. 获取投标信封(整本 1 份 或 分卷 N 份, 须齐套且全部已解析)
        envelope = await self._load_bid_envelope(project_id)
        invoke_cancel_check(cancel_check)
        if not (envelope.is_complete and envelope.all_parsed):
            raise ValueError(f"项目 {project_id} 没有已解析的投标文件")

        from app.core.database import db_session_scope
        from app.core.retriever.material_inventory import (
            build_and_persist_project_inventory,
            collect_noun_universe,
            merge_project_retrieval_bags,
        )
        async with db_session_scope() as inv_session:
            await build_and_persist_project_inventory(
                inv_session, project_id, envelope.documents, all_confirmed
            )
            await inv_session.commit()
        
        if not requirements:
            logger.info("[投标提取] 所有招标要求已有投标响应，无需重复提取")
            async with db_session_scope() as inv_session:
                await merge_project_retrieval_bags(
                    inv_session,
                    project_id,
                    collect_noun_universe(all_confirmed),
                    bid_part_by_document={
                        int(doc.id): getattr(doc, "bid_part", None)
                        for doc in envelope.documents
                    },
                )
                await inv_session.commit()
            return []
        
        logger.info(
            f"[投标提取] 投标信封文档: {list(envelope.document_ids)}，"
            f"待处理: {len(requirements)} 条，并发数: {self.concurrency}"
        )
        
        # 3. 并行提取响应（使用信号量控制并发数）
        semaphore = asyncio.Semaphore(self.concurrency)
        inflight = 0
        completed_units = 0
        all_responses: list[BidResponse] = []
        total_to_process = len(requirements)
        skipped = total_requirements - total_to_process

        def emit_progress() -> None:
            invoke_unit_progress(
                heartbeat_callback,
                skipped + completed_units,
                total_requirements,
                inflight,
            )

        emit_progress()

        async def extract_with_semaphore(req: Requirement) -> BidResponse:
            """DB 连接与 LLM 调用解耦：读/写各用短生命周期 session，LLM 期间不占连接: 短 session 全部改走 db_session_scope, 经进程级许可信号量,
 防止并发协程突发占满 QueuePool。
 """
            req_id = req.id
            primary_doc_id = envelope.primary_document_id
            nonlocal inflight, completed_units
            async with semaphore:
                inflight += 1
                emit_progress()
                try:
                    invoke_cancel_check(cancel_check)
                    from app.core.database import db_session_scope
                    response = await self._extract_single_response_decoupled(
                        requirement=req,
                        envelope=envelope,
                        session_factory=db_session_scope,
                    )
                    invoke_cancel_check(cancel_check)
                    response = response if response else self._create_not_found_response_by_id(req_id, primary_doc_id)
                    if save_to_db:
                        async with db_session_scope() as session:
                            invoke_cancel_check(cancel_check)
                            await self._persist_response_with_chunks(session, response)
                            invoke_cancel_check(cancel_check)
                    completed_units += 1
                    return response
                except Exception as e:
                    invoke_cancel_check(cancel_check)
                    logger.error(f"要求 {req_id} 响应提取失败: {e}")
                    response = self._create_not_found_response_by_id(req_id, primary_doc_id)
                    if save_to_db:
                        try:
                            from app.core.database import db_session_scope as _scope
                            async with _scope() as fallback_session:
                                invoke_cancel_check(cancel_check)
                                await self._persist_response_with_chunks(fallback_session, response)
                                invoke_cancel_check(cancel_check)
                        except Exception as save_err:
                            logger.warning(f"要求 {req_id} 兜底保存失败: {save_err}")
                    completed_units += 1
                    return response
                finally:
                    inflight -= 1
                    emit_progress()
        
        # 创建所有任务
        tasks = [extract_with_semaphore(req) for req in requirements]
        
        # 逐步收集结果(写入已由各协程独立完成,主循环只收集+心跳)
        log_batch_size = 10
        
        for task in asyncio.as_completed(tasks):
            invoke_cancel_check(cancel_check)
            response = await task
            invoke_cancel_check(cancel_check)
            all_responses.append(response)
            done_count = len(all_responses)
            emit_progress()
            
            if done_count % log_batch_size == 0 or done_count == total_to_process:
                percent = done_count * 100 // total_to_process
                logger.info(
                    f"[投标提取] 进度: {done_count}/{total_to_process} ({percent}%)"
                )
        
        logger.info(f"[投标提取] 完成: 共 {len(all_responses)} 条投标响应")
        async with db_session_scope() as inv_session:
            await merge_project_retrieval_bags(
                inv_session,
                project_id,
                collect_noun_universe(all_confirmed),
                bid_part_by_document={
                    int(doc.id): getattr(doc, "bid_part", None)
                    for doc in envelope.documents
                },
            )
            await inv_session.commit()
        return all_responses
    
    async def _get_confirmed_requirements(
        self,
        project_id: int
    ) -> list[Requirement]:
        """获取项目的已确认且可评审的招标要求（统一闸门口径）"""
        result = await self.db.execute(
            select(Requirement).where(
                Requirement.project_id == project_id,
                *reviewable_requirement_conditions(),
            ).order_by(Requirement.id)
        )
        return list(result.scalars().all())

    async def _get_existing_response_requirement_ids(
        self,
        project_id: int
    ) -> set[int]:
        """获取项目中已存在响应的招标要求ID"""
        result = await self.db.execute(
            select(BidResponse.requirement_id).join(
                Requirement,
                BidResponse.requirement_id == Requirement.id
            ).where(
                Requirement.project_id == project_id
            )
        )
        return {row[0] for row in result.fetchall()}
    
    async def _load_bid_envelope(self, project_id: int) -> BidEnvelope:
        """加载项目投标信封(委托 core.bid_envelope, 保留实例挂钩便于测试注入)。"""
        return await load_bid_envelope(self.db, project_id)
    
    async def _extract_single_response_decoupled(
        self,
        requirement: Requirement,
        envelope: BidEnvelope,
        session_factory,
    ) -> Optional[BidResponse]:
        """DB 连接与 LLM 调用解耦版本的单条提取

 每个 DB 操作阶段使用短生命周期 session，LLM 调用期间不占用连接。
 逻辑与 _extract_single_response 完全等价。

 Args:
 requirement: 招标要求
 envelope: 投标信封(整本 1 份 或 分卷 N 份文档)
 session_factory: 返回 async 上下文管理器 (yield AsyncSession) 的可调用对象;
 典型为 db_session_scope (走进程级许可) 或 async_session_factory
 (向后兼容, 单元测试用)。
 """
        req_id = requirement.id
        req_desc = requirement.description
        req_category = requirement.category
        req_is_mandatory = requirement.is_mandatory
        # 未找到/技术失败等无归因场景回退主文档; 有命中时按最高命中文档归因
        primary_doc_id = envelope.primary_document_id
        # P1 B4: 评分项上下文与检索查询扩展（非评分项均为空字符串）
        _scoring_ctx = build_scoring_context(requirement)
        _query_ext = build_query_extension(requirement)
        _search_desc = f"{req_desc} {_query_ext}".strip() if _query_ext else req_desc

        # 分面契约——描述复合分解 + 人工确认子要求, 供首轮多路检索、
        # 补搜触发判定与二轮输入分面保底共同消费
        # 招标提取阶段 LLM 关键词并入分面来源(此前整条检索链未消费)
        facets = build_evidence_facets(
            req_desc,
            parse_json_list(getattr(requirement, "sub_requirements", None)),
            keywords=parse_json_list(getattr(requirement, "keywords", None)),
        )
        recovery_request = BidRecoveryRequest(
            requirement_id=req_id,
            description=req_desc,
            category=req_category,
            is_mandatory=bool(req_is_mandatory),
            bid_document_id=primary_doc_id,
            facets=facets,
            envelope_document_ids=envelope.document_ids,
        )

        async def _search_one_document(
            retriever: SemanticRetriever, document_id: int
        ) -> list[SearchResult]:
            """单文档多路检索(要求描述/类别/分面已绑定)。"""
            return await self._multi_query_search(
                retriever=retriever,
                document_id=document_id,
                description=_search_desc,
                category=req_category,
                extra_queries=tuple(f.query for f in facets),
                facets=facets,
            )

        from app.core.extractor.facet_closure import (
            build_facet_states,
            coverage_with_inventory,
            format_verified_materials_block,
            inventory_hit_pages,
            merge_inventory_seeds,
            rewrite_missing_elements,
        )
        from app.core.retriever.material_inventory import load_material_facts

        project_id = int(getattr(requirement, "project_id", 0) or 0)
        facts = ()

        # ── Phase 1: 短 session -- 信封首轮检索(整本=历史单文档序列) ──
        async with session_factory() as session:
            if project_id:
                facts = await load_material_facts(session, project_id)
            retriever = SemanticRetriever(session)
            reassembler = ContextReassembler(session)
            first_round = await retrieve_first_round_evidence(
                retriever,
                reassembler,
                envelope.document_ids,
                search_single_document=_search_one_document,
                requirement_description=req_desc,
                is_mandatory=bool(req_is_mandatory),
                top_k=self.top_k,
            )
            search_results = await merge_inventory_seeds(
                session,
                first_round.primary_results,
                facets,
                facts,
            )
        image_chunks = first_round.image_results
        # ── session 已释放 ──────────────────────────────────────────

        # 首轮可见集分面保底——每分面首条命中提前到队首, 防止强势
        # 分面在 top_k 主槽与输入预算截断中挤掉弱势分面的唯一证据
        # (与二轮补搜 build_second_round_callback 的分面保底口径对齐)
        search_results = order_results_facet_first(search_results, facets)

        # 零候选不再早退——跳过首轮提取, 直接进入补搜
        extracted = None
        if search_results or image_chunks:
            extracted = await self._call_llm_extract(
                req_id=req_id,
                category=req_category,
                description=req_desc,
                is_mandatory=req_is_mandatory,
                search_results=search_results,
                image_chunks=image_chunks,
                scoring_context=_scoring_ctx,
                verified_materials=format_verified_materials_block(facts, facets),
            )
        else:
            logger.info(f"要求 {req_id}: 首轮检索零候选, 跳过首轮提取直接进入补搜")

        # 累计全部检索命中,供 not-found 可观测与饥饿检测使用
        seen_results = list(search_results) + list(image_chunks)

        # ── Phase 3（条件）: 单调补搜（: 触发不再限否决项/found=False;
        # partial、missing_elements、分面缺口均可补救; 证据与结果只增不减）──
        coverage = coverage_with_inventory(facets, seen_results, facts)
        if should_attempt_recovery(extracted, seen_results, coverage):
            if coverage.missing_keys:
                logger.info(
                    f"要求 {req_id}: 分面覆盖缺口 {list(coverage.missing_keys)[:5]}, 启动补搜"
                )
            outcome = await recover_bid_response(
                recovery_request,
                extracted,
                seen_results,
                session_factory,
                    build_second_round_callback(
                    self._call_llm_extract,
                    recovery_request,
                    _scoring_ctx,
                    format_verified_materials_block(facts, facets),
                ),
            )
            extracted = outcome.extracted
            seen_results = outcome.evidence

        if not extracted or not extracted.found:
            # P0-D: 基础设施失败与业务缺失分离——模型故障不得伪装成"未找到响应"
            if isinstance(extracted, ExtractionFailedSignal):
                logger.warning(
                    f"要求 {req_id}: 提取失败({extracted.reason})，落库为待复核项"
                )
                return build_extraction_failed_response(
                    req_id, primary_doc_id, reason=extracted.reason,
                )
            coverage = coverage_with_inventory(facets, seen_results, facts)
            finding = await detect_starvation_for_request(
                recovery_request, seen_results, session_factory
            )
            inventory_pages = inventory_hit_pages(facts, facets)
            verdict = decide_bid_response_persist(
                facet_missing=bool(coverage.missing_keys),
                starvation=finding is not None,
                pack_has_substantial=pack_has_substantial_hits(
                    [item.content for item in seen_results]
                ),
                inventory_material_hit=bool(inventory_pages),
                inventory_pages=inventory_pages,
            )
            facet_states = build_facet_states(
                facets, coverage, facts, llm_missing=()
            )
            if verdict.action == "pack_as_partial":
                pages = [item.page_num for item in seen_results if item.page_num] or inventory_pages
                partial = ExtractedBidResponse(
                    requirement_id=req_id,
                    content=build_pack_partial_content(
                        [item.content for item in seen_results]
                    ),
                    evidence_type="other",
                    source_pages=list(pages[:10]),
                    found=True,
                    completeness="partial",
                    missing_elements=rewrite_missing_elements(
                        ["检索已命中待确认"], facts
                    ) or ["检索已命中待确认"],
                    relevance_note=verdict.relevance_note or HIT_UNCONFIRMED_NOTE,
                )
                return self._create_response(
                    requirement_id=req_id,
                    bid_document_id=envelope.document_id_for(seen_results),
                    extracted=partial,
                    search_results=seen_results,
                    facet_states=facet_states,
                )
            response = self._create_not_found_response_by_id(
                req_id, primary_doc_id, search_results=seen_results
            )
            response.facet_states = facet_states
            if verdict.action == "insufficient":
                response.content = EVIDENCE_INSUFFICIENT_CONTENT
                if finding:
                    response.relevance_note = format_starvation_note(finding)
                else:
                    response.relevance_note = "分面覆盖缺口，证据不足待复核"
            return response

        # 归因: 按最高相似度命中所在文档(分卷时区分商务/技术标, 整本即主文档)
        coverage = coverage_with_inventory(facets, seen_results, facts)
        extracted = extracted.model_copy(
            update={
                "missing_elements": rewrite_missing_elements(
                    extracted.missing_elements, facts
                )
            }
        )
        return self._create_response(
            requirement_id=req_id,
            bid_document_id=envelope.document_id_for(seen_results),
            extracted=extracted,
            search_results=seen_results,
            facet_states=build_facet_states(
                facets, coverage, facts, extracted.missing_elements
            ),
        )

    async def _multi_query_search(
        self,
        retriever: SemanticRetriever,
        document_id: int,
        description: str,
        category=None,
        extra_queries: tuple[str, ...] = (),
        facets=(),
    ) -> list[SearchResult]:
        """分解复合要求为子查询,多路检索取并集,辅以目录导航: 首轮召回委托证据编排器(dense+lexical+section/entity+RRF)。
 目录导航在编排器未命中 section 时回退 _toc_guided_fetch。

 Args:
 retriever: 语义检索器
 document_id: 投标文档ID
 description: 招标要求原文
 category: 要求类别(RequirementCategory),用于先验增强
 extra_queries: 分面查询(EvidenceFacet.query),去重后并入子查询
 facets: 分面契约,供编排器覆盖快照

 Returns:
 list[SearchResult]: 去重合并后的检索结果,按融合分降序
 """
        from app.core.retriever.evidence_pipeline.entity_retriever import (
            extract_entity_terms,
        )

        doc = await retriever._get_document(document_id)
        has_toc = bool(doc and getattr(doc, "toc_map", None))
        query = RetrievalQuery(
            document_id=document_id,
            description=description,
            extra_queries=tuple(extra_queries or ()),
            category=category,
            facets=tuple(facets or ()),
            has_toc_map=has_toc,
            top_k=self.top_k,
            identifier_terms=tuple(extract_entity_terms(description)),
            min_similarity=0.3,
        )
        package = await assemble_evidence_package(
            retriever.db, query, retriever=retriever
        )
        results = list(package.results)
        from app.core.parser.toc_parser import TocParser, deserialize_toc_map
        from app.core.retriever.evidence_coverage import evaluate_facet_coverage

        coverage = package.coverage or evaluate_facet_coverage(facets, results)
        toc_map = deserialize_toc_map(getattr(doc, "toc_map", None)) if doc else {}
        parser = TocParser()
        missing_nouns = list(coverage.missing_keys) or [f.key for f in (facets or ())]
        toc_has_noun = any(
            parser.lookup_physical_pages(noun, toc_map) for noun in missing_nouns
        ) if toc_map else False
        if (
            "section" not in package.channels_used
            and coverage.missing_keys
            and toc_has_noun
        ):
            toc_chunks = await self._toc_guided_fetch(
                retriever=retriever,
                document_id=document_id,
                description=description,
                existing_ids={item.chunk_id for item in results},
                facets=facets,
            )
            if toc_chunks:
                results = list(results) + toc_chunks
        return results[: self.top_k * 2]

    async def _toc_guided_fetch(
        self,
        retriever: SemanticRetriever,
        document_id: int,
        description: str,
        existing_ids: set[int],
        facets=(),
    ) -> list[SearchResult]:
        """利用目录映射定位投标文档中的对应页码,直接获取chunk

        Args:
            retriever: 语义检索器
            document_id: 投标文档ID
            description: 招标要求描述
            existing_ids: 已有chunk ID,用于去重

        Returns:
            list[SearchResult]: 目录导航补充的chunk
        """
        from app.core.retriever.evidence_pipeline.contracts import RetrievalPlan
        from app.core.retriever.evidence_pipeline.section_retriever import (
            search_section_chunks,
        )

        query = RetrievalQuery(
            document_id=document_id,
            description=description,
            has_toc_map=True,
            facets=tuple(facets or ()),
        )
        return await search_section_chunks(
            retriever.db,
            query,
            RetrievalPlan(use_section=True),
            existing_ids=existing_ids,
        )

    # 二次深搜迁移至 bid_response_recovery.run_deep_evidence_search
    # (分面驱动 + 证据单调合并), 本文件不再保留 _deep_search。

    @staticmethod
    def _clip_context_text(text: str, limit: int) -> str:
        """在预算内保留片段首尾关键信息，避免整段粗暴截断。"""
        if limit <= 0 or len(text) <= limit:
            return text
        marker = "\n...[TRUNCATED]...\n"
        if limit <= len(marker) + 16:
            return text[:limit]
        head = max(24, int(limit * 0.7))
        tail = max(12, limit - head - len(marker))
        if head + tail + len(marker) > limit:
            head = max(12, limit - tail - len(marker))
        return text[:head].rstrip() + marker + text[-tail:].lstrip()

    def _get_context_budget(self, reduction_round: int = 0) -> int:
        """获取当前轮次可用的检索上下文预算。"""
        base_budget = max(400, int(settings.BID_EXTRACT_CONTEXT_CHAR_BUDGET))
        if reduction_round <= 0:
            return base_budget
        ratio = float(settings.BID_EXTRACT_RETRY_REDUCTION_RATIO)
        ratio = min(max(ratio, 0.3), 0.95)
        return max(400, int(base_budget * (ratio ** reduction_round)))

    def _build_budgeted_context(
        self,
        search_results: list[SearchResult],
        image_chunks: list[SearchResult] | None,
        context_budget: int,
    ) -> tuple[str, dict[str, int]]:
        """按优先级构建受预算约束的检索上下文。"""
        per_chunk_limit = max(40, int(settings.BID_EXTRACT_CHUNK_CHAR_LIMIT))
        sections: list[tuple[str, str]] = []
        primary_count = min(len(search_results), max(1, self.top_k))

        def _build_text_section(index: int, result: SearchResult) -> str:
            page_info = f"第{result.page_num}页" if result.page_num else "未知页"
            clipped = self._clip_context_text(result.content, per_chunk_limit)
            return (
                f"【片段{index}】({page_info}, 相似度:{result.similarity:.2f})\n"
                f"{clipped}\n"
            )

        for idx, result in enumerate(search_results[:primary_count], start=1):
            sections.append(("text", _build_text_section(idx, result)))

        if image_chunks:
            sections.append((
                "image_header",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "【以下为关联页面的图片附件与证据摘要 -- 投标人可能以扫描件形式提供了证明材料】\n",
            ))
            for chunk in image_chunks:
                page_info = f"第{chunk.page_num}页" if chunk.page_num else "未知页"
                clipped = self._clip_context_text(chunk.content, per_chunk_limit)
                sections.append(("image", f"▶ ({page_info})\n{clipped}\n"))

        for idx, result in enumerate(search_results[primary_count:], start=primary_count + 1):
            sections.append(("overflow", _build_text_section(idx, result)))

        parts: list[str] = []
        used_chars = 0
        kept_text_sections = 0
        kept_image_sections = 0
        truncated_sections = 0
        dropped_sections = 0

        for idx, (kind, section) in enumerate(sections):
            separator_len = 1 if parts else 0
            projected = used_chars + separator_len + len(section)
            if projected <= context_budget:
                parts.append(section)
                used_chars = projected
                if kind in {"text", "overflow"}:
                    kept_text_sections += 1
                elif kind == "image":
                    kept_image_sections += 1
                continue

            remaining = context_budget - used_chars - separator_len
            if kind != "image_header" and remaining >= 120:
                clipped_section = self._clip_context_text(section, remaining)
                if clipped_section:
                    parts.append(clipped_section)
                    used_chars += separator_len + len(clipped_section)
                    truncated_sections += 1
                    if kind in {"text", "overflow"}:
                        kept_text_sections += 1
                    elif kind == "image":
                        kept_image_sections += 1
            dropped_sections = len(sections) - idx - (1 if truncated_sections else 0)
            break

        if not parts:
            for kind, section in sections:
                if kind == "image_header":
                    continue
                clipped_section = self._clip_context_text(section, context_budget)
                if clipped_section:
                    parts.append(clipped_section)
                    used_chars = len(clipped_section)
                    truncated_sections = 1
                    if kind in {"text", "overflow"}:
                        kept_text_sections = 1
                    elif kind == "image":
                        kept_image_sections = 1
                    dropped_sections = max(len(sections) - 1, 0)
                    break

        return "\n".join(parts), {
            "used_chars": used_chars,
            "budget_chars": context_budget,
            "kept_text_sections": kept_text_sections,
            "kept_image_sections": kept_image_sections,
            "truncated_sections": truncated_sections,
            "dropped_sections": max(dropped_sections, 0),
            "total_text_sections": len(search_results),
            "total_image_sections": len(image_chunks or []),
        }

    async def _call_llm_extract(
        self,
        req_id: int,
        category,
        description: str,
        is_mandatory: bool,
        search_results: list[SearchResult],
        image_chunks: list[SearchResult] | None = None,
        scoring_context: str = "",
        verified_materials: str = "",
    ) -> Optional[ExtractedBidResponse | ExtractionFailedSignal]:
        """调用 LLM 提取响应

 P0-D: 基础设施失败(超时/重试耗尽)返回 ExtractionFailedSignal
 (found=False, 可安全流经深搜分支), 与业务 not-found(None) 分离;
 调用方据此落库"待复核"而非伪装成 [未找到响应](根治)。

 Args:
 req_id: 招标要求ID
 category: 要求分类(枚举或None)
 description: 要求描述文本
 is_mandatory: 是否为否决项
 search_results: 正文检索结果
 image_chunks: 图片证据检索结果
 scoring_context: 评分项评审标准块（非评分项传 ""）
 """
        extra_instruction = ""

        def _render_prompt(
            reduction_round: int,
        ) -> tuple[str, str, dict[str, int]]:
            """按收缩轮次渲染 (system, user, 上下文观测)——预检与发送共用同一渲染。"""
            bid_chunks_text, context_meta = self._build_budgeted_context(
                search_results=search_results,
                image_chunks=image_chunks,
                context_budget=self._get_context_budget(reduction_round),
            )
            system_msg, user_msg = get_bid_extract_prompt(
                requirement_id=req_id,
                category=category.value if category else "unknown",
                requirement_description=description,
                is_mandatory=bool(is_mandatory),
                bid_chunks=bid_chunks_text,
                scoring_context=scoring_context,
                verified_materials=verified_materials,
            )
            if extra_instruction:
                user_msg += extra_instruction
            return system_msg, user_msg, context_meta

        # 发送前硬上限适配(与审查侧 review_input_gate 同口径)——
        # 先在本地按收缩阶梯找到可发送轮次; 收缩到底仍超限则走既有
        # "[提取失败待复核]"合同, 禁止发送注定超限的请求(400/超时不可控)。
        base_reduction_round = fit_extract_reduction_round(
            lambda r: _render_prompt(r)[:2],
            max_rounds=max(0, int(settings.REVIEW_INPUT_SHRINK_RETRIES)),
        )
        if base_reduction_round is None:
            logger.warning(
                f"要求 {req_id}: 提取 Prompt 收缩到底仍超硬上限, 转待复核"
            )
            return ExtractionFailedSignal(reason=OVERSIZE_REASON)
        if base_reduction_round > 0:
            logger.info(
                f"要求 {req_id}: 提取 Prompt 超硬上限, "
                f"本地收缩至 reduction_round={base_reduction_round} 后发送"
            )

        for attempt in range(self.max_retries):
            if attempt > 0:
                await asyncio.sleep(min(2 ** attempt, 16))

            attempt_context_meta: dict[str, int] = {}

            async def _send_request(payload: _BidExtractRetryPayload) -> dict:
                nonlocal attempt_context_meta
                system_msg, user_msg, context_meta = _render_prompt(
                    payload.reduction_round
                )
                attempt_context_meta = context_meta
                if context_meta["truncated_sections"] or context_meta["dropped_sections"]:
                    logger.info(
                        "要求 {}: 输入预算收敛 budget={} used={} kept_text={} kept_image={} truncated={} dropped={}",
                        req_id,
                        context_meta["budget_chars"],
                        context_meta["used_chars"],
                        context_meta["kept_text_sections"],
                        context_meta["kept_image_sections"],
                        context_meta["truncated_sections"],
                        context_meta["dropped_sections"],
                    )
                # S.3.3 同类排查：统一投标响应提取重试策略为"首次 0.1 / 重试降至 0.0"
                # 输出预算 1500：ExtractedBidResponse schema 需承载 content(50-150字)
                # + evidence_type + source_pages + completeness + missing_elements
                # + linked_to + evidence_anchor + relevance_score/note 共 10+ 字段。
                # 注意: 本路径裸调 chat_json，不具备 chat_with_validation 的截断扩容链；
                # 1000 偏紧会提高 JSON 截断失败率。
                return await asyncio.wait_for(
                    self.llm_client.chat_json(
                        user_message=user_msg,
                        system_message=system_msg,
                        temperature=0.1 if attempt == 0 else 0.0,
                        max_tokens=1500,
                        json_schema=self._resolve_cd_schema(),
                    ),
                    timeout=self.item_timeout,
                )

            def _reduce_payload(
                payload: _BidExtractRetryPayload,
                retry_round: int,
            ) -> tuple[_BidExtractRetryPayload, dict[str, object]]:
                next_payload = _BidExtractRetryPayload(
                    reduction_round=payload.reduction_round + 1,
                )
                removed_segments: list[str] = []
                if attempt_context_meta.get("dropped_sections", 0):
                    removed_segments.append("overflow_sections")
                if attempt_context_meta.get("truncated_sections", 0):
                    removed_segments.append("truncated_sections")
                kept_segments = ["primary_text_sections"]
                if attempt_context_meta.get("kept_image_sections", 0):
                    kept_segments.append("image_evidence_sections")
                return next_payload, {
                    "removed_segments": removed_segments,
                    "kept_segments": kept_segments,
                    "retry_attempt": retry_round,
                    "used_chars": attempt_context_meta.get("used_chars"),
                }

            def _describe_payload(
                payload: _BidExtractRetryPayload,
            ) -> dict[str, int]:
                return {
                    "reduction_round": payload.reduction_round,
                    "budget_chars": self._get_context_budget(payload.reduction_round),
                }

            try:
                response = await retry_on_input_too_long_400(
                    operation_name=f"要求 {req_id} 提取",
                    initial_payload=_BidExtractRetryPayload(
                        reduction_round=base_reduction_round,
                    ),
                    send_request=_send_request,
                    reduce_payload_semantically=_reduce_payload,
                    max_retries=max(self.max_retries - attempt - 1, 0),
                    describe_payload=_describe_payload,
                )

                response = require_dict_response(
                    response, operation_name=f"要求 {req_id} 提取"
                )

                if not response.get("found", True):
                    logger.debug(f"要求 {req_id}: LLM 返回 found=false，视为未找到响应")
                    return None

                if not response.get("content") or response.get("content") is None:
                    logger.debug(f"要求 {req_id}: LLM 返回 content 为空，视为未找到响应")
                    return None

                if not response.get("evidence_type"):
                    response["evidence_type"] = "other"

                if response.get("completeness") not in {"full", "partial", "form_only"}:
                    response["completeness"] = "partial"

                if response.get("missing_elements") is None:
                    response["missing_elements"] = []

                response["found"] = True

                return ExtractedBidResponse(**response)

            except asyncio.TimeoutError:
                logger.warning(
                    f"要求 {req_id}: LLM 提取超时（>{self.item_timeout}s）"
                )
                return ExtractionFailedSignal(reason="timeout")
            except json.JSONDecodeError as e:
                logger.warning(
                    f"JSON 解析失败 (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt == 0:
                    extra_instruction += "\n\n请确保输出为有效的 JSON 格式，不要包含任何非JSON内容。"
                
            except ValidationError as e:
                logger.warning(
                    f"数据校验失败 (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if "content" in str(e) and "None" in str(e):
                    logger.debug(f"要求 {req_id}: 校验失败因 content 为空，视为未找到响应")
                    return None
        
        logger.error(
            f"要求 {req_id} 响应提取失败，超过最大重试次数 "
            f"(category={category}, is_mandatory={is_mandatory})"
        )
        return ExtractionFailedSignal(reason="retry_exhausted")
    
    def _create_response(
        self,
        requirement_id: int,
        bid_document_id: int,
        extracted: ExtractedBidResponse,
        search_results: list[SearchResult],
        facet_states=None,
    ) -> BidResponse:
        """创建投标响应对象"""
        source_chunk_id = search_results[0].chunk_id if search_results else None
        
        # ── 证据闸门: source_pages 与 search_results 页码交叉校验 (推广) ──
        with guard_observe(
            "_source_pages_cross_verify", "investigate",
            target_id=requirement_id,
        ) as _pages_obs:
            chunk_pages = {
                r.page_num for r in search_results
                if hasattr(r, "page_num") and r.page_num is not None
            }
            source_pages = None
            if extracted.source_pages:
                if chunk_pages:
                    verified = [p for p in extracted.source_pages if p in chunk_pages]
                    unverified = [p for p in extracted.source_pages if p not in chunk_pages]
                    if unverified:
                        _pages_obs["verdict_hint"] = "warn"
                        logger.info(
                            f"要求 {requirement_id}: LLM页码 {unverified} 不在检索chunk页码集合 "
                            f"{sorted(chunk_pages)} 中, 仅保留已验证页码 {verified}"
                        )
                    final_pages = verified if verified else sorted(chunk_pages)[:10]
                else:
                    final_pages = extracted.source_pages
                source_pages = ",".join(str(p) for p in final_pages)
                if len(source_pages) > 500:
                    pages = final_pages[:50]
                    source_pages = ",".join(str(p) for p in pages)
                    logger.warning(
                        f"要求 {requirement_id}: source_pages 过长({len(final_pages)}个页码), "
                        f"截断为前{len(pages)}个"
                    )
            elif chunk_pages:
                final_pages = sorted(chunk_pages)[:10]
                source_pages = ",".join(str(p) for p in final_pages)
                logger.debug(
                    f"要求 {requirement_id}: LLM未返回页码, 回退到检索chunk页码 {final_pages}"
                )
        
        evidence_type = None
        if extracted.evidence_type:
            evidence_type = EVIDENCE_TYPE_MAP.get(extracted.evidence_type)

        evidence_anchor = self._derive_evidence_anchor(
            extracted.evidence_anchor,
            extracted.content,
            extracted.source_pages,
        )

        response = BidResponse(
            requirement_id=requirement_id,
            document_id=bid_document_id,
            source_chunk_id=source_chunk_id,
            content=extracted.content,
            evidence_type=evidence_type,
            source_pages=source_pages,
            completeness=extracted.completeness,
            missing_elements=json.dumps(extracted.missing_elements, ensure_ascii=False) if extracted.missing_elements else None,
            linked_to=extracted.linked_to,
            evidence_anchor=evidence_anchor,
            relevance_score=extracted.relevance_score,
            relevance_note=extracted.relevance_note,
            facet_states=facet_states,
        )

        # Fix-02: 挂载待持久化的检索命中记录，由 _persist_response_with_chunks 消费。
        # 使用非 ORM 属性存储，持久化函数可访问，不影响 SQLAlchemy 字段映射。
        response._pending_retrieval_chunks = [
            {
                "chunk_id": r.chunk_id,
                "document_id": r.document_id,
                "page_num": r.page_num,
                "similarity": r.similarity,
                "matched_sub_query": r.matched_sub_query,
                "matched_keywords": r.matched_keywords,
                "role": r.role or "primary",
            }
            for r in (search_results or [])
            if r.chunk_id is not None
        ]
        return response

    def _derive_evidence_anchor(
        self,
        provided_anchor: str | None,
        content: str | None,
        source_pages: list[int] | None,
    ) -> str | None:
        """优先提取结构化编号，其次回退页码簇。"""
        if provided_anchor:
            return provided_anchor.strip()[:255]
        text = content or ""
        patterns = [
            r"合同(?:编号|号)?[:：]?\s*([A-Za-z0-9\-]{6,})",
            r"审计(?:报告)?(?:编号|号)?[:：]?\s*([A-Za-z0-9\-]{4,})",
            r"发票(?:号码|号)?[:：]?\s*([A-Za-z0-9\-]{6,})",
            r"税务(?:登记证)?(?:编号|号)?[:：]?\s*([A-Za-z0-9\-]{6,})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)[:255]
        fallback = re.search(r"[A-Za-z0-9\-]{8,}", text)
        if fallback:
            return fallback.group(0)[:255]
        if source_pages:
            compact = sorted({int(p) for p in source_pages})[:6]
            return f"pages:{'-'.join(str(p) for p in compact)}"
        return None
    
    def _create_not_found_response_by_id(
        self,
        requirement_id: int,
        bid_document_id: int,
        search_results: list[SearchResult] | None = None,
    ) -> BidResponse:
        """创建未找到响应的记录(接收标量ID,用于并发场景避免访问ORM对象): 挂载检索命中供持久化(可观测性)——此前 not-found 响应
 的 bid_response_chunks 为零记录,导致检索失效无法回放排障。
 """
        response = BidResponse(
            requirement_id=requirement_id,
            document_id=bid_document_id,
            source_chunk_id=None,
            content="[未找到响应]",
            evidence_type=None,
            source_pages=None,
            completeness=None,
            missing_elements=None,
            linked_to=None,
            evidence_anchor=None,
        )
        # 与 _create_response 相同的命中映射,由 _persist_response_with_chunks 消费
        response._pending_retrieval_chunks = [
            {
                "chunk_id": r.chunk_id,
                "document_id": r.document_id,
                "page_num": r.page_num,
                "similarity": r.similarity,
                "matched_sub_query": r.matched_sub_query,
                "matched_keywords": r.matched_keywords,
                "role": r.role or "primary",
            }
            for r in (search_results or [])
            if r.chunk_id is not None
        ]
        return response

    @staticmethod
    async def _persist_response_with_chunks(
        session: AsyncSession,
        response: BidResponse,
    ) -> None:
        """Fix-02 持久化：先写 BidResponse，flush 取 id，再批量写 bid_response_chunks。

        Chunks 写入失败不阻断主流程（savepoint 吞掉异常 + degraded 日志），
        确保"审查 Gate 证据持久化"的新能力对"投标响应提取"主路径零额外风险。
        """
        from app.models.bid_response_chunk import BidResponseChunk

        pending = list(getattr(response, "_pending_retrieval_chunks", []) or [])
        # 避免重复持久化（防重试场景）
        try:
            response._pending_retrieval_chunks = []  # noqa: SLF001
        except Exception:
            pass

        session.add(response)
        if not pending:
            await session.commit()
            return

        try:
            await session.flush()  # 拿到 response.id
            bid_response_id = response.id
            session.add_all([
                BidResponseChunk(
                    bid_response_id=bid_response_id,
                    chunk_id=hit["chunk_id"],
                    document_id=hit["document_id"],
                    page_num=hit.get("page_num"),
                    similarity=hit.get("similarity"),
                    matched_sub_query=hit.get("matched_sub_query"),
                    matched_keywords=hit.get("matched_keywords"),
                    role=hit.get("role") or "primary",
                )
                for hit in pending
            ])
            await session.commit()
        except Exception as err:
            # chunks 写入失败不阻断：回滚到 savepoint 仅写 response
            await session.rollback()
            logger.warning(
                f"[bid_response_chunks degraded] 写入检索命中失败, "
                f"BidResponse 单独提交: req={response.requirement_id}, err={err}"
            )
            try:
                session.add(response)
                response._pending_retrieval_chunks = []  # noqa: SLF001
                await session.commit()
            except Exception as commit_err:
                await session.rollback()
                logger.warning(
                    f"[bid_response_chunks degraded] 降级后再次提交失败: "
                    f"req={response.requirement_id}, err={commit_err}"
                )

    def _get_default_timeout(self) -> float:
        """根据AI模式返回默认超时（秒）"""
        return settings.CLOUD_TIMEOUT if get_ai_mode() == "cloud" else 300.0

    # 旧四词桶 _has_missing_subfacets 已删除——完整性判断改由
    # evidence_coverage.evaluate_facet_coverage 按分面/实体粒度执行。


# 工厂函数
def get_bid_extractor(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    concurrency: Optional[int] = None,
    project_id: Optional[int] = None
) -> BidExtractor:
    """获取投标响应提取器实例

    Args:
        db: 数据库会话
        llm_client: LLM客户端（可选，默认根据AI模式配置自动选择）
        concurrency: 并行调用LLM的并发数（可选，默认根据AI模式动态选择）

    Returns:
        BidExtractor: 提取器实例
    """
    return BidExtractor(db=db, llm_client=llm_client, concurrency=concurrency, project_id=project_id)
