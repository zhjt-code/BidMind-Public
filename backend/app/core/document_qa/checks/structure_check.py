"""
职责: Q1 结构检查——标题层级跳跃、空章节(有标题无内容; 有版面的页表格、图片区也算内容)
不做: 不校验目录、不校验编号连续性(分属 toc/numbering 检查器); 不自行拼版面文字(经 region_text 取数)
"""

from __future__ import annotations

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
from app.core.document_qa.region_text import KIND_VISUAL, section_units

CODE = "structure"

_MAX_FINDINGS = 10
_EMPTY_SECTION_MIN_CHARS = 10
_BBOX_TOL = 0.02


def _folded(text: str) -> str:
    return "".join((text or "").split())


def _bbox_close(left, right, *, tol: float = _BBOX_TOL) -> bool:
    if not left or not right:
        return False
    return all(abs(a - b) <= tol for a, b in zip(left, right))


def _outline_headings(model: DocumentModel, rules: dict) -> list:
    """只保留表外大纲节点。括号清单与表内编号不参与跳跃/空章节。"""
    from app.core.document_qa.author_spans import outline_nodes

    return outline_nodes(model, rules)


def _same_numbering_family(left, right) -> bool:
    """有 kind 则必须同句式家族; 双边都未标注时退回旧夹具的整数比较。"""
    if left.kind and right.kind:
        return left.kind == right.kind
    return not left.kind and not right.kind


def _level_jump_findings(model: DocumentModel, rules: dict) -> list[Finding]:
    findings: list[Finding] = []
    previous = None
    for heading in _outline_headings(model, rules):
        if (
            previous is not None
            and _same_numbering_family(previous, heading)
            and heading.level > previous.level + 1
        ):
            findings.append(Finding(
                rule_code="DOC-STR-001",
                category=QaCategory.STRUCTURE,
                severity=QaSeverity.P2,
                certainty=QaCertainty.PROBABLE,
                title="标题层级跳跃",
                message=(
                    f"标题「{heading.display}」(第{heading.level}级)直接出现在"
                    f"「{previous.display}」(第{previous.level}级)之后, 中间缺少过渡层级"
                ),
                suggestion="检查是否漏写中间层级标题, 或该标题层级标错",
                evidence=Evidence(
                    kind="pair",
                    items=(
                        EvidenceItem(
                            excerpt=previous.display, page=previous.page,
                            bbox=previous.bbox, role="primary",
                        ),
                        EvidenceItem(
                            excerpt=heading.display, page=heading.page,
                            bbox=heading.bbox, role="comparison",
                        ),
                    ),
                ),
                anchor=f"level_jump:{previous.display}>{heading.display}",
                closed_measurement=True,
            ))
        previous = heading
    return findings


def _empty_section_findings(model: DocumentModel, rules: dict) -> list[Finding]:
    """空章节: 大纲标题与下一同级/更高级大纲标题之间内容过少。

    按 section_units 的 (页, 上, 左) 顺序定位标题间单元: 有版面的页取页眉页脚以外的全部区域,
    其余页退回旧文字块; 无单元(降级)时跳过。清单句式(paren 等)不是同级, 不能关区间。
    """
    headings = _outline_headings(model, rules)
    units = section_units(model)
    if not units or not headings:
        return []

    heading_keys = {(h.page, h.display) for h in headings}

    def position_of(heading) -> int:
        needle = _folded(heading.display)
        for index, unit in enumerate(units):
            if unit.page != heading.page:
                continue
            if heading.bbox and _bbox_close(unit.bbox, heading.bbox):
                return index
            first_line = _folded(unit.text.split("\n", 1)[0])
            if needle and first_line.startswith(needle):
                return index
        return -1

    findings: list[Finding] = []
    for i, heading in enumerate(headings):
        next_heading = None
        for candidate in headings[i + 1:]:
            if candidate.level <= heading.level:
                next_heading = candidate
                break
        if next_heading is None:
            continue
        start = position_of(heading)
        end = position_of(next_heading)
        if start < 0 or end < 0 or end <= start:
            continue
        page_from, page_to = heading.page, next_heading.page
        image_pages = {
            page.page_num
            for page in model.pages
            if page_from <= page.page_num <= page_to and page.image_count > 0
        }
        if image_pages:
            continue
        body_chars = 0
        for unit in units[start + 1:end]:
            first_line = unit.text.split("\n", 1)[0].strip()
            if (unit.page, first_line) in heading_keys:
                continue
            # 图片/图表/印章区没有文件文字, 单独出现也算章节内容
            body_chars += _EMPTY_SECTION_MIN_CHARS if unit.kind == KIND_VISUAL else len(_folded(unit.text))
        if body_chars < _EMPTY_SECTION_MIN_CHARS:
            findings.append(Finding(
                rule_code="DOC-STR-002",
                category=QaCategory.STRUCTURE,
                severity=QaSeverity.P1,
                certainty=QaCertainty.PROBABLE,
                title="空章节",
                message=(
                    f"标题「{heading.display}」(第{heading.page}页)之后到下一同级标题"
                    f"「{next_heading.display}」之间几乎没有正文内容"
                ),
                suggestion="确认该章节内容是否漏写或漏排",
                evidence=Evidence(
                    kind="single",
                    items=(EvidenceItem(
                        excerpt=heading.display, page=heading.page, bbox=heading.bbox,
                    ),),
                ),
                anchor=f"empty_section:{heading.display}",
                closed_measurement=True,
            ))
    return findings


def _bookmark_level_findings(model: DocumentModel, rules: dict) -> list[Finding]:
    """书签层级浅于同名大纲标题才报。没有书签不报; 书签更深或同级不报。"""
    if not model.bookmarks:
        return []
    by_key: dict[str, object] = {}
    for heading in _outline_headings(model, rules):
        for key in (_folded(heading.display), _folded(heading.title)):
            if key and key not in by_key:
                by_key[key] = heading
    findings: list[Finding] = []
    for bookmark in model.bookmarks:
        heading = by_key.get(_folded(bookmark.title))
        if heading is None or bookmark.level >= heading.level:
            continue
        findings.append(Finding(
            rule_code="DOC-STR-003",
            category=QaCategory.STRUCTURE,
            severity=QaSeverity.P2,
            certainty=QaCertainty.PROBABLE,
            title="书签层级与正文不一致",
            message=(
                f"书签「{bookmark.title}」为第{bookmark.level}级, "
                f"正文标题为第{heading.level}级"
            ),
            suggestion="按正文标题层级调整书签",
            evidence=Evidence(
                kind="pair",
                items=(
                    EvidenceItem(
                        excerpt=bookmark.title, page=bookmark.page, role="primary",
                    ),
                    EvidenceItem(
                        excerpt=heading.display, page=heading.page,
                        bbox=heading.bbox, role="comparison",
                    ),
                ),
            ),
            anchor=f"bookmark_level:{_folded(bookmark.title)}",
            closed_measurement=True,
        ))
        if len(findings) >= _MAX_FINDINGS:
            break
    return findings


def check_structure(model: DocumentModel, rules: dict) -> CheckerOutcome:
    findings = (
        _level_jump_findings(model, rules)
        + _empty_section_findings(model, rules)
        + _bookmark_level_findings(model, rules)
    )
    return CheckerOutcome(
        code=CODE, status=CheckerStatus.OK, findings=findings[:_MAX_FINDINGS],
    )
