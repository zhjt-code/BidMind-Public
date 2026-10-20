"""R2-7 Pair 主题：五、各对文件详细分析

迁出自 `CollusionReportGenerator._build_pair_details` / `_build_single_pair` /
`_build_dimension_table` / `_build_findings` / `_build_single_finding`（R2-7 Step 6）。

包含 5 个函数:
- `build_pair_details`     （五、各对文件详细分析）
- `build_single_pair`      （单对文件渲染，section_prefix 支持群组 fallback 复用）
- `build_dimension_table`  （8 维度评分表）
- `build_findings`         （证据发现列表，按级别分组）
- `build_single_finding`   （单条证据渲染，含 evidences / quote_a/b / detail 兜底）

依赖:
- `_RISK_LABELS` / `_RISK_COLORS` / `_RISK_ORDER` / `_MODE_LABELS` / `_DIM_NAMES`
  / `_DIM_TIERS`（主模块常量）
- `_score_level` / `_score_color` / `_replace_legacy_pair_aliases`
  / `_render_findings_by_level`（主模块 helper）
- `_THEME_*` 主题色 + `_FONT_NAME` + `_PAGE_WIDTH`
- 主类 `self.s_*` 样式族 + `self._to_safe_paragraph_text`

跨主题调用约定:
- `build_findings` -> `self._build_single_finding`(...)（同主题，但通过主类
  thin forwarder 触发，保留 monkey patch 锚点）
- `build_single_pair` -> `self._build_dimension_table`(...) + `self._build_findings`(...)
- 群组主题 `build_single_group` 在 fallback 路径上调用 `self._build_single_pair`,
  触发主类 thin forwarder 链路，与原实现 byte-equal
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from app.core.report.collusion_generator import (
    _DIM_NAMES,
    _DIM_TIERS,
    _FONT_NAME,
    _MODE_LABELS,
    _PAGE_WIDTH,
    _RISK_COLORS,
    _RISK_LABELS,
    _RISK_ORDER,
    _THEME_BG_ALT,
    _THEME_BG_HEADER,
    _THEME_BORDER,
    _THEME_TEXT_LIGHT,
    _THEME_TEXT_MUTED,
    _render_findings_by_level,
    _replace_legacy_pair_aliases,
    _score_color,
    _score_level,
)

if TYPE_CHECKING:
    from app.schemas.collusion import CollusionPairResult, CollusionResults

    from app.core.report.collusion_generator import CollusionReportGenerator


# ---------------------------------------------------------------------------
# 五、各对文件详细分析
# ---------------------------------------------------------------------------


def build_pair_details(
    self: "CollusionReportGenerator",
    results: "CollusionResults",
    replace_refs=None,
) -> list:
    items: list = [Paragraph("五、各对文件详细分析", self.s_h1)]

    if not results.pair_results:
        items.append(Paragraph("暂无对比数据。", self.s_body))
        return items

    sorted_pairs = sorted(
        results.pair_results,
        key=lambda p: _RISK_ORDER.get(p.overall_risk, 5),
    )

    for i, pair in enumerate(sorted_pairs, 1):
        pair_items = self._build_single_pair(i, pair, replace_refs)
        items.extend(pair_items)

    return items


def build_single_pair(
    self: "CollusionReportGenerator",
    idx: int,
    pair: "CollusionPairResult",
    replace_refs=None,
    section_prefix: str = "5.",
) -> list:
    """构建单对文件的详细分析"""
    items: list = []
    risk = pair.overall_risk
    risk_label = _RISK_LABELS.get(risk, "未知")
    risk_color = _RISK_COLORS.get(risk, colors.gray)
    mode_label = _MODE_LABELS.get(pair.analysis_mode or "", pair.analysis_mode or "")

    # 标题
    items.append(Paragraph(
        self._to_safe_paragraph_text(
            f"{section_prefix}{idx}  {pair.doc_a_name}  vs  {pair.doc_b_name}"
        ),
        self.s_h2,
    ))

    # 风险横幅
    sim_str = f"向量相似度: {pair.similarity_score:.3f}" if pair.similarity_score is not None else ""
    banner_parts = [f"风险等级: {risk_label}"]
    if sim_str:
        banner_parts.append(sim_str)
    banner_parts.append(f"分析模式: {mode_label}")
    banner_text = "  |  ".join(banner_parts)

    banner_data = [[Paragraph(
        self._to_safe_paragraph_text(banner_text),
        ParagraphStyle("BN", fontName=_FONT_NAME, fontSize=9,
                       textColor=colors.white, alignment=TA_CENTER),
    )]]
    banner_tbl = Table(banner_data, colWidths=[_PAGE_WIDTH])
    banner_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), risk_color),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    items.append(banner_tbl)
    items.append(Spacer(1, 3 * mm))

    # 维度得分表
    items += self._build_dimension_table(pair)

    # 证据发现
    items += self._build_findings(
        pair=pair,
        replace_refs=replace_refs,
        doc_a_id=pair.doc_a_id,
        doc_b_id=pair.doc_b_id,
        doc_a_name=pair.doc_a_name,
        doc_b_name=pair.doc_b_name,
    )

    # 关注项（不构成正式证据，含 negative_finding 反向陈述，履行 4.5.4 透明化）
    items += self._build_attention_section(pair)

    # 重合片段（向量预筛选高相似 chunk 原文，供人工甄别）
    items += self._build_evidence_chunks_section(pair)

    items.append(Spacer(1, 6 * mm))
    return items


def build_dimension_table(
    self: "CollusionReportGenerator",
    pair: "CollusionPairResult",
) -> list:
    """构建维度得分表"""
    items: list = []

    header = ["维度", "编号", "证据级别", "得分", "风险程度"]
    rows = [header]
    has_data = False

    for attr, (code, name) in _DIM_NAMES.items():
        score = getattr(pair, attr, None)
        if score is None:
            continue
        has_data = True
        tier = _DIM_TIERS.get(attr, "-")
        tier_label = {"I": "一级", "II": "二级", "III": "三级"}.get(tier, "-")
        level = _score_level(score)
        rows.append([name, code, tier_label, f"{score:.2f}", level])

    if not has_data:
        return items

    col_ws = [_PAGE_WIDTH * 0.30, _PAGE_WIDTH * 0.10,
               _PAGE_WIDTH * 0.18, _PAGE_WIDTH * 0.14, _PAGE_WIDTH * 0.14]
    # 补足精度差异
    remainder = _PAGE_WIDTH - sum(col_ws)
    col_ws[-1] += remainder

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
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _THEME_BORDER),
        ("BOX", (0, 0), (-1, -1), 0.5, _THEME_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, _THEME_BG_ALT]),
    ]

    # 得分列着色
    for row_idx in range(1, len(rows)):
        score_val = float(rows[row_idx][3])
        sc = _score_color(score_val)
        style_cmds.append(("TEXTCOLOR", (3, row_idx), (4, row_idx), sc))

    tbl.setStyle(TableStyle(style_cmds))
    items.append(tbl)
    items.append(Spacer(1, 3 * mm))
    return items


def build_findings(
    self: "CollusionReportGenerator",
    pair: "CollusionPairResult",
    replace_refs=None,
    doc_a_id: int | None = None,
    doc_b_id: int | None = None,
    doc_a_name: str = "",
    doc_b_name: str = "",
) -> list:
    """构建证据发现列表，按级别分组"""
    from app.core.collusion.analyzer_parts.dimension_signals import (
        is_hollow_dimension_signal,
    )

    # PDF 与结果页一致，过滤无可核对路径的空壳维度信号
    findings = [
        f for f in (pair.findings or [])
        if not is_hollow_dimension_signal(f)
    ]
    if not findings:
        return []

    items: list = [
        Paragraph(
            f"发现证据 (共 {len(findings)} 条)",
            self.s_h3,
        )
    ]

    # 按级别分组（仅识别对象属性 level；dict 型 finding 归入 unknown，与历史行为一致）
    grouped: dict[str, list] = {"first": [], "second": [], "third": [], "unknown": []}
    for f in findings:
        if hasattr(f, "level"):
            lvl = f.level if f.level in grouped else "unknown"
        else:
            lvl = "unknown"
        grouped[lvl].append(f)

    items += _render_findings_by_level(
        grouped,
        style_name="LH",
        render_one=lambda fi_idx, f: self._build_single_finding(
            idx=fi_idx,
            finding=f,
            replace_refs=replace_refs,
            doc_a_id=doc_a_id,
            doc_b_id=doc_b_id,
            doc_a_name=doc_a_name,
            doc_b_name=doc_b_name,
        ),
    )

    return items


def build_single_finding(
    self: "CollusionReportGenerator",
    idx: int,
    finding,
    replace_refs=None,
    doc_a_id: int | None = None,
    doc_b_id: int | None = None,
    doc_a_name: str = "文档1",
    doc_b_name: str = "文档2",
) -> list:
    """构建单条证据发现"""
    items: list = []

    if hasattr(finding, "description"):
        desc = replace_refs(finding.description) if replace_refs else finding.description
    elif hasattr(finding, "summary"):
        desc = replace_refs(finding.summary) if replace_refs else finding.summary
    elif isinstance(finding, str):
        desc = replace_refs(finding) if replace_refs else finding
    else:
        desc = str(finding)
    desc = _replace_legacy_pair_aliases(desc, doc_a_name, doc_b_name)

    dim_tag = ""
    if hasattr(finding, "dimension") and finding.dimension:
        dim_tag = f"[{finding.dimension}] "

    certainty_tag = ""
    if hasattr(finding, "certainty") and finding.certainty is not None and finding.certainty > 0.05:
        certainty_tag = f" (确定性: {int(finding.certainty * 100)}%)"

    items.append(Paragraph(
        self._to_safe_paragraph_text(
            f"{idx}. {dim_tag}{desc}{certainty_tag}"
        ),
        self.s_body_indent,
    ))

    # 新结构 evidences（优先）
    evidences = getattr(finding, "evidences", None) or []
    for ev in evidences:
        ev_doc_name = getattr(ev, "doc_name", None)
        ev_doc_id = getattr(ev, "doc_id", None)
        ev_quote = getattr(ev, "quote", None)
        ev_location = getattr(ev, "location", None)

        if isinstance(ev, dict):
            ev_doc_name = ev.get("doc_name")
            ev_doc_id = ev.get("doc_id")
            ev_quote = ev.get("quote")
            ev_location = ev.get("location")

        if not ev_quote and not ev_location:
            continue

        if not ev_doc_name:
            if ev_doc_id == doc_a_id:
                ev_doc_name = doc_a_name
            elif ev_doc_id == doc_b_id:
                ev_doc_name = doc_b_name
            else:
                ev_doc_name = f"文档{ev_doc_id}" if ev_doc_id is not None else "文档"

        quote_text = replace_refs(ev_quote) if (replace_refs and ev_quote) else (ev_quote or "")
        quote_text = _replace_legacy_pair_aliases(quote_text, doc_a_name, doc_b_name)
        loc = f" [{ev_location}]" if ev_location else ""
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"{ev_doc_name}{loc}: {quote_text}"),
            self.s_quote,
        ))

    # 旧字段回退（兼容历史数据）
    quote_a = getattr(finding, "quote_a", None)
    page_a = getattr(finding, "page_a", None)
    if quote_a and not evidences:
        loc_a = f" [{page_a}]" if page_a else ""
        qa_text = replace_refs(quote_a) if replace_refs else quote_a
        qa_text = _replace_legacy_pair_aliases(qa_text, doc_a_name, doc_b_name)
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"{doc_a_name}{loc_a}: {qa_text}"),
            self.s_quote,
        ))

    # 文件B引用
    quote_b = getattr(finding, "quote_b", None)
    page_b = getattr(finding, "page_b", None)
    if quote_b and not evidences:
        loc_b = f" [{page_b}]" if page_b else ""
        qb_text = replace_refs(quote_b) if replace_refs else quote_b
        qb_text = _replace_legacy_pair_aliases(qb_text, doc_a_name, doc_b_name)
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"{doc_b_name}{loc_b}: {qb_text}"),
            self.s_quote,
        ))

    # 兼容旧字段 evidence
    evidence = getattr(finding, "evidence", None)
    if evidence and not quote_a and not quote_b:
        ev_text = replace_refs(evidence) if replace_refs else evidence
        ev_text = _replace_legacy_pair_aliases(ev_text, doc_a_name, doc_b_name)
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"原文摘录: {ev_text}"),
            self.s_quote,
        ))

    # 详细分析
    detail = getattr(finding, "detail", None)
    if detail and detail != desc:
        dt_text = replace_refs(detail) if replace_refs else detail
        dt_text = _replace_legacy_pair_aliases(dt_text, doc_a_name, doc_b_name)
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"分析: {dt_text}"),
            ParagraphStyle("DT", parent=self.s_small,
                           leftIndent=16, textColor=_THEME_TEXT_LIGHT),
        ))

    # 跨字段串联命中（cross_field_correlator）：规则/来源字段/法规对照
    # 与前端 FindingDetail 跨字段卡对齐，履行 collusion.mdc 4.5.2 透传要求
    risk_pattern = getattr(finding, "risk_pattern", None)
    source_field_a = getattr(finding, "source_field_a", None)
    source_field_b = getattr(finding, "source_field_b", None)
    legal_reference = getattr(finding, "legal_reference", None)
    cross_parts: list[str] = []
    if risk_pattern:
        cross_parts.append(f"规则: {risk_pattern}")
    if source_field_a or source_field_b:
        cross_parts.append(f"来源字段: {source_field_a or '-'} / {source_field_b or '-'}")
    if legal_reference:
        cross_parts.append(f"法规对照: {legal_reference}")
    if cross_parts:
        items.append(Paragraph(
            self._to_safe_paragraph_text(" | ".join(cross_parts)),
            ParagraphStyle("FCrossField", parent=self.s_small,
                           leftIndent=16, textColor=_THEME_TEXT_LIGHT),
        ))

    return items


# ---------------------------------------------------------------------------
# 关注项 / 重合片段（pair 详情补充板块，与前端 PairDetail 对齐）
# ---------------------------------------------------------------------------

_ATTENTION_CATEGORY_LABELS = {
    "business_relationship": "正常业务往来",
    "unverified_quote": "部分可验证引用",
    "metadata_note": "元数据备注",
    "negative_finding": "反向陈述(不构成证据)",
}


def build_attention_section(
    self: "CollusionReportGenerator",
    pair: "CollusionPairResult",
) -> list:
    """关注项小节：不构成正式证据但值得注意的发现（含反向陈述 negative_finding）"""
    attention_items = getattr(pair, "attention_items", None) or []
    if not attention_items:
        return []

    items: list = [Paragraph(
        f"关注项 (共 {len(attention_items)} 条，不作为正式定性证据)",
        self.s_h3,
    )]
    for ai_idx, ai in enumerate(attention_items, 1):
        category = getattr(ai, "category", "") or ""
        dimension = getattr(ai, "dimension", "") or ""
        summary = getattr(ai, "summary", "") or ""
        detail = getattr(ai, "detail", None)
        source_reason = getattr(ai, "source_reason", "") or ""

        cat_label = _ATTENTION_CATEGORY_LABELS.get(category, category or "关注")
        dim_tag = f"[{dimension}] " if dimension else ""
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"{ai_idx}. {dim_tag}{cat_label}: {summary}"),
            self.s_body_indent,
        ))
        if detail:
            items.append(Paragraph(
                self._to_safe_paragraph_text(f"说明: {detail}"),
                ParagraphStyle("AIDetail", parent=self.s_small,
                               leftIndent=16, textColor=_THEME_TEXT_LIGHT),
            ))
        if source_reason:
            items.append(Paragraph(
                self._to_safe_paragraph_text(f"列为关注项原因: {source_reason}"),
                ParagraphStyle("AIReason", parent=self.s_small,
                               leftIndent=16, textColor=_THEME_TEXT_MUTED),
            ))
    items.append(Spacer(1, 3 * mm))
    return items


def build_evidence_chunks_section(
    self: "CollusionReportGenerator",
    pair: "CollusionPairResult",
) -> list:
    """重合片段小节：向量预筛选出的高相似 chunk 对原文（标注疑似通用模板/白名单）"""
    chunks = getattr(pair, "evidence_chunks", None) or []
    if not chunks:
        return []

    def _trunc(t: object, n: int = 220) -> str:
        s = str(t or "")
        return s if len(s) <= n else s[:n] + "..."

    sorted_chunks = sorted(
        chunks, key=lambda c: getattr(c, "score", None) or 0.0, reverse=True
    )[:8]

    items: list = [Paragraph(
        f"重合片段 (共 {len(chunks)} 处，展示相似度最高 {len(sorted_chunks)} 处)",
        self.s_h3,
    )]
    for ci, c in enumerate(sorted_chunks, 1):
        text_a = getattr(c, "text_a", "") or ""
        text_b = getattr(c, "text_b", "") or ""
        page_a = getattr(c, "page_a", None)
        page_b = getattr(c, "page_b", None)
        score = getattr(c, "score", None)
        boilerplate = getattr(c, "boilerplate_flag", False)
        kb_match = getattr(c, "kb_rule_match", False)

        tags: list[str] = []
        if score is not None:
            tags.append(f"相似度 {score:.2f}")
        if boilerplate:
            tags.append("疑似通用模板")
        if kb_match:
            tags.append("命中白名单")
        tag_str = f"  ({' | '.join(tags)})" if tags else ""
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"{ci}.{tag_str}"),
            self.s_body_indent,
        ))
        if text_a:
            loc_a = f" [P{page_a}]" if page_a else ""
            items.append(Paragraph(
                self._to_safe_paragraph_text(f"文件A{loc_a}: {_trunc(text_a)}"),
                self.s_quote,
            ))
        if text_b:
            loc_b = f" [P{page_b}]" if page_b else ""
            items.append(Paragraph(
                self._to_safe_paragraph_text(f"文件B{loc_b}: {_trunc(text_b)}"),
                self.s_quote,
            ))
    items.append(Spacer(1, 3 * mm))
    return items
