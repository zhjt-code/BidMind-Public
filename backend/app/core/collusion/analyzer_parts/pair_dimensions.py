"""串标分析器 S3 pair 维度 LLM 调用方法

Task 8 Step 7：从 CollusionAnalyzer 主类迁出 S3 维度 LLM 调用 4 个方法到本
子模块。主类保留同名 thin forwarder 兼容外部调用。

迁出范围：
- analyze_entity_dims（111 行 D1/D2 实体维度分析）
- analyze_text_dims（160 行 D3 文本模式分析）
- analyze_business_dims（122 行 D4/D5 商务维度分析）
- deep_search_empty_dims（121 行空维度二次检索）

子模块函数签名规范（方案 B）：见 pair_compare.py 头部。
"""

from __future__ import annotations


# 补全 import 修复
from app.core.ai.prompt_registry import resolve_user_template
from app.core.collusion.analyzer_parts.constants import _DIM_MAP
from app.core.collusion.analyzer_parts.extraction_validation import _format_extraction
from app.core.collusion.prompts import build_similar_chunks_text
from app.core.parser.image_extractor import is_image_related_chunk_content
from app.models.document import Document
from typing import Optional

from typing import Any

from loguru import logger

from app.core.config import get_collusion_compare_timeout
from app.core.collusion.analyzer_parts.constants import (
    _shrink_list_semantically,
    _shrink_text_blocks,
)
from app.core.collusion.knowledge_base import (
    build_dynamic_normal_similarity_hint,
    build_normal_similarity_hint,
)
from app.core.collusion.prompts import (
    COLLUSION_BUSINESS_PROMPT,
    COLLUSION_ENTITY_PROMPT,
    COLLUSION_SINGLE_DIM_PROMPT,
    COLLUSION_TEXT_PATTERN_PROMPT,
    DIMENSION_INSTRUCTIONS,
    build_profile_business_context,
    build_profile_domain_context,
    build_tender_context,
)
from app.schemas.collusion import (
    LLMBusinessResult,
    LLMEntityResult,
    LLMSingleDimResult,
    LLMTextPatternResult,
)


async def analyze_entity_dims(
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
    """分组分析 -- A组: D1人员重叠 + D2企业关联"""
    initial_payload = {
        "structured_a": _format_extraction(info_a),
        "structured_b": _format_extraction(info_b),
        "context_a": entity_ctx_a or "(无定向检索结果)",
        "context_b": entity_ctx_b or "(无定向检索结果)",
        "tender_context": build_tender_context(tender_format),
    }

    def _render_user_message(payload: dict[str, Any]) -> str:
        return resolve_user_template(
            "collusion.pair_compare_entity", COLLUSION_ENTITY_PROMPT
        ).format(
            similarity_pct=f"{similarity_score * 100:.0f}",
            coverage_pct=coverage_pct,
            max_continuous_span=max_continuous_span,
            doc_a_id=doc_a.id,
            doc_a_name=doc_a.filename,
            doc_b_id=doc_b.id,
            doc_b_name=doc_b.filename,
            structured_a=payload["structured_a"],
            context_a=payload["context_a"],
            structured_b=payload["structured_b"],
            context_b=payload["context_b"],
            tender_context=payload["tender_context"],
        )

    def _reduce_payload(
        payload: dict[str, Any],
        retry_round: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reduced = dict(payload)
        removed_segments: list[str] = []

        if reduced.get("context_a") and reduced["context_a"] != "(无定向检索结果)":
            reduced["context_a"] = _shrink_text_blocks(str(reduced["context_a"]), min_blocks=1)
            removed_segments.append("context_a")
        elif reduced.get("context_b") and reduced["context_b"] != "(无定向检索结果)":
            reduced["context_b"] = _shrink_text_blocks(str(reduced["context_b"]), min_blocks=1)
            removed_segments.append("context_b")
        elif reduced.get("structured_a"):
            reduced["structured_a"] = _shrink_text_blocks(str(reduced["structured_a"]), min_blocks=1)
            removed_segments.append("structured_a")
        elif reduced.get("structured_b"):
            reduced["structured_b"] = _shrink_text_blocks(str(reduced["structured_b"]), min_blocks=1)
            removed_segments.append("structured_b")
        else:
            reduced["tender_context"] = _shrink_text_blocks(str(reduced["tender_context"]), min_blocks=1)
            removed_segments.append("tender_context")

        return reduced, {
            "removed_segments": removed_segments,
            "kept_segments": ["entity_prompt_contract"],
            "retry_attempt": retry_round,
        }

    def _describe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "context_a_chars": len(payload.get("context_a", "") or ""),
            "context_b_chars": len(payload.get("context_b", "") or ""),
            "structured_a_chars": len(payload.get("structured_a", "") or ""),
            "structured_b_chars": len(payload.get("structured_b", "") or ""),
        }

    try:
        result = await self._call_llm_with_input_guard(
            operation_name=f"串标entity组({doc_a.id},{doc_b.id})",
            initial_payload=initial_payload,
            render_user_message=_render_user_message,
            reduce_payload_semantically=_reduce_payload,
            response_model=LLMEntityResult,
            timeout=get_collusion_compare_timeout(),
            max_tokens=2000,
            describe_payload=_describe_payload,
        )
        d1 = result.get("d1_personnel")
        d2 = result.get("d2_company")
        logger.info(
            f"[Analyzer] entity组完成: ({doc_a.id},{doc_b.id}), "
            f"d1={d1.get('score') if isinstance(d1, dict) else d1}, "
            f"d2={d2.get('score') if isinstance(d2, dict) else d2}"
        )
        return result
    except Exception as e:
        logger.warning(
            f"[Analyzer] entity组分析失败: ({doc_a.id}, {doc_b.id}), err={e}"
        )
        self._dim_retry.record(
            key=f"entity_{doc_a.id}_{doc_b.id}",
            retry_coro_factory=lambda: self._analyze_entity_dims(
                doc_a, doc_b, info_a, info_b,
                entity_ctx_a, entity_ctx_b,
                similarity_score, tender_format,
            ),
            fallback_applied="empty_dict",
        )
        return {}


async def analyze_text_dims(
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
    """分组分析 -- B组: D3文本雷同 + D6文档格式 + D7异常一致"""
    filtered_chunks = [
        c for c in similar_chunks
        if not (
            is_image_related_chunk_content(c.get("text_a", ""))
            or is_image_related_chunk_content(c.get("text_b", ""))
        )
    ]
    if len(filtered_chunks) < len(similar_chunks):
        logger.info(
            f"[Analyzer] text组VL过滤: {len(similar_chunks)}->{len(filtered_chunks)}对"
        )

    tender_ctx = build_tender_context(tender_format)
    profile_ctx = build_profile_domain_context(industry_profile)
    kb_ctx = build_normal_similarity_hint(filtered_chunks)
    # self.db 系 typo (应为 self._db), 一直抛 AttributeError 致
    # dynamic_kb_ctx 从未生效。修复时同步改为短 session, 避免连接池竞争;
    # 包 try/except 保证即使后续动态知识库异常也不阻塞 text 维度分析。
    dynamic_kb_ctx = ""
    try:
        allowed_kb_ids = await self._get_kb_usage_allowed_ids()
        if allowed_kb_ids:
            from app.core.database import async_session_factory as _kb_factory
            async with _kb_factory() as _kb_session:
                dynamic_kb_ctx = await build_dynamic_normal_similarity_hint(
                    _kb_session,
                    filtered_chunks,
                    owner_id=await self._get_project_owner_id(),
                    allowed_document_ids=allowed_kb_ids,
                )
    except Exception as _kb_err:
        logger.debug(f"[Analyzer] text组动态KB上下文构建失败(忽略): {_kb_err}")
    if dynamic_kb_ctx:
        kb_ctx = "\n\n".join(part for part in [kb_ctx, dynamic_kb_ctx] if part)
        logger.info(
            "[知识库辅助] flow=collusion_check project={} dim_group=text "
            "policy=normal_similarity_exemption_only",
            self._project_id,
        )
    initial_payload = {
        "similar_chunks": list(filtered_chunks),
        "tender_context": tender_ctx,
        "profile_context": profile_ctx,
        "kb_context": kb_ctx,
    }

    def _render_user_message(payload: dict[str, Any]) -> str:
        # P1-2: 不再把 tender + profile + kb 拼接到同一占位符,
        # TEXT prompt 模板已新增独立 {industry_profile_context} / {kb_context}
        # 占位符, 与 BUSINESS prompt 设计风格统一,避免 LLM 看到的标签错位。
        return resolve_user_template(
            "collusion.pair_compare_text", COLLUSION_TEXT_PATTERN_PROMPT
        ).format(
            similarity_pct=f"{similarity_score * 100:.0f}",
            coverage_pct=coverage_pct,
            max_continuous_span=max_continuous_span,
            doc_a_name=doc_a.filename,
            doc_b_name=doc_b.filename,
            doc_a_id=doc_a.id,
            doc_b_id=doc_b.id,
            similar_chunks=build_similar_chunks_text(
                payload.get("similar_chunks", []),
                doc_a_id=doc_a.id,
                doc_b_id=doc_b.id,
                doc_a_name=doc_a.filename,
                doc_b_name=doc_b.filename,
            ),
            tender_context=payload.get("tender_context", "") or "",
            industry_profile_context=payload.get("profile_context", "") or "",
            kb_context=payload.get("kb_context", "") or "",
        )

    def _reduce_payload(
        payload: dict[str, Any],
        retry_round: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reduced = dict(payload)
        removed_segments: list[str] = []

        similar_chunks_payload = list(reduced.get("similar_chunks", []))
        if len(similar_chunks_payload) > 2:
            reduced["similar_chunks"] = _shrink_list_semantically(similar_chunks_payload, min_items=2)
            removed_segments.append("similar_chunks")
        elif reduced.get("kb_context"):
            reduced["kb_context"] = _shrink_text_blocks(str(reduced["kb_context"]), min_blocks=1)
            removed_segments.append("kb_context")
        elif reduced.get("profile_context"):
            reduced["profile_context"] = _shrink_text_blocks(str(reduced["profile_context"]), min_blocks=1)
            removed_segments.append("profile_context")
        else:
            reduced["tender_context"] = _shrink_text_blocks(str(reduced.get("tender_context", "")), min_blocks=1)
            removed_segments.append("tender_context")

        return reduced, {
            "removed_segments": removed_segments,
            "kept_segments": ["highest_similarity_chunks", "prompt_contract"],
            "retry_attempt": retry_round,
        }

    def _describe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "chunk_pairs": len(payload.get("similar_chunks", [])),
            "tender_chars": len(payload.get("tender_context", "") or ""),
            "profile_chars": len(payload.get("profile_context", "") or ""),
            "kb_chars": len(payload.get("kb_context", "") or ""),
        }

    try:
        result = await self._call_llm_with_input_guard(
            operation_name=f"串标text组({doc_a.id},{doc_b.id})",
            initial_payload=initial_payload,
            render_user_message=_render_user_message,
            reduce_payload_semantically=_reduce_payload,
            response_model=LLMTextPatternResult,
            timeout=get_collusion_compare_timeout(),
            max_tokens=3000,
            describe_payload=_describe_payload,
        )
        d3 = result.get("d3_text")
        d6 = result.get("d6_format")
        d7 = result.get("d7_abnormal")
        logger.info(
            f"[Analyzer] text组完成: ({doc_a.id},{doc_b.id}), "
            f"d3={d3.get('score') if isinstance(d3, dict) else d3}, "
            f"d6={d6.get('score') if isinstance(d6, dict) else d6}, "
            f"d7={d7.get('score') if isinstance(d7, dict) else d7}"
        )
        return result
    except Exception as e:
        logger.warning(
            f"[Analyzer] text组分析失败: ({doc_a.id}, {doc_b.id}), err={e}"
        )
        self._dim_retry.record(
            key=f"text_{doc_a.id}_{doc_b.id}",
            retry_coro_factory=lambda: self._analyze_text_dims(
                doc_a, doc_b, similar_chunks,
                similarity_score, tender_format,
                industry_profile=industry_profile,
                coverage_pct=coverage_pct,
                max_continuous_span=max_continuous_span,
            ),
            fallback_applied="empty_dict",
        )
        return {}


async def analyze_business_dims(
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
    """分组分析 -- C组: D4报价异常 + D5资质业绩"""
    initial_payload = {
        "structured_a": _format_extraction(info_a),
        "structured_b": _format_extraction(info_b),
        "context_a": business_ctx_a or "(无定向检索结果)",
        "context_b": business_ctx_b or "(无定向检索结果)",
        "tender_context": build_tender_context(tender_format),
        "industry_profile_context": build_profile_business_context(industry_profile),
    }

    def _render_user_message(payload: dict[str, Any]) -> str:
        return resolve_user_template(
            "collusion.pair_compare_business", COLLUSION_BUSINESS_PROMPT
        ).format(
            similarity_pct=f"{similarity_score * 100:.0f}",
            coverage_pct=coverage_pct,
            max_continuous_span=max_continuous_span,
            doc_a_id=doc_a.id,
            doc_a_name=doc_a.filename,
            doc_b_id=doc_b.id,
            doc_b_name=doc_b.filename,
            structured_a=payload["structured_a"],
            context_a=payload["context_a"],
            structured_b=payload["structured_b"],
            context_b=payload["context_b"],
            tender_context=payload["tender_context"],
            industry_profile_context=payload["industry_profile_context"],
        )

    def _reduce_payload(
        payload: dict[str, Any],
        retry_round: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reduced = dict(payload)
        removed_segments: list[str] = []

        if reduced.get("industry_profile_context"):
            reduced["industry_profile_context"] = _shrink_text_blocks(
                str(reduced["industry_profile_context"]),
                min_blocks=1,
            )
            removed_segments.append("industry_profile_context")
        elif reduced.get("context_a") and reduced["context_a"] != "(无定向检索结果)":
            reduced["context_a"] = _shrink_text_blocks(str(reduced["context_a"]), min_blocks=1)
            removed_segments.append("context_a")
        elif reduced.get("context_b") and reduced["context_b"] != "(无定向检索结果)":
            reduced["context_b"] = _shrink_text_blocks(str(reduced["context_b"]), min_blocks=1)
            removed_segments.append("context_b")
        elif reduced.get("structured_a"):
            reduced["structured_a"] = _shrink_text_blocks(str(reduced["structured_a"]), min_blocks=1)
            removed_segments.append("structured_a")
        elif reduced.get("structured_b"):
            reduced["structured_b"] = _shrink_text_blocks(str(reduced["structured_b"]), min_blocks=1)
            removed_segments.append("structured_b")
        else:
            reduced["tender_context"] = _shrink_text_blocks(str(reduced["tender_context"]), min_blocks=1)
            removed_segments.append("tender_context")

        return reduced, {
            "removed_segments": removed_segments,
            "kept_segments": ["business_prompt_contract"],
            "retry_attempt": retry_round,
        }

    def _describe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "context_a_chars": len(payload.get("context_a", "") or ""),
            "context_b_chars": len(payload.get("context_b", "") or ""),
            "profile_chars": len(payload.get("industry_profile_context", "") or ""),
        }

    try:
        result = await self._call_llm_with_input_guard(
            operation_name=f"串标business组({doc_a.id},{doc_b.id})",
            initial_payload=initial_payload,
            render_user_message=_render_user_message,
            reduce_payload_semantically=_reduce_payload,
            response_model=LLMBusinessResult,
            timeout=get_collusion_compare_timeout(),
            max_tokens=2000,
            describe_payload=_describe_payload,
        )
        d4 = result.get("d4_price")
        d5 = result.get("d5_qualification")
        logger.info(
            f"[Analyzer] business组完成: ({doc_a.id},{doc_b.id}), "
            f"d4={d4.get('score') if isinstance(d4, dict) else d4}, "
            f"d5={d5.get('score') if isinstance(d5, dict) else d5}"
        )
        return result
    except Exception as e:
        logger.warning(
            f"[Analyzer] business组分析失败: ({doc_a.id}, {doc_b.id}), err={e}"
        )
        self._dim_retry.record(
            key=f"business_{doc_a.id}_{doc_b.id}",
            retry_coro_factory=lambda: self._analyze_business_dims(
                doc_a, doc_b, info_a, info_b,
                business_ctx_a, business_ctx_b,
                similarity_score, tender_format,
                coverage_pct=coverage_pct,
                max_continuous_span=max_continuous_span,
                industry_profile=industry_profile,
            ),
            fallback_applied="empty_dict",
        )
        return {}


async def deep_search_empty_dims(
    self,
    compare_result: dict,
    doc_a: Document,
    doc_b: Document,
    similarity_score: float,
    tender_format: Optional[dict],
) -> tuple[dict, bool]:
    """零分维度深搜补救 -- 返回 (updated_result, any_recovered)

    仅针对 entity/business 组的 D1/D2/D4/D5，当得分为 0 或缺失时
    用更宽松阈值重新检索并单维度 LLM 重试。
    """
    recoverable_dims = {
        "d1_personnel": "entity",
        "d2_company": "entity",
        "d4_price": "business",
        "d5_qualification": "business",
    }

    any_recovered = False
    half_timeout = get_collusion_compare_timeout() / 2

    for dim_key, dim_group in recoverable_dims.items():
        dim_data = compare_result.get(dim_key)
        dim_score = dim_data.get("score", 0) if isinstance(dim_data, dict) else 0
        if dim_data is not None and dim_score > 0:
            continue

        instruction = DIMENSION_INSTRUCTIONS.get(dim_key)
        if not instruction:
            continue

        _, dim_label = _DIM_MAP[dim_key]

        try:
            from app.core.retriever.evidence_pipeline.dimension_context import (
                DimensionRetrieveSpec,
            )

            spec = DimensionRetrieveSpec(
                channels=(
                    ("entity", "lexical") if dim_group == "entity"
                    else ("table", "lexical")
                )
            )
            ctx_a = await self._retrieve_dimension_context(
                doc_a.id, dim_group,
                min_similarity=0.3, top_k=12, max_chars=12000,
                spec=spec,
            )
            ctx_b = await self._retrieve_dimension_context(
                doc_b.id, dim_group,
                min_similarity=0.3, top_k=12, max_chars=12000,
                spec=spec,
            )

            if not ctx_a and not ctx_b:
                continue

            initial_payload = {
                "context_a": ctx_a or "(无检索结果)",
                "context_b": ctx_b or "(无检索结果)",
                "tender_context": build_tender_context(tender_format),
            }

            def _render_user_message(payload: dict[str, Any]) -> str:
                return resolve_user_template(
                    "collusion.single_dim_search", COLLUSION_SINGLE_DIM_PROMPT
                ).format(
                    similarity_pct=f"{similarity_score * 100:.0f}",
                    dim_label=dim_label,
                    dim_instruction=instruction,
                    doc_a_id=doc_a.id,
                    doc_a_name=doc_a.filename,
                    context_a=payload["context_a"],
                    doc_b_id=doc_b.id,
                    doc_b_name=doc_b.filename,
                    context_b=payload["context_b"],
                    tender_context=payload["tender_context"],
                )

            def _reduce_payload(
                payload: dict[str, Any],
                retry_round: int,
            ) -> tuple[dict[str, Any], dict[str, Any]]:
                reduced = dict(payload)
                removed_segments: list[str] = []
                if reduced.get("context_a") and reduced["context_a"] != "(无检索结果)":
                    reduced["context_a"] = _shrink_text_blocks(str(reduced["context_a"]), min_blocks=1)
                    removed_segments.append("context_a")
                elif reduced.get("context_b") and reduced["context_b"] != "(无检索结果)":
                    reduced["context_b"] = _shrink_text_blocks(str(reduced["context_b"]), min_blocks=1)
                    removed_segments.append("context_b")
                else:
                    reduced["tender_context"] = _shrink_text_blocks(str(reduced["tender_context"]), min_blocks=1)
                    removed_segments.append("tender_context")
                return reduced, {
                    "removed_segments": removed_segments,
                    "kept_segments": [dim_key],
                    "retry_attempt": retry_round,
                }

            result = await self._call_llm_with_input_guard(
                operation_name=f"串标单维补救{dim_key}({doc_a.id},{doc_b.id})",
                initial_payload=initial_payload,
                render_user_message=_render_user_message,
                reduce_payload_semantically=_reduce_payload,
                response_model=LLMSingleDimResult,
                timeout=half_timeout,
                max_tokens=800,
            )

            if result.get("score", 0) > 0:
                compare_result[dim_key] = result
                for f in result.get("findings") or []:
                    compare_result.setdefault("key_findings", []).append(f)
                any_recovered = True
                logger.info(
                    f"[Analyzer] 深搜补救成功: {dim_key}, "
                    f"score={result.get('score')}, "
                    f"pair=({doc_a.id},{doc_b.id})"
                )
        except Exception as e:
            logger.warning(
                f"[Analyzer] 深搜补救失败: {dim_key}, "
                f"pair=({doc_a.id},{doc_b.id}), err={e}"
            )

    return compare_result, any_recovered
