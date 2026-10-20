"""串标分析器综合研判域纯函数

Task 8 Step 5：从 analyzer.py 迁出综合研判相关 16 个纯函数 + 2 个常量到
analyzer_parts/synthesis_helpers.py。主 analyzer.py 顶部 re-export 保持外部
import 路径零变化。

收纳范围（按原文顺序）：
- 全局 finding 常量：_GLOBAL_FINDING_TYPE_PRIORITY / _GLOBAL_FINDING_TYPE_LABEL
- 上下文构造：_build_global_findings_context / _format_group_evidence_for_llm /
  _build_pair_summaries / _build_pair_facts_context
- 综合研判兜底：_simple_synthesis
- 风险/质量评估：_highest_risk_local / _build_failed_pair_result /
  _build_coverage_stats / _apply_profile_confidence_to_candidate_scores /
  _build_data_quality_flags / _build_profile_quality_flags
- 项目级决策：_derive_project_decision / _build_project_narrative_summary /
  _build_project_summary_payload
- 群组合并：_result_to_dict（依赖 findings._normalize_compare_result）/
  _merge_group_results

设计原则：
- 全部为纯函数 + 常量，无 self 依赖
- 通过模块内 import 引入 constants 与 findings 中的依赖符号
"""

from __future__ import annotations

import json
from typing import Optional

from loguru import logger

from app.core.collusion.analyzer_parts.constants import (
    _DECISION_ENGINE_VERSION,
    _DIM_KEYS,
    _RISK_RANK_LOCAL,
)
from app.core.collusion.analyzer_parts.findings import (
    _EVIDENCE_LEVEL_ORDER,
    _normalize_compare_result,
)

# 补全 import 修复
from app.core.collusion.prompts import build_similar_chunks_text
from app.models.collusion_result import CollusionResult
from app.models.document import Document

_GLOBAL_FINDING_TYPE_PRIORITY = {
    "personnel_overlap": 0,   # D1 一级证据
    "company_relation": 1,    # D2 一级证据
    "pricing_pattern": 2,     # D4 二级证据
    "metadata_cluster": 3,    # D8 三级证据
    "unknown": 9,
}


_GLOBAL_FINDING_TYPE_LABEL = {
    "personnel_overlap": "D1 人员重叠",
    "company_relation": "D2 企业关联",
    "pricing_pattern": "D4 报价异常",
    "metadata_cluster": "D8 元数据聚类",
}


def _build_global_findings_context(
    global_findings: list[dict] | None,
    max_items: int = 10,
) -> str:
    """P0-2: 将 GlobalFeatureResult.global_findings 精选格式化为 Prompt 段落。

    按 `_GLOBAL_FINDING_TYPE_PRIORITY`（D1→D2→D4→D8）排序取 top-N。
    空集返回兜底文本，供 Prompt 在无全局发现时也保持格式稳定。
    """
    if not global_findings:
        return "（无跨文件全局发现，项目级结论请仅依据文件对/群组信息。）"

    filtered: list[dict] = []
    for item in global_findings:
        if not isinstance(item, dict):
            continue
        filtered.append(item)

    if not filtered:
        return "（无跨文件全局发现，项目级结论请仅依据文件对/群组信息。）"

    filtered.sort(
        key=lambda f: (
            _GLOBAL_FINDING_TYPE_PRIORITY.get(str(f.get("type") or "unknown"), 9),
            -len(f.get("involved_doc_ids") or []),
        )
    )

    lines: list[str] = []
    for item in filtered[:max_items]:
        ftype = str(item.get("type") or "unknown")
        label = _GLOBAL_FINDING_TYPE_LABEL.get(ftype, ftype)
        names = item.get("involved_names") or []
        names_suffix = (
            f"（涉及: {', '.join(str(n) for n in names[:4])}）"
            if isinstance(names, list) and names
            else ""
        )
        desc = item.get("description") or ""
        lines.append(f"- [{label}] {desc}{names_suffix}")

    if len(filtered) > max_items:
        lines.append(f"- ...（另有 {len(filtered) - max_items} 条全局发现已省略）")

    return "\n".join(lines)


def _format_group_evidence_for_llm(
    fused_evidence: list[dict] | None,
    aggregated_evidence: list[dict] | None,
    max_items: int = 15,
) -> str:
    """构造群组研判 LLM 的 aggregated_evidence 段文本。

    - 若传入 fused_evidence 非空，按 evidence_level + dimension 排序，展示
      level / dimension / description / evidences 简要。
    - 否则回退原 aggregated_evidence 的简要格式（兼容旧路径）。
    - 跳过已遗留但现在不应注入的 `_fused` 占位符（历史数据兼容）。
    """

    def _line_from_fused(item: dict) -> str | None:
        if not isinstance(item, dict):
            return None
        if item.get("_fused") is True:
            return None
        dimension = str(item.get("dimension") or "?")
        level = str(item.get("level") or item.get("evidence_level") or "unknown")
        desc = (
            item.get("description")
            or item.get("summary")
            or item.get("detail")
            or ""
        )
        involved_names = item.get("involved_doc_names") or item.get("involved_names") or []
        if isinstance(involved_names, list) and involved_names:
            names_suffix = f"（涉及: {', '.join(str(n) for n in involved_names[:4])}）"
        else:
            names_suffix = ""
        return f"- [{level}][{dimension}] {desc}{names_suffix}"

    def _line_from_legacy(item: dict) -> str | None:
        if not isinstance(item, dict):
            return None
        if item.get("_fused") is True:
            return None
        return f"- [{item.get('dimension', '?')}] {item.get('description', '')}"

    fused_lines: list[str] = []
    if fused_evidence:
        items = sorted(
            (x for x in fused_evidence if isinstance(x, dict) and not x.get("_fused")),
            key=lambda f: (
                _EVIDENCE_LEVEL_ORDER.get(
                    str(f.get("level") or f.get("evidence_level") or "unknown").lower(),
                    3,
                ),
                str(f.get("dimension") or "Z"),
            ),
        )
        for item in items[:max_items]:
            line = _line_from_fused(item)
            if line:
                fused_lines.append(line)

    if fused_lines:
        return "\n".join(fused_lines)

    legacy_lines: list[str] = []
    for ev in (aggregated_evidence or [])[: max_items if max_items else 10]:
        line = _line_from_legacy(ev)
        if line:
            legacy_lines.append(line)

    return "\n".join(legacy_lines) or "(无结构化证据)"


def _build_pair_summaries(pair_results: list[dict]) -> str:
    """将所有对比结果格式化为综合研判的结构化 JSON 输入"""
    summary_items: list[dict] = []
    for res in pair_results:
        if not isinstance(res, dict):
            continue
        pair_ref = res.get("pair_ref") or {}
        findings = res.get("key_findings") or []
        top_findings = []
        for finding in findings[:3]:
            if isinstance(finding, dict):
                top_findings.append(
                    finding.get("summary")
                    or finding.get("description")
                    or finding.get("detail")
                    or ""
                )
            else:
                top_findings.append(str(finding))
        summary_items.append(
            {
                "pair_id": res.get("pair_id"),
                "doc_a_id": pair_ref.get("doc_a_id"),
                "doc_b_id": pair_ref.get("doc_b_id"),
                "doc_a_name": pair_ref.get("doc_a_name"),
                "doc_b_name": pair_ref.get("doc_b_name"),
                "risk_level": res.get("risk_level"),
                "overall_risk": res.get("overall_risk", "inconclusive"),
                "analysis_state": res.get("analysis_state"),
                "decision_state": res.get("decision_state"),
                "quality_flags": res.get("quality_flags") or [],
                "top_findings": [x for x in top_findings if x],
            }
        )
    return json.dumps(summary_items, ensure_ascii=False, indent=2)


def _simple_synthesis(pair_results: list[dict]) -> dict:
    """LLM综合研判失败时的简化结论

    P1-4:
      - 当 overall 为 high/medium/low 时，按 pair 风险降序生成 top-3
        suspected_groups，避免"项目级高风险但 suspected_groups 为空"的口径矛盾
    """
    risk_levels = [r.get("overall_risk", "inconclusive") for r in pair_results]
    if "high" in risk_levels:
        overall = "high"
    elif "medium" in risk_levels:
        overall = "medium"
    elif "low" in risk_levels:
        overall = "low"
    elif all(r == "none" for r in risk_levels):
        overall = "none"
    else:
        overall = "inconclusive"

    # P1-4: 风险排序优先级（high > medium > low）
    _RISK_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0, "inconclusive": 0}
    suspected_groups: list[dict] = []
    if overall in {"high", "medium", "low"}:
        ranked = sorted(
            [r for r in pair_results if isinstance(r, dict)],
            key=lambda r: _RISK_RANK.get(str(r.get("overall_risk", "") or "").lower(), 0),
            reverse=True,
        )
        for res in ranked[:3]:
            risk = str(res.get("overall_risk", "") or "").lower()
            if risk not in {"high", "medium", "low"}:
                continue
            pair_ref = res.get("pair_ref") or {}
            a_id = pair_ref.get("doc_a_id")
            b_id = pair_ref.get("doc_b_id")
            if not a_id or not b_id:
                continue
            a_name = pair_ref.get("doc_a_name", f"文件{a_id}")
            b_name = pair_ref.get("doc_b_name", f"文件{b_id}")
            suspected_groups.append({
                "document_ids": [a_id, b_id],
                "document_names": [a_name, b_name],
                "risk_level": risk,
                "reason": "LLM 综合研判失败，基于文件对风险自动兜底生成（需人工复核）。",
            })

    return {
        "overall_risk": overall,
        "suspected_groups": suspected_groups,
        "collusion_network": None,
        "bid_accompanying_pattern": None,
        "summary": f"综合研判（LLM失败，基于{len(pair_results)}对比结果自动推导）",
        "investigation_suggestions": (
            ["建议重点复核高风险对子的具体证据。"]
            if overall in {"high", "medium", "low"}
            else ["当前分析未发现串标嫌疑。"]
        ),
    }


def _build_pair_facts_context(
    *,
    effective_info_a: str,
    effective_info_b: str,
    similar_chunks: list[dict],
    chunks_kwargs: dict,
) -> str:
    """将程序化整理后的事实输入提供给 pair judgement Prompt。"""
    chunk_text = build_similar_chunks_text(similar_chunks, **chunks_kwargs)
    return (
        "### 文件A结构化事实\n"
        f"{effective_info_a}\n\n"
        "### 文件B结构化事实\n"
        f"{effective_info_b}\n\n"
        "### 高相似文本与定位事实\n"
        f"{chunk_text}"
    )


def _highest_risk_local(risks: list[str]) -> str:
    if not risks:
        return "inconclusive"
    return max(risks, key=lambda r: _RISK_RANK_LOCAL.get(str(r).lower(), 0))


def _build_failed_pair_result(
    *,
    doc_a: Document,
    doc_b: Document,
    similarity_score: float,
    reason: str,
) -> dict:
    """构造单对分析失败的占位结果，确保下游可见且不误判为无风险。"""
    base = {
        "overall_risk": "inconclusive",
        "review_state": "failed",
        "analysis_state": "failed",
        "decision_state": "failed",
        "risk_level": None,
        "quality_flags": ["分析失败，当前仅保留占位结果"],
        "risk_explanation": "该对文件分析过程中发生异常，当前结果仅作待复核占位。",
        "failure_reason": reason[:500],
        "similarity_score": similarity_score,
        "key_findings": [],
        "findings": [],
    }
    return _normalize_compare_result(
        compare_result=base,
        doc_a_id=min(doc_a.id, doc_b.id),
        doc_b_id=max(doc_a.id, doc_b.id),
        doc_a_name=doc_a.filename if doc_a.id < doc_b.id else doc_b.filename,
        doc_b_name=doc_b.filename if doc_a.id < doc_b.id else doc_a.filename,
    )


def _build_coverage_stats(
    *,
    pair_results: list[dict],
    total_pairs: int,
    llm_pair_count: int,
    skipped_count: int,
) -> dict:
    analyzed_pairs = len(pair_results) + skipped_count
    failed_pairs = sum(1 for r in pair_results if (r.get("decision_state") == "failed" or r.get("review_state") == "failed"))
    review_required_pairs = sum(
        1 for r in pair_results
        if r.get("decision_state") in {"needs_review", "skipped"}
    ) + skipped_count
    clear_pairs = sum(
        1 for r in pair_results
        if r.get("decision_state") == "decidable" and str(r.get("risk_level") or r.get("overall_risk") or "").lower() == "none"
    )
    suspicious_pairs = sum(
        1 for r in pair_results
        if r.get("decision_state") == "decidable" and str(r.get("risk_level") or r.get("overall_risk") or "").lower() in {"high", "medium", "low"}
    )
    algorithm_only_pairs = sum(1 for r in pair_results if (r.get("analysis_mode") == "algorithm_only"))
    inconclusive_pairs = sum(
        1 for r in pair_results
        if str(r.get("overall_risk") or "").lower() == "inconclusive"
    ) + skipped_count
    return {
        "total_pairs": total_pairs,
        "analyzed_pairs": analyzed_pairs,
        "llm_analyzed_pairs": llm_pair_count,
        "algorithm_only_pairs": algorithm_only_pairs,
        "skipped_pairs": skipped_count,
        "failed_pairs": failed_pairs,
        "inconclusive_pairs": inconclusive_pairs,
        "review_required_pairs": review_required_pairs,
        "clear_pairs": clear_pairs,
        "suspicious_pairs": suspicious_pairs,
        "coverage_ratio": round((analyzed_pairs / total_pairs), 4) if total_pairs > 0 else 1.0,
    }


def _apply_profile_confidence_to_candidate_scores(
    candidate_scores: dict[tuple[int, int], float],
    *,
    industry_profile: dict | None,
) -> dict[tuple[int, int], float]:
    # industry_profile=None 视同 LLM 调用失败（与新 industry_profiler.py 的
    # status="llm_failed" 等价），统一走 unknown 兜底权重 0.8。
    confidence = str((industry_profile or {}).get("confidence", "unknown")).lower()
    factor = {
        "high": 1.0,
        "medium": 0.95,
        "low": 0.85,
        "unknown": 0.8,
    }.get(confidence, 0.8)
    return {
        pair: round(max(0.0, min(score * factor, 1.0)), 3)
        for pair, score in candidate_scores.items()
    }


def _build_data_quality_flags(
    *,
    coverage_stats: dict,
    has_tender_doc: bool,
) -> list[str]:
    """构造 data_quality_flags（仅保留需要立即引起用户决策的告警）。: 覆盖率、降级数、分析失败数等事实信号已统一由
 ``narrative_builder.build_factual_explanation`` 拼装成事实段，
 此处不再重复输出运维语，避免事实段与 flag 在 UI 中双展示。
 """
    flags: list[str] = []
    if not has_tender_doc:
        flags.append("未上传招标文件参照，部分文本一致性可能来源于招标模板，请人工甄别。")
    return flags


def _build_profile_quality_flags(industry_profile: dict | None) -> list[str]:
    """按行业画像 status 输出用户态质量告警。

    与 industry_profiler.py 的四种 status 一一映射。industry_profile=None
    保持向后兼容，视同 status="llm_failed"。
    """
    if not industry_profile:
        return ["行业画像生成失败（AI 调用未返回结果），D3/D4/D5 已退化为不依赖行业先验的保守判定路径。"]

    status = str(industry_profile.get("status") or "").strip().lower()
    # status 缺失时按既有 confidence/industry_type 推断（兼容旧 snapshot）
    if not status:
        confidence = str(industry_profile.get("confidence", "unknown")).lower()
        industry = str(industry_profile.get("industry_type", "unknown") or "unknown")
        signal_count = len(industry_profile.get("signals") or [])
        if signal_count < 2:
            status = "insufficient_signals"
        elif industry == "unknown" or confidence in {"low", "unknown"}:
            status = "llm_returned_unknown"
        else:
            status = "success"

    if status == "success":
        return []

    if status == "llm_failed":
        failure_reason = str(industry_profile.get("failure_reason") or "").strip()
        reason_hint = f"（原因：{failure_reason}）" if failure_reason else ""
        return [
            f"行业画像生成失败{reason_hint}，AI 调用未返回有效结果，D3/D4/D5 已退化为不依赖行业先验的保守判定路径。"
        ]

    if status == "insufficient_signals":
        signal_count = int(industry_profile.get("signal_count") or 0)
        return [
            f"行业画像材料信号不足（仅识别到 {signal_count} 项有效信号），无法形成可靠行业先验，请勿将行业判定作为主要依据。"
        ]

    if status == "llm_returned_unknown":
        return ["AI 已读取材料但未能归纳出明确行业类型，行业先验缺失，相关维度需结合原文证据复核。"]

    # 兜底（防御新增 status 未及时更新文案）
    return [f"行业画像状态异常（status={status}），请人工复核。"]


def _derive_project_decision(
    *,
    pair_results: list[dict],
    clusters: list,
    coverage_stats: dict,
) -> dict:
    """基于结构化结果统一裁决项目级业务结论。"""
    suspicious_risks = [
        str(r.get("risk_level") or r.get("overall_risk") or "").lower()
        for r in pair_results
        if r.get("decision_state") == "decidable"
        and str(r.get("risk_level") or r.get("overall_risk") or "").lower() in {"high", "medium", "low"}
    ]
    suspicious_risks.extend(
        str(getattr(c, "risk_level", "") or "").lower()
        for c in (clusters or [])
        if str(getattr(c, "risk_level", "") or "").lower() in {"high", "medium", "low"}
    )

    clear_pairs = int(coverage_stats.get("clear_pairs", 0) or 0)
    suspicious_pairs = int(coverage_stats.get("suspicious_pairs", 0) or 0)
    failed_pairs = int(coverage_stats.get("failed_pairs", 0) or 0)
    review_required_pairs = int(coverage_stats.get("review_required_pairs", 0) or 0)
    analyzed_pairs = int(coverage_stats.get("analyzed_pairs", 0) or 0)

    if suspicious_risks or suspicious_pairs > 0:
        confidence = "high" if failed_pairs == 0 and review_required_pairs == 0 else "medium"
        return {
            "project_decision": "suspicious_found",
            "project_risk_level": _highest_risk_local(suspicious_risks),
            "decision_confidence": confidence,
            "review_required": failed_pairs > 0 or review_required_pairs > 0,
        }

    if analyzed_pairs <= 0 or failed_pairs == analyzed_pairs:
        return {
            "project_decision": "analysis_failed",
            "project_risk_level": None,
            "decision_confidence": "low",
            "review_required": True,
        }

    if clear_pairs == analyzed_pairs and review_required_pairs == 0 and failed_pairs == 0:
        return {
            "project_decision": "clear_no_risk",
            "project_risk_level": "none",
            "decision_confidence": "high",
            "review_required": False,
        }

    confidence = "low" if failed_pairs > 0 else "medium"
    return {
        "project_decision": "partial_review_needed",
        "project_risk_level": "none" if clear_pairs > 0 else None,
        "decision_confidence": confidence,
        "review_required": True,
    }


def _build_project_narrative_summary(
    *,
    project_decision: str,
    coverage_stats: dict,
    project_risk_level: Optional[str],
    has_tender_doc: bool,
) -> str:
    """生成统一的项目级说明文字，作为页面/PDF 的单一业务真源。"""
    analyzed_pairs = int(coverage_stats.get("analyzed_pairs", 0) or 0)
    clear_pairs = int(coverage_stats.get("clear_pairs", 0) or 0)
    suspicious_pairs = int(coverage_stats.get("suspicious_pairs", 0) or 0)
    review_required_pairs = int(coverage_stats.get("review_required_pairs", 0) or 0)
    failed_pairs = int(coverage_stats.get("failed_pairs", 0) or 0)

    if project_decision == "suspicious_found":
        risk_label = {
            "high": "高风险",
            "medium": "中风险",
            "low": "低风险",
        }.get(str(project_risk_level or "").lower(), "异常")
        summary = (
            f"已完成 {analyzed_pairs} 对文件交叉比对，发现 {suspicious_pairs} 对存在可直接决策的异常证据，"
            f"项目级结论为{risk_label}疑似串标。"
        )
        if review_required_pairs or failed_pairs:
            summary += f" 另有 {review_required_pairs + failed_pairs} 对结果仍需人工补充复核。"
        return summary

    if project_decision == "clear_no_risk":
        if has_tender_doc:
            tender_note = "已结合招标文件参照过滤模板性重合。"
        else:
            tender_note = "（当前未上传招标文件参照，结论基于投标文件间的直接比对。）"
        return (
            f"已完成 {analyzed_pairs} 对文件交叉比对，{clear_pairs} 对均未发现可支撑串标认定的异常证据，"
            f"判定为无串标嫌疑。{tender_note}"
        )

    if project_decision == "analysis_failed":
        return (
            f"当前自动化分析链路未产出足够的有效结果（失败 {failed_pairs} 对），"
            "暂无法形成项目级结论，需重新分析或人工复核。"
        )

    pending = review_required_pairs + failed_pairs
    return (
        f"已完成 {analyzed_pairs} 对文件交叉比对，"
        f"其中 {clear_pairs} 对未发现异常，"
        f"{pending} 对分析结果待复核。"
    )


def _build_project_summary_payload(
    *,
    synthesis: dict,
    pair_results: list[dict],
    clusters: list,
    total_pairs: int,
    llm_pair_count: int,
    skipped_count: int,
    has_tender_doc: bool,
    industry_profile: dict | None,
    global_findings_extra: list[dict] | None = None,
) -> dict:
    coverage_stats = _build_coverage_stats(
        pair_results=pair_results,
        total_pairs=total_pairs,
        llm_pair_count=llm_pair_count,
        skipped_count=skipped_count,
    )
    group_risks = [str(c.risk_level).lower() for c in (clusters or []) if getattr(c, "risk_level", None)]
    pair_risks = [str(r.get("overall_risk", "inconclusive")).lower() for r in pair_results]
    if group_risks:
        display_overall_risk = _highest_risk_local(group_risks)
        risk_basis = "groups"
    else:
        display_overall_risk = _highest_risk_local(pair_risks) if pair_risks else "inconclusive"
        risk_basis = "pairs"

    decision_info = _derive_project_decision(
        pair_results=pair_results,
        clusters=clusters,
        coverage_stats=coverage_stats,
    )
    project_risk_level = decision_info["project_risk_level"]
    project_decision = decision_info["project_decision"]
    narrative_summary = _build_project_narrative_summary(
        project_decision=project_decision,
        coverage_stats=coverage_stats,
        project_risk_level=project_risk_level,
        has_tender_doc=has_tender_doc,
    )

    llm_risk = str(synthesis.get("overall_risk", "inconclusive")).lower()
    display_overall_risk = project_risk_level or "inconclusive"
    risk_basis = "groups" if group_risks and project_decision == "suspicious_found" else "pairs"
    risk_consistency_status = "aligned" if llm_risk == display_overall_risk else "diverged"
    risk_consistency_note = None
    if risk_consistency_status == "diverged":
        basis_label = "群组风险" if risk_basis == "groups" else "文件对风险"
        risk_consistency_note = (
            f"项目级结论以规则裁决器为准；LLM 文本仅作说明，当前风险按{basis_label}口径统一展示。"
        )

    return {
        "summary_source_version": 4,
        "overall_risk": display_overall_risk,
        "project_decision": project_decision,
        "project_risk_level": project_risk_level,
        "decision_confidence": decision_info["decision_confidence"],
        "review_required": decision_info["review_required"],
        "decision_basis": risk_basis,
        "decision_engine_version": _DECISION_ENGINE_VERSION,
        "overall_risk_basis": risk_basis,
        "risk_distribution_basis": risk_basis,
        "risk_consistency_status": risk_consistency_status,
        "risk_consistency_note": risk_consistency_note,
        "summary": synthesis.get("summary", ""),
        "narrative_summary": narrative_summary,
        "suspected_groups": synthesis.get("suspected_groups") or [],
        "investigation_suggestions": synthesis.get("investigation_suggestions") or [],
        "collusion_network": synthesis.get("collusion_network"),
        "bid_accompanying_pattern": synthesis.get("bid_accompanying_pattern"),
        "groups_summary": synthesis.get("groups_summary", ""),
        "ungrouped_notes": synthesis.get("ungrouped_notes"),
        "coverage_stats": coverage_stats,
        "data_quality_flags": _build_data_quality_flags(
            coverage_stats=coverage_stats,
            has_tender_doc=has_tender_doc,
        ),
        "profile_quality_flags": _build_profile_quality_flags(industry_profile),
        "industry_profile_snapshot": industry_profile,
        "industry_profile_confidence": (
            str(industry_profile.get("confidence", "unknown")).lower()
            if industry_profile else "unknown"
        ),
        "industry_profile_status": (
            str(industry_profile.get("status") or "").strip().lower() or None
            if industry_profile else "llm_failed"
        ),
        "industry_profile_signals": (industry_profile.get("signals") or []) if industry_profile else [],
        # 后端拼装的事实段（与 synthesis.summary 研判段并列两路真源）；
        # synthesis_stage._inject_factual_explanation 已在 synthesis dict 中写入，
        # 这里直接透传到 CollusionProjectSummary，避免重复计算。
        "factual_explanation": synthesis.get("factual_explanation"),
        "factual_explanation_items": synthesis.get("factual_explanation_items") or [],
        # P0-3: 游离全局发现（未归入任何群组的 D4/D8 等）→ API `/results` 会附加到 global_findings
        "global_findings_extra": list(global_findings_extra or []),
    }


def _result_to_dict(
    r: CollusionResult,
    doc_a_id: int,
    doc_b_id: int,
    doc_a_name: Optional[str] = None,
    doc_b_name: Optional[str] = None,
) -> dict:
    """将 CollusionResult ORM 对象转换为稳定的身份映射结构"""
    data = r.llm_analysis or {"overall_risk": r.overall_risk, "key_findings": r.findings}
    return _normalize_compare_result(
        compare_result=data,
        doc_a_id=doc_a_id,
        doc_b_id=doc_b_id,
        doc_a_name=doc_a_name or f"文件{doc_a_id}",
        doc_b_name=doc_b_name or f"文件{doc_b_id}",
    )


def _merge_group_results(results: list, d8_result: dict) -> dict:
    """合并各分组 LLM 结果 + D8 算法结果为统一 compare_result

    每个 result 可能是 dict（正常）或 Exception（gather return_exceptions=True）。
    """
    merged: dict = {}
    all_findings: list = []
    group_explanations: list[str] = []

    for res in results:
        if isinstance(res, Exception):
            logger.warning(f"[Analyzer] 分组分析异常: {res}")
            continue
        if not isinstance(res, dict):
            continue
        for key, val in res.items():
            if key.startswith("d") and key[1:2].isdigit():
                merged[key] = val
                if isinstance(val, dict):
                    for f in val.get("findings") or []:
                        all_findings.append(f)
            elif key == "risk_explanation" and val:
                group_explanations.append(val)

    # 注入 D8 算法结果
    if d8_result.get("d8_metadata"):
        merged["d8_metadata"] = d8_result["d8_metadata"]
        d8_findings = d8_result["d8_metadata"].get("findings") or []
        all_findings.extend(d8_findings)

    # 确保所有 8 维度都有值（缺失的补 None）
    for dim_key in _DIM_KEYS:
        if dim_key not in merged:
            merged[dim_key] = None

    merged["key_findings"] = all_findings
    if group_explanations:
        merged["_group_explanations"] = group_explanations

    return merged
