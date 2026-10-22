"""
职责: PDF 审查报告的"文档质检"独立章节构建(汇总表 + 按类别分组明细)
不做: 不读数据库、不参与响应审查评分/结论、不修改 generator 其他章节、
      不把本章节的构建异常抛给报告生成(异常时本章节降级为一句说明)
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, Spacer, Table, TableStyle

from app.core.bid_envelope import BID_PART_LABELS

_CATEGORY_LABELS = {
    "structure": "结构",
    "toc": "目录",
    "layout": "版式",
    "language": "语言",
    "duplication": "重复",
    "numbering": "编号",
    "consistency": "一致性",
    "reference": "引用",
    "hygiene": "文档卫生",
    "integrity": "页面完整性",
}
_SEVERITY_LABELS = {"P0": "严重", "P1": "高", "P2": "中", "P3": "低"}
_DISPOSITION_LABELS = {
    "pending": "待处理",
    "confirmed": "确认问题",
    "false_positive": "误报",
    "ignored": "已忽略",
}
_MAX_REPORT_FINDINGS = 100
_FALLBACK_TEXT = "文档质检章节生成失败，不影响本报告其余内容，请在系统结果页查看。"


def _format_evidence_pages(evidence: dict[str, Any]) -> str:
    """证据页码: 分卷带卷名(商务标 P3；技术标 P12), 整本仅页码。"""
    grouped: dict[Optional[str], list[int]] = {}
    for item in (evidence or {}).get("items", []):
        page = item.get("page")
        if page is None:
            continue
        part = item.get("bid_part")
        pages = grouped.setdefault(part, [])
        if page not in pages:
            pages.append(page)
    if not grouped:
        return "-"
    segments: list[str] = []
    for part, pages in grouped.items():
        pages_text = "、".join(f"P{p}" for p in pages[:6])
        if part:
            segments.append(f"{BID_PART_LABELS.get(part, part)} {pages_text}")
        else:
            segments.append(pages_text)
    return "；".join(segments)


def build_document_qa_story(
    payload: Optional[dict[str, Any]],
    styles: dict,
    *,
    safe_text=lambda text: text,
) -> list:
    """构建文档质检章节 story 元素列表。

    payload 为 None 表示无有效质检运行, 章节输出"未执行"说明而非静默省略。
    """
    story: list = [PageBreak(), Paragraph("文档质检", styles["ChineseHeading"])]
    story.append(Paragraph(
        safe_text(
            "以下为投标文件内生质量检查结果(结构/目录/编号/引用/一致性等), "
            "仅反映文档编制质量, 不构成对招标要求符合性的判定。"
        ),
        styles["ChineseSmall"],
    ))
    story.append(Spacer(1, 8))

    if not payload:
        story.append(Paragraph(
            safe_text("本项目暂无有效的文档质检结果(未执行或执行失败)。"),
            styles["ChineseBody"],
        ))
        return story

    try:
        story.extend(_payload_story(payload, styles, safe_text))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 报告章节生成失败(本章节降级, 不影响报告): {exc}")
        story.append(Paragraph(safe_text(_FALLBACK_TEXT), styles["ChineseBody"]))
    return story


def _payload_story(payload: dict[str, Any], styles: dict, safe_text) -> list:
    """汇总表与按类别明细; 在独立列表里构建, 失败时调用方整段丢弃。"""
    story: list = []
    scan = payload.get("scan") or {}
    summary = payload.get("summary") or {}
    findings = list(payload.get("findings") or [])

    severity_counts = summary.get("by_severity") or {}
    disposition_counts = summary.get("by_disposition") or {}
    summary_rows = [
        ["指标", "数值"],
        ["发现问题总数", str(summary.get("total", 0))],
        ["严重(P0)/高(P1)", f"{severity_counts.get('P0', 0)} / {severity_counts.get('P1', 0)}"],
        ["中(P2)/低(P3)", f"{severity_counts.get('P2', 0)} / {severity_counts.get('P3', 0)}"],
        ["待处理", str(disposition_counts.get("pending", 0))],
        ["已确认问题", str(disposition_counts.get("confirmed", 0))],
        ["质检状态", str(scan.get("status") or "-")],
    ]
    summary_table = Table(summary_rows, colWidths=[60 * mm, 100 * mm])
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), styles["ChineseBody"].fontName),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 10))

    if not findings:
        story.append(Paragraph(
            safe_text("本次质检未发现问题。"), styles["ChineseBody"],
        ))
        return story

    # 按类别分组, 组内保持严重度排序(载荷已按 severity 排序)
    by_category: dict[str, list[dict[str, Any]]] = {}
    for finding in findings[:_MAX_REPORT_FINDINGS]:
        by_category.setdefault(str(finding.get("category") or ""), []).append(finding)

    for category, items in by_category.items():
        label = _CATEGORY_LABELS.get(category, category)
        story.append(Paragraph(
            safe_text(f"{label}({len(items)}项)"), styles["ChineseHeading"],
        ))
        for finding in items:
            severity = _SEVERITY_LABELS.get(str(finding.get("severity")), "-")
            disposition = _DISPOSITION_LABELS.get(str(finding.get("disposition")), "-")
            pages_text = _format_evidence_pages(finding.get("evidence") or {})
            story.append(Paragraph(
                safe_text(
                    f"[{severity}] {finding.get('title', '')}: {finding.get('message', '')}"
                    f"（位置: {pages_text}；处置: {disposition}）"
                ),
                styles["ChineseBody"],
            ))
            story.append(Spacer(1, 4))
        story.append(Spacer(1, 6))

    if len(findings) > _MAX_REPORT_FINDINGS:
        story.append(Paragraph(
            safe_text(
                f"其余 {len(findings) - _MAX_REPORT_FINDINGS} 项详见系统结果页。"
            ),
            styles["ChineseSmall"],
        ))
    return story
