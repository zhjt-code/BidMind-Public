"""R2-7 概览/结论/分布/矩阵/建议/声明主题（pair 报告专属"非 pair-detail"章节）

迁出自 `CollusionReportGenerator._build_overview` / `_build_conclusion` /
`_build_risk_distribution` / `_build_similarity_matrix` / `_build_suggestions` /
`_build_disclaimer`（R2-7 Step 5）。

包含 6 个函数:
- `build_overview`           （一、分析概览）
- `build_conclusion`         （二、总体审查结论）
- `build_risk_distribution`  （三、文件对/群组风险分布）
- `build_similarity_matrix`  （四、可疑度矩阵；群组报告 _generate_group_based_report
  也复用本函数渲染"二、可疑度矩阵"，差异仅在主类编排器内修改章节标题）
- `build_suggestions`        （六、调查建议；群组报告复用并改写为"六、调查建议汇总"）
- `build_disclaimer`         （免责声明，pair + 群组报告均复用）

依赖:
- `_RISK_LABELS` / `_RISK_COLORS` / `_RISK_BG_LIGHT` / `_MODE_LABELS`（主模块常量）
- `_THEME_*` 主题色 + `_FONT_NAME` + `_PAGE_WIDTH`（主模块常量）
- 主类 `self.s_*` 样式族 + `self._to_safe_paragraph_text`
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from app.core.report.collusion_generator import (
    _FONT_NAME,
    _MODE_LABELS,
    _PAGE_WIDTH,
    _RISK_BG_LIGHT,
    _RISK_COLORS,
    _RISK_LABELS,
    _RISK_ORDER,
    _THEME_BG_ALT,
    _THEME_BG_HEADER,
    _THEME_BG_LIGHT,
    _THEME_BORDER,
    _THEME_TEXT,
    _THEME_TEXT_LIGHT,
)

if TYPE_CHECKING:
    from app.core.report.collusion_generator import CollusionReportGenerator
    from app.schemas.collusion import CollusionResults


# ---------------------------------------------------------------------------
# 一、分析概览
# ---------------------------------------------------------------------------


def build_overview(self: "CollusionReportGenerator", results: "CollusionResults") -> list:
    items: list = [Paragraph("一、分析概览", self.s_h1)]

    total_docs = len(results.document_labels) if results.document_labels else 0
    coverage = getattr(results, "coverage_stats", None)
    total_pairs = coverage.total_pairs if coverage else len(results.pair_results or [])

    risk_counts = {"high": 0, "medium": 0, "low": 0, "none": 0, "inconclusive": 0}
    for p in (results.pair_results or []):
        risk_counts[p.overall_risk] = risk_counts.get(p.overall_risk, 0) + 1

    overview_data = [
        ["指标", "数值"],
        ["分析文件数量", str(total_docs)],
        ["交叉对比组合数", str(total_pairs)],
        ["招标文件参照", "是" if results.has_tender_doc else "否"],
        ["行业画像置信度", str(getattr(results, "industry_profile_confidence", "unknown") or "unknown")],
        ["已落库对比结果", str(coverage.analyzed_pairs if coverage else len(results.pair_results or []))],
        ["覆盖率", f"{round((coverage.coverage_ratio if coverage else 1.0) * 100)}%"],
        ["待复核跳过对数", str(coverage.skipped_pairs if coverage else 0)],
        ["失败占位对数", str(coverage.failed_pairs if coverage else 0)],
        ["需人工复核对数", str(getattr(coverage, "review_required_pairs", 0) if coverage else 0)],
        ["高风险对数", str(risk_counts.get("high", 0))],
        ["中风险对数", str(risk_counts.get("medium", 0))],
        ["低风险对数", str(risk_counts.get("low", 0))],
        ["无风险对数", str(risk_counts.get("none", 0))],
        ["已完成无异常对数", str(getattr(coverage, "clear_pairs", 0) if coverage else 0)],
    ]

    overview_tbl = Table(overview_data, colWidths=[100, _PAGE_WIDTH - 100])
    overview_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, 0), _THEME_BG_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (0, -1), _THEME_BG_LIGHT),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, _THEME_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, _THEME_BG_ALT]),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    items.append(overview_tbl)
    items.append(Spacer(1, 6 * mm))
    return items


# ---------------------------------------------------------------------------
# 二、总体审查结论
# ---------------------------------------------------------------------------


def build_conclusion(
    self: "CollusionReportGenerator",
    results: "CollusionResults",
    replace_refs=None,
) -> list:
    items: list = [Paragraph("二、总体审查结论", self.s_h1)]
    project_decision = getattr(results, "project_decision", "")

    # 疑似串通组合
    if results.suspected_groups:
        items.append(Paragraph("疑似串通组合", self.s_h2))
        for i, grp in enumerate(results.suspected_groups, 1):
            if isinstance(grp, dict):
                docs = "、".join(grp.get("document_names", []))
                risk = _RISK_LABELS.get(grp.get("risk_level", ""), "")
                reason = grp.get("reason", "")
            elif hasattr(grp, "document_names"):
                docs = "、".join(grp.document_names)
                risk = _RISK_LABELS.get(getattr(grp, "risk_level", ""), "")
                reason = getattr(grp, "reason", "")
            elif isinstance(grp, list):
                docs = "、".join(str(g) for g in grp)
                risk = ""
                reason = ""
            else:
                docs = str(grp)
                risk = ""
                reason = ""

            risk_tag = f" [{risk}]" if risk else ""
            grp_header = f"组合 {i}{risk_tag}: {docs}"

            # 用表格做色块条
            grp_risk_key = ""
            if isinstance(grp, dict):
                grp_risk_key = grp.get("risk_level", "")
            elif hasattr(grp, "risk_level"):
                grp_risk_key = getattr(grp, "risk_level", "")
            bg = _RISK_BG_LIGHT.get(grp_risk_key, _THEME_BG_LIGHT)
            border_c = _RISK_COLORS.get(grp_risk_key, _THEME_BORDER)

            grp_data = [[Paragraph(grp_header, ParagraphStyle(
                "GH", fontName=_FONT_NAME, fontSize=10,
                textColor=_THEME_TEXT,
            ))]]
            if reason:
                reason_text = replace_refs(reason) if replace_refs else reason
                grp_data.append([Paragraph(
                    self._to_safe_paragraph_text(f"认定理由: {reason_text}"),
                    ParagraphStyle("GR", fontName=_FONT_NAME, fontSize=9,
                                   textColor=_THEME_TEXT_LIGHT),
                )])

            grp_tbl = Table(grp_data, colWidths=[_PAGE_WIDTH])
            grp_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("LINEBELOW", (0, -1), (-1, -1), 0, bg),
                ("LINEBEFORE", (0, 0), (0, -1), 3, border_c),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]))
            items.append(grp_tbl)
            items.append(Spacer(1, 2 * mm))
    elif project_decision == "clear_no_risk":
        items.append(Paragraph("本次自动化分析未发现疑似串通组合。", self.s_body))
    elif project_decision == "partial_review_needed":
        items.append(Paragraph("当前仍有待复核文件对，暂不直接给出“无风险”结论。", self.s_body))
    elif project_decision == "analysis_failed":
        items.append(Paragraph("当前自动化分析未形成有效结论，需重新分析或人工复核。", self.s_body))
    else:
        items.append(Paragraph("当前未形成可直接展示的疑似串通组合。", self.s_body))

    # 综合研判
    narrative_summary = getattr(results, "narrative_summary", None)
    if narrative_summary:
        items.append(Spacer(1, 4 * mm))
        items.append(Paragraph("项目级结论", self.s_h2))
        items.append(Paragraph(
            self._to_safe_paragraph_text(
                replace_refs(narrative_summary) if replace_refs else narrative_summary
            ),
            self.s_body,
        ))

    # 事实说明段（后端按规则拼装的结构化事实：覆盖率/降级原因/行业画像状态等）
    factual_explanation = getattr(results, "factual_explanation", None)
    if factual_explanation:
        items.append(Spacer(1, 4 * mm))
        items.append(Paragraph("事实说明", self.s_h2))
        items.append(Paragraph(
            self._to_safe_paragraph_text(factual_explanation),
            self.s_body,
        ))

    if results.synthesis_summary:
        text = replace_refs(results.synthesis_summary) if replace_refs else results.synthesis_summary
        items.append(Spacer(1, 4 * mm))
        items.append(Paragraph("补充说明", self.s_h2))
        items.append(Paragraph(self._to_safe_paragraph_text(text), self.s_body))

    if getattr(results, "data_quality_flags", None):
        items.append(Spacer(1, 4 * mm))
        items.append(Paragraph("数据质量提示", self.s_h2))
        for flag in results.data_quality_flags:
            items.append(
                Paragraph(self._to_safe_paragraph_text(f"• {flag}"), self.s_body)
            )

    if getattr(results, "profile_quality_flags", None):
        items.append(Spacer(1, 4 * mm))
        items.append(Paragraph("行业画像状态", self.s_h2))
        items.append(Paragraph(
            self._to_safe_paragraph_text(
                f"行业画像置信度: {getattr(results, 'industry_profile_confidence', 'unknown') or 'unknown'}"
            ),
            self.s_body,
        ))
        for flag in results.profile_quality_flags:
            items.append(
                Paragraph(self._to_safe_paragraph_text(f"• {flag}"), self.s_body)
            )

    if getattr(results, "risk_consistency_note", None):
        items.append(Spacer(1, 4 * mm))
        items.append(Paragraph("风险口径说明", self.s_h2))
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"• {results.risk_consistency_note}"),
            self.s_body,
        ))

    items.append(Spacer(1, 6 * mm))
    return items


# ---------------------------------------------------------------------------
# 三、风险分布
# ---------------------------------------------------------------------------


def build_risk_distribution(
    self: "CollusionReportGenerator",
    results: "CollusionResults",
) -> list:
    pairs = results.pair_results or []
    groups = results.groups or []
    basis = getattr(results, "risk_distribution_basis", "pairs")
    if basis == "groups" and not groups:
        return []
    if basis != "groups" and not pairs:
        return []

    title = "三、群组风险分布" if basis == "groups" else "三、文件对风险分布"
    items: list = [Paragraph(title, self.s_h1)]

    rows = []
    if basis == "groups":
        header = ["群组", "风险等级", "可信度", "待复核边数"]
        rows = [header]
        sorted_groups = sorted(groups, key=lambda g: _RISK_ORDER.get(g.risk_level, 5))
        for g in sorted_groups:
            rows.append([
                Paragraph(
                    self._to_safe_paragraph_text(f"群组 {g.group_index + 1}"),
                    self.s_small,
                ),
                _RISK_LABELS.get(g.risk_level, "未知"),
                f"{round((g.group_confidence or 0) * 100)}%" if g.group_confidence is not None else "-",
                str(g.uncertain_edge_count or 0),
            ])
        col_ws = [_PAGE_WIDTH * 0.30, _PAGE_WIDTH * 0.22, _PAGE_WIDTH * 0.22, _PAGE_WIDTH * 0.26]
        sorted_rows_for_style = sorted_groups
        risk_value_getter = lambda g: g.risk_level  # noqa: E731
    else:
        header = ["文件对", "风险等级", "向量相似度", "分析模式"]
        sorted_pairs = sorted(pairs, key=lambda p: _RISK_ORDER.get(p.overall_risk, 5))
        rows = [header]
        for p in sorted_pairs:
            name = f"{p.doc_a_name} vs {p.doc_b_name}"
            if getattr(p, "decision_state", "decidable") == "decidable":
                risk_label = _RISK_LABELS.get(getattr(p, "risk_level", None) or p.overall_risk, "未知")
            elif getattr(p, "decision_state", "") == "failed":
                risk_label = "分析失败"
            else:
                risk_label = "需人工复核"
            sim = f"{p.similarity_score:.3f}" if p.similarity_score is not None else "-"
            mode = _MODE_LABELS.get(p.analysis_mode or "", p.analysis_mode or "-")
            rows.append([
                Paragraph(self._to_safe_paragraph_text(name), self.s_small),
                risk_label,
                sim,
                mode,
            ])
        col_ws = [_PAGE_WIDTH * 0.38, _PAGE_WIDTH * 0.18,
                   _PAGE_WIDTH * 0.18, _PAGE_WIDTH * 0.26]
        sorted_rows_for_style = sorted_pairs
        risk_value_getter = lambda p: p.overall_risk  # noqa: E731

    tbl = Table(rows, colWidths=col_ws)

    style_cmds = [
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), _THEME_BG_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, _THEME_BORDER),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, _THEME_BG_ALT]),
    ]
    # 风险等级着色
    for row_idx, row_obj in enumerate(sorted_rows_for_style, 1):
        rc = _RISK_COLORS.get(risk_value_getter(row_obj), _THEME_TEXT)
        style_cmds.append(("TEXTCOLOR", (1, row_idx), (1, row_idx), rc))

    tbl.setStyle(TableStyle(style_cmds))
    items.append(tbl)
    items.append(Spacer(1, 6 * mm))
    return items


# ---------------------------------------------------------------------------
# 四、可疑度矩阵
# ---------------------------------------------------------------------------


def build_similarity_matrix(
    self: "CollusionReportGenerator",
    results: "CollusionResults",
) -> list:
    labels = results.document_labels
    matrix = results.similarity_matrix
    if not labels or not matrix:
        return []

    items: list = [Paragraph("四、可疑度矩阵", self.s_h1)]

    n = len(labels)
    short = [lb[:10] + ".." if len(lb) > 10 else lb for lb in labels]

    header = [""] + short
    table_data = [header]
    for i in range(n):
        row = [short[i]]
        for j in range(n):
            val = matrix[i][j]
            if val is None or i == j:
                row.append("-")
            else:
                row.append(f"{round(val * 100)}%")
        table_data.append(row)

    col_w = min(28, max(18, int((_PAGE_WIDTH / mm) / (n + 1)))) * mm
    col_widths = [col_w] * (n + 1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), _THEME_BG_HEADER),
        ("BACKGROUND", (0, 0), (0, -1), _THEME_BG_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _THEME_BORDER),
        ("BOX", (0, 0), (-1, -1), 1, _THEME_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, n + 1):
        for j in range(1, n + 1):
            if i == j:
                continue
            val = matrix[i - 1][j - 1]
            if val is None:
                continue
            if val >= 0.7:
                style_cmds.append(
                    ("BACKGROUND", (j, i), (j, i), colors.HexColor("#FFCCCC")))
            elif val >= 0.4:
                style_cmds.append(
                    ("BACKGROUND", (j, i), (j, i), colors.HexColor("#FFF0CC")))

    tbl = Table(table_data, colWidths=col_widths)
    tbl.setStyle(TableStyle(style_cmds))
    items.append(tbl)
    items.append(Paragraph(
        "注: 红色底 >= 70%(高可疑), 黄色底 40%-69%(中可疑)",
        self.s_small,
    ))
    items.append(Spacer(1, 6 * mm))
    return items


# ---------------------------------------------------------------------------
# 六、调查建议
# ---------------------------------------------------------------------------


def build_suggestions(
    self: "CollusionReportGenerator",
    results: "CollusionResults",
    replace_refs=None,
) -> list:
    items: list = [Paragraph("六、调查建议", self.s_h1)]

    suggestions = results.investigation_suggestions or []
    if suggestions:
        if isinstance(suggestions, str):
            suggestions = [suggestions]

        sug_rows = [["序号", "建议内容"]]
        for i, s in enumerate(suggestions, 1):
            s_text = replace_refs(s) if replace_refs else s
            sug_rows.append([
                str(i),
                Paragraph(self._to_safe_paragraph_text(s_text), self.s_body),
            ])

        sug_tbl = Table(sug_rows, colWidths=[40, _PAGE_WIDTH - 40])
        sug_tbl.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BACKGROUND", (0, 0), (-1, 0), _THEME_BG_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, _THEME_BORDER),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, _THEME_BG_ALT]),
        ]))
        items.append(sug_tbl)
    else:
        if results.overall_risk in ("high", "medium"):
            items.append(Paragraph(
                "建议对以上疑似串通组合进行人工复核，"
                "重点核查人员身份、企业关联关系及报价依据。",
                self.s_body,
            ))
        else:
            items.append(Paragraph(
                "本次分析未发现明显串通迹象，无需额外调查。",
                self.s_body,
            ))

    items.append(Spacer(1, 8 * mm))
    return items


# ---------------------------------------------------------------------------
# 免责声明
# ---------------------------------------------------------------------------


def build_disclaimer(self: "CollusionReportGenerator") -> list:
    items: list = [Spacer(1, 6 * mm)]

    line_tbl = Table([[""]], colWidths=[_PAGE_WIDTH])
    line_tbl.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, _THEME_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    items.append(line_tbl)
    items.append(Spacer(1, 3 * mm))
    items.append(Paragraph(
        "本报告由 BidMind AI 系统自动生成，仅供参考，最终判定请以人工审核为准。",
        self.s_footer,
    ))
    items.append(Paragraph(
        "报告中的风险评估基于文档内容的自动化分析，不构成法律意见。",
        self.s_footer,
    ))
    return items
