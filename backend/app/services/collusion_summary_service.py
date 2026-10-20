"""串标审查项目级 Summary 收口服务（R2-6 拆分自 collusion router）

承载项目级综合结论的"单真源"裁决逻辑（防御）。

核心职责：
- 项目级决策推断（clear_no_risk / suspicious_found / partial_review_needed / analysis_failed）
- 实时 narrative 文案生成（不沿用 stored LLM 文案）
- 疑似群组规范化
- 风险等级聚合（pair + group 多源取最高）

设计原则：
- **纯函数模块**：无类、无全局状态、无 DB 操作；
- **依赖单向**：本模块不依赖 collusion_view_service / collusion_status_service；
- **R2-7 复用入口**：CollusionReportGenerator 直接 import 本模块复用决策推断。

参考：
- v2 §八 R2-6 卡片 §6.3 service 公共接口设计
- v2 §八 R2-7 卡片（CollusionReportGenerator 主题拆分）
"""

from __future__ import annotations

from app.models.collusion_group import CollusionGroup
from app.models.collusion_result import CollusionResult
from app.models.project import Project
from app.schemas.collusion import (
    CollusionProjectSummary,
    CoverageStats,
    SuspectedGroup,
)


# ---------------------------------------------------------------------------
# 风险等级排序（pair / group 多源取最高时使用）
# ---------------------------------------------------------------------------

RISK_RANK: dict[str, int] = {
    "high": 4, "medium": 3, "low": 2, "none": 1, "inconclusive": 0,
}


def highest_risk(risks: list[str]) -> str:
    """从多个风险等级中取最高值"""
    if not risks:
        return "inconclusive"
    return max(risks, key=lambda r: RISK_RANK.get(r, 0))


def derive_overall_risk(pair_results, group_records=None) -> str:
    """从各对结果 + 群体结果推导整体风险等级

    页面级 overallRisk 取 pair 和 group 两个来源的最高值，
    确保与群体风险显示一致，不出现矛盾。
    """
    if not pair_results:
        return "inconclusive"
    pair_risks = [r.overall_risk for r in pair_results]
    group_risks = [
        g.risk_level for g in (group_records or [])
        if g.risk_level
    ]
    return highest_risk(pair_risks + group_risks)


def derive_display_overall_risk(
    pair_results: list[CollusionResult],
    group_records: list[CollusionGroup],
) -> tuple[str, str]:
    """统一页面展示口径：有群组时按群组风险，无群组时按文件对风险。"""
    group_risks = [g.risk_level for g in group_records if g.risk_level]
    if group_risks:
        return highest_risk(group_risks), "groups"

    pair_risks = [r.overall_risk for r in pair_results if r.overall_risk]
    if pair_risks:
        return highest_risk(pair_risks), "pairs"

    return "inconclusive", "pairs"


# ---------------------------------------------------------------------------
# 疑似群组规范化（LLM 输出 → SuspectedGroup pydantic 模型）
# ---------------------------------------------------------------------------


def normalize_suspected_groups(
    groups: list,
    doc_id_to_name: dict[int, str],
) -> list[SuspectedGroup]:
    """将综合研判分组结果规范化为结构化模型"""
    name_to_id = {v: k for k, v in doc_id_to_name.items()}
    result: list[SuspectedGroup] = []

    for item in groups:
        if not isinstance(item, dict):
            continue
        doc_ids = [int(x) for x in (item.get("document_ids") or []) if str(x).isdigit()]
        doc_names = [str(x) for x in (item.get("document_names") or []) if str(x).strip()]

        if not doc_ids and doc_names:
            doc_ids = [name_to_id[name] for name in doc_names if name in name_to_id]
        if not doc_names and doc_ids:
            doc_names = [doc_id_to_name.get(i, f"文件{i}") for i in doc_ids]

        if not doc_ids and not doc_names:
            continue

        risk = str(item.get("risk_level", "medium")).lower()
        if risk not in {"high", "medium", "low"}:
            risk = "medium"

        result.append(SuspectedGroup(
            document_ids=doc_ids,
            document_names=doc_names,
            risk_level=risk,
            reason=str(item.get("reason", "")),
        ))

    return result


# ---------------------------------------------------------------------------
# 项目级决策推断（单真源）
# ---------------------------------------------------------------------------


def infer_decision_state(r: CollusionResult) -> str:
    """回填单对 pair 的 decision_state（summary/status 多处共用的单真源规则）

    优先取 llm_analysis 存值（小写规范化），缺失时按 analysis_mode /
    overall_risk 推断。
    """
    decision_state = str((r.llm_analysis or {}).get("decision_state") or "").lower()
    if decision_state:
        return decision_state
    if r.analysis_mode == "skipped":
        return "skipped"
    if r.analysis_mode == "failed":
        return "failed"
    if r.overall_risk == "inconclusive":
        return "needs_review"
    return "decidable"


def derive_project_decision(
    *,
    pair_results: list[CollusionResult],
    group_records: list[CollusionGroup],
    coverage_stats: CoverageStats,
) -> dict:
    """从 pair + group + coverage 推断项目级决策（4 种 ProjectDecision 之一）"""
    suspicious_risks: list[str] = []
    for r in pair_results:
        llm = r.llm_analysis or {}
        decision_state = infer_decision_state(r)
        risk_level = str(llm.get("risk_level") or r.overall_risk or "").lower()
        if decision_state == "decidable" and risk_level in {"high", "medium", "low"}:
            suspicious_risks.append(risk_level)

    suspicious_risks.extend(
        str(g.risk_level).lower()
        for g in (group_records or [])
        if str(g.risk_level).lower() in {"high", "medium", "low"}
    )

    if suspicious_risks:
        return {
            "project_decision": "suspicious_found",
            "project_risk_level": highest_risk(suspicious_risks),
            "decision_confidence": "high" if coverage_stats.failed_pairs == 0 and coverage_stats.review_required_pairs == 0 else "medium",
            "review_required": coverage_stats.failed_pairs > 0 or coverage_stats.review_required_pairs > 0,
        }

    if coverage_stats.analyzed_pairs <= 0 or coverage_stats.failed_pairs == coverage_stats.analyzed_pairs:
        return {
            "project_decision": "analysis_failed",
            "project_risk_level": None,
            "decision_confidence": "low",
            "review_required": True,
        }

    if (
        coverage_stats.clear_pairs == coverage_stats.analyzed_pairs
        and coverage_stats.review_required_pairs == 0
        and coverage_stats.failed_pairs == 0
    ):
        return {
            "project_decision": "clear_no_risk",
            "project_risk_level": "none",
            "decision_confidence": "high",
            "review_required": False,
        }

    return {
        "project_decision": "partial_review_needed",
        "project_risk_level": "none" if coverage_stats.clear_pairs > 0 else None,
        "decision_confidence": "low" if coverage_stats.failed_pairs > 0 else "medium",
        "review_required": True,
    }


# ---------------------------------------------------------------------------
# Narrative 文案生成（实时由规则裁决器生成，不沿用 stored LLM 文案）
# ---------------------------------------------------------------------------


def build_narrative_summary(
    *,
    decision_info: dict,
    coverage_stats: CoverageStats,
    has_tender_doc: bool,
) -> str:
    """从决策结果 + 覆盖统计 + 招标参照构建项目级 narrative 文案"""
    if decision_info["project_decision"] == "suspicious_found":
        risk_label = {
            "high": "高风险",
            "medium": "中风险",
            "low": "低风险",
        }.get(str(decision_info["project_risk_level"] or "").lower(), "异常")
        return (
            f"当前发现 {coverage_stats.suspicious_pairs} 对存在可直接决策的异常证据，"
            f"项目级结论为{risk_label}疑似串标。"
        )
    if decision_info["project_decision"] == "clear_no_risk":
        if has_tender_doc:
            tender_note = "已结合招标文件参照过滤模板性重合。"
        else:
            tender_note = "（当前未上传招标文件参照，结论基于投标文件间的直接比对。）"
        return (
            f"已完成 {coverage_stats.clear_pairs} 对无异常核查，"
            f"当前未发现可支撑串标认定的异常证据，判定为无串标嫌疑。{tender_note}"
        )
    if decision_info["project_decision"] == "analysis_failed":
        return "当前自动化分析链路未形成足够结果，需重新分析或人工复核。"
    pending = coverage_stats.review_required_pairs + coverage_stats.failed_pairs
    return (
        f"已完成 {coverage_stats.analyzed_pairs} 对文件交叉比对，"
        f"其中 {coverage_stats.clear_pairs} 对未发现异常，"
        f"{pending} 对分析结果待复核。"
    )


# ---------------------------------------------------------------------------
# 项目级 Summary 收口（单真源 — 不信任 stored，全量实时重建）
# ---------------------------------------------------------------------------


def load_or_build_project_summary(
    *,
    project: Project,
    pair_results: list[CollusionResult],
    group_records: list[CollusionGroup],
    coverage_stats: CoverageStats,
    has_tender_doc: bool,
    doc_id_to_name: dict[int, str],
) -> CollusionProjectSummary:
    """项目级 summary 单真源收口 

 核心原则:
 1. narrative_summary 必须由规则裁决器实时生成, 不沿用 stored 中的 LLM 文案
 2. suspected_groups 必须从实时 group_records 结构化数据重建
 3. overall_risk 以规则裁决结果为准, 不信任 stored
 4. LLM 文案降级为补充说明(summary字段), 不主导结论
 """
    stored = project.collusion_summary or {}

    decision_info = derive_project_decision(
        pair_results=pair_results,
        group_records=group_records,
        coverage_stats=coverage_stats,
    )
    narrative = build_narrative_summary(
        decision_info=decision_info,
        coverage_stats=coverage_stats,
        has_tender_doc=has_tender_doc,
    )

    real_suspected_groups: list[dict] = []
    for g in (group_records or []):
        real_suspected_groups.append({
            "document_ids": g.member_doc_ids or [],
            "document_names": [
                doc_id_to_name.get(did, f"文件{did}")
                for did in (g.member_doc_ids or [])
            ],
            "risk_level": g.risk_level or "medium",
            "reason": g.network_description or "",
        })
    real_suspected_groups = normalize_suspected_groups(
        real_suspected_groups, doc_id_to_name=doc_id_to_name,
    )

    # data_quality_flags 仅保留"招标参照缺失"这类用户决策需要立刻看到的告警；
    # 其余事实信号（覆盖率、降级原因、分析失败数）已统一由
    # `factual_explanation` 段拼装，避免与事实段重复输出造成用户阅读疲劳。
    data_quality_flags: list[str] = []
    if not has_tender_doc:
        data_quality_flags.append("未上传招标文件参照，部分文本一致性可能来源于招标模板，请人工甄别。")

    llm_summary = ""
    risk_consistency_note = None
    if isinstance(stored, dict) and stored:
        llm_summary = stored.get("summary") or ""
        llm_risk = str(stored.get("overall_risk", "inconclusive")).lower()
        rule_risk = str(decision_info["project_risk_level"] or "inconclusive").lower()
        if llm_risk != rule_risk and llm_risk != "inconclusive":
            risk_consistency_note = (
                f"LLM综合研判风险等级({llm_risk})与规则裁决结果({rule_risk})不一致, "
                f"以规则裁决为准。LLM文案仅供参考。"
            )

    risk_basis = "groups" if group_records else "pairs"

    stored_profile = stored.get("industry_profile_snapshot") if isinstance(stored, dict) else None
    stored_profile_conf = (
        stored.get("industry_profile_confidence", "unknown")
        if isinstance(stored, dict) else "unknown"
    )
    stored_profile_status = (
        stored.get("industry_profile_status")
        if isinstance(stored, dict) else None
    ) or (
        # 兼容旧 snapshot（无顶层 status）：从 industry_profile_snapshot 内取
        str((stored_profile or {}).get("status") or "").strip().lower() or None
        if stored_profile else None
    )
    stored_factual_explanation = (
        stored.get("factual_explanation")
        if isinstance(stored, dict) else None
    )
    stored_factual_explanation_items = (
        stored.get("factual_explanation_items", [])
        if isinstance(stored, dict) else []
    )
    stored_profile_signals = (
        stored.get("industry_profile_signals", [])
        if isinstance(stored, dict) else []
    )
    stored_profile_flags = (
        stored.get("profile_quality_flags", [])
        if isinstance(stored, dict) else []
    ) or ["当前项目尚未持久化行业画像快照，行业先验状态未知。"]
    stored_suggestions = (
        stored.get("investigation_suggestions", [])
        if isinstance(stored, dict) else []
    ) or (["请优先查看高风险与待复核对子。"] if pair_results else [])

    return CollusionProjectSummary(
        summary_source_version=max(
            int(stored.get("summary_source_version", 1)) if isinstance(stored, dict) else 1,
            1,
        ),
        overall_risk=decision_info["project_risk_level"] or "inconclusive",
        project_decision=decision_info["project_decision"],
        project_risk_level=decision_info["project_risk_level"],
        decision_confidence=decision_info["decision_confidence"],
        review_required=decision_info["review_required"],
        decision_basis=risk_basis,
        decision_engine_version=int(stored.get("decision_engine_version", 1)) if isinstance(stored, dict) else 1,
        overall_risk_basis=risk_basis,
        risk_distribution_basis=risk_basis,
        risk_consistency_status="aligned" if not risk_consistency_note else "diverged",
        risk_consistency_note=risk_consistency_note,
        summary=llm_summary,
        narrative_summary=narrative,
        suspected_groups=real_suspected_groups,
        investigation_suggestions=stored_suggestions,
        coverage_stats=coverage_stats,
        data_quality_flags=data_quality_flags,
        profile_quality_flags=stored_profile_flags,
        industry_profile_snapshot=stored_profile,
        industry_profile_confidence=stored_profile_conf,
        industry_profile_status=stored_profile_status,
        industry_profile_signals=stored_profile_signals,
        factual_explanation=stored_factual_explanation,
        factual_explanation_items=stored_factual_explanation_items,
    )
