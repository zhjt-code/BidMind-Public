"""评分项逐条评审章节构建模块

职责: 构建投标审查 PDF 中的评分项逐条评审章节元素。
不做: 不查询数据库、不计算评分聚合、不执行审查判定。

 策略: 长文本（评审标准、逐规则判定、投标响应）全部以可分页 Paragraph
渲染，不嵌入不可分页的 Table 单元格。Table 仅存放短标识字段（序号/评分项名/满分/预估得分）。
"""

from typing import Any
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.platypus import PageBreak, Paragraph, Spacer, Table, TableStyle

from app.core.extractor.evidence_verdict import user_facing_response_text
from app.core.review_contracts import is_scoring_universe_member

_STATUS_LABEL: dict[str, str] = {
    "met": "达标",
    "partial": "部分达标",
    "not": "未达标",
    "unknown": "无法判定",
}
_VOLUME_LABELS: dict[str, str] = {
    "technical": "技术标",
    "price": "价格",
    "commercial": "商务标",
    "service": "服务",
    "qualification": "资格",
    "staffing": "人员",
    "performance": "业绩",
    "other": "其他",
    "unspecified": "其他",
}

_MAX_RESPONSE_CHARS = 400
_MAX_RULES_SHOWN = 5


def _safe_text(text: object) -> str:
    """将业务文本转换为 ReportLab Paragraph 安全字符串。"""
    if text is None:
        return ""
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return xml_escape(normalized).replace("\n", "<br/>")


def _filter_scoring_items(results: list[dict]) -> list[dict]:
    """过滤评分项全集: 与 is_scoring_universe_member / 结果页 isScoringUniverseMember 同一口径。"""
    return [
        r for r in results
        if is_scoring_universe_member(
            requirement_kind=r.get("requirement_kind"),
            is_key_review=r.get("is_key_review"),
            max_score=r.get("max_score"),
        )
    ]


def _group_by_lot_volume(items: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """按 (lot, volume) 分组，未设置时以空串作 key，保证无组标题的情况也正常渲染。"""
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        key = (item.get("lot") or "", item.get("volume_label") or item.get("volume") or "")
        groups.setdefault(key, []).append(item)
    return groups


def _volume_label(volume: object) -> str:
    """评分卷标识转用户可读中文名，未知值原样保留。"""
    key = str(volume or "").strip().lower()
    return _VOLUME_LABELS.get(key, str(volume or "").strip())


def _render_rule_assessments_flow(
    assessments: list[dict] | None,
    comment: str | None,
    styles: Any,
) -> list:
    """将逐规则判定渲染为可分页 Paragraph 列表。: 这些元素在 Table 之外独立渲染，支持跨页分页。
 老数据兜底: assessments 为 None/空时用 comment 补充。
 """
    flow: list = []
    if assessments:
        for ra in assessments:
            excerpt = ra.get("rule_excerpt", "")
            raw_status = ra.get("status", "unknown")
            status_label = _STATUS_LABEL.get(raw_status, raw_status)
            reason = ra.get("reason", "")
            points = ra.get("points")
            pts_text = f" | 预估得分: {points}" if points is not None else ""
            combined = f"[{status_label}]{pts_text}  {excerpt} → {reason}"
            flow.append(Paragraph(_safe_text(combined), styles["ChineseSmall"]))
    elif comment:
        flow.append(
            Paragraph(_safe_text(f"审查意见: {comment}"), styles["ChineseSmall"])
        )
    return flow


def _render_single_scoring_item_elements(
    idx: int,
    item: dict,
    font_name: str,
    styles: Any,
) -> list:
    """渲染单个评分项的所有 PDF 元素（短表 + 可分页段落）。

    Table 只放短字段（序号/评分项名/满分/预估得分），长文本置于 Table 之外。
    """
    elements: list = []

    name = item.get("score_item_name") or item.get("requirement_summary") or f"评分项 #{idx}"
    est = item.get("estimated_score")
    max_s = item.get("max_score")
    est_text = f"{est:.1f}" if est is not None else "待评"
    max_text = f"{max_s:.1f}" if max_s is not None else "—"

    # 短标题行表格（仅存放短字段，保证不会因长文本导致单行过高）
    header_data = [
        ["#", "评分项名称", "满分", "预估得分"],
        [
            str(idx),
            Paragraph(_safe_text(name), styles["ChineseBody"]),
            max_text,
            est_text,
        ],
    ]
    header_table = Table(header_data, colWidths=[22, 286, 60, 62])
    header_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F4FD")),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(header_table)

    # 评审标准摘要（长规则文本以 Paragraph 渲染，不入表格单元格）
    score_rules: list = item.get("score_rules") or []
    deduction_rules: list = item.get("deduction_rules") or []
    all_rules = (
        [("得分", r) for r in score_rules[:_MAX_RULES_SHOWN]]
        + [("扣分", r) for r in deduction_rules[: max(0, _MAX_RULES_SHOWN - len(score_rules))]]
    )
    if all_rules:
        elements.append(Paragraph("评审标准摘要:", styles["ChineseSmall"]))
        for rule_type, rule in all_rules:
            rule_text = (
                rule.get("rule_desc", rule) if isinstance(rule, dict) else str(rule)
            )
            elements.append(
                Paragraph(
                    _safe_text(f"  [{rule_type}] {rule_text}"),
                    styles["ChineseSmall"],
                )
            )

    # 逐规则判定摘要（可分页 Paragraph，不入表格）
    assessments = item.get("rule_assessments")
    comment = item.get("comment")
    assessment_flow = _render_rule_assessments_flow(assessments, comment, styles)
    if assessment_flow:
        elements.append(Paragraph("逐规则判定摘要:", styles["ChineseSmall"]))
        elements.extend(assessment_flow)

    # 投标响应摘要（截断后以 Paragraph 渲染，不入表格）
    response = user_facing_response_text(
        content=item.get("response_content"),
        summary=item.get("response_summary"),
    )
    if response:
        truncated = response[:_MAX_RESPONSE_CHARS]
        if len(response) > _MAX_RESPONSE_CHARS:
            truncated += "…"
        elements.append(Paragraph("投标响应摘要:", styles["ChineseSmall"]))
        elements.append(Paragraph(_safe_text(truncated), styles["ChineseBody"]))

    elements.append(Spacer(1, 6))
    return elements


def build_scoring_detail_story(
    results: list[dict],
    font_name: str,
    styles: Any,
) -> list:
    """构建评分项逐条评审章节的全部 Story 元素。

    参数:
        results:   审查结果 dict 列表（来自 generator 的 sorted_results）。
        font_name: ReportLab 注册字体名称（由 generator 传入以保持一致）。
        styles:    ReportLab StyleSheet1（含 ChineseHeading/ChineseBody/ChineseSmall）。

    返回:
        list: ReportLab Flowable 列表，无评分项时返回空列表。
    """
    scoring_items = _filter_scoring_items(results)
    if not scoring_items:
        return []

    story: list = [
        PageBreak(),
        Paragraph("评分项逐条评审", styles["ChineseHeading"]),
        Paragraph(
            "注: 本节逐项展示评分项的规则判定情况，仅供评标参考，不替代专家评分。",
            styles["ChineseSmall"],
        ),
        Spacer(1, 8),
    ]

    groups = _group_by_lot_volume(scoring_items)

    global_idx = 1
    for (lot, volume), items in sorted(groups.items()):
        # 输出分组标题（lot/volume 均为空则为无分组模式，不渲染标题）
        label_parts: list[str] = []
        if lot:
            label_parts.append(f"采购包: {lot}")
        if volume:
            label_parts.append(f"卷: {_volume_label(volume)}")
        if label_parts:
            story.append(
                Paragraph("  |  ".join(label_parts), styles["ChineseHeading"])
            )

        for item in items:
            story.extend(
                _render_single_scoring_item_elements(
                    global_idx, item, font_name, styles
                )
            )
            global_idx += 1

    return story
