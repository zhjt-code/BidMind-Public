"""
职责: Q2 目录检查——目录条目与正文标题的存在性/顺序/标注页码一致性
不做: 不重建目录、不检查编号连续性
"""

from __future__ import annotations

from typing import Optional

from rapidfuzz import fuzz

from app.core.document_qa.contracts import (
    CheckerOutcome,
    CheckerStatus,
    DocumentModel,
    Evidence,
    EvidenceItem,
    Finding,
    QaCategory,
    QaCertainty,
    QaHeading,
    QaSeverity,
    QaTocEntry,
)
from app.core.parser.page_coordinates import normalize_toc_title, strip_toc_numbering

CODE = "toc"

_MIN_TOC_ENTRIES = 3       # 目录条目过少视为未识别到真实目录, 整检查跳过
_MIN_HEADINGS_FOR_MISS = 3  # 正文标题过少时不做"目录有正文无"(标题召回不足会误报)
_FUZZ_THRESHOLD = 85
_MAX_FINDINGS_PER_RULE = 8


def _norm(title: str) -> str:
    return strip_toc_numbering(normalize_toc_title(title))


def _truncation_overlap(entry_norm: str, candidates: set[str]) -> int:
    """折行后只剩前缀时仍是同一标题。短于 4 字的片段不去抢匹配。返回较短一侧的长度。"""
    best = 0
    for candidate in candidates:
        short, long = (entry_norm, candidate) if len(entry_norm) <= len(candidate) else (candidate, entry_norm)
        if len(short) >= 4 and len(long) - len(short) <= 16 and long.startswith(short):
            best = max(best, len(short))
    return best


def _match_heading(
    entry: QaTocEntry,
    headings: list[QaHeading],
) -> Optional[QaHeading]:
    """三级匹配: 原文相等 -> 归一化相等 -> 归一化模糊(>=阈值)。

    标题同时按 display(含编号)与 title(不含编号)比较: 目录条目常不带
    正文编号前缀("投标函" vs "一 投标函"), 只比 display 会漏配。
    """
    entry_norm = _norm(entry.title)
    if not entry_norm:
        return None
    best: Optional[QaHeading] = None
    best_score = 0.0
    prefix: Optional[QaHeading] = None
    prefix_len = 0
    for heading in headings:
        candidates = {_norm(heading.display), _norm(heading.title)}
        candidates.discard("")
        if not candidates:
            continue
        if entry.title.strip() == heading.display.strip() or entry_norm in candidates:
            return heading
        overlap = _truncation_overlap(entry_norm, candidates)
        if overlap > prefix_len:
            prefix, prefix_len = heading, overlap
        score = max(fuzz.ratio(entry_norm, c) for c in candidates)
        if score >= _FUZZ_THRESHOLD and score > best_score:
            best, best_score = heading, score
    return prefix or best


def check_toc(model: DocumentModel, rules: dict) -> CheckerOutcome:
    entries = model.toc_entries
    if len(entries) < _MIN_TOC_ENTRIES:
        return CheckerOutcome(code=CODE, status=CheckerStatus.SKIPPED)

    findings: list[Finding] = []
    matched: list[tuple[QaTocEntry, QaHeading]] = []
    missing: list[QaTocEntry] = []
    from app.core.document_qa.author_spans import outline_nodes

    outline = outline_nodes(model, rules)
    image_pages = {page.page_num for page in model.pages if page.image_only}
    for entry in entries:
        heading = _match_heading(entry, outline)
        if heading is not None:
            matched.append((entry, heading))
        elif entry.physical_page not in image_pages:
            # 目标页是扫描件/图片时正文标题不可读, 不在质检范围, 不能据此判缺失
            missing.append(entry)

    # 1) 目录条目未找到对应正文标题
    if len(outline) >= _MIN_HEADINGS_FOR_MISS:
        for entry in missing[:_MAX_FINDINGS_PER_RULE]:
            findings.append(Finding(
                rule_code="DOC-TOC-001",
                category=QaCategory.TOC,
                severity=QaSeverity.P1,
                certainty=QaCertainty.PROBABLE,
                title="目录条目未找到对应正文",
                message=f"目录条目「{entry.title}」(标注第{entry.printed_page}页)未在正文标题中找到对应内容",
                suggestion="确认该章节是否漏排, 或目录与正文标题措辞不一致",
                evidence=Evidence(
                    kind="single",
                    items=(EvidenceItem(
                        excerpt=f"{entry.title} …… {entry.printed_page}",
                        page=entry.physical_page,
                    ),),
                ),
                anchor=f"toc_missing:{_norm(entry.title)}",
            ))

    # 2) 一级正文标题未列入目录(possible, 低噪)
    matched_headings = {id(h) for _, h in matched}
    unlisted = [
        h for h in outline
        if h.level == 1 and id(h) not in matched_headings
        and (_norm(h.display) or _norm(h.title))
        and not any(
            _norm(e.title) in {_norm(h.display), _norm(h.title)} for e in entries
        )
    ]
    for heading in unlisted[:5]:
        findings.append(Finding(
            rule_code="DOC-TOC-002",
            category=QaCategory.TOC,
            severity=QaSeverity.P3,
            certainty=QaCertainty.POSSIBLE,
            title="正文标题未列入目录",
            message=f"正文一级标题「{heading.display}」(第{heading.page}页)未在目录中找到",
            suggestion="确认目录是否遗漏该章节",
            evidence=Evidence(
                kind="single",
                items=(EvidenceItem(
                    excerpt=heading.display, page=heading.page, bbox=heading.bbox,
                ),),
            ),
            anchor=f"toc_unlisted:{_norm(heading.display)}",
        ))

    # 3) 目录顺序与正文顺序不一致(按匹配标题物理页判断逆序)
    inversions = 0
    previous_page = 0
    inversion_pair = None
    for entry, heading in matched:
        if heading.page < previous_page:
            inversions += 1
            if inversion_pair is None:
                inversion_pair = (entry, heading)
        previous_page = max(previous_page, heading.page)
    if inversions and inversion_pair:
        entry, heading = inversion_pair
        findings.append(Finding(
            rule_code="DOC-TOC-003",
            category=QaCategory.TOC,
            severity=QaSeverity.P2,
            certainty=QaCertainty.PROBABLE,
            title="目录顺序与正文不一致",
            message=(
                f"共{inversions}处目录条目顺序与正文实际顺序不符, "
                f"如「{entry.title}」对应正文出现在更早的第{heading.page}页"
            ),
            suggestion="核对目录条目排列顺序是否与正文一致",
            evidence=Evidence(
                kind="single",
                items=(EvidenceItem(
                    excerpt=entry.title, page=heading.page, bbox=heading.bbox,
                ),),
            ),
            anchor="toc_order",
        ))

    # 4) 目录标注页码与正文实际页不符(仅坐标解析可靠时)
    # 差1页给 possible(章起始页与标题页存在合法1页差), 差更多给 probable
    if model.toc_resolution_reliable:
        for entry, heading in matched:
            if entry.physical_page is None:
                continue
            page_diff = abs(heading.page - entry.physical_page)
            if page_diff >= 1:
                findings.append(Finding(
                    rule_code="DOC-TOC-004",
                    category=QaCategory.TOC,
                    severity=QaSeverity.P2,
                    certainty=(
                        QaCertainty.POSSIBLE if page_diff == 1 else QaCertainty.PROBABLE
                    ),
                    title="目录页码与正文不符",
                    message=(
                        f"目录条目「{entry.title}」标注第{entry.printed_page}页"
                        f"(推算物理第{entry.physical_page}页), 正文标题实际在第{heading.page}页"
                    ),
                    suggestion="更新目录页码或检查正文分页",
                    evidence=Evidence(
                        kind="pair",
                        items=(
                            EvidenceItem(
                                excerpt=f"{entry.title} …… {entry.printed_page}",
                                page=entry.physical_page, role="primary",
                            ),
                            EvidenceItem(
                                excerpt=heading.display, page=heading.page,
                                bbox=heading.bbox, role="comparison",
                            ),
                        ),
                        expected=f"第{entry.physical_page}页",
                        actual=f"第{heading.page}页",
                    ),
                    anchor=f"toc_page:{_norm(entry.title)}",
                    closed_measurement=True,
                ))
                if sum(1 for f in findings if f.rule_code == "DOC-TOC-004") >= _MAX_FINDINGS_PER_RULE:
                    break

    return CheckerOutcome(code=CODE, status=CheckerStatus.OK, findings=findings)
