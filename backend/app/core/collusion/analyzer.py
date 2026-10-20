"""串标分析编排器

编排 S1~S4 四阶段分析流程：
  S1a: 招标格式要求提取（仅当有招标文件时，1次LLM）
  S1b: 各投标文件关键信息提取（N次LLM，并行）
  S1c: 向量相似度预筛选（numpy矩阵运算，<2秒）
  S3:  深度对比分析（LLM，按预筛选结果分级，并行）
  S4:  综合研判（1次LLM）

并发控制：asyncio.Semaphore，并发数来自 get_review_concurrency()
失败兜底：每个 LLM 调用有独立超时 + 失败生成兜底结果（不阻断流程）
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional, Callable

from loguru import logger
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory, db_session_scope
from app.core.observability.instrument import instrument as _observability_instrument
from app.models.document import Document, DocumentType
from app.models.collusion_extraction import CollusionExtraction
from app.models.collusion_result import CollusionResult, CollusionRiskLevel
from app.models.project import Project
from app.schemas.collusion import (
    CollusionProjectSummary,
    LLMExtractionResult,
    LLMTenderFormatResult,
    LLMCompareResult,
    LLMSynthesisResult,
    LLMEntityResult,
    LLMTextPatternResult,
    LLMBusinessResult,
    LLMSingleDimResult,
    DIMENSION_EVIDENCE_LEVEL_CONTRACT,
    EVIDENCE_LEVEL_CORRECTED_FLAG,
    coerce_evidence_level_for_dim,
    build_pair_id,
    is_flattened_field_string,
    is_valid_person_name,
    sanitize_quote,
)
from app.core.ai.ai_factory import get_review_client
from app.core.ai.critic.flows import FLOW_COLLUSION
from app.core.ai.input_guard import retry_on_input_too_long_400
from app.core.ai.retry_tracker import LLMRetryTracker
from app.core.config import (
    settings,
    get_review_concurrency,
    get_collusion_extract_timeout,
    get_collusion_compare_timeout,
    get_collusion_synthesis_timeout,
)
from app.core.collusion.prompts import (
    COLLUSION_SYSTEM_PROMPT as _CODE_COLLUSION_SYSTEM,
    COLLUSION_TENDER_FORMAT_PROMPT,
    build_collusion_extract_route_prompt,
    COLLUSION_PAIR_JUDGEMENT_PROMPT,
    COLLUSION_ENTITY_PROMPT,
    COLLUSION_TEXT_PATTERN_PROMPT,
    COLLUSION_BUSINESS_PROMPT,
    COLLUSION_SINGLE_DIM_PROMPT,
    DIMENSION_INSTRUCTIONS,
    COLLUSION_PROJECT_SUMMARY_PROMPT,
    COLLUSION_GROUP_ANALYSIS_PROMPT,
    build_tender_context,
    build_similar_chunks_text,
    build_profile_domain_context,
    build_profile_business_context,
)
from app.core.ai.prompt_registry import get_registry, resolve_user_template
from app.core.observability.guard_instrument import observe_guard
from app.core.collusion.feature_flags import (
    use_fused_evidence_to_llm,
    use_global_findings_enrich,
    use_d4_full_coverage,
)
from app.core.collusion.llm_keepalive import await_with_collusion_keepalive
from app.core.collusion.similarity import batch_compute_similarity
from app.core.collusion.global_features import GlobalFeatureAnalyzer, GlobalFeatureResult
from app.core.collusion.graph_cluster import (
    build_collusion_graph,
    CollusionCluster,
    enrich_clusters_with_global_findings,
)
from app.core.collusion.extraction_routes import (
    EXTRACTION_ROUTE_CONFIGS,
    ExtractionRouteConfig,
    build_empty_extraction,
    get_extraction_route,
    merge_route_extractions,
)
from app.core.collusion.knowledge_base import (
    build_dynamic_normal_similarity_hint,
    build_normal_similarity_hint,
)
from app.core.collusion.normalizers import (
    address_building_kind,
    credit_code_kind,
    is_credit_code_related,
    is_nearby_phone,
    is_same_address_building,
    nearby_phone_kind,
    normalize_cert_code,
    normalize_company_name,
    normalize_credit_code,
    normalize_id_tail,
    normalize_phone,
)
from app.core.collusion.industry_profiler import CollusionIndustryProfiler
from app.core.config import get_collusion_group_timeout
from app.models.collusion_group import CollusionGroup
from app.schemas.collusion import (
    LLMGroupAnalysisResult,
)
from app.core.parser.image_extractor import (
    is_annotation_chunk_content,
    is_evidence_chunk_content,
    is_image_related_chunk_content,
)


# Task 8 Step 1: 模块级常量与基础工具迁出到 analyzer_parts.constants
# 此处 re-export 兑现外部 `from app.core.collusion.analyzer import _xxx` 契约。
from app.core.collusion.analyzer_parts.constants import (
    ExtractionQualityError,
    _EXTRACTION_FAIL_THRESHOLD,
    _MAX_EXTRACT_CHARS,
    _IMAGE_BUDGET,
    _MAX_TENDER_CHARS,
    _DIM_MAP,
    _DIM_KEYS,
    _CERTAINTY_MIN,
    _DECISION_ENGINE_VERSION,
    _RISK_RANK_LOCAL,
    _DIMENSION_RETRIEVAL,
    _shrink_list_semantically,
    _shrink_text_blocks,
    _trim_pair_results_for_summary,
)

# Task 8 Step 6-9: 阶段方法迁出的子模块（analyzer_parts 不反向 import 本模块）
from app.core.collusion.analyzer_parts import extraction_stage
from app.core.collusion.analyzer_parts import pair_compare
from app.core.collusion.analyzer_parts import pair_dimensions
from app.core.collusion.analyzer_parts import pair_evidence
from app.core.collusion.analyzer_parts import pair_persistence
from app.core.collusion.analyzer_parts import group_stage
from app.core.collusion.analyzer_parts import synthesis_stage


def _get_collusion_system() -> str:
    """从 Registry 获取串标 system prompt，fallback 到代码常量"""
    resolved = get_registry().get_prompt("collusion.tender_format_extract")
    return resolved.system_message or _CODE_COLLUSION_SYSTEM


class CollusionAnalyzer:
    """串标分析编排器

    Usage:
        analyzer = CollusionAnalyzer(db, project_id)
        await analyzer.run(heartbeat_cb=...)
    """

    def __init__(
        self,
        db: AsyncSession,
        project_id: int,
        heartbeat_callback: Optional[Callable[[str, str, Optional[dict[str, Any]]], None]] = None,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> None:
        self._db = db
        self._project_id = project_id
        # project_id 已经是构造参数，直接用于模型绑定
        self._heartbeat_cb = heartbeat_callback or (lambda stage, progress, extra=None: None)
        self._cancel_check = cancel_check
        self._client = get_review_client(project_id=project_id)
        self._semaphore = asyncio.Semaphore(get_review_concurrency())
        self._db_lock = asyncio.Lock()
        self._dim_retry = LLMRetryTracker("collusion_dims")
        self._synthesis_retry = LLMRetryTracker("collusion_synthesis")
        self._group_retry = LLMRetryTracker("collusion_group")
        # P0-3: 未归入任何嫌疑群组的全局 findings（D4/D8），供 _save_project_summary 使用
        self._global_findings_extra: list[dict] = []
        # project_id 在分析全程不变, owner_id 一次加载即可,
        # 避免每对并发对比重复 self._db.execute 触发连接池竞争。
        self._project_owner_id_cache: int | None = None
        self._project_owner_id_loaded: bool = False
        # 知识库使用配置在分析全程不变, 一次加载即可; 默认未启用代表完全不向
        # Prompt 注入动态知识库, 满足"用户入口显式裁决"硬约束。
        self._kb_usage_loaded: bool = False
        self._kb_usage_allowed_ids: list[int] = []
        self._kb_usage_active: bool = False
        self._last_heartbeat_stage = "S1"
        self._last_heartbeat_progress = "串标分析中"
        self._last_heartbeat_extra: dict[str, Any] = {}

    def _check_cancelled(self) -> None:
        if self._cancel_check is not None:
            self._cancel_check()

    def _heartbeat(
        self,
        stage: str,
        progress: str = "",
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        self._check_cancelled()
        self._last_heartbeat_stage = stage
        self._last_heartbeat_progress = progress
        self._last_heartbeat_extra = extra or {}
        try:
            self._heartbeat_cb(stage, progress, extra)
        except Exception:
            pass

    def _resolve_cd_schema(self, response_model) -> dict | None:
        """B2.8 — 按灰度比例决定是否启用 json_object 约束（collusion 流程）。

 修复：原版使用 build_response_format(response_model) 生成 strict json_schema
 格式，但 aihubmix 路由下的 deepseek-chat 返回 "This response_format type is unavailable"。
 与 extractor/reviewer 对齐，统一使用基础 json_object 模式，保证兼容性且避免每次
 调用都先触发一次 400 错误。strict schema 的精度保障由 chat_with_validation 的
 Pydantic 校验 + 自动重试 + jsonrepair 覆盖。
 """
        from app.core.ai.constrained_decoding import should_use_constrained_decoding
        if should_use_constrained_decoding(
            self._project_id,
            settings.CONSTRAINED_DECODING_RATIO_COLLUSION,
        ):
            return {"type": "json_object"}
        return None

    async def _call_llm(
        self,
        user_message: str,
        response_model,
        timeout: float,
        max_tokens: int = 2000,
    ) -> dict:
        """统一 LLM 调用：wait_for + chat_with_validation + model_dump"""
        self._check_cancelled()
        result = await await_with_collusion_keepalive(
            asyncio.wait_for(
                self._client.chat_with_validation(
                    user_message=user_message,
                    response_model=response_model,
                    system_message=_get_collusion_system(),
                    temperature=0.1,
                    max_tokens=max_tokens,
                    json_schema=self._resolve_cd_schema(response_model),
                ),
                timeout=timeout,
            ),
            heartbeat=self._heartbeat,
            stage=self._last_heartbeat_stage,
            progress=self._last_heartbeat_progress or "LLM调用中",
            extra=self._last_heartbeat_extra,
        )
        self._check_cancelled()
        return result.model_dump()

    async def _call_llm_with_input_guard(
        self,
        *,
        operation_name: str,
        initial_payload: dict[str, Any],
        render_user_message: Callable[[dict[str, Any]], str],
        reduce_payload_semantically: Callable[[dict[str, Any], int], tuple[dict[str, Any], dict[str, Any] | None]],
        response_model,
        timeout: float,
        max_tokens: int = 2000,
        describe_payload: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> dict:
        """仅在输入长度超限400时对当前调用做语义级收缩重试。"""

        cd_schema = self._resolve_cd_schema(response_model)

        async def _send_request(payload: dict[str, Any]):
            self._check_cancelled()
            result = await await_with_collusion_keepalive(
                asyncio.wait_for(
                    self._client.chat_with_validation(
                        user_message=render_user_message(payload),
                        response_model=response_model,
                        system_message=_get_collusion_system(),
                        temperature=0.1,
                        max_tokens=max_tokens,
                        json_schema=cd_schema,
                    ),
                    timeout=timeout,
                ),
                heartbeat=self._heartbeat,
                stage=self._last_heartbeat_stage,
                progress=self._last_heartbeat_progress or "LLM调用中",
                extra=self._last_heartbeat_extra,
            )
            self._check_cancelled()
            return result.model_dump()

        self._check_cancelled()
        return await retry_on_input_too_long_400(
            operation_name=operation_name,
            initial_payload=initial_payload,
            send_request=_send_request,
            reduce_payload_semantically=reduce_payload_semantically,
            max_retries=1,
            describe_payload=describe_payload,
        )

    # ── 公开入口 ────────────────────────────────────────────────────────────────

    @_observability_instrument(
        name="collusion_analyzer_run_seconds",
        layer="stage",
        pillar="efficiency",
        direction="lower_better",
        business="collusion",
        measure="latency",
        description="围标分析核心编排器单次执行耗时",
    )
    async def run(self) -> dict:
        """Execute full collusion analysis flow with multi-party group identification"""
        self._check_cancelled()
        docs = await self._load_documents()
        self._check_cancelled()
        bid_docs = sorted(
            [d for d in docs if d.doc_type == DocumentType.BID],
            key=lambda d: d.id,
        )
        tender_doc = next((d for d in docs if d.doc_type == DocumentType.TENDER), None)

        if len(bid_docs) < 2:
            raise ValueError(f"串标分析需要至少2份投标文件，当前只有 {len(bid_docs)} 份")

        bid_ids = [d.id for d in bid_docs]
        tender_id = tender_doc.id if tender_doc else None
        doc_names = {d.id: d.filename for d in bid_docs}
        if tender_doc:
            doc_names[tender_doc.id] = tender_doc.filename

        logger.info(
            f"[Analyzer] 开始分析 project={self._project_id}, "
            f"bid_docs={bid_ids}, tender_doc={tender_id}"
        )

        # -- S1a: 招标格式要求提取 --
        tender_format: Optional[dict] = None
        # P1-5: 区分"无招标文件"与"招标文件存在但格式提取失败"两种情形,
        # 用 self 状态字段传递给下游 pair_compare 追加 quality_flag,
        # 避免 D3/D6 误判由模板导致的相似为可疑串通。
        self._tender_format_extract_failed = False
        if tender_doc:
            self._heartbeat("S1a", "提取招标格式要求")
            self._check_cancelled()
            tender_format = await self._extract_tender_format(tender_doc)
            self._check_cancelled()
            if tender_format is None:
                self._tender_format_extract_failed = True
                logger.warning(
                    f"[Analyzer] 招标文件存在但格式参照提取失败 "
                    f"(project={self._project_id}, tender_doc={tender_doc.id}); "
                    f"下游 pair 将追加 quality_flag 提示风险"
                )
            await self._save_extraction(
                document_id=tender_doc.id,
                doc_type="tender",
                extraction={},
                format_requirements=tender_format,
            )

        # -- S1b + S1c: 信息提取 + 向量预筛选（并行） --
        self._heartbeat(
            "S1b",
            f"0/{len(bid_docs)} 份",
            {
                "substage": "extract_bid_docs",
                "current": 0,
                "total": len(bid_docs),
                "stage_progress_pct": 0,
                "message": "开始提取投标文件信息",
            },
        )

        s1b_task = asyncio.create_task(self._extract_all_bid_docs(bid_docs))

        # S1c 一次性向量加载 + numpy 计算的连接生命周期不应污染主 session;
        # 包入按需短 session, 与 S1b LLM 调用并行的同时各自独立持有连接,
        # 用完立刻释放, 避免叠加触发 idle 超时。
        # 与 S1b 并发的 _extract_all_bid_docs 同期争抢连接, 通过
        # db_session_scope 走进程级许可信号量保护 QueuePool。
        async def _run_similarity():
            async with db_session_scope() as sim_session:
                return await batch_compute_similarity(
                    sim_session, bid_ids, tender_id
                )

        s1c_task = asyncio.create_task(_run_similarity())

        tender_text_for_profile: Optional[str] = None
        if tender_doc:
            tender_text_for_profile = await self._get_document_text(
                tender_doc.id, max_chars=8000
            )

        self._check_cancelled()
        extractions_list, similarity_result = await asyncio.gather(
            s1b_task, s1c_task,
        )
        self._check_cancelled()
        extractions: dict[int, dict] = dict(zip(bid_ids, extractions_list))

        # -- S1d: 行业画像（基于已抽取材料，禁止预置行业背景） --
        industry_profile = await self._generate_industry_profile(
            tender_text=tender_text_for_profile,
            bid_extractions=[ext for ext in extractions.values() if not _is_empty_extraction(ext)],
        )
        self._check_cancelled()

        await self._check_extraction_quality(bid_docs)

        # -- 空输入质量闸门: 全部提取为空或无 embedding 时直接失败 --
        non_empty_count = sum(
            1 for ext in extractions.values() if not _is_empty_extraction(ext)
        )
        has_any_embedding = bool(
            similarity_result.high_pairs
            or similarity_result.medium_pairs
            or any(s > 0 for s in similarity_result.pair_scores.values())
        )
        if non_empty_count == 0 and not has_any_embedding:
            raise ExtractionQualityError(
                "所有投标文件均未提取到有效信息且无可用向量，"
                "无法进行串标分析。请检查文档是否解析成功后重新发起。"
            )

        logger.info(
            f"[Analyzer] S1完成: 提取={len(extractions)}份, "
            f"非空提取={non_empty_count}份, "
            f"高相似对={len(similarity_result.high_pairs)}, "
            f"中相似对={len(similarity_result.medium_pairs)}, "
            f"行业画像={'已生成' if industry_profile else '未生成'}"
        )

        # -- S2: 全局特征分析(纯算法) --
        self._check_cancelled()
        self._heartbeat(
            "S2",
            "全局特征分析中",
            {
                "substage": "global_features",
                "stage_progress_pct": 10,
                "message": "全局特征分析中",
            },
        )
        gf_analyzer = GlobalFeatureAnalyzer(extractions, doc_names)
        global_features = gf_analyzer.analyze()
        # 缓存到 self 供 _save_pair_result 注入跨字段命中
        self._global_features_obj = global_features
        self._check_cancelled()

        self._heartbeat(
            "S2",
            f"发现{len(global_features.personnel_overlaps)}组人员重叠、"
            f"{len(global_features.company_edges)}对企业关联",
            {
                "substage": "global_features_done",
                "stage_progress_pct": 100,
                "message": (
                    f"发现{len(global_features.personnel_overlaps)}组人员重叠、"
                    f"{len(global_features.company_edges)}对企业关联"
                ),
            },
        )

        # -- S3: 智能筛对 + LLM深度对比 --
        from itertools import combinations
        all_pairs = list(combinations(bid_ids, 2))

        adjusted_candidate_scores = _apply_profile_confidence_to_candidate_scores(
            similarity_result.candidate_scores,
            industry_profile=industry_profile,
        )
        # P1-1: 灰度开启后，智能筛对额外纳入 D4 报价异常命中与 pricing_patterns 覆盖
        _d4_enabled = use_d4_full_coverage(self._project_id)
        algo_hits = self._get_algorithm_hits(extractions, include_d4=_d4_enabled)
        llm_pairs = gf_analyzer.get_smart_filter_pairs(
            all_pairs, adjusted_candidate_scores, algo_hits,
            include_pricing_patterns=_d4_enabled,
        )
        skipped_pairs = [p for p in all_pairs if p not in set(llm_pairs)]

        self._heartbeat(
            "S3",
            f"0/{len(llm_pairs)}对(跳过{len(skipped_pairs)}对低相似度)",
            {
                "substage": "prepare_pairs",
                "current": 0,
                "total": len(llm_pairs),
                "stage_progress_pct": 0,
                "message": f"已筛出 {len(llm_pairs)} 对重点文件",
            },
        )
        logger.info(
            f"[Analyzer] 智能筛对: LLM分析={len(llm_pairs)}对, "
            f"跳过={len(skipped_pairs)}对, 总计={len(all_pairs)}对"
        )

        pair_results = await self._run_deep_comparison(
            bid_docs=bid_docs,
            extractions=extractions,
            similarity_result=similarity_result,
            gating_scores=adjusted_candidate_scores,
            tender_format=tender_format,
            selected_pairs=llm_pairs,
            industry_profile=industry_profile,
        )
        self._check_cancelled()

        await self._save_skipped_pairs(
            skipped_pairs, bid_docs, similarity_result, extractions,
        )
        self._check_cancelled()

        # -- S3.5: 图聚类 --
        self._heartbeat(
            "S3.5",
            "构建关系图",
            {
                "substage": "build_graph",
                "stage_progress_pct": 10,
                "message": "构建关系图",
            },
        )
        clusters = build_collusion_graph(
            pair_results, global_features, doc_names=doc_names,
        )

        # P0-3: 灰度开启后，将 D4/D8 全局发现注入群组证据，游离部分记录到 analyzer
        # 成员变量，供 _build_project_summary_payload 汇总到 Project.collusion_summary
        if use_global_findings_enrich(self._project_id):
            self._global_findings_extra = enrich_clusters_with_global_findings(
                clusters, global_features, doc_names=doc_names,
            )
        else:
            self._global_findings_extra = []

        await self._save_groups(clusters)
        self._check_cancelled()
        self._heartbeat(
            "S3.5",
            f"识别到{len(clusters)}个嫌疑群组",
            {
                "substage": "groups_identified",
                "current": len(clusters),
                "total": len(clusters),
                "stage_progress_pct": 60,
                "message": f"识别到{len(clusters)}个嫌疑群组",
            },
        )

        logger.info(f"[Analyzer] 图聚类完成: {len(clusters)}个嫌疑群组")

        # -- S3.6: 证据融合 --
        if clusters:
            self._heartbeat(
                "S3.5",
                "证据融合中",
                {
                    "substage": "fuse_evidence",
                    "stage_progress_pct": 85,
                    "message": "证据融合中",
                },
            )
            await self._fuse_group_evidence(clusters, doc_names, global_features)
            self._check_cancelled()
            logger.info("[Analyzer] 证据融合完成")

        # -- S4: 群组研判 + 全局综合 --
        self._heartbeat(
            "S4",
            "群组研判中",
            {
                "substage": "group_analysis",
                "current": 0,
                "total": len(clusters),
                "stage_progress_pct": 0,
                "message": "群组研判中",
            },
        )
        if clusters:
            await self._run_group_analysis(clusters, extractions, doc_names, global_features)
            self._check_cancelled()

        synthesis = await self._run_overall_synthesis(
            bid_docs=bid_docs,
            clusters=clusters,
            pair_results=pair_results,
            global_features=global_features,
            has_tender_doc=tender_doc is not None,
            total_pairs=len(all_pairs),
            llm_pair_count=len(llm_pairs),
            skipped_count=len(skipped_pairs),
            industry_profile=industry_profile,
        )
        self._check_cancelled()

        if self._synthesis_retry.has_failures:
            self._check_cancelled()
            retry_stats = await self._synthesis_retry.retry_all()
            self._check_cancelled()
            for detail in retry_stats.details:
                if detail["success"] and isinstance(detail["result"], dict):
                    synthesis = detail["result"]
                    logger.info("[Analyzer] 综合研判重试成功，替换简化结论")
                    break

        summary_payload = _build_project_summary_payload(
            synthesis=synthesis,
            pair_results=pair_results,
            clusters=clusters,
            total_pairs=len(all_pairs),
            llm_pair_count=len(llm_pairs),
            skipped_count=len(skipped_pairs),
            has_tender_doc=tender_doc is not None,
            industry_profile=industry_profile,
            global_findings_extra=self._global_findings_extra,
        )
        self._check_cancelled()
        await self._save_project_summary(summary_payload)
        self._check_cancelled()

        logger.info(f"[Analyzer] 分析完成, 整体风险={synthesis.get('overall_risk')}")
        return synthesis

    # ── S1a ─────────────────────────────────────────────────────────────────────

    async def _extract_tender_format(self, tender_doc: Document) -> Optional[dict]:
        return await extraction_stage.extract_tender_format(self, tender_doc)

    # ── S1d ─────────────────────────────────────────────────────────────────────

    async def _generate_industry_profile(
        self,
        tender_text: Optional[str] = None,
        bid_extractions: Optional[list[dict]] = None,
    ) -> Optional[dict]:
        return await extraction_stage.generate_industry_profile(self, tender_text, bid_extractions)

    # ── S1b ─────────────────────────────────────────────────────────────────────

    async def _extract_all_bid_docs(self, bid_docs: list[Document]) -> list[dict]:
        return await extraction_stage.extract_all_bid_docs(self, bid_docs)

    async def _check_extraction_quality(self, bid_docs: list[Document]) -> None:
        return await extraction_stage.check_extraction_quality(self, bid_docs)

    async def _extract_one_bid_doc(self, doc: Document) -> dict:
        return await extraction_stage.extract_one_bid_doc(self, doc)

    async def _run_multi_route_extraction(
        self,
        *,
        doc: Document,
        route_contexts: dict[str, dict],
    ) -> dict:
        return await extraction_stage.run_multi_route_extraction(self, doc=doc, route_contexts=route_contexts)

    async def _extract_route_payload(
        self,
        *,
        doc: Document,
        route_name: str,
        context_meta: dict,
    ) -> tuple[str, dict]:
        return await extraction_stage.extract_route_payload(self, doc=doc, route_name=route_name, context_meta=context_meta)

    async def _build_extraction_route_contexts(self, doc_id: int) -> dict[str, dict]:
        return await extraction_stage.build_extraction_route_contexts(self, doc_id)

    async def _get_document_chunks(self, doc_id: int) -> list[dict]:
        return await extraction_stage.get_document_chunks(self, doc_id)

    def _select_chunks_for_route(
        self,
        chunks: list[dict],
        config: ExtractionRouteConfig,
    ) -> list[dict]:
        return extraction_stage.select_chunks_for_route(self, chunks, config)

    def _format_extraction_chunks(self, chunks: list[dict], max_chars: int) -> str:
        return extraction_stage.format_extraction_chunks(self, chunks, max_chars)

    def _build_collusion_metadata_info(self, *, extraction: dict, file_path: str) -> dict:
        return extraction_stage.build_collusion_metadata_info(self, extraction=extraction, file_path=file_path)

    def _read_pdf_metadata(self, file_path: str) -> dict[str, str]:
        return extraction_stage.read_pdf_metadata(self, file_path)

    # ── S3 ──────────────────────────────────────────────────────────────────────

    async def _run_deep_comparison(
        self,
        bid_docs: list[Document],
        extractions: dict[int, dict],
        similarity_result,
        gating_scores: dict[tuple[int, int], float],
        tender_format: Optional[dict],
        selected_pairs: Optional[list[tuple[int, int]]] = None,
        industry_profile: Optional[dict] = None,
    ) -> list[dict]:
        return await pair_compare.run_deep_comparison(self, bid_docs, extractions, similarity_result, gating_scores, tender_format, selected_pairs, industry_profile)

    async def _compare_one_pair(
        self,
        doc_a: Document,
        doc_b: Document,
        info_a: dict,
        info_b: dict,
        similarity_score: float,
        gating_score: float,
        similar_chunks: list[dict],
        tender_format: Optional[dict],
        industry_profile: Optional[dict] = None,
        coverage_ratio: float = 0.0,
        max_continuous_span: int = 0,
    ) -> dict:
        return await pair_compare.compare_one_pair(self, doc_a, doc_b, info_a, info_b, similarity_score, gating_score, similar_chunks, tender_format, industry_profile, coverage_ratio, max_continuous_span)

    async def _llm_compare(
        self,
        doc_a: Document,
        doc_b: Document,
        info_a: dict,
        info_b: dict,
        similarity_score: float,
        similar_chunks: list[dict],
        tender_format: Optional[dict],
        *,
        mode: str = "full",
    ) -> dict:
        return await pair_compare.llm_compare(self, doc_a, doc_b, info_a, info_b, similarity_score, similar_chunks, tender_format, mode=mode)

    # ── S3 分组并行对比 ──────────────────────────────────────────────────────────

    async def _retrieve_dimension_context(
        self,
        doc_id: int,
        dim_group: str,
        *,
        min_similarity: float = 0.3,
        top_k: int = 8,
        max_chars: int = 10000,
        **kwargs,
    ) -> str:
        return await pair_compare.retrieve_dimension_context(
            self, doc_id, dim_group,
            min_similarity=min_similarity, top_k=top_k, max_chars=max_chars,
            **kwargs,
        )

    async def _analyze_entity_dims(
        self,
        doc_a: Document,
        doc_b: Document,
        info_a: dict,
        info_b: dict,
        entity_ctx_a: str,
        entity_ctx_b: str,
        similarity_score: float,
        tender_format: Optional[dict],
        coverage_pct: str = "0",
        max_continuous_span: str = "0",
    ) -> dict:
        return await pair_dimensions.analyze_entity_dims(self, doc_a, doc_b, info_a, info_b, entity_ctx_a, entity_ctx_b, similarity_score, tender_format, coverage_pct, max_continuous_span)

    async def _analyze_text_dims(
        self,
        doc_a: Document,
        doc_b: Document,
        similar_chunks: list[dict],
        similarity_score: float,
        tender_format: Optional[dict],
        industry_profile: Optional[dict] = None,
        coverage_pct: str = "0",
        max_continuous_span: str = "0",
    ) -> dict:
        return await pair_dimensions.analyze_text_dims(self, doc_a, doc_b, similar_chunks, similarity_score, tender_format, industry_profile, coverage_pct, max_continuous_span)

    async def _analyze_business_dims(
        self,
        doc_a: Document,
        doc_b: Document,
        info_a: dict,
        info_b: dict,
        business_ctx_a: str,
        business_ctx_b: str,
        similarity_score: float,
        tender_format: Optional[dict],
        coverage_pct: str = "0",
        max_continuous_span: str = "0",
        industry_profile: Optional[dict] = None,
    ) -> dict:
        return await pair_dimensions.analyze_business_dims(self, doc_a, doc_b, info_a, info_b, business_ctx_a, business_ctx_b, similarity_score, tender_format, coverage_pct, max_continuous_span, industry_profile)

    async def _compare_one_pair_v2(
        self,
        doc_a: Document,
        doc_b: Document,
        info_a: dict,
        info_b: dict,
        similarity_score: float,
        similar_chunks: list[dict],
        tender_format: Optional[dict],
        industry_profile: Optional[dict] = None,
        coverage_ratio: float = 0.0,
        max_continuous_span: int = 0,
    ) -> tuple[dict, str]:
        return await pair_compare.compare_one_pair_v2(self, doc_a, doc_b, info_a, info_b, similarity_score, similar_chunks, tender_format, industry_profile, coverage_ratio, max_continuous_span)

    async def _deep_search_empty_dims(
        self,
        compare_result: dict,
        doc_a: Document,
        doc_b: Document,
        similarity_score: float,
        tender_format: Optional[dict],
    ) -> tuple[dict, bool]:
        return await pair_dimensions.deep_search_empty_dims(self, compare_result, doc_a, doc_b, similarity_score, tender_format)

    # ── S4 ──────────────────────────────────────────────────────────────────────

    async def _run_synthesis(
        self,
        bid_docs: list[Document],
        pair_results: list[dict],
        has_tender_doc: bool,
        total_pairs: int,
        llm_pair_count: int,
        skipped_count: int,
        industry_profile: Optional[dict] = None,
        global_features: Optional["GlobalFeatureResult"] = None,  # noqa: F821 lazy type
    ) -> dict:
        return await synthesis_stage.run_synthesis(self, bid_docs, pair_results, has_tender_doc, total_pairs, llm_pair_count, skipped_count, industry_profile, global_features)

    async def _save_synthesis_snapshot(self, pair_results: list[dict], synthesis: dict) -> None:
        return await synthesis_stage.save_synthesis_snapshot(self, pair_results, synthesis)

    async def _save_project_summary(self, summary_payload: dict) -> None:
        return await synthesis_stage.save_project_summary(self, summary_payload)

    async def _enrich_findings_with_context(
        self, compare_result: dict, doc_a_id: int, doc_b_id: int
    ) -> None:
        return await pair_evidence.enrich_findings_with_context(self, compare_result, doc_a_id, doc_b_id)

    async def _backfill_compare_result_evidence_refs(
        self,
        compare_result: dict,
        doc_a_id: int,
        doc_b_id: int,
    ) -> None:
        return await pair_evidence.backfill_compare_result_evidence_refs(self, compare_result, doc_a_id, doc_b_id)

    # -- S2/S3.5/S4 多方群组识别 -----------------------------------------------

    def _get_algorithm_hits(
        cls,
        extractions: dict[int, dict],
        *,
        include_d4: bool = False,
    ) -> set[tuple[int, int]]:
        return pair_persistence.get_algorithm_hits(cls, extractions, include_d4=include_d4)

    @staticmethod
    def _get_algorithm_d4_hits(
        extractions: dict[int, dict],
    ) -> set[tuple[int, int]]:
        """P1-1: 两份文件报价呈现精确比例/等差/尾数一致时命中 D4。"""
        from itertools import combinations
        from app.core.collusion.global_features import parse_price

        hits: set[tuple[int, int]] = set()
        prices: dict[int, float] = {}
        for doc_id, ext in extractions.items():
            price_info = ext.get("price_info") or {}
            if not isinstance(price_info, dict):
                continue
            val = parse_price(price_info.get("total_price"))
            if val is not None and val > 0:
                prices[doc_id] = val

        ids_sorted = sorted(prices.keys())
        for id_a, id_b in combinations(ids_sorted, 2):
            pa, pb = prices[id_a], prices[id_b]
            lo, hi = min(pa, pb), max(pa, pb)
            if hi <= 0:
                continue
            ratio = lo / hi
            # 精确比例（陪标常见模式：0.85–0.99 刻意接近）
            if 0.85 <= ratio < 1.0:
                hits.add((min(id_a, id_b), max(id_a, id_b)))
                continue
            # 尾数完全一致（报价习惯雷同）——尾数为 00 的整数报价极为常见，
            # 不具备可疑性，需排除，避免误报；同时要求 pa != pb
            tail = int(round(pa)) % 100
            if tail != 0 and tail == int(round(pb)) % 100 and pa != pb:
                hits.add((min(id_a, id_b), max(id_a, id_b)))

        return hits

    @staticmethod
    def _get_algorithm_d1d2_hits(extractions: dict[int, dict]) -> set[tuple[int, int]]:
        return pair_persistence.get_algorithm_d1d2_hits(extractions)

    async def _save_skipped_pairs(
        self,
        skipped_pairs: list[tuple[int, int]],
        bid_docs: list[Document],
        similarity_result,
        extractions: dict[int, dict] | None = None,
    ) -> None:
        return await pair_persistence.save_skipped_pairs(
            self, skipped_pairs, bid_docs, similarity_result, extractions,
        )

    async def _save_groups(self, clusters: list[CollusionCluster]) -> None:
        return await group_stage.save_groups(self, clusters)

    async def _fuse_group_evidence(
        self,
        clusters: list[CollusionCluster],
        doc_names: dict[int, str],
        global_features: GlobalFeatureResult,
    ) -> None:
        return await group_stage.fuse_group_evidence(self, clusters, doc_names, global_features)

    async def _save_group_findings(
        self,
        cluster_index: int,
        fused_findings: list[dict],
    ) -> None:
        return await group_stage.save_group_findings(self, cluster_index, fused_findings)

    async def _run_group_analysis(
        self,
        clusters: list[CollusionCluster],
        extractions: dict[int, dict],
        doc_names: dict[int, str],
        global_features: GlobalFeatureResult,
    ) -> None:
        return await group_stage.run_group_analysis(self, clusters, extractions, doc_names, global_features)

    async def _retry_group_analysis(
        self,
        idx: int,
        cluster: Any,
        member_list: str,
        agg_evidence_text: str,
        gf_text: str,
        doc_names: dict,
    ) -> Optional[dict]:
        return await group_stage.retry_group_analysis(self, idx, cluster, member_list, agg_evidence_text, gf_text, doc_names)

    async def _run_overall_synthesis(
        self,
        bid_docs: list[Document],
        clusters: list[CollusionCluster],
        pair_results: list[dict],
        global_features: GlobalFeatureResult,
        has_tender_doc: bool,
        total_pairs: int,
        llm_pair_count: int,
        skipped_count: int,
        industry_profile: Optional[dict] = None,
    ) -> dict:
        # P1-2 静态契约（test_collusion_p1_fixes.py::TestOverallSynthesisTimeoutUnified
        # 通过正则搜索本函数体）：超时取 get_collusion_synthesis_timeout
        # （实际调用在 synthesis_stage.run_overall_synthesis）。
        return await synthesis_stage.run_overall_synthesis(self, bid_docs, clusters, pair_results, global_features, has_tender_doc, total_pairs, llm_pair_count, skipped_count, industry_profile)

    # ── DB 操作 ──────────────────────────────────────────────────────────────────

    async def _load_documents(self) -> list[Document]:
        return await extraction_stage.load_documents(self)

    async def _get_project_owner_id(self) -> int | None:
        return await extraction_stage.get_project_owner_id(self)

    async def _get_kb_usage_allowed_ids(self) -> list[int]:
        return await extraction_stage.get_kb_usage_allowed_ids(self)

    async def _get_document_text(self, doc_id: int, max_chars: int) -> str:
        return await extraction_stage.get_document_text(self, doc_id, max_chars)

    async def _query_extraction(self, doc_id: int) -> Optional[CollusionExtraction]:
        return await extraction_stage.query_extraction(self, doc_id)

    async def _query_pair_result(self, doc_a_id: int, doc_b_id: int) -> Optional[CollusionResult]:
        return await pair_persistence.query_pair_result(self, doc_a_id, doc_b_id)

    async def _save_extraction(
        self,
        document_id: int,
        doc_type: str,
        extraction: dict,
        format_requirements: Optional[dict] = None,
        status: str = "success",
        error_message: Optional[str] = None,
    ) -> None:
        """保存信息提取结果（独立会话+commit，避免干扰主session并发读取）: 写入前做顶层 CollusionExtractionPayload 兜底校验（仅 warning，
 不阻断保存），用于捕捉字段类型异常 / 子键漂移，为下一阶段的迁移铺路。
 """
        _validate_extraction_payload(extraction, document_id=document_id, doc_type=doc_type)

        metadata_info = extraction.get("metadata_info")
        if not isinstance(metadata_info, dict):
            metadata_info = {}
        # (2026-04-24): subcontractors 现在独立列 subcontractors_info
        # 承载；此处防御性剔除 metadata_info 中的错位残留（任何上游误传入），
        # 保证 metadata_info 的语义契约收敛到"仅 PDF 元数据"。
        metadata_info.pop("subcontractors", None)

        subcontractors_value = extraction.get("subcontractors") or None

        field_values = {
            "company_info": extraction.get("company_info"),
            "personnel_info": extraction.get("personnel_list"),
            "price_info": extraction.get("price_info"),
            "technical_features": extraction.get("technical_highlights"),
            "qualification_info": extraction.get("qualifications"),
            "guarantee_info": extraction.get("past_projects"),
            "metadata_info": metadata_info or None,
            "subcontractors_info": subcontractors_value,
            "format_requirements": format_requirements,
            "extraction_status": status,
            "error_message": error_message,
        }
        # 单文档信息提取在并发循环中, 短 session 走许可信号量。
        async with db_session_scope() as save_db:
            rows = await save_db.execute(
                select(CollusionExtraction).where(
                    CollusionExtraction.project_id == self._project_id,
                    CollusionExtraction.document_id == document_id,
                )
            )
            existing = rows.scalar_one_or_none()
            if existing:
                for k, v in field_values.items():
                    setattr(existing, k, v)
            else:
                save_db.add(CollusionExtraction(
                    project_id=self._project_id,
                    document_id=document_id,
                    doc_type=doc_type,
                    **field_values,
                ))
            await save_db.commit()

    async def _maybe_run_cove_judge_collusion(
        self,
        compare_result: dict,
        pair_label: str,
        mode: str,
    ) -> None:
        return await pair_evidence.maybe_run_cove_judge_collusion(self, compare_result, pair_label, mode)

    async def _prefetch_chunks_for_gate(
        self,
        doc_a_id: int,
        doc_b_id: int,
    ) -> tuple[
        dict[int, set[int]],
        dict[int, list[tuple[int | None, str]]],
    ]:
        return await pair_evidence.prefetch_chunks_for_gate(self, doc_a_id, doc_b_id)

    def _inject_cross_field_findings(
        self,
        compare_result: dict,
        doc_a_id: int,
        doc_b_id: int,
    ) -> None:
        return pair_evidence.inject_cross_field_findings(self, compare_result, doc_a_id, doc_b_id)

    async def _save_pair_result(
        self,
        doc_a_id: int,
        doc_b_id: int,
        similarity_score: float,
        compare_result: dict,
        analysis_mode: str,
        similar_chunks: Optional[list[dict]] = None,
    ) -> None:
        return await pair_persistence.save_pair_result(
            self, doc_a_id, doc_b_id, similarity_score, compare_result, analysis_mode,
            similar_chunks=similar_chunks,
        )

    # ── 工具方法 ─────────────────────────────────────────────────────────────────

    def _extraction_to_dict(self, ext: CollusionExtraction) -> dict:
        # (2026-04-24): subcontractors 优先从独立列 subcontractors_info
        # 读取；兼容未执行 migration 的旧数据 / 手工回滚场景，fallback 到
        # metadata_info.subcontractors 错位子键。确保 metadata_info 返回的
        # dict 中不再含 subcontractors 子键，与写入路径契约对齐。
        metadata_info_raw = ext.metadata_info
        metadata_info: Optional[dict]
        fallback_subs: list | None = None
        if isinstance(metadata_info_raw, dict):
            metadata_info = dict(metadata_info_raw)
            fallback_subs = metadata_info.pop("subcontractors", None)
        else:
            metadata_info = metadata_info_raw

        primary_subs = ext.subcontractors_info
        subcontractors = primary_subs if primary_subs is not None else fallback_subs

        return {
            "company_info": ext.company_info,
            "personnel_list": ext.personnel_info,
            "price_info": ext.price_info,
            "technical_highlights": ext.technical_features,
            "qualifications": ext.qualification_info,
            "past_projects": ext.guarantee_info,
            "metadata_info": metadata_info,
            "subcontractors": subcontractors or [],
        }


# Task 8 Step 2: 抽取域纯函数迁出到 analyzer_parts.extraction_validation
# 此处 re-export 兑现外部 `from app.core.collusion.analyzer import _xxx` 契约。
from app.core.collusion.analyzer_parts.extraction_validation import (
    _is_empty_extraction,
    _validate_extraction_payload,
    _extract_docx_structure_from_info,
    _build_chunk_text_blob,
    _build_chunk_fallback_context,
    _empty_extraction,
    _format_extraction,
    _infer_doc_subtype,
)


# Task 8 Step 3: 算法兜底域纯函数迁出到 analyzer_parts.algorithmic
# 此处 re-export 兑现外部 `from app.core.collusion.analyzer import _xxx` 契约。
from app.core.collusion.analyzer_parts.algorithmic import (
    _algorithm_compare,
    _algorithm_d4_score,
    _META_CHECKS,
    _inject_d8_from_metadata,
    _is_close_pdf_datetime,
)


# Task 8 Step 4: findings/attention 域纯函数迁出到 analyzer_parts.findings
# 此处 re-export 兑现外部 `from app.core.collusion.analyzer import _xxx` 契约。
from app.core.collusion.analyzer_parts.findings import (
    _has_positive_dimension_score,
    _has_dimension_payload,
    _finalize_pair_contract,
    _EVIDENCE_LEVEL_ORDER,
    _FLAT_FINDING_RECOVERED_FLAG,
    _recover_flattened_findings,
    _extract_structured_findings,
    _maybe_promote_to_attention,
    _collect_attention_from_dim,
    _normalize_compare_result,
    _build_evidences_for_pair,
    _extract_page_number,
    _normalize_evidence_text,
    _compact_evidence_quote,
    _has_verifiable_evidence_refs,
    _normalize_dim_evidence_levels,
    _consistency_check,
    _derive_overall_risk_from_dims,
    _HOLLOW_EVIDENCE_PATTERNS,
)


# Task 8 Step 5: synthesis 域纯函数迁出到 analyzer_parts.synthesis_helpers
# 此处 re-export 兑现外部 `from app.core.collusion.analyzer import _xxx` 契约。
from app.core.collusion.analyzer_parts.synthesis_helpers import (
    _GLOBAL_FINDING_TYPE_PRIORITY,
    _GLOBAL_FINDING_TYPE_LABEL,
    _build_global_findings_context,
    _format_group_evidence_for_llm,
    _build_pair_summaries,
    _simple_synthesis,
    _build_pair_facts_context,
    _highest_risk_local,
    _build_failed_pair_result,
    _build_coverage_stats,
    _apply_profile_confidence_to_candidate_scores,
    _build_data_quality_flags,
    _build_profile_quality_flags,
    _derive_project_decision,
    _build_project_narrative_summary,
    _build_project_summary_payload,
    _result_to_dict,
    _merge_group_results,
)


# ============================================================================
# 证据闸门残留工具 -- 历史
# ============================================================================
# (2026-04-24): legacy `_verify_d1_d2_claims` / `_enforce_d5_business_rules`
# / `_verify_finding_pages` / `_apply_verification_gate` 四个函数已整体下线，
# 其算法等价逻辑已迁移到 app.core.ai.gates.collusion 下对应 Gate 实现。
# 本文件仅保留 _HOLLOW_EVIDENCE_PATTERNS —— 被 _extract_structured_findings /
# _maybe_promote_to_attention 使用，与 legacy 闸门无关。



# (2026-04-24): 以下 4 个 legacy 函数已整体下线，等价算法迁移到
#   - app.core.ai.gates.collusion.d1_personnel_claim.D1PersonnelClaimGate
#   - app.core.ai.gates.collusion.d2_company_claim.D2CompanyClaimGate
#   - app.core.ai.gates.collusion.d5_business_relation.D5BusinessRelationGate
#   - app.core.ai.gates.collusion.d5_qual_type_exemption.D5QualTypeExemptionGate
#   - app.core.ai.gates.collusion.finding_page_quote_verify.FindingPageQuoteVerifyGate
# 由 build_pipeline_for("collusion") 自动注册并执行。


# _infer_doc_subtype: Task 8 Step 2 已迁出到 analyzer_parts.extraction_validation
# 顶部 re-export 已兑现外部 import 路径。
