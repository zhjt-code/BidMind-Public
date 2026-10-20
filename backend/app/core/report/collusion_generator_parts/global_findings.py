"""R2-7 全局发现主题：build_global_findings_grouped

迁出自 `CollusionReportGenerator._build_global_findings_grouped`（R2-7 Step 4）。

依赖:
- `_THEME_TEXT_LIGHT` / `_THEME_TEXT_MUTED` / `_FONT_NAME` / `_PAGE_WIDTH`（主模块常量）
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
    _PAGE_WIDTH,
    _THEME_TEXT_LIGHT,
    _THEME_TEXT_MUTED,
)

if TYPE_CHECKING:
    from app.schemas.collusion import CollusionResults

    from app.core.report.collusion_generator import CollusionReportGenerator


def build_global_findings_grouped(
    self: "CollusionReportGenerator",
    results: "CollusionResults",
) -> list:
    """五、全局特征发现 (按类型分组，含涉及文档名)"""
    findings = results.global_findings or []
    if not findings:
        return []

    items: list = [Paragraph("五、全局特征发现", self.s_h1)]
    items.append(Spacer(1, 3 * mm))

    _TYPE_META = {
        "personnel_overlap": ("人员重叠", colors.HexColor("#D9534F")),
        "company_relation": ("企业关联", colors.HexColor("#E67E22")),
        "pricing_pattern": ("报价异常", colors.HexColor("#F0AD4E")),
        "metadata_cluster": ("元数据聚类", colors.HexColor("#5BC0DE")),
        "cross_field_correlation": ("跨字段串联", colors.HexColor("#8E44AD")),
    }

    grouped: dict[str, list] = {}
    for f in findings:
        grouped.setdefault(f.type, []).append(f)

    type_order = [
        "personnel_overlap", "company_relation",
        "pricing_pattern", "metadata_cluster",
        "cross_field_correlation",
    ]
    for type_key in type_order:
        type_findings = grouped.pop(type_key, None)
        if not type_findings:
            continue

        label, color = _TYPE_META.get(
            type_key, (type_key, _THEME_TEXT_MUTED))

        type_header = [[Paragraph(
            f"{label} -- {len(type_findings)} 条发现",
            ParagraphStyle("GFH", fontName=_FONT_NAME, fontSize=9,
                           textColor=colors.white),
        )]]
        type_tbl = Table(type_header, colWidths=[_PAGE_WIDTH])
        type_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), color),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        items.append(type_tbl)

        for fi_idx, f in enumerate(type_findings, 1):
            involved = "\u3001".join(
                f.involved_names) if f.involved_names else ""
            involved_tag = f" [涉及: {involved}]" if involved else ""
            items.append(Paragraph(
                self._to_safe_paragraph_text(
                    f"{fi_idx}. {f.description}{involved_tag}"
                ),
                self.s_body_indent,
            ))
            # 跨字段命中追加法规对照行
            if type_key == "cross_field_correlation":
                legal_ref = getattr(f, "legal_reference", None)
                risk_pattern = getattr(f, "risk_pattern", None)
                extra_parts: list[str] = []
                if risk_pattern:
                    extra_parts.append(f"规则: {risk_pattern}")
                if legal_ref:
                    extra_parts.append(f"法规对照: {legal_ref}")
                if extra_parts:
                    items.append(Paragraph(
                        self._to_safe_paragraph_text(" | ".join(extra_parts)),
                        ParagraphStyle(
                            "GFCrossField",
                            parent=self.s_small,
                            leftIndent=24,
                            textColor=_THEME_TEXT_LIGHT,
                        ),
                    ))

        items.append(Spacer(1, 3 * mm))

    for type_key, type_findings in grouped.items():
        items.append(Paragraph(
            self._to_safe_paragraph_text(f"{type_key} ({len(type_findings)} 条)"),
            self.s_body,
        ))
        for fi_idx, f in enumerate(type_findings, 1):
            items.append(Paragraph(
                self._to_safe_paragraph_text(f"{fi_idx}. {f.description}"),
                self.s_body_indent,
            ))
        items.append(Spacer(1, 3 * mm))

    items.append(Spacer(1, 5 * mm))
    return items
