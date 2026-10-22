"""串标分析器 S4 综合研判方法

Task 8 Step 9：从 CollusionAnalyzer 主类迁出 S4 综合研判 4 个方法。
主类保留同名 thin forwarder 兼容外部调用。

迁出范围：
- run_synthesis（109 行 pair 摘要 LLM 综合）
- run_overall_synthesis（176 行项目级整体研判）
- save_synthesis_snapshot（34 行综合快照落库）
- save_project_summary（13 行项目摘要持久化）

子模块函数签名规范（方案 B）：函数名去前导下划线，self 名字保留。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select

from app.core.ai.prompt_registry import resolve_user_template
from app.core.collusion.analyzer_parts.constants import (
    _shrink_text_blocks,
    _trim_pair_results_for_summary,
)
from app.core.collusion.analyzer_parts.synthesis_helpers import (
    _build_coverage_stats,
    _build_global_findings_context,
    _build_pair_summaries,
    _build_profile_quality_flags,
    _simple_synthesis,
)

# 补全 import 修复
from app.core.collusion.feature_flags import use_global_findings_enrich
from app.core.collusion.global_features import GlobalFeatureResult
from app.core.collusion.graph_cluster import CollusionCluster
from app.core.collusion.narrative_builder import (
    build_factual_explanation,
    build_factual_explanation_items,
)
from app.core.collusion.prompts import (
    COLLUSION_OVERALL_SYNTHESIS_PROMPT,
    COLLUSION_PROJECT_SUMMARY_PROMPT,
)
from app.core.config import (
    get_collusion_synthesis_timeout,
)
from app.core.database import async_session_factory
from app.models.collusion_result import CollusionResult
from app.models.document import Document
from app.models.project import Project
from app.schemas.collusion import CollusionProjectSummary, LLMSynthesisResult


def _inject_factual_explanation(
    synthesis: dict,
    *,
    coverage_stats: dict,
    pair_results: list[dict],
    industry_profile: Optional[dict],
    has_tender_doc: bool,
) -> dict:
    """在 synthesis 出口统一注入 factual_explanation 与 industry_profile_status。

    与 LLM 研判段并列两路真源：
    - synthesis["summary"]: LLM 研判推断段（已通过 Prompt 收敛为纯研判）
    - synthesis["factual_explanation"]: 后端按规则拼装的事实段
    """
    if not isinstance(synthesis, dict):
        return synthesis
    synthesis["factual_explanation"] = build_factual_explanation(
        coverage_stats=coverage_stats,
        pair_results=pair_results,
        industry_profile=industry_profile,
        has_tender_doc=has_tender_doc,
    )
    synthesis["factual_explanation_items"] = build_factual_explanation_items(
        coverage_stats=coverage_stats,
        pair_results=pair_results,
        industry_profile=industry_profile,
        has_tender_doc=has_tender_doc,
    )
    if industry_profile and isinstance(industry_profile, dict):
        synthesis["industry_profile_status"] = (
            str(industry_profile.get("status") or "").strip().lower() or None
        )
    elif industry_profile is None:
        synthesis["industry_profile_status"] = "llm_failed"
    return synthesis


async def run_synthesis(
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
    """综合研判（所有对比完成后1次LLM调用）"""
    coverage_stats = _build_coverage_stats(
        pair_results=pair_results,
        total_pairs=total_pairs,
        llm_pair_count=llm_pair_count,
        skipped_count=skipped_count,
    )
    profile_quality_flags = _build_profile_quality_flags(industry_profile)

    # P0-2: 无群组分支也注入全局 findings 原始证据（灰度控制）
    if global_features is not None and use_global_findings_enrich(self._project_id):
        global_findings_context = _build_global_findings_context(
            getattr(global_features, "global_findings", None),
            max_items=10,
        )
    else:
        global_findings_context = "（未启用全局发现增强；结论仅依据文件对/群组信息。）"

    initial_payload = {
        "pair_results": list(pair_results),
        "groups_summary": "(无嫌疑群组)",
        "coverage_summary": json.dumps(coverage_stats, ensure_ascii=False, indent=2),
        "profile_quality_summary": json.dumps({
            "has_tender_doc": has_tender_doc,
            "profile_quality_flags": profile_quality_flags,
            "industry_profile_confidence": (
                str(industry_profile.get("confidence", "unknown")).lower()
                if industry_profile else "unknown"
            ),
        }, ensure_ascii=False, indent=2),
        "global_findings_context": global_findings_context,
    }

    def _render_user_message(payload: dict[str, Any]) -> str:
        return resolve_user_template(
            "collusion.project_summary", COLLUSION_PROJECT_SUMMARY_PROMPT
        ).format(
            pair_judgements=_build_pair_summaries(payload["pair_results"]),
            groups_summary=payload["groups_summary"],
            coverage_summary=payload["coverage_summary"],
            profile_quality_summary=payload["profile_quality_summary"],
            global_findings_context=payload["global_findings_context"],
        )

    def _reduce_payload(
        payload: dict[str, Any],
        retry_round: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reduced = dict(payload)
        removed_segments: list[str] = []
        if len(reduced["pair_results"]) > 3:
            reduced["pair_results"] = _trim_pair_results_for_summary(
                reduced["pair_results"],
                min_items=3,
            )
            removed_segments.append("pair_summaries")
        elif reduced.get("groups_summary") and reduced["groups_summary"] != "(无嫌疑群组)":
            reduced["groups_summary"] = _shrink_text_blocks(str(reduced["groups_summary"]), min_blocks=1)
            removed_segments.append("groups_summary")
        elif reduced.get("coverage_summary"):
            reduced["coverage_summary"] = _shrink_text_blocks(str(reduced["coverage_summary"]), min_blocks=1)
            removed_segments.append("coverage_summary")
        else:
            reduced["global_findings_context"] = _shrink_text_blocks(
                str(reduced.get("global_findings_context") or ""), min_blocks=1,
            )
            removed_segments.append("global_findings_context")
        return reduced, {
            "removed_segments": removed_segments,
            "kept_segments": ["highest_risk_pairs", "summary_prompt_contract"],
            "retry_attempt": retry_round,
        }

    try:
        synthesis = await self._call_llm_with_input_guard(
            operation_name=f"串标综合研判(project={self._project_id})",
            initial_payload=initial_payload,
            render_user_message=_render_user_message,
            reduce_payload_semantically=_reduce_payload,
            response_model=LLMSynthesisResult,
            timeout=get_collusion_synthesis_timeout(),
        )
        return _inject_factual_explanation(
            synthesis,
            coverage_stats=coverage_stats,
            pair_results=pair_results,
            industry_profile=industry_profile,
            has_tender_doc=has_tender_doc,
        )
    except Exception as e:
        logger.warning(f"[Analyzer] 综合研判失败: {e}, 使用简化结论")
        self._synthesis_retry.record(
            key=f"synthesis_{self._project_id}",
            retry_coro_factory=lambda: self._call_llm_with_input_guard(
                operation_name=f"串标综合研判(project={self._project_id})",
                initial_payload=initial_payload,
                render_user_message=_render_user_message,
                reduce_payload_semantically=_reduce_payload,
                response_model=LLMSynthesisResult,
                timeout=get_collusion_synthesis_timeout(),
            ),
            fallback_applied="simple_synthesis",
        )
        return _inject_factual_explanation(
            _simple_synthesis(pair_results),
            coverage_stats=coverage_stats,
            pair_results=pair_results,
            industry_profile=industry_profile,
            has_tender_doc=has_tender_doc,
        )


async def save_synthesis_snapshot(self, pair_results: list[dict], synthesis: dict) -> None:
    """将综合研判结果快照保存到一条 pair 记录，供结果接口与报告读取"""
    if not pair_results:
        return

    candidates = sorted(
        [p for p in pair_results if isinstance(p, dict)],
        key=lambda p: str(p.get("pair_id", "")),
    )
    if not candidates:
        return
    selected = candidates[0]
    pair_ref = selected.get("pair_ref") or {}
    doc_a_id = pair_ref.get("doc_a_id")
    doc_b_id = pair_ref.get("doc_b_id")
    if not doc_a_id or not doc_b_id:
        return

    async with async_session_factory() as save_db:
        rows = await save_db.execute(
            select(CollusionResult).where(
                CollusionResult.project_id == self._project_id,
                CollusionResult.doc_a_id == doc_a_id,
                CollusionResult.doc_b_id == doc_b_id,
            )
        )
        record = rows.scalar_one_or_none()
        if not record:
            return
        base = record.llm_analysis or {}
        base["_synthesis"] = synthesis or {}
        record.llm_analysis = base
        await save_db.commit()


async def save_project_summary(self, summary_payload: dict) -> None:
    """保存项目级串标综合结论真源"""
    validated = CollusionProjectSummary.model_validate(summary_payload)
    async with async_session_factory() as save_db:
        rows = await save_db.execute(
            select(Project).where(Project.id == self._project_id)
        )
        project = rows.scalar_one_or_none()
        if not project:
            return
        project.collusion_summary = validated.model_dump(mode="json")
        await save_db.commit()


async def run_overall_synthesis(
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
    """全局综合研判"""
    import json

    if not clusters:
        return await self._run_synthesis(
            bid_docs=bid_docs,
            pair_results=pair_results,
            has_tender_doc=has_tender_doc,
            total_pairs=total_pairs,
            llm_pair_count=llm_pair_count,
            skipped_count=skipped_count,
            industry_profile=industry_profile,
            global_features=global_features,
        )

    groups_summary_parts = []
    for idx, cluster in enumerate(clusters):
        names = ", ".join(cluster.member_names[:5])
        groups_summary_parts.append(
            f"群组{idx + 1}({cluster.risk_level}): {names}"
        )
    groups_summary = "\n".join(groups_summary_parts)

    ungrouped = []
    grouped_doc_ids: set[int] = set()
    for c in clusters:
        grouped_doc_ids.update(c.member_doc_ids)
    for res in pair_results:
        if not isinstance(res, dict):
            continue
        risk = res.get("overall_risk", "none")
        if risk in ("none", "inconclusive"):
            continue
        pair_ref = res.get("pair_ref") or {}
        a_id = pair_ref.get("doc_a_id")
        b_id = pair_ref.get("doc_b_id")
        if a_id and b_id and a_id not in grouped_doc_ids and b_id not in grouped_doc_ids:
            a_name = pair_ref.get("doc_a_name", f"文件{a_id}")
            b_name = pair_ref.get("doc_b_name", f"文件{b_id}")
            ungrouped.append(f"{a_name} vs {b_name}: {risk}")

    ungrouped_text = "\n".join(ungrouped) if ungrouped else "(无)"

    coverage_stats = _build_coverage_stats(
        pair_results=pair_results,
        total_pairs=total_pairs,
        llm_pair_count=llm_pair_count,
        skipped_count=skipped_count,
    )
    profile_quality_flags = _build_profile_quality_flags(industry_profile)
    gf_stats = {
        "personnel_overlap_groups": len(global_features.personnel_overlaps),
        "company_edges": len(global_features.company_edges),
        "pricing_patterns": len(global_features.pricing_patterns),
        "metadata_clusters": len(global_features.metadata_clusters),
    }
    # P0-2: 灰度开启时注入 global_findings_context；关闭时传兜底空串，
    # 保证 Prompt 模板 {global_findings_context} 占位符在 .format() 时不会 KeyError
    if use_global_findings_enrich(self._project_id):
        global_findings_context = _build_global_findings_context(
            getattr(global_features, "global_findings", None),
            max_items=10,
        )
    else:
        global_findings_context = "（未启用全局发现增强；结论仅依据文件对/群组信息。）"

    initial_payload = {
        "pair_results": list(pair_results),
        "groups_summary": json.dumps({
            "group_count": len(clusters),
            "groups_summary": groups_summary,
            "ungrouped_pairs": ungrouped_text,
            "global_stats": gf_stats,
        }, ensure_ascii=False, indent=2),
        "coverage_summary": json.dumps(coverage_stats, ensure_ascii=False, indent=2),
        "profile_quality_summary": json.dumps({
            "has_tender_doc": has_tender_doc,
            "profile_quality_flags": profile_quality_flags,
            "industry_profile_confidence": (
                str(industry_profile.get("confidence", "unknown")).lower()
                if industry_profile else "unknown"
            ),
        }, ensure_ascii=False, indent=2),
        "global_findings_context": global_findings_context,
    }

    def _render_overall_user_message(payload: dict[str, Any]) -> str:
        # P0-1: 有群组路径专用 prompt（COLLUSION_OVERALL_SYNTHESIS_PROMPT）,
        # 明示 {groups_summary} 为 JSON 字符串结构 + 字段说明; 无群组路径
        # 由 _run_synthesis -> COLLUSION_PROJECT_SUMMARY_PROMPT 处理,
        # 两个 prompt 不再混用。
        return resolve_user_template(
            "collusion.overall_synthesis", COLLUSION_OVERALL_SYNTHESIS_PROMPT
        ).format(
            pair_judgements=_build_pair_summaries(payload["pair_results"]),
            groups_summary=payload["groups_summary"],
            coverage_summary=payload["coverage_summary"],
            profile_quality_summary=payload["profile_quality_summary"],
            global_findings_context=payload["global_findings_context"],
        )

    def _reduce_overall_payload(
        payload: dict[str, Any],
        retry_round: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reduced = dict(payload)
        removed_segments: list[str] = []
        # P0-2: 收缩顺序：pair_results → groups_summary → coverage_summary →
        # global_findings_context（最后收缩，优先保留全局发现）
        if len(reduced["pair_results"]) > 3:
            reduced["pair_results"] = _trim_pair_results_for_summary(
                reduced["pair_results"],
                min_items=3,
            )
            removed_segments.append("pair_summaries")
        elif reduced.get("groups_summary"):
            reduced["groups_summary"] = _shrink_text_blocks(str(reduced["groups_summary"]), min_blocks=1)
            removed_segments.append("groups_summary")
        elif reduced.get("coverage_summary"):
            reduced["coverage_summary"] = _shrink_text_blocks(str(reduced["coverage_summary"]), min_blocks=1)
            removed_segments.append("coverage_summary")
        else:
            # 最后才收缩 global_findings_context
            reduced["global_findings_context"] = _shrink_text_blocks(
                str(reduced.get("global_findings_context") or ""), min_blocks=1,
            )
            removed_segments.append("global_findings_context")
        return reduced, {
            "removed_segments": removed_segments,
            "kept_segments": ["highest_risk_pairs", "group_summary_contract"],
            "retry_attempt": retry_round,
        }

    try:
        # P1-2: 全局综合与 _run_synthesis 保持一致使用 synthesis_timeout，
        # 避免群组 timeout 与项目级 timeout 口径混淆（历史 bug）
        # P0-1: max_tokens 与 _run_synthesis 默认值（2000）对齐, 避免多群组场景
        # 下 suspected_groups + collusion_network + investigation_suggestions
        # 累加超出 1000 token 后被截断。
        synthesis = await self._call_llm_with_input_guard(
            operation_name=f"串标全局综合(project={self._project_id})",
            initial_payload=initial_payload,
            render_user_message=_render_overall_user_message,
            reduce_payload_semantically=_reduce_overall_payload,
            response_model=LLMSynthesisResult,
            timeout=get_collusion_synthesis_timeout(),
            max_tokens=2000,
        )
        return _inject_factual_explanation(
            synthesis,
            coverage_stats=coverage_stats,
            pair_results=pair_results,
            industry_profile=industry_profile,
            has_tender_doc=has_tender_doc,
        )
    except Exception as e:
        logger.warning(f"[Analyzer] 全局综合研判失败: {e}")
        self._synthesis_retry.record(
            key=f"overall_synthesis_{self._project_id}",
            retry_coro_factory=lambda: self._call_llm_with_input_guard(
                operation_name=f"串标全局综合(project={self._project_id})",
                initial_payload=initial_payload,
                render_user_message=_render_overall_user_message,
                reduce_payload_semantically=_reduce_overall_payload,
                response_model=LLMSynthesisResult,
                timeout=get_collusion_synthesis_timeout(),
                max_tokens=2000,
            ),
            fallback_applied="simple_synthesis",
        )
        return _inject_factual_explanation(
            _simple_synthesis(pair_results),
            coverage_stats=coverage_stats,
            pair_results=pair_results,
            industry_profile=industry_profile,
            has_tender_doc=has_tender_doc,
        )
