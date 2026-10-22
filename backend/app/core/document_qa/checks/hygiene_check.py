"""
职责: Q9 文档卫生检查——模板占位残留(正文与表格)、乱码/缺字符号、PDF 批注残留
不做: 不扫描词表以外的错别字、不做重复检测、不读页眉页脚/公式/图片区
"""

from __future__ import annotations

import re

from app.core.document_qa.author_spans import grid_line_texts, prose_spans
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

CODE = "hygiene"

_MAX_PER_PATTERN = 5
_MAX_TOTAL = 20


def _hygiene_texts(model: DocumentModel) -> list[tuple[int, str]]:
    """正文跨度、网格单行和题注。不把同一视觉行的两个单元格黏成一个匹配。"""
    from app.core.document_qa.author_spans import caption_lines

    texts = [(span.page, span.text) for span in prose_spans(model)]
    texts.extend(grid_line_texts(model))
    if model.regions:
        texts.extend(caption_lines(model))
    return texts


def check_hygiene(model: DocumentModel, rules: dict) -> CheckerOutcome:
    config = rules.get("hygiene") or {}
    findings: list[Finding] = []
    units = _hygiene_texts(model)

    # 1) 占位符/模板残留(表格单元格里留下的占位同样是缺陷)
    for spec in config.get("placeholder_patterns", []):
        pattern = re.compile(str(spec.get("pattern") or ""))
        label = str(spec.get("label") or "模板残留")
        hits = 0
        for page_num, paragraph in units:
            matched = pattern.search(paragraph)
            if not matched:
                continue
            start = max(0, matched.start() - 15)
            excerpt = paragraph[start:matched.end() + 15]
            findings.append(Finding(
                rule_code="DOC-HYG-001",
                category=QaCategory.HYGIENE,
                severity=QaSeverity.P2,
                certainty=QaCertainty.PROBABLE,
                title=label,
                message=f"第{page_num}页发现{label}「{matched.group(0)}」",
                suggestion="替换为正式内容后再提交",
                evidence=Evidence(
                    kind="single",
                    items=(EvidenceItem(excerpt=excerpt, page=page_num),),
                ),
                anchor=f"hygiene:{label}:{matched.group(0)}:{page_num}",
                closed_measurement=True,
            ))
            hits += 1
            if hits >= _MAX_PER_PATTERN:
                break

    # 2) 乱码/缺字符号连串。按跨度或单行, 不把相邻区域的符号拼成一串
    garbled = config.get("garbled") or {}
    chars = "".join(garbled.get("chars") or [])
    min_run = int(garbled.get("min_run") or 2)
    if chars:
        garbled_re = re.compile(f"[{re.escape(chars)}]{{{min_run},}}")
        hits = 0
        for page_num, text in units:
            for matched in garbled_re.finditer(text or ""):
                start = max(0, matched.start() - 15)
                findings.append(Finding(
                    rule_code="DOC-HYG-002",
                    category=QaCategory.HYGIENE,
                    severity=QaSeverity.P2,
                    certainty=QaCertainty.PROBABLE,
                    title=str(garbled.get("label") or "疑似乱码"),
                    message=f"第{page_num}页出现连续乱码/缺字符号「{matched.group(0)[:10]}」",
                    suggestion="检查原文件字体嵌入或该处内容是否损坏",
                    evidence=Evidence(
                        kind="single",
                        items=(EvidenceItem(
                            excerpt=text[start:matched.end() + 15], page=page_num,
                        ),),
                    ),
                    anchor=f"garbled:{page_num}:{matched.start()}",
                    closed_measurement=True,
                ))
                hits += 1
                if hits >= _MAX_PER_PATTERN:
                    break
            if hits >= _MAX_PER_PATTERN:
                break

    # 2b) 配置词表中的确定性错词
    for item in config.get("typos") or []:
        wrong = str(item.get("wrong") or "")
        right = str(item.get("right") or "")
        if not wrong:
            continue
        hits = 0
        for page_num, paragraph in units:
            index = paragraph.find(wrong)
            if index < 0:
                continue
            start = max(0, index - 10)
            excerpt = paragraph[start:index + len(wrong) + 10]
            findings.append(Finding(
                rule_code="DOC-HYG-004",
                category=QaCategory.HYGIENE,
                severity=QaSeverity.P2,
                certainty=QaCertainty.PROBABLE,
                title="确定性错词",
                message=f"第{page_num}页出现「{wrong}」, 通常应写为「{right or '正确用字'}」",
                suggestion="按规范用字替换后再提交",
                evidence=Evidence(
                    kind="single",
                    items=(EvidenceItem(excerpt=excerpt, page=page_num),),
                ),
                anchor=f"typo:{wrong}:{page_num}",
                closed_measurement=True,
            ))
            hits += 1
            if hits >= _MAX_PER_PATTERN:
                break

    # 3) PDF 批注残留。批注列表本身就是完整对象
    annotated_pages = [p for p in model.pages if p.annotation_texts]
    if annotated_pages:
        sample = annotated_pages[0]
        pages_text = "、".join(str(p.page_num) for p in annotated_pages[:8])
        findings.append(Finding(
            rule_code="DOC-HYG-003",
            category=QaCategory.HYGIENE,
            severity=QaSeverity.P2,
            certainty=QaCertainty.CERTAIN,
            title="PDF 批注残留",
            message=(
                f"第{pages_text}页存在 PDF 批注/便签(共{sum(len(p.annotation_texts) for p in annotated_pages)}条), "
                f"如「{sample.annotation_texts[0][:30]}」"
            ),
            suggestion="正式提交前删除内部批注",
            evidence=Evidence(
                kind="sequence",
                items=tuple(
                    EvidenceItem(
                        excerpt=(p.annotation_texts[0][:50] or "批注"),
                        page=p.page_num, bbox=PAGE_BBOX,
                    )
                    for p in annotated_pages[:6]
                ),
            ),
            anchor="annotations",
            closed_measurement=True,
        ))

    return CheckerOutcome(
        code=CODE, status=CheckerStatus.OK, findings=findings[:_MAX_TOTAL],
    )
