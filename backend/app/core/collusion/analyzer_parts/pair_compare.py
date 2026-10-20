"""串标分析器 S3 pair 比对核心方法

Task 8 Step 7：从 CollusionAnalyzer 主类迁出 S3 核心比对 5 个方法到本子模块。
主类保留同名 thin forwarder 兼容外部调用。

子模块函数签名规范（方案 B）：
- 函数名 = 原 method 名去前导下划线（_run_deep_comparison -> run_deep_comparison）
- 第一参数 self 名字保留；方法体内 self.xxx 通过主类 thin forwarder 链路工作。
- 跨子模块调用走 self.xxx (走主类 thin forwarder)，避免直接 import 兄弟子模块。

迁出范围：
- run_deep_comparison（107 行编排器）
- compare_one_pair（172 行 legacy 单对编排）
- llm_compare（104 行 LLM 调用主路径）
- retrieve_dimension_context（89 行维度上下文检索）
- compare_one_pair_v2（233 行新版多维并行编排）
"""

from __future__ import annotations


# 补全 import 修复
from app.core.ai.prompt_registry import resolve_user_template
from app.core.collusion.analyzer_parts.extraction_validation import _build_chunk_text_blob
from app.core.collusion.analyzer_parts.extraction_validation import _extract_docx_structure_from_info
from app.core.collusion.analyzer_parts.synthesis_helpers import _build_failed_pair_result
from app.core.collusion.analyzer_parts.synthesis_helpers import _build_pair_facts_context
from app.core.collusion.analyzer_parts.synthesis_helpers import _merge_group_results
from app.core.collusion.analyzer_parts.synthesis_helpers import _result_to_dict
from app.schemas.collusion import build_pair_id

import asyncio
from typing import Optional

from loguru import logger

from app.core.config import (
    settings,
    get_collusion_compare_timeout,
)
from app.core.database import async_session_factory
from app.core.collusion.analyzer_parts.constants import (
    _DIM_KEYS,
)
from app.core.collusion.analyzer_parts.algorithmic import (
    _algorithm_compare,
    _inject_d8_from_metadata,
)
from app.core.collusion.analyzer_parts.extraction_validation import (
    _build_chunk_fallback_context,
    _format_extraction,
    _infer_doc_subtype,
    _is_empty_extraction,
)
from app.core.collusion.analyzer_parts.findings import (
    _consistency_check,
    _finalize_pair_contract,
    _normalize_compare_result,
)
from app.core.collusion.knowledge_base import (
    build_dynamic_normal_similarity_hint,
    build_normal_similarity_hint,
)
from app.core.collusion.prompts import (
    COLLUSION_PAIR_JUDGEMENT_PROMPT,
    build_tender_context,
)
from app.core.collusion.rules.pair_gate import enforce_pair_scenario_ledger
from app.models.document import Document
from app.schemas.collusion import LLMCompareResult


def _entity_spec(info: dict, doc: Document):
    from app.core.collusion.identifier_index import build_index
    from app.core.retriever.evidence_pipeline.dimension_context import DimensionRetrieveSpec
    from app.core.retriever.evidence_pipeline.entity_retriever import (
        terms_and_pages_from_identifier_index,
    )

    index = build_index(doc.id, doc.filename or "", info or {})
    terms, pages = terms_and_pages_from_identifier_index(index)
    return DimensionRetrieveSpec(identifier_terms=terms, identifier_pages=pages)


async def run_deep_comparison(
    self,
    bid_docs: list[Document],
    extractions: dict[int, dict],
    similarity_result,
    gating_scores: dict[tuple[int, int], float],
    tender_format: Optional[dict],
    selected_pairs: Optional[list[tuple[int, int]]] = None,
    industry_profile: Optional[dict] = None,
) -> list[dict]:
    """并行执行文件对的深度对比分析（支持智能筛对）"""
    from itertools import combinations

    doc_map = {d.id: d for d in bid_docs}
    if selected_pairs is not None:
        pairs_to_analyze = selected_pairs
    else:
        pairs_to_analyze = list(combinations([d.id for d in bid_docs], 2))
    tasks = []

    async def _compare_with_placeholder(
        *,
        doc_a: Document,
        doc_b: Document,
        info_a: dict,
        info_b: dict,
        sim_score: float,
        gating_score: float,
        similar_chunks_for_pair: list[dict],
        coverage_for_pair: float,
        span_for_pair: int,
    ) -> dict:
        try:
            return await self._compare_one_pair(
                doc_a=doc_a,
                doc_b=doc_b,
                info_a=info_a,
                info_b=info_b,
                similarity_score=sim_score,
                gating_score=gating_score,
                similar_chunks=similar_chunks_for_pair,
                tender_format=tender_format,
                industry_profile=industry_profile,
                coverage_ratio=coverage_for_pair,
                max_continuous_span=span_for_pair,
            )
        except Exception as exc:
            logger.error(
                f"[Analyzer] 单对分析异常: pair={build_pair_id(doc_a.id, doc_b.id)}, err={exc}",
                exc_info=True,
            )
            failed_result = _build_failed_pair_result(
                doc_a=doc_a,
                doc_b=doc_b,
                similarity_score=sim_score,
                reason=str(exc) or "单对分析异常",
            )
            await self._save_pair_result(
                doc_a_id=min(doc_a.id, doc_b.id),
                doc_b_id=max(doc_a.id, doc_b.id),
                similarity_score=sim_score,
                compare_result=failed_result,
                analysis_mode="failed",
                similar_chunks=similar_chunks_for_pair,
            )
            return failed_result

    for doc_a_id, doc_b_id in pairs_to_analyze:
        sim_score = similarity_result.pair_scores.get((doc_a_id, doc_b_id), 0.0)
        gating_score = gating_scores.get((doc_a_id, doc_b_id), sim_score)
        similar_chunks = similarity_result.similar_chunks.get((doc_a_id, doc_b_id), [])
        coverage = similarity_result.coverage_ratios.get((doc_a_id, doc_b_id), 0.0)
        span = similarity_result.max_continuous_spans.get((doc_a_id, doc_b_id), 0)

        tasks.append(
            _compare_with_placeholder(
                doc_a=doc_map[doc_a_id],
                doc_b=doc_map[doc_b_id],
                info_a=extractions.get(doc_a_id, {}),
                info_b=extractions.get(doc_b_id, {}),
                sim_score=sim_score,
                gating_score=gating_score,
                similar_chunks_for_pair=similar_chunks,
                coverage_for_pair=coverage,
                span_for_pair=span,
            )
        )

    pair_results = []
    completed = 0
    for coro in asyncio.as_completed(tasks):
        result = await coro
        pair_results.append(result)
        completed += 1
        self._heartbeat(
            "S3",
            f"{completed}/{len(tasks)} 对",
            {
                "substage": "pair_completed",
                "current": completed,
                "total": len(tasks),
                "stage_progress_pct": 100,
                "message": f"已完成 {completed}/{len(tasks)} 对重点文件对比",
            },
        )

    return pair_results


async def _run_gate_pipeline(
    self,
    compare_result: dict,
    info_a: dict,
    info_b: dict,
    doc_a_id: int,
    doc_b_id: int,
    tender_format: Optional[dict],
) -> None:
    """构造 gate evidence 并执行串标 Gate Pipeline（v1/v2 双路径共用）

 原地修改 compare_result（run_all_stages 的 patch 回写 + failure_state 字段）。
 (2026-04-24): legacy `_verify_*` 分支已下线，串标流 Gate Pipeline
 无条件执行；灰度比例配置 GATE_PIPELINE_RATIO_COLLUSION 同步移除。
 """
    from app.core.ai.gates import build_pipeline_for
    from app.core.ai.gates.base import GateContext
    from app.core.contracts.degradation_signal import drain_signals

    _page_sets, _chunk_texts = await self._prefetch_chunks_for_gate(
        doc_a_id, doc_b_id,
    )
    # B2: 注入 page-indexed chunks，供 FindingPageQuoteVerifyGate
    # 做 page × quote 联合校验
    _page_to_contents = getattr(self, "_last_page_to_contents", None) or {}
    _pair_ref = compare_result.get("pair_ref") or {}
    # S.2.8 (D-025 方案 c): 为 D6FormatAlgorithmicGate 预留 evidence
    # 字段。docx_structure 未生产时 Gate 原生走 "结构信息不可用 →
    # pass + confidence=0" 分支，不误报；P2-1 以后 extractor 补采可
    # 直接被读取。
    # D7 接线: 拼接 chunk_texts 为 text_a/text_b，让
    # D7AbnormalConsistencyGate 跳出 "文本不可用" 兜底，启用 LCS 与
    # 共同错别字算法判定。
    _evidence = {
        "info_a": info_a, "info_b": info_b,
        "page_sets": _page_sets, "chunk_texts": _chunk_texts,
        "page_to_contents": _page_to_contents,
        "doc_a_id": doc_a_id, "doc_b_id": doc_b_id,
        "doc_a_name": _pair_ref.get("doc_a_name"),
        "doc_b_name": _pair_ref.get("doc_b_name"),
        "docx_structure_a": _extract_docx_structure_from_info(info_a),
        "docx_structure_b": _extract_docx_structure_from_info(info_b),
        "tender_format_whitelist": tender_format or {},
        "text_a": _build_chunk_text_blob(_chunk_texts, doc_a_id),
        "text_b": _build_chunk_text_blob(_chunk_texts, doc_b_id),
    }
    _cfg = {"project_id": self._project_id}
    _pipeline = build_pipeline_for("collusion")
    _ctx_post_llm = GateContext(
        flow="collusion", stage="post_llm",
        target=compare_result, evidence=_evidence, config=_cfg,
    )
    _ctx_post_pipe = GateContext(
        flow="collusion", stage="post_pipeline",
        target=compare_result, evidence=_evidence, config=_cfg,
    )
    _d_signals = {s.source for s in drain_signals()}
    _verdicts, _state, _reason = _pipeline.run_all_stages(
        ctx_post_llm=_ctx_post_llm,
        ctx_post_pipeline=_ctx_post_pipe,
        apply_patch_to=compare_result,
        degraded_signals=_d_signals,
    )
    if settings.FAILURE_STATE_ENABLED:
        compare_result["failure_state"] = _state.value
        compare_result["failure_reason"] = _reason


async def compare_one_pair(
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
    """单对文件的深度对比分析（带断点续跑 + 分级策略）"""
    if doc_a.id > doc_b.id:
        doc_a, doc_b = doc_b, doc_a
        info_a, info_b = info_b, info_a

    pair_id = build_pair_id(doc_a.id, doc_b.id)

    # 检查断点续跑
    existing = await self._query_pair_result(doc_a.id, doc_b.id)
    if existing:
        logger.info(f"[Analyzer] 跳过已分析对: ({doc_a.id}, {doc_b.id})")
        return _result_to_dict(
            existing,
            doc_a_id=doc_a.id,
            doc_b_id=doc_b.id,
            doc_a_name=doc_a.filename,
            doc_b_name=doc_b.filename,
        )

    high_thresh = settings.COLLUSION_SIMILARITY_HIGH
    med_thresh = settings.COLLUSION_SIMILARITY_MEDIUM

    async with self._semaphore:
        extraction_degraded = (
            _is_empty_extraction(info_a) or _is_empty_extraction(info_b)
        )

        used_v2 = False
        if gating_score >= high_thresh:
            compare_result, mode = await self._compare_one_pair_v2(
                doc_a, doc_b, info_a, info_b,
                similarity_score, similar_chunks, tender_format,
                industry_profile=industry_profile,
                coverage_ratio=coverage_ratio,
                max_continuous_span=max_continuous_span,
            )
            used_v2 = True
        elif gating_score >= med_thresh:
            compare_result = await self._llm_compare(
                doc_a, doc_b, info_a, info_b, similarity_score, similar_chunks, tender_format,
                mode="simplified",
            )
            mode = "simplified_degraded" if extraction_degraded else "simplified"
        else:
            compare_result = _algorithm_compare(
                info_a, info_b, doc_a.filename, doc_b.filename
            )
            mode = "algorithm_only"
            await self._enrich_findings_with_context(
                compare_result, doc_a.id, doc_b.id
            )

        if not used_v2:
            _inject_d8_from_metadata(compare_result, info_a, info_b)

        compare_result = _normalize_compare_result(
            compare_result=compare_result,
            doc_a_id=doc_a.id,
            doc_b_id=doc_b.id,
            doc_a_name=doc_a.filename,
            doc_b_name=doc_b.filename,
        )

        # ── 证据闸门 ──
        # S.2.6: v2 内部已跑过 Gate Pipeline 和 _maybe_run_cove_judge_collusion，
        # 外层加 used_v2 守卫防止双重 pipeline / 双重 CoVe-Judge 导致 patch
        # 二次施加与指标重复计数。
        # 外层仍执行 _normalize_compare_result / _finalize_pair_contract / review_state
        # 设置等收尾逻辑（v2 内部未跑）。
        if not used_v2:
            await _run_gate_pipeline(
                self, compare_result, info_a, info_b,
                doc_a.id, doc_b.id, tender_format,
            )

        compare_result.setdefault(
            "review_state",
            "degraded" if "degraded" in mode else "completed",
        )
        compare_result["gating_score"] = gating_score
        if any(chunk.get("kb_rule_match") for chunk in similar_chunks):
            # 提取本对相似 chunk 命中的通用白名单类别中文 label，
            # 既写入结构化字段供 narrative_builder 消费，又把"命中类别"
            # 显式塞进 quality_flag 文案，避免被 LLM summary 压缩成笼统措辞。
            from app.core.collusion.knowledge_base import extract_matched_categories
            matched_labels = extract_matched_categories(similar_chunks)
            compare_result["whitelist_categories"] = matched_labels
            compare_result.setdefault("quality_flags", [])
            if matched_labels:
                _categories_text = "、".join(matched_labels)
                compare_result["quality_flags"].append(
                    f"本对相似片段命中通用模板类别（{_categories_text}），"
                    "重合可能源于行业模板、法规或通用章节，非串通证据。"
                )
            else:
                compare_result["quality_flags"].append(
                    "本对相似片段命中通用白名单（具体类别未识别），重合可能源于模板、法规或行业通用章节。"
                )

        # P1-5: 招标文件存在但格式参照提取失败时, 给本 pair 追加 quality_flag,
        # 让前端可显式提示"白名单缺失, 本对可能高估模板性重合"。
        if getattr(self, "_tender_format_extract_failed", False):
            compare_result.setdefault("quality_flags", [])
            _tender_failed_flag = (
                "招标文件存在但格式参照提取失败，本对可能高估模板性重合，请人工复核。"
            )
            if _tender_failed_flag not in compare_result["quality_flags"]:
                compare_result["quality_flags"].append(_tender_failed_flag)

        compare_result = _finalize_pair_contract(
            compare_result,
            analysis_mode=mode,
            similarity_score=similarity_score,
            high_threshold=settings.COLLUSION_SIMILARITY_HIGH,
            extraction_degraded=extraction_degraded,
        )
        enforce_pair_scenario_ledger(compare_result, doc_a, doc_b, info_a, info_b)

        # B5.10 / B5.12: CoVe + Judge 对 HIGH 风险对子做复核
        # S.2.6: v2 路径内部已调 _maybe_run_cove_judge_collusion，外层跳过
        # 避免重复采样与指标翻倍
        if not used_v2:
            await self._maybe_run_cove_judge_collusion(compare_result, pair_id, mode)

        # 保存到数据库
        await self._save_pair_result(
            doc_a_id=doc_a.id,
            doc_b_id=doc_b.id,
            similarity_score=similarity_score,
            compare_result=compare_result,
            analysis_mode=mode,
            similar_chunks=similar_chunks,
        )

        logger.debug(f"[Analyzer] 对比完成 pair_id={pair_id}, mode={mode}")
        return compare_result


async def llm_compare(
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
    """LLM 深度对比（full=完整8维度, simplified=简化版限制chunk数和token）"""
    max_tokens = 3000 if mode == "full" else 2000
    chunks_kwargs: dict = dict(
        doc_a_id=doc_a.id, doc_b_id=doc_b.id,
        doc_a_name=doc_a.filename, doc_b_name=doc_b.filename,
    )
    if mode == "simplified":
        chunks_kwargs["max_pairs"] = 5

    # 提取为空时用 similar_chunks 原文兜底
    a_empty = _is_empty_extraction(info_a)
    b_empty = _is_empty_extraction(info_b)
    effective_info_a = _format_extraction(info_a)
    effective_info_b = _format_extraction(info_b)

    if (a_empty or b_empty) and similar_chunks:
        if a_empty:
            fallback_a = _build_chunk_fallback_context(similar_chunks, "a")
            if fallback_a:
                effective_info_a = (
                    "【注意: 结构化提取失败，以下为文档原文片段】\n" + fallback_a
                )
                logger.info(
                    f"[Analyzer] 文档{doc_a.id}提取为空，使用chunk原文兜底"
                )
        if b_empty:
            fallback_b = _build_chunk_fallback_context(similar_chunks, "b")
            if fallback_b:
                effective_info_b = (
                    "【注意: 结构化提取失败，以下为文档原文片段】\n" + fallback_b
                )
                logger.info(
                    f"[Analyzer] 文档{doc_b.id}提取为空，使用chunk原文兜底"
                )

    try:
        kb_hint = build_normal_similarity_hint(similar_chunks)
        # 历史属性 self.db 系 typo (应为 self._db), 一直抛 AttributeError
        # 被 try/except 吞掉, dynamic_kb_hint 实际从未生效。
        # 修复时同步改为短 session, 避免与并发对比协程在 self._db 上排队竞争。
        allowed_kb_ids = await self._get_kb_usage_allowed_ids()
        dynamic_kb_hint = ""
        if allowed_kb_ids:
            from app.core.database import async_session_factory as _kb_factory
            async with _kb_factory() as _kb_session:
                dynamic_kb_hint = await build_dynamic_normal_similarity_hint(
                    _kb_session,
                    similar_chunks,
                    owner_id=await self._get_project_owner_id(),
                    allowed_document_ids=allowed_kb_ids,
                )
        if dynamic_kb_hint:
            kb_hint = "\n\n".join(part for part in [kb_hint, dynamic_kb_hint] if part)
            logger.info(
                "[知识库辅助] flow=collusion_check project={} pair=({},{}) "
                "policy=normal_similarity_exemption_only",
                self._project_id,
                doc_a.id,
                doc_b.id,
            )
        return await self._call_llm(
            user_message=resolve_user_template(
                "collusion.pair_judgement", COLLUSION_PAIR_JUDGEMENT_PROMPT
            ).format(
                doc_a_id=doc_a.id,
                doc_a_name=doc_a.filename,
                doc_b_id=doc_b.id,
                doc_b_name=doc_b.filename,
                similarity_pct=f"{similarity_score * 100:.0f}%",
                pair_facts=_build_pair_facts_context(
                    effective_info_a=effective_info_a,
                    effective_info_b=effective_info_b,
                    similar_chunks=similar_chunks,
                    chunks_kwargs=chunks_kwargs,
                ),
                tender_context=build_tender_context(tender_format),
                industry_profile_context=kb_hint,
            ),
            response_model=LLMCompareResult,
            timeout=get_collusion_compare_timeout(),
            max_tokens=max_tokens,
        )
    except Exception as e:
        logger.warning(
            f"[Analyzer] LLM{mode}对比失败: ({doc_a.id}, {doc_b.id}), err={e}, 使用算法兜底"
        )
        fallback = _algorithm_compare(info_a, info_b, doc_a.filename, doc_b.filename)
        fallback["overall_risk"] = "inconclusive"
        return fallback


async def retrieve_dimension_context(
    self,
    doc_id: int,
    dim_group: str,
    *,
    min_similarity: float = 0.3,
    top_k: int = 8,
    max_chars: int = 10000,
    spec=None,
) -> str:
    """按维度组定向检索文档片段，用于分组 LLM 分析: 关键词 LIKE+limit(5) 改为词项通道; 可选 spec 指定 entity/table 通道。
 """
    from app.core.retriever.evidence_pipeline.dimension_context import (
        DimensionRetrieveSpec,
        collect_dimension_context_text,
    )

    retrieve_spec = spec if spec is not None else DimensionRetrieveSpec()
    async with async_session_factory() as session:
        return await collect_dimension_context_text(
            session,
            doc_id,
            dim_group,
            retrieve_spec,
            min_similarity=min_similarity,
            top_k=top_k,
            max_chars=max_chars,
        )


async def compare_one_pair_v2(
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
    """分组并行对比(高相似度对) -- 返回 (compare_result, analysis_mode)"""
    pair_id = f"{min(doc_a.id, doc_b.id)}:{max(doc_a.id, doc_b.id)}"
    # Phase 1: 并行检索 entity + business 上下文（4路并行）
    self._heartbeat(
        "S3",
        "正在检索文档内容",
        {
            "substage": "retrieve_context",
            "stage_progress_pct": 12,
            "pair_id": pair_id,
            "message": "正在检索文档内容",
        },
    )
    entity_ctx_a, entity_ctx_b, business_ctx_a, business_ctx_b = (
        await asyncio.gather(
            self._retrieve_dimension_context(doc_a.id, "entity", spec=_entity_spec(info_a, doc_a)),
            self._retrieve_dimension_context(doc_b.id, "entity", spec=_entity_spec(info_b, doc_b)),
            self._retrieve_dimension_context(doc_a.id, "business"),
            self._retrieve_dimension_context(doc_b.id, "business"),
        )
    )

    logger.info(
        f"[Analyzer] v2检索完成: ({doc_a.id},{doc_b.id}), "
        f"entity_ctx_a={len(entity_ctx_a)}字, entity_ctx_b={len(entity_ctx_b)}字, "
        f"business_ctx_a={len(business_ctx_a)}字, business_ctx_b={len(business_ctx_b)}字, "
        f"similar_chunks={len(similar_chunks)}对"
    )

    # Phase 2: 三组 LLM 并行分析 + D8 算法
    self._heartbeat(
        "S3",
        "多维度并行分析中",
        {
            "substage": "analyze_dimensions",
            "stage_progress_pct": 55,
            "pair_id": pair_id,
            "message": "多维度并行分析中",
        },
    )

    d8_result: dict = {}
    _inject_d8_from_metadata(d8_result, info_a, info_b)

    cov_pct = f"{coverage_ratio * 100:.0f}"
    span_val = str(max_continuous_span)

    # ── 步骤6: 文档子类型感知 -- 商务标跳过 D3/D4 ──
    subtype_a = _infer_doc_subtype(info_a, doc_a.filename)
    subtype_b = _infer_doc_subtype(info_b, doc_b.filename)
    both_commercial = subtype_a == "commercial" and subtype_b == "commercial"
    skipped_dims: list[str] = []

    if both_commercial:
        # P1-6: 商务标常含报价（投标总价/分项价/保证金等）, 不应整体跳过 D4。
        # 仅跳过 D3/D6/D7 三个偏技术方案/文档格式的维度, D4/D5 由 business 组
        # 正常跑出, 由 LLM 自行判断"商务标内无报价"时空 findings 即可。
        logger.info(
            f"[EvidenceGate] 双方均为商务标 "
            f"({doc_a.filename}={subtype_a}, {doc_b.filename}={subtype_b}), "
            f"跳过 D3/D6/D7 维度（D4 报价仍正常分析）"
        )
        skipped_dims = ["d3_text", "d6_format", "d7_abnormal"]

    dim_tasks = [
        self._analyze_entity_dims(
            doc_a, doc_b, info_a, info_b,
            entity_ctx_a, entity_ctx_b,
            similarity_score, tender_format,
            coverage_pct=cov_pct, max_continuous_span=span_val,
        ),
    ]
    if not both_commercial:
        dim_tasks.append(
            self._analyze_text_dims(
                doc_a, doc_b, similar_chunks,
                similarity_score, tender_format,
                industry_profile=industry_profile,
                coverage_pct=cov_pct, max_continuous_span=span_val,
            ),
        )
        dim_tasks.append(
            self._analyze_business_dims(
                doc_a, doc_b, info_a, info_b,
                business_ctx_a, business_ctx_b,
                similarity_score, tender_format,
                coverage_pct=cov_pct, max_continuous_span=span_val,
                industry_profile=industry_profile,
            ),
        )
    else:
        dim_tasks.append(
            self._analyze_business_dims(
                doc_a, doc_b, info_a, info_b,
                business_ctx_a, business_ctx_b,
                similarity_score, tender_format,
                coverage_pct=cov_pct, max_continuous_span=span_val,
                industry_profile=industry_profile,
            ),
        )

    self._dim_retry.clear()

    group_results = await asyncio.gather(
        *dim_tasks,
        return_exceptions=True,
    )

    if self._dim_retry.has_failures:
        retry_stats = await self._dim_retry.retry_all_concurrent(max_concurrency=3)
        for detail in retry_stats.details:
            if detail["success"] and isinstance(detail["result"], dict) and detail["result"]:
                group_results.append(detail["result"])

    # Phase 3: 合并结果
    compare_result = _merge_group_results(list(group_results), d8_result)

    scored_dims = [
        k for k in _DIM_KEYS
        if isinstance(compare_result.get(k), dict) and (compare_result[k].get("score") or 0) > 0
    ]
    logger.info(
        f"[Analyzer] v2合并完成: ({doc_a.id},{doc_b.id}), "
        f"有效维度={scored_dims or '(无)'}"
    )

    # Phase 4: 零分维度深搜补救
    compare_result, any_recovered = await self._deep_search_empty_dims(
        compare_result, doc_a, doc_b,
        similarity_score, tender_format,
    )

    mode = "grouped_parallel"
    if any_recovered:
        mode = "grouped_parallel_recovered"
        self._heartbeat(
            "S3",
            "正在补充分析",
            {
                "substage": "deep_search",
                "stage_progress_pct": 85,
                "pair_id": pair_id,
                "message": "正在补充分析",
            },
        )

    # Phase 5: 证据闸门 + 跨维度证据共享 + 一致性检查
    self._heartbeat(
        "S3",
        "整理对比结果",
        {
            "substage": "finalize_pair",
            "stage_progress_pct": 95,
            "pair_id": pair_id,
            "message": "正在整理对比结果",
        },
    )

    # ── 证据闸门 ──
    # evidence 构造与 pipeline 执行同 v1 路径，见 _run_gate_pipeline
    await _run_gate_pipeline(
        self, compare_result, info_a, info_b,
        doc_a.id, doc_b.id, tender_format,
    )

    if skipped_dims:
        compare_result["skipped_dims"] = skipped_dims
        compare_result["skipped_dims_reason"] = (
            f"双方文档均为商务标(subtype: {subtype_a}/{subtype_b}), "
            f"不含技术方案和报价信息, 跳过无效维度以避免幻觉"
        )

    # 跨维提示 (D1->D2 / D3->D7) 已经由 CrossDimensionHintGate 在 post_pipeline
    # 阶段处理（见 app.core.ai.gates.collusion.cross_dimension_hint）。
    # 早期遗留的 ``_cross_dimension_evidence_share`` 调用与 Gate 同源，重复执行会
    # 在同一对 pair 上双写 advisory finding，故此处保留 Gate 单一入口。
    compare_result = _consistency_check(compare_result, similarity_score)

    # B5.10 / B5.12: CoVe + Judge 对 HIGH 风险对子做复核 (v2 路径)
    _pair_label = f"{doc_a.id}-{doc_b.id}" if (doc_a and doc_b) else "v2"
    await self._maybe_run_cove_judge_collusion(compare_result, _pair_label, mode)

    return compare_result, mode
