"""R2-7 封面主题：build_cover

迁出自 `CollusionReportGenerator._build_cover`（R2-7 Step 3）。

依赖:
- `_RISK_LABELS` / `_RISK_COLORS` / `_FONT_NAME` / `_PAGE_WIDTH`（主模块常量）
- 主类 `self.s_*` 样式族 + `self._to_safe_paragraph_text`

datetime 兼容性: 主模块 `from datetime import datetime` 在测试期可被 monkeypatch
冻结，子模块通过函数内 lazy import `from app.core.report.collusion_generator
import datetime as _datetime` 取主模块命名空间引用，保证 monkeypatch.setattr(
collusion_generator, "datetime", ...) 生效。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from app.core.report.collusion_generator import (
    _FONT_NAME,
    _PAGE_WIDTH,
    _RISK_COLORS,
    _RISK_LABELS,
)

if TYPE_CHECKING:
    from app.models.project import Project
    from app.schemas.collusion import CollusionResults

    from app.core.report.collusion_generator import CollusionReportGenerator


def build_cover(
    self: "CollusionReportGenerator",
    project: "Project",
    results: "CollusionResults",
    replace_refs=None,
) -> list:
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
    mode_text = "含招标文件参照" if results.has_tender_doc else "无招标文件参照"
    # lazy import 取主模块命名空间的 datetime 引用,保证 monkey patch 透明生效
    from app.core.report.collusion_generator import datetime as _datetime  # noqa: PLC0415
    now = _datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    items: list = [
        Spacer(1, 40 * mm),
        Paragraph("串标审查报告", self.s_title),
        Spacer(1, 6 * mm),
        Paragraph(
            self._to_safe_paragraph_text(f"项目: {project.name}"),
            self.s_subtitle,
        ),
        Spacer(1, 3 * mm),
        Paragraph(self._to_safe_paragraph_text(f"生成时间: {now}"), self.s_meta),
        Paragraph(
            self._to_safe_paragraph_text(f"分析模式: {mode_text}"),
            self.s_meta,
        ),
        Spacer(1, 15 * mm),
    ]

    # 风险结论大色块
    risk_data = [[Paragraph(
        f"<b>项目级结论: {decision_label}</b>",
        ParagraphStyle("RB", fontName=_FONT_NAME, fontSize=18,
                       textColor=colors.white, alignment=TA_CENTER),
    )]]
    risk_tbl = Table(risk_data, colWidths=[_PAGE_WIDTH])
    risk_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), risk_color),
        ("BOX", (0, 0), (-1, -1), 0, colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 16),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    items.append(risk_tbl)

    # 结论摘要
    summary_text = getattr(results, "narrative_summary", None) or results.synthesis_summary
    if summary_text:
        text = replace_refs(summary_text) if replace_refs else summary_text
        items.append(Spacer(1, 10 * mm))
        items.append(Paragraph("结论摘要", self.s_h2))
        items.append(Paragraph(self._to_safe_paragraph_text(text), self.s_body))

    return items
