"""串标审查项目级"事实说明段"拼装器（修复）

职责: 基于结构化串标结果（coverage_stats / pair_results 中的 quality 信号 /
 industry_profile 中的 status）拼装项目级"事实说明段"，供 UI 与 PDF 复用。
不做: LLM 调用、风险等级裁决、群组识别、PDF 样式渲染、HTML/Markdown 转义、
 i18n 翻译（i18n 闸门由 router 序列化前统一执行）。

设计目的: 与 同源问题 -- "用户态文案 = 后端事实段 + AI 研判段未分离"。
LLM project_summary 节点在压缩 quality_flags 时会丢失语义细节（白名单命中类别、
行业画像失败原因、LLM 返回的 raw 风险值等），故由本模块直接从结构化字段拼装
"事实段"，让 LLM 仅专注于"研判推断段"。

输出风格: 自然语言段落，禁止 Markdown/HTML 语法，使用半角圆点和换行符组织条目。
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# 行业画像 status -> 用户态说明（与 industry_profiler.py 四种 status 一一映射）
# 与 synthesis_helpers._build_profile_quality_flags 文案保持同源语义，但更聚焦
# "可信度边界"提示，避免重复。
# ---------------------------------------------------------------------------
_INDUSTRY_STATUS_NARRATIVES = {
    "success": "",  # 正常状态不进事实段（无需向用户额外解释）
    "llm_returned_unknown": (
        "AI 已读取材料但未能归纳出明确行业类型，行业先验缺失，"
        "D3/D4/D5 已退化为不依赖行业先验的保守判定路径。"
    ),
    "insufficient_signals": (
        "材料中有效行业信号过少（不足以归纳行业），"
        "行业先验缺失，建议补充行业背景资料后重新分析。"
    ),
    "llm_failed": (
        "行业画像 AI 调用失败，未能形成行业先验，"
        "D3/D4/D5 已退化为不依赖行业先验的保守判定路径。"
    ),
}


def build_factual_explanation(
    *,
    coverage_stats: dict | None,
    pair_results: list[dict] | None,
    industry_profile: dict | None,
    has_tender_doc: bool,
) -> Optional[str]:
    """拼装项目级"事实说明段"。

    Args:
        coverage_stats: 由 ``_build_coverage_stats`` 输出的覆盖率统计字典。
        pair_results: 文件对判定结果列表，每项含 quality_flags /
            whitelist_categories / degradation_reason 等结构化降级信号。
        industry_profile: ``CollusionIndustryProfiler.generate()`` 输出，含
            status / failure_reason / confidence 等字段。
        has_tender_doc: 项目是否上传了招标文件参照。

    Returns:
        拼装后的事实说明文本；如所有信号均为正常状态（无降级、无待复核、
        无行业画像异常）则返回 None，避免在 UI 上展示空板块。
    """
    sections = build_factual_explanation_items(
        coverage_stats=coverage_stats,
        pair_results=pair_results,
        industry_profile=industry_profile,
        has_tender_doc=has_tender_doc,
    )

    tender_line = _describe_tender_context(has_tender_doc)
    if tender_line:
        sections.append(tender_line)

    if not sections:
        return None
    return "\n".join(sections).strip() or None


def build_factual_explanation_items(
    *,
    coverage_stats: dict | None,
    pair_results: list[dict] | None,
    industry_profile: dict | None,
    has_tender_doc: bool,
) -> list[str]:
    """拼装项目级事实说明条目。

    职责: 保留覆盖率、文件对降级、行业画像状态的列表语义，供 UI 编号展示。
    不做: 输出使用限制类提示（如缺招标文件）、LLM 研判、HTML/Markdown 渲染。
    """
    del has_tender_doc  # 缺招标文件是使用限制，不属于事实依据条目。
    items: list[str] = []

    coverage_line = _describe_coverage_brief(coverage_stats or {})
    if coverage_line:
        items.append(coverage_line)

    degradation_lines = _describe_pair_degradations(pair_results or [])
    items.extend(degradation_lines)

    industry_line = _describe_industry_profile_state(industry_profile)
    if industry_line:
        items.append(f"行业画像：{industry_line}")

    return items


# ---------------------------------------------------------------------------
# 内部 helper（私有，命名体现具体业务动作，参数 <= 3）
# ---------------------------------------------------------------------------


def _describe_coverage_brief(coverage_stats: dict) -> str:
    """生成覆盖率简述行。

    示例: "本次共完成 3 对文件交叉比对（覆盖率 100%），其中 1 对未发现异常，
    2 对已转人工复核或分析失败。"
    """
    if not isinstance(coverage_stats, dict):
        return ""
    total = int(coverage_stats.get("total_pairs") or 0)
    analyzed = int(coverage_stats.get("analyzed_pairs") or 0)
    clear = int(coverage_stats.get("clear_pairs") or 0)
    suspicious = int(coverage_stats.get("suspicious_pairs") or 0)
    needs_review = int(coverage_stats.get("review_required_pairs") or 0)
    failed = int(coverage_stats.get("failed_pairs") or 0)
    ratio = coverage_stats.get("coverage_ratio")
    if total <= 0 and analyzed <= 0:
        return ""

    try:
        ratio_pct = round(float(ratio) * 100) if ratio is not None else None
    except (TypeError, ValueError):
        ratio_pct = None

    head = f"本次共完成 {analyzed} 对文件交叉比对"
    if total > 0 and ratio_pct is not None:
        head += f"（覆盖率 {ratio_pct}%）"
    parts: list[str] = []
    if clear:
        parts.append(f"{clear} 对未发现异常")
    if suspicious:
        parts.append(f"{suspicious} 对存在异常证据")
    pending = max(0, needs_review + failed)
    if pending:
        parts.append(f"{pending} 对已转人工复核或分析失败")
    if not parts:
        return head + "。"
    return head + "，其中 " + "、".join(parts) + "。"


def _describe_pair_degradations(pair_results: list[dict]) -> list[str]:
    """遍历 pair_results，输出"该对降级"事实说明列表。

    仅收纳 decision_state in {needs_review, skipped, failed} 或携带
    whitelist_categories / degradation_reason / quality_flags 的对子。
    每行格式: "<文件A> × <文件B>：<降级原因摘要>"
    """
    lines: list[str] = []
    for res in pair_results or []:
        if not isinstance(res, dict):
            continue
        decision_state = str(res.get("decision_state") or "").lower()
        review_state = str(res.get("review_state") or "").lower()
        quality_flags = res.get("quality_flags") or []
        whitelist_cats = res.get("whitelist_categories") or []
        degradation = res.get("degradation_reason") or {}

        # 只展示存在事实信号的对子
        has_signal = (
            decision_state in {"needs_review", "skipped", "failed"}
            or review_state in {"degraded", "failed", "needs_review"}
            or bool(quality_flags)
            or bool(whitelist_cats)
            or bool(degradation)
        )
        if not has_signal:
            continue

        pair_ref = res.get("pair_ref") or {}
        a_name = pair_ref.get("doc_a_name") or f"文件{pair_ref.get('doc_a_id', '?')}"
        b_name = pair_ref.get("doc_b_name") or f"文件{pair_ref.get('doc_b_id', '?')}"

        reason_bits: list[str] = []
        if whitelist_cats:
            reason_bits.append(
                "白名单命中（" + "、".join(str(c) for c in whitelist_cats) + "），"
                "重合可能源于通用模板"
            )
        if isinstance(degradation, dict) and degradation:
            d_type = str(degradation.get("type") or "").lower()
            raw = str(degradation.get("raw_value") or "").strip()
            if d_type == "inconclusive_insufficient_evidence":
                # 对齐：inconclusive 是合法的"信息不足→无法判定"语义，
                # 不得说成"未返回有效风险等级"，避免与真·非法枚举混淆。
                reason_bits.append("当前有效证据不足以判定串标风险，已转人工复核")
            elif d_type == "invalid_risk_enum":
                raw_show = raw if raw else "空"
                reason_bits.append(f"AI 未返回有效风险等级（返回值={raw_show!r}），已转人工复核")
            elif d_type:
                reason_bits.append(f"降级类型 {d_type}，已转人工复核")
        if not reason_bits and decision_state == "failed":
            reason_bits.append("本对分析失败，建议重新执行")
        if not reason_bits and decision_state in {"needs_review", "skipped"}:
            reason_bits.append("已转人工复核")

        summary = "；".join(reason_bits) if reason_bits else "存在数据质量信号"
        lines.append(f"{a_name} × {b_name}：{summary}")
    return lines


def _describe_industry_profile_state(industry_profile: dict | None) -> str:
    """按 status 输出行业画像状态说明（含 failure_reason）。

    industry_profile=None 视同 status="llm_failed"（与新 industry_profiler.py
    兼容契约一致）。返回空字符串表示无需在事实段提及。
    """
    if not industry_profile:
        return _INDUSTRY_STATUS_NARRATIVES["llm_failed"]
    status = str(industry_profile.get("status") or "").strip().lower()
    # 兼容旧 snapshot（无 status 字段）：按 confidence/industry_type 推断
    if not status:
        confidence = str(industry_profile.get("confidence", "unknown")).lower()
        industry_type = str(industry_profile.get("industry_type", "unknown") or "unknown")
        signal_count = len(industry_profile.get("signals") or [])
        if signal_count < 2:
            status = "insufficient_signals"
        elif industry_type == "unknown" or confidence in {"low", "unknown"}:
            status = "llm_returned_unknown"
        else:
            status = "success"

    narrative = _INDUSTRY_STATUS_NARRATIVES.get(status)
    if narrative is None:
        return f"状态异常（status={status}），请人工复核。"
    if not narrative:
        return ""

    if status == "llm_failed":
        failure_reason = str(industry_profile.get("failure_reason") or "").strip()
        if failure_reason:
            return f"{narrative}（失败原因：{failure_reason}）"
    return narrative


def _describe_tender_context(has_tender_doc: bool) -> str:
    """提示是否上传招标文件参照。仅在缺失时输出，避免冗余。"""
    if has_tender_doc:
        return ""
    return (
        "当前未上传招标文件参照，部分文本一致性可能源于招标模板，请结合证据原文人工甄别。"
    )
