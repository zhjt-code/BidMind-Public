"""
职责: 正文段落级字体/字号离群判定; 有版面的页只取正文区, 公式/表格/页眉页脚区不计主流样式也不参与离群
不做: 不判定标题/表格/公式/页眉页脚/图注样式、不做行距、不自行按区域取字(经 region_text 取数)
"""

from __future__ import annotations

from typing import Optional

from app.core.document_qa.author_spans import outline_nodes, prose_blocks
from app.core.document_qa.contracts import (
    CheckerOutcome,
    CheckerStatus,
    DocumentModel,
    Evidence,
    EvidenceItem,
    Finding,
    QaBlock,
    QaCategory,
    QaCertainty,
    QaHeading,
    QaSeverity,
    QaSpan,
)
from app.core.document_qa.text_shapes import strip_font_subset_prefix

CODE = "layout"
_RULE_CODE = "DOC-LAY-001"

_DEFAULTS = {
    "min_body_chars": 40,
    "outlier_span_chars": 24,
    "max_findings": 6,
    "small_size_delta": 1.5,
}


_FAMILY_MARKERS = (
    ("hei", ("simhei", "heiti", "yahei", "msyh", "黑体", "hei")),
    ("kai", ("kaiti", "楷", "kai")),
    ("fang", ("fangsong", "仿宋", "fang")),
    ("song", ("simsun", "stsong", "songti", "宋", "song")),
)


def _font_family(font: str) -> str:
    raw = strip_font_subset_prefix(font).lower()
    for family, markers in _FAMILY_MARKERS:
        if any(marker in raw for marker in markers):
            return family
    return raw or "unknown"


def _style_key(font: str, size: float) -> str:
    return f"{_font_family(font)}|{round(size, 1)}"


def _same_band(size: float, majority_size: float, delta: float) -> bool:
    return abs(size - majority_size) < delta


def _histogram_from_blocks(blocks: list[QaBlock]) -> dict[str, int]:
    histogram: dict[str, int] = {}
    for block in blocks:
        for span in block.spans:
            text = span.text.strip()
            if not text:
                continue
            key = _style_key(span.font, span.size)
            histogram[key] = histogram.get(key, 0) + len(text)
    return histogram


def _majority_style(
    model: DocumentModel, blocks: list[QaBlock],
) -> Optional[tuple[str, float, str]]:
    histogram = _histogram_from_blocks(blocks)
    if not histogram:
        raw = dict((model.style_profile or {}).get("font_size_chars") or {})
        histogram = {}
        for key, count in raw.items():
            font, _, size_text = str(key).partition("|")
            try:
                family_key = _style_key(font, float(size_text))
            except ValueError:
                continue
            histogram[family_key] = histogram.get(family_key, 0) + int(count)
    if not histogram:
        return None
    majority_key = max(histogram, key=histogram.get)
    family, _, size_text = majority_key.partition("|")
    try:
        return family, float(size_text), majority_key
    except ValueError:
        return None


def _union_bbox(spans: list[QaSpan]) -> tuple[float, float, float, float]:
    return (
        min(span.bbox[0] for span in spans),
        min(span.bbox[1] for span in spans),
        max(span.bbox[2] for span in spans),
        max(span.bbox[3] for span in spans),
    )


def _is_heading_block(block: QaBlock, headings: list[QaHeading]) -> bool:
    first = (block.text.split("\n", 1)[0] or "").strip()
    for heading in headings:
        if heading.page != block.page:
            continue
        display = heading.display
        if display and first.startswith(display):
            return True
        if heading.title and heading.title in first:
            return True
    return False


def _folded_len(text: str) -> int:
    return len("".join(text.split()))


def check_layout(model: DocumentModel, rules: dict) -> CheckerOutcome:
    blocks = prose_blocks(model)
    if not blocks:
        return CheckerOutcome(code=CODE, status=CheckerStatus.SKIPPED)

    majority = _majority_style(model, blocks)
    if majority is None:
        return CheckerOutcome(code=CODE, status=CheckerStatus.OK)

    cfg = rules.get("layout") or {}
    min_body_chars = int(cfg.get("min_body_chars", _DEFAULTS["min_body_chars"]))
    outlier_span_chars = int(cfg.get("outlier_span_chars", _DEFAULTS["outlier_span_chars"]))
    max_findings = int(cfg.get("max_findings", _DEFAULTS["max_findings"]))
    small_size_delta = float(cfg.get("small_size_delta", _DEFAULTS["small_size_delta"]))
    majority_family, majority_size, majority_key = majority

    findings: list[Finding] = []
    for block in blocks:
        if block.page in model.toc_zone:
            continue
        if _is_heading_block(block, outline_nodes(model)):
            continue
        if _folded_len(block.text) < min_body_chars:
            continue
        run: list[QaSpan] = []
        run_chars = 0

        def flush_run() -> None:
            nonlocal run_chars
            if run and run_chars >= outlier_span_chars and len(findings) < max_findings:
                excerpt = "".join(span.text for span in run).strip()[:80]
                findings.append(Finding(
                    rule_code=_RULE_CODE,
                    category=QaCategory.LAYOUT,
                    severity=QaSeverity.P3,
                    certainty=QaCertainty.POSSIBLE,
                    title="正文版式离群",
                    message=(
                        f"第{block.page}页正文连续 {run_chars} 字使用非主流样式"
                        f"(主流 {majority_key})"
                    ),
                    suggestion="确认是否为有意强调, 否则统一为正文字体与字号",
                    evidence=Evidence(
                        kind="single",
                        items=(EvidenceItem(
                            excerpt=excerpt, page=block.page, bbox=_union_bbox(run),
                        ),),
                        expected=majority_key,
                        actual=_style_key(run[0].font, run[0].size),
                    ),
                    anchor=f"layout:{block.page}:{excerpt[:40]}",
                    closed_measurement=True,
                ))
            run.clear()
            run_chars = 0

        for span in block.spans:
            text = span.text.strip()
            if not text:
                continue
            key = _style_key(span.font, span.size)
            family = _font_family(span.font)
            small = span.size <= majority_size - small_size_delta
            in_band = family == majority_family and _same_band(
                span.size, majority_size, small_size_delta,
            )
            if key == majority_key or small or in_band:
                flush_run()
                continue
            run.append(span)
            run_chars += len(text)
        flush_run()
        if len(findings) >= max_findings:
            break

    from app.core.document_qa.checks.layout_axes import axis_findings

    findings.extend(axis_findings(
        model, rules, already=len(findings), limit=max_findings,
    ))
    return CheckerOutcome(code=CODE, status=CheckerStatus.OK, findings=findings[:max_findings])
