"""
职责: Q10 页面完整性检查——空白页、页面尺寸/方向离群
不做: 不做表格断裂、不做 OCR、不把扫描件/图片/转曲页当空白页
"""

from __future__ import annotations

import statistics

from app.core.document_qa.contracts import (
    CheckerOutcome,
    CheckerStatus,
    DocumentModel,
    Evidence,
    EvidenceItem,
    Finding,
    QaCategory,
    QaCertainty,
    QaSeverity,
)
from app.core.document_qa.evidence_geom import PAGE_BBOX

CODE = "page_integrity"

_MIN_PAGES_FOR_OUTLIER = 5
_SIZE_TOLERANCE_RATIO = 0.05
_MINORITY_RATIO = 0.2


def check_page_integrity(model: DocumentModel, rules: dict) -> CheckerOutcome:
    findings: list[Finding] = []
    pages = model.pages
    if not pages:
        return CheckerOutcome(code=CODE, status=CheckerStatus.SKIPPED)

    # 1) 空白页(无文本/无图片/无表格, 且版面也没看到页眉页脚页码以外的内容)
    blank_pages = [
        p.page_num for p in pages
        if p.char_count == 0 and p.image_count == 0 and p.table_count == 0
        and not p.image_only
    ]
    if blank_pages:
        pages_text = "、".join(str(p) for p in blank_pages[:10])
        findings.append(Finding(
            rule_code="DOC-PAGE-001",
            category=QaCategory.INTEGRITY,
            severity=QaSeverity.P2,
            certainty=QaCertainty.PROBABLE,
            title="空白页",
            message=f"第{pages_text}页无任何文本、图片或表格内容(共{len(blank_pages)}页)",
            suggestion="确认是否为有意留白(如分隔页), 否则删除或补充内容",
            evidence=Evidence(
                kind="sequence",
                items=tuple(
                    EvidenceItem(
                        excerpt=f"第{p}页为空白页", page=p, bbox=PAGE_BBOX,
                    )
                    for p in blank_pages[:6]
                ),
            ),
            anchor=f"blank_pages:{pages_text}",
            closed_measurement=True,
        ))

    if len(pages) >= _MIN_PAGES_FOR_OUTLIER:
        # 2) 方向离群(少数页与主流方向不一致)
        landscape = [p for p in pages if p.width > p.height > 0]
        portrait = [p for p in pages if p.height >= p.width > 0]
        minority = landscape if len(landscape) < len(portrait) else portrait
        majority_label = "纵向" if minority is landscape else "横向"
        minority_label = "横向" if minority is landscape else "纵向"
        if minority and len(minority) <= len(pages) * _MINORITY_RATIO:
            pages_text = "、".join(str(p.page_num) for p in minority[:8])
            findings.append(Finding(
                rule_code="DOC-PAGE-002",
                category=QaCategory.INTEGRITY,
                severity=QaSeverity.P3,
                certainty=QaCertainty.POSSIBLE,
                title="页面方向不一致",
                message=(
                    f"文档主体为{majority_label}页面, 第{pages_text}页为{minority_label}"
                    "(大表格/图纸横排属正常情况)"
                ),
                suggestion="确认这些页面方向是否符合预期",
                evidence=Evidence(
                    kind="sequence",
                    items=tuple(
                        EvidenceItem(
                            excerpt=f"第{p.page_num}页为{minority_label}",
                            page=p.page_num, bbox=PAGE_BBOX,
                        )
                        for p in minority[:6]
                    ),
                ),
                anchor="orientation_minority",
                closed_measurement=True,
            ))

        # 3) 纸张尺寸离群(同方向内与中位数偏差过大)
        for orient_pages, orient_label in ((portrait, "纵向"), (landscape, "横向")):
            if len(orient_pages) < _MIN_PAGES_FOR_OUTLIER:
                continue
            median_w = statistics.median(p.width for p in orient_pages)
            median_h = statistics.median(p.height for p in orient_pages)
            if median_w <= 0 or median_h <= 0:
                continue
            outliers = [
                p for p in orient_pages
                if abs(p.width - median_w) / median_w > _SIZE_TOLERANCE_RATIO
                or abs(p.height - median_h) / median_h > _SIZE_TOLERANCE_RATIO
            ]
            if outliers and len(outliers) <= len(orient_pages) * _MINORITY_RATIO:
                pages_text = "、".join(str(p.page_num) for p in outliers[:8])
                findings.append(Finding(
                    rule_code="DOC-PAGE-003",
                    category=QaCategory.INTEGRITY,
                    severity=QaSeverity.P3,
                    certainty=QaCertainty.POSSIBLE,
                    title="纸张尺寸不一致",
                    message=(
                        f"{orient_label}页面主体尺寸约 {median_w:.0f}x{median_h:.0f}pt, "
                        f"第{pages_text}页尺寸明显不同(可能为扫描件拼入或版面设置不一致)"
                    ),
                    suggestion="确认这些页面来源与版面设置",
                    evidence=Evidence(
                        kind="sequence",
                        items=tuple(
                            EvidenceItem(
                                excerpt=f"第{p.page_num}页 {p.width:.0f}x{p.height:.0f}pt",
                                page=p.page_num, bbox=PAGE_BBOX,
                            )
                            for p in outliers[:6]
                        ),
                    ),
                    anchor=f"size_outlier:{orient_label}",
                    closed_measurement=True,
                ))

    findings.extend(_low_resolution_findings(model))
    return CheckerOutcome(code=CODE, status=CheckerStatus.OK, findings=findings)


def _low_resolution_findings(model: DocumentModel) -> list[Finding]:
    """显示框大、像素尺寸小的图。小图标不报, 像素足够的大图不报。"""
    findings: list[Finding] = []
    for image in model.images:
        display_w = image.bbox[2] - image.bbox[0]
        display_h = image.bbox[3] - image.bbox[1]
        if display_w < 0.25 and display_h < 0.15:
            continue
        pixels = max(image.pixel_width, image.pixel_height)
        if pixels <= 0 or pixels > 160:
            continue
        findings.append(Finding(
            rule_code="DOC-PAGE-004",
            category=QaCategory.INTEGRITY,
            severity=QaSeverity.P3,
            certainty=QaCertainty.PROBABLE,
            title="低分辨率图片",
            message=(
                f"第{image.page}页图片显示尺寸较大, 但像素仅 "
                f"{image.pixel_width}x{image.pixel_height}"
            ),
            suggestion="换用原始分辨率图片, 避免放大模糊",
            evidence=Evidence(
                kind="single",
                items=(EvidenceItem(
                    excerpt=f"{image.pixel_width}x{image.pixel_height}",
                    page=image.page, bbox=image.bbox,
                ),),
            ),
            anchor=f"low_res:{image.page}:{image.pixel_width}x{image.pixel_height}",
            closed_measurement=True,
        ))
    return findings[:8]
