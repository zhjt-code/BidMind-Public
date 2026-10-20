"""
职责: 从正文 span/行框收集字体字号以外的离群轴(颜色、左缩进、页边距)
不做: 不判定标题和表格、不取字、不调用复核
"""

from __future__ import annotations

from statistics import median

from app.core.document_qa.checks.layout_check import (
    _folded_len,
    _is_heading_block,
    _union_bbox,
)
from app.core.document_qa.contracts import (
    DocumentModel,
    Evidence,
    EvidenceItem,
    Finding,
    QaBlock,
    QaCategory,
    QaCertainty,
    QaSeverity,
    QaSpan,
)
from app.core.document_qa.author_spans import outline_nodes, prose_blocks

_RULE_CODE = "DOC-LAY-001"


def _cfg(rules: dict, key: str, default: float) -> float:
    return float((rules.get("layout") or {}).get(key, default))


def _body_candidates(model: DocumentModel, min_chars: int) -> list[QaBlock]:
    kept: list[QaBlock] = []
    for block in prose_blocks(model):
        if block.page in model.toc_zone:
            continue
        if _is_heading_block(block, outline_nodes(model)):
            continue
        if _folded_len(block.text) < min_chars:
            continue
        kept.append(block)
    return kept


def _append(
    findings: list[Finding],
    *,
    page: int,
    message: str,
    excerpt: str,
    bbox: tuple[float, float, float, float],
    anchor: str,
    limit: int,
) -> None:
    if len(findings) >= limit:
        return
    findings.append(Finding(
        rule_code=_RULE_CODE,
        category=QaCategory.LAYOUT,
        severity=QaSeverity.P3,
        certainty=QaCertainty.POSSIBLE,
        title="正文版式离群",
        message=message,
        suggestion="确认是否为有意强调, 否则与正文版式对齐",
        evidence=Evidence(
            kind="single",
            items=(EvidenceItem(excerpt=excerpt[:80], page=page, bbox=bbox),),
        ),
        anchor=anchor,
        closed_measurement=True,
    ))


def _color_findings(
    blocks: list[QaBlock],
    *,
    min_chars: int,
    limit: int,
) -> list[Finding]:
    counts: dict[int, int] = {}
    for block in blocks:
        for span in block.spans:
            text = span.text.strip()
            if text:
                counts[span.color] = counts.get(span.color, 0) + len(text)
    if not counts:
        return []
    majority = max(counts, key=counts.get)
    findings: list[Finding] = []
    for block in blocks:
        run: list[QaSpan] = []
        run_chars = 0

        def flush() -> None:
            nonlocal run_chars
            if run and run_chars >= min_chars:
                excerpt = "".join(span.text for span in run).strip()
                _append(
                    findings,
                    page=block.page,
                    message=f"第{block.page}页正文连续 {run_chars} 字颜色偏离主流",
                    excerpt=excerpt,
                    bbox=_union_bbox(run),
                    anchor=f"layout_color:{block.page}:{excerpt[:24]}",
                    limit=limit,
                )
            run.clear()
            run_chars = 0

        for span in block.spans:
            text = span.text.strip()
            if not text:
                continue
            if span.color == majority:
                flush()
                continue
            run.append(span)
            run_chars += len(text)
        flush()
        if len(findings) >= limit:
            break
    return findings


def _content_lines(blocks: list[QaBlock]) -> list:
    """有行几何时按行看缩进; 旧夹具只有块框时退回块本身。"""
    lines = []
    for block in blocks:
        if block.lines:
            lines.extend(block.lines)
        else:
            lines.append(block)
    return lines


def _line_is_heading(line, headings) -> bool:
    text = (line.text or "").strip()
    if not text:
        return False
    for heading in headings:
        if heading.page != line.page:
            continue
        display = heading.display or ""
        if display and text.startswith(display):
            return True
    return False


def _indent_findings(
    model: DocumentModel,
    *,
    delta: float,
    min_chars: int,
    max_x0: float,
    limit: int,
) -> list[Finding]:
    """按每段第一行的左缘比较。折行后的缩进是续行, 不是另一级缩进。"""
    lines = []
    for block in prose_blocks(model):
        if block.page in model.toc_zone:
            continue
        sample = block.lines[0] if block.lines else block
        if sample.bbox[0] >= max_x0:
            continue
        if _line_is_heading(sample, outline_nodes(model)):
            continue
        lines.append(sample)
    weights: dict[float, int] = {}
    for line in lines:
        if _folded_len(line.text) < 8:
            continue
        bucket = round(line.bbox[0], 2)
        weights[bucket] = weights.get(bucket, 0) + _folded_len(line.text)
    if not weights:
        return []
    majority = max(weights, key=weights.get)
    findings: list[Finding] = []
    run = []
    run_chars = 0

    def flush() -> None:
        nonlocal run_chars
        if run and run_chars >= min_chars:
            excerpt = "".join(line.text for line in run).strip()
            _append(
                findings,
                page=run[0].page,
                message=f"第{run[0].page}页正文左缩进明显大于同文主流",
                excerpt=excerpt,
                bbox=_union_bbox(run),
                anchor=f"layout_indent:{run[0].page}:{excerpt[:24]}",
                limit=limit,
            )
        run.clear()
        run_chars = 0

    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        if line.bbox[0] - majority < delta:
            flush()
            continue
        run.append(line)
        run_chars += _folded_len(text)
    flush()
    return findings


def _margin_findings(
    model: DocumentModel,
    blocks: list[QaBlock],
    *,
    delta_pt: float,
    limit: int,
) -> list[Finding]:
    """用绝对磅值比页边。归一化坐标会把 Letter 窄边距折成和 A4 差不多的比例。"""
    width = {page.page_num: page.width for page in model.pages if page.width > 0}
    by_page: dict[int, list] = {}
    for line in _content_lines(blocks):
        if line.page not in width or _folded_len(line.text) < 8:
            continue
        by_page.setdefault(line.page, []).append(line)
    if len(by_page) < 5:
        return []
    insets = {
        page: min(line.bbox[0] * width[page] for line in page_lines)
        for page, page_lines in by_page.items()
    }
    baseline = float(median(insets.values()))
    findings: list[Finding] = []
    for page, inset in sorted(insets.items()):
        if baseline - inset < delta_pt:
            continue
        sample = by_page[page][0]
        _append(
            findings,
            page=page,
            message=f"第{page}页正文左边距明显窄于同文其他页",
            excerpt=sample.text.strip(),
            bbox=sample.bbox,
            anchor=f"layout_margin:{page}",
            limit=limit,
        )
        if len(findings) >= limit:
            break
    return findings


def axis_findings(
    model: DocumentModel,
    rules: dict,
    *,
    already: int,
    limit: int,
) -> list[Finding]:
    """颜色、缩进、边距离群。已有字体字号发现占用的名额从 limit 里扣掉。"""
    room = max(0, limit - already)
    if room <= 0:
        return []
    min_chars = int(_cfg(rules, "min_body_chars", 40))
    blocks = _body_candidates(model, min_chars)
    findings: list[Finding] = []
    findings.extend(_color_findings(
        blocks, min_chars=int(_cfg(rules, "color_min_chars", 24)), limit=room,
    ))
    findings.extend(_indent_findings(
        model,
        delta=_cfg(rules, "indent_delta", 0.06),
        min_chars=int(_cfg(rules, "indent_min_chars", 24)),
        max_x0=_cfg(rules, "indent_max_x0", 0.24),
        limit=room,
    ))
    findings.extend(_margin_findings(
        model, blocks, delta_pt=_cfg(rules, "margin_delta_pt", 18), limit=room,
    ))
    return findings[:room]
