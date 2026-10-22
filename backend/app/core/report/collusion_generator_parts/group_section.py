"""R2-7 Group 主题：群组报告各章节

迁出自 `CollusionReportGenerator._build_group_overview` / `_build_group_conclusion` /
`_build_group_details` / `_build_single_group` / `_build_group_findings_section` /
`_build_single_group_finding`（R2-7 Step 7）。

包含 6 个函数:
- `build_group_overview`         （一、分析概览，群组模式增强版）
- `build_group_conclusion`       （三、综合结论，含群组概览表）
- `build_group_details`          （四、嫌疑群组详细分析）
- `build_single_group`           （单群组完整渲染：风险/角色/网络/策略/证据/调查）
- `build_group_findings_section` （群组级融合证据分级展示）
- `build_single_group_finding`   （单条群组级证据，含 N 文档引用块）

依赖:
- `_RISK_LABELS` / `_RISK_COLORS` / `_RISK_ORDER`（主模块常量）
- `_render_findings_by_level`（主模块 helper）
- `_THEME_*` 主题色 + `_FONT_NAME` + `_PAGE_WIDTH`
- 主类 `self.s_*` 样式族 + `self._to_safe_paragraph_text`

跨主题调用:
- `build_single_group` fallback 路径调用 `self._build_single_pair`（pair 主题），
  触发主类 thin forwarder 链路；不允许子模块直接互调
- `build_single_group` 调用 `self._build_group_findings_section`,
  `build_group_findings_section` 调用 `self._build_single_group_finding`,
  全部通过主类 thin forwarder 触发
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, Spacer, Table, TableStyle

from app.core.report.collusion_generator import (
    _FONT_NAME,
    _PAGE_WIDTH,
    _RISK_COLORS,
    _RISK_LABELS,
    _RISK_ORDER,
    _THEME_BG_ALT,
    _THEME_BG_HEADER,
    _THEME_BG_LIGHT,
    _THEME_BORDER,
    _THEME_TEXT,
    _THEME_TEXT_LIGHT,
    _render_findings_by_level,
)

if TYPE_CHECKING:
    from app.core.report.collusion_generator import CollusionReportGenerator
    from app.schemas.collusion import CollusionPairResult, CollusionResults


# ---------------------------------------------------------------------------
# 一、分析概览（群组模式）
# ---------------------------------------------------------------------------


def build_group_overview(
    self: "CollusionReportGenerator",
    results: "CollusionResults",
) -> list:
    """一、分析概览 (群组模式增强版，含群组/LLM分析统计)"""
    items: list = [Paragraph("一、分析概览", self.s_h1)]

    total_docs = len(results.document_labels) if results.document_labels else 0
    all_pairs = results.pair_results or []
    coverage = getattr(results, "coverage_stats", None)
    total_pairs = coverage.total_pairs if coverage else len(all_pairs)

    llm_pairs = coverage.llm_analyzed_pairs if coverage else sum(
        1 for p in all_pairs
        if p.analysis_mode not in ("skipped", "algorithm_only", None)
    )
    skipped_pairs = coverage.skipped_pairs if coverage else sum(
        1 for p in all_pairs if p.analysis_mode == "skipped"
    )
    algo_pairs = coverage.algorithm_only_pairs if coverage else max(total_pairs - llm_pairs - skipped_pairs, 0)
    failed_pairs = coverage.failed_pairs if coverage else sum(
        1 for p in all_pairs if p.analysis_mode == "failed"
    )

    groups = results.groups or []
    grp_risk = {"high": 0, "medium": 0, "low": 0}
    for g in groups:
        rk = str(g.risk_level).lower()
        if rk in grp_risk:
            grp_risk[rk] += 1

    pair_risk = {"high": 0, "medium": 0, "low": 0, "none": 0}
    for p in all_pairs:
        rk = str(p.overall_risk).lower()
        if rk in pair_risk:
            pair_risk[rk] += 1

    overview_data = [
        ["指标", "数值"],
        ["分析文件数量", str(total_docs)],
        ["招标文件参照", "是" if results.has_tender_doc else "否"],
        ["行业画像置信度", str(getattr(results, "industry_profile_confidence", "unknown") or "unknown")],
        ["交叉对比组合数", str(total_pairs)],
        ["LLM 深度分析对数", str(llm_pairs)],
        ["算法快速对比对数", str(algo_pairs)],
        ["智能筛对跳过数", str(skipped_pairs)],
        ["失败占位对数", str(failed_pairs)],
        ["覆盖率", f"{round((coverage.coverage_ratio if coverage else 1.0) * 100)}%"],
        ["识别嫌疑群组数", str(len(groups))],
        ["高风险群组", str(grp_risk["high"])],
        ["中风险群组", str(grp_risk["medium"])],
        ["低风险群组", str(grp_risk["low"])],
        ["高风险文件对", str(pair_risk["high"])],
        ["中风险文件对", str(pair_risk["medium"])],
    ]

    overview_tbl = Table(overview_data, colWidths=[120, _PAGE_WIDTH - 120])
    overview_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, 0), _THEME_BG_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (0, -1), _THEME_BG_LIGHT),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, _THEME_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _THEME_BG_ALT]),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    items.append(overview_tbl)
    items.append(Spacer(1, 6 * mm))
    return items


# ---------------------------------------------------------------------------
# 三、综合结论（群组模式）
# ---------------------------------------------------------------------------


def build_group_conclusion(
    self: "CollusionReportGenerator",
    results: "CollusionResults",
    replace_refs=None,
) -> list:
    """三、综合结论 (含整体风险色块 + 群组概览表)"""
    items: list = [Paragraph("三、综合结论", self.s_h1)]

    risk = results.overall_risk
    risk_label = _RISK_LABELS.get(risk, "未知")
    risk_color = {
        "clear_no_risk": _RISK_COLORS["none"],
        "suspicious_found": _RISK_COLORS.get(risk, colors.gray),
        "partial_review_needed": _RISK_COLORS["inconclusive"],
        "analysis_failed": colors.HexColor("#777777"),
    }.get(getattr(results, "project_decision", ""), _RISK_COLORS.get(risk, colors.gray))
    decision_label = {
        "clear_no_risk": "未发现串标嫌疑",
        "suspicious_found": "发现疑似串标",
        "partial_review_needed": "需人工复核",
        "analysis_failed": "分析失败",
    }.get(getattr(results, "project_decision", ""), risk_label)

    risk_data = [[Paragraph(
        f"<b>项目级结论: {decision_label}</b>",
        ParagraphStyle("GRB", fontName=_FONT_NAME, fontSize=14,
                       textColor=colors.white, alignment=TA_CENTER),
    )]]
    risk_tbl = Table(risk_data, colWidths=[_PAGE_WIDTH])
    risk_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), risk_color),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    items.append(risk_tbl)
    items.append(Spacer(1, 4 * mm))

    # 项目级结论（统一裁决说明），与前端 group 概览 narrative 对齐
    narrative_summary = getattr(results, "narrative_summary", None)
    if narrative_summary:
        text = replace_refs(narrative_summary) if replace_refs else narrative_summary
        items.append(Paragraph("项目级结论", self.s_h2))
        items.append(Paragraph(self._to_safe_paragraph_text(text), self.s_body))
        items.append(Spacer(1, 4 * mm))

    # 事实说明段（结构化事实），与前端"事实"段对齐
    factual_explanation = getattr(results, "factual_explanation", None)
    if factual_explanation:
        items.append(Paragraph("事实说明", self.s_h2))
        items.append(Paragraph(
            self._to_safe_paragraph_text(factual_explanation), self.s_body))
        items.append(Spacer(1, 4 * mm))

    if results.synthesis_summary:
        text = (replace_refs(results.synthesis_summary)
                if replace_refs else results.synthesis_summary)
        items.append(Paragraph("补充说明", self.s_h2))
        items.append(Paragraph(self._to_safe_paragraph_text(text), self.s_body))
        items.append(Spacer(1, 4 * mm))

    # 群组概览表
    groups = results.groups or []
    if groups:
        items.append(Paragraph("嫌疑群组概览", self.s_h2))

        tbl_data = [["群组", "风险等级", "成员", "关联证据"]]
        pair_map = {p.pair_id: p for p in (results.pair_results or [])}

        for g in groups:
            g_risk = _RISK_LABELS.get(
                str(g.risk_level).lower(), str(g.risk_level))
            members = "\u3001".join(g.member_names) if g.member_names else "(未知)"

            ev_count = 0
            for pid in (g.related_pair_ids or []):
                p = pair_map.get(pid)
                if p:
                    ev_count += len(p.findings or [])

            tbl_data.append([
                f"群组 {g.group_index + 1}",
                g_risk,
                Paragraph(self._to_safe_paragraph_text(members), self.s_small),
                f"{ev_count} 条",
            ])

        col_ws = [_PAGE_WIDTH * 0.12, _PAGE_WIDTH * 0.14,
                  _PAGE_WIDTH * 0.56, _PAGE_WIDTH * 0.18]
        ov_tbl = Table(tbl_data, colWidths=col_ws)

        style_cmds = [
            ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), _THEME_BG_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("ALIGN", (0, 1), (0, -1), "CENTER"),
            ("ALIGN", (1, 1), (1, -1), "CENTER"),
            ("ALIGN", (3, 1), (3, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, _THEME_BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, _THEME_BG_ALT]),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]
        for row_idx, g in enumerate(groups, 1):
            rc = _RISK_COLORS.get(str(g.risk_level).lower(), _THEME_TEXT)
            style_cmds.append(
                ("TEXTCOLOR", (1, row_idx), (1, row_idx), rc))

        ov_tbl.setStyle(TableStyle(style_cmds))
        items.append(ov_tbl)

    items.append(Spacer(1, 6 * mm))
    return items


# ---------------------------------------------------------------------------
# 四、嫌疑群组详细分析
# ---------------------------------------------------------------------------


def build_group_details(
    self: "CollusionReportGenerator",
    results: "CollusionResults",
    pair_map: dict[str, "CollusionPairResult"],
    replace_refs=None,
) -> list:
    """四、嫌疑群组详细分析"""
    groups = results.groups or []
    if not groups:
        return []

    items: list = [Paragraph("四、嫌疑群组详细分析", self.s_h1)]

    for gi, g in enumerate(groups):
        if gi > 0:
            items.append(PageBreak())
        items += self._build_single_group(g, pair_map, replace_refs)

    return items


def build_single_group(
    self: "CollusionReportGenerator",
    group,
    pair_map,
    replace_refs=None,
) -> list:
    """构建单个嫌疑群组的完整分析

    包含: 风险横幅 -> 角色分析 -> 网络描述 -> 串通策略
          -> 关键证据概述 -> 关联文件对证据详情 -> 调查建议
    """
    items: list = []

    grp_num = group.group_index + 1
    risk_key = str(group.risk_level).lower()
    risk_label = _RISK_LABELS.get(risk_key, str(group.risk_level))
    risk_color = _RISK_COLORS.get(risk_key, colors.gray)
    members_text = "\u3001".join(
        group.member_names) if group.member_names else "(未知)"

    # -- 群组标题 --
    items.append(Paragraph(
        self._to_safe_paragraph_text(
            f"4.{grp_num}  群组 {grp_num}: {members_text}"
        ),
        self.s_h2,
    ))

    # -- 风险横幅 --
    member_count = len(group.member_names or [])
    evidence_count = 0
    related_pairs: list = []
    for pid in (group.related_pair_ids or []):
        p = pair_map.get(pid)
        if p:
            related_pairs.append(p)
            evidence_count += len(p.findings or [])

    banner_parts = [
        f"风险等级: {risk_label}",
        f"成员: {member_count} 家",
        f"关联证据: {evidence_count} 条",
    ]
    if getattr(group, "group_confidence", None) is not None:
        banner_parts.append(
            f"可信度: {round(float(group.group_confidence) * 100)}%"
        )
    if getattr(group, "uncertain_edge_count", 0):
        banner_parts.append(f"待复核边: {group.uncertain_edge_count} 条")
    banner_text = "  |  ".join(banner_parts)

    banner_data = [[Paragraph(
        self._to_safe_paragraph_text(banner_text),
        ParagraphStyle("GBN", fontName=_FONT_NAME, fontSize=9,
                       textColor=colors.white, alignment=TA_CENTER),
    )]]
    banner_tbl = Table(banner_data, colWidths=[_PAGE_WIDTH])
    banner_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), risk_color),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    items.append(banner_tbl)
    items.append(Spacer(1, 4 * mm))

    # -- 角色分析表 --
    if group.roles:
        items.append(Paragraph("成员角色分析", self.s_h3))
        role_data = [["公司名称", "角色", "判定依据"]]
        for role in group.roles:
            role_data.append([
                Paragraph(
                    self._to_safe_paragraph_text(
                        getattr(role, 'company_name', '') or ''
                    ),
                    self.s_small),
                getattr(role, 'role', '角色不明'),
                Paragraph(
                    self._to_safe_paragraph_text(
                        getattr(role, 'role_evidence', '') or ''
                    ),
                    self.s_small),
            ])
        role_tbl = Table(
            role_data,
            colWidths=[_PAGE_WIDTH * 0.28,
                       _PAGE_WIDTH * 0.14,
                       _PAGE_WIDTH * 0.58],
        )
        role_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), _THEME_BG_HEADER),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, -1), _FONT_NAME),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, _THEME_BORDER),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (1, 0), (1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [colors.white, _THEME_BG_ALT]),
        ]))
        items.append(role_tbl)
        items.append(Spacer(1, 3 * mm))

    # -- 网络关系描述 --
    network_desc = getattr(group, 'network_description', None)
    if network_desc:
        items.append(Paragraph("<b>串通网络描述:</b>", self.s_body))
        items.append(
            Paragraph(self._to_safe_paragraph_text(network_desc), self.s_body_indent)
        )
        items.append(Spacer(1, 2 * mm))

    # -- 串通策略推断 --
    if group.collusion_strategy:
        items.append(Paragraph("<b>串通策略推断:</b>", self.s_body))
        items.append(Paragraph(
            self._to_safe_paragraph_text(group.collusion_strategy),
            self.s_body_indent))
        items.append(Spacer(1, 2 * mm))

    # -- 关键证据概述 --
    if group.key_evidence_summary:
        items.append(Paragraph("<b>关键证据概述:</b>", self.s_body))
        items.append(Paragraph(
            self._to_safe_paragraph_text(group.key_evidence_summary),
            self.s_body_indent))
        items.append(Spacer(1, 3 * mm))

    # -- 群组级融合证据（优先） / 关联文件对证据详情（回退） --
    group_findings = getattr(group, 'findings', None) or []
    if group_findings:
        items += self._build_group_findings_section(
            grp_num, group_findings, replace_refs)
    elif related_pairs:
        items.append(Paragraph(
            f"关联文件对证据详情 (共 {len(related_pairs)} 对)",
            self.s_h3,
        ))
        items.append(Spacer(1, 2 * mm))

        sorted_rel = sorted(
            related_pairs,
            key=lambda p: _RISK_ORDER.get(p.overall_risk, 5),
        )

        for pi, pair in enumerate(sorted_rel, 1):
            items += self._build_single_pair(
                idx=pi,
                pair=pair,
                replace_refs=replace_refs,
                section_prefix=f"4.{grp_num}.",
            )

    # -- 调查建议 --
    if group.investigation_focus:
        items.append(Paragraph("<b>调查建议:</b>", self.s_body))
        for i, suggestion in enumerate(group.investigation_focus, 1):
            items.append(Paragraph(
                self._to_safe_paragraph_text(f"  {i}. {suggestion}"),
                self.s_body_indent))
        items.append(Spacer(1, 3 * mm))

    items.append(Spacer(1, 8 * mm))
    return items


def build_group_findings_section(
    self: "CollusionReportGenerator",
    grp_num: int,
    findings: list,
    replace_refs=None,
) -> list:
    """构建群组级融合证据展示（按级别分组，含 N 文档引用）"""
    from app.core.collusion.analyzer_parts.dimension_signals import (
        is_hollow_dimension_signal,
    )

    # 与结果页一致，过滤空壳维度信号
    findings = [f for f in (findings or []) if not is_hollow_dimension_signal(f)]
    if not findings:
        return []

    items: list = []

    items.append(Paragraph(
        f"群组证据详情 (共 {len(findings)} 条)",
        self.s_h3,
    ))

    # 按级别分组（对象属性与 dict 两种 finding 形态均可取 level，与历史行为一致）
    grouped: dict[str, list] = {
        "first": [], "second": [], "third": [], "unknown": [],
    }
    for f in findings:
        lvl = getattr(f, "level", None)
        if lvl is None and isinstance(f, dict):
            lvl = f.get("level")
        lvl = lvl if lvl in grouped else "unknown"
        grouped[lvl].append(f)

    items += _render_findings_by_level(
        grouped,
        style_name="GLH",
        render_one=lambda fi_idx, f: self._build_single_group_finding(
            fi_idx, f, replace_refs),
    )

    return items


def build_single_group_finding(
    self: "CollusionReportGenerator",
    idx: int,
    finding,
    replace_refs=None,
) -> list:
    """构建单条群组级证据（含 N 文档引用块）"""
    items: list = []

    if isinstance(finding, dict):
        desc = finding.get("description", "")
        detail = finding.get("detail")
        dim = finding.get("dimension", "")
        certainty = finding.get("certainty")
        evidences = finding.get("evidences") or []
        involved = finding.get("involved_doc_names") or []
    else:
        desc = getattr(finding, "description", "")
        detail = getattr(finding, "detail", None)
        dim = getattr(finding, "dimension", "")
        certainty = getattr(finding, "certainty", None)
        evidences = getattr(finding, "evidences", None) or []
        involved = getattr(finding, "involved_doc_names", None) or []

    if replace_refs and desc:
        desc = replace_refs(desc)

    dim_tag = f"[{dim}] " if dim and dim != "unknown" else ""
    cert_tag = ""
    if certainty and certainty > 0.05:
        cert_tag = f" (确定性: {int(certainty * 100)}%)"

    involved_tag = ""
    if len(involved) > 2:
        involved_tag = f" [{len(involved)}份文档]"

    items.append(Paragraph(
        self._to_safe_paragraph_text(
            f"{idx}. {dim_tag}{desc}{cert_tag}{involved_tag}"
        ),
        self.s_body_indent,
    ))

    for ev in evidences:
        ev_name = ""
        ev_quote = ""
        ev_loc = ""
        if isinstance(ev, dict):
            ev_name = ev.get("doc_name") or ""
            ev_quote = ev.get("quote") or ""
            ev_loc = ev.get("location") or ""
        else:
            ev_name = getattr(ev, "doc_name", "") or ""
            ev_quote = getattr(ev, "quote", "") or ""
            ev_loc = getattr(ev, "location", "") or ""

        if not ev_quote and not ev_loc:
            continue

        if replace_refs and ev_quote:
            ev_quote = replace_refs(ev_quote)
        loc_str = f" [{ev_loc}]" if ev_loc else ""
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"{ev_name}{loc_str}: {ev_quote}"),
            self.s_quote,
        ))

    if detail and detail != desc:
        if replace_refs:
            detail = replace_refs(detail)
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"分析: {detail}"),
            ParagraphStyle("GDT", parent=self.s_small,
                           leftIndent=16, textColor=_THEME_TEXT_LIGHT),
        ))

    # 跨字段串联命中（群组级证据）：规则/法规对照，与 global_findings 口径一致
    if isinstance(finding, dict):
        risk_pattern = finding.get("risk_pattern")
        legal_reference = finding.get("legal_reference")
    else:
        risk_pattern = getattr(finding, "risk_pattern", None)
        legal_reference = getattr(finding, "legal_reference", None)
    cross_parts: list[str] = []
    if risk_pattern:
        cross_parts.append(f"规则: {risk_pattern}")
    if legal_reference:
        cross_parts.append(f"法规对照: {legal_reference}")
    if cross_parts:
        items.append(Paragraph(
            self._to_safe_paragraph_text(" | ".join(cross_parts)),
            ParagraphStyle("GFCrossField", parent=self.s_small,
                           leftIndent=16, textColor=_THEME_TEXT_LIGHT),
        ))

    return items
