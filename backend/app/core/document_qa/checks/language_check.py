"""
职责: Q4 语言检查(确定性子集)——括号不闭合、重复标点、中英文标点混排(正文与表格)
不做: 不做错别字/语法(V3, 需语料评测后引入)、不改写文本、不读公式/页眉页脚/图片区
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Callable, Optional

from app.core.document_qa.author_spans import (
    ProseSpan,
    bracket_spans,
    grid_rows,
    line_for_offset,
    prose_spans,
)
from app.core.document_qa.text_shapes import bracket_residual, first_unmatched_bracket
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

CODE = "language"

_MAX_PER_RULE = 8

# 重复句读(省略号……与破折号——不在其列)
_REPEAT_PUNCT_RE = re.compile(r"([。；！？，,;])\1+")
# CJK 字符之间夹半角逗号/分号(排除数字千分位)
_HALFWIDTH_BETWEEN_CJK_RE = re.compile(r"[\u4e00-\u9fff][,;][\u4e00-\u9fff]")

_Detector = Callable[[int, str], Optional[Finding]]


_BRACKET_WINDOW = 20


def _bracket_span_finding(span: ProseSpan) -> Optional[Finding]:
    """证据只留未配括号前后约二十字, 页和框落在那一行。"""
    index = first_unmatched_bracket(span.text)
    if index is None:
        return None
    start = max(0, index - _BRACKET_WINDOW)
    end = min(len(span.text), index + _BRACKET_WINDOW + 1)
    window = span.text[start:end]
    line = line_for_offset(span, index)
    page_num = line.page if line is not None else span.page
    bbox = line.bbox if line is not None else None
    leftover_open, extra_close = bracket_residual(span.text)
    return Finding(
        rule_code="DOC-LANG-001",
        category=QaCategory.LANGUAGE,
        severity=QaSeverity.P2,
        certainty=QaCertainty.PROBABLE,
        title="括号未闭合",
        message=(
            f"第{page_num}页括号未闭合，如「{window}」"
            f"(开{leftover_open}/闭{extra_close})"
        ),
        suggestion="补齐缺失的括号",
        evidence=Evidence(
            kind="single",
            items=(EvidenceItem(excerpt=window, page=page_num, bbox=bbox),),
        ),
        anchor=f"bracket:{page_num}:{window[:30]}",
        closed_measurement=True,
    )


def _repeat_punct_finding(page_num: int, text: str) -> Optional[Finding]:
    matched = _REPEAT_PUNCT_RE.search(text)
    if not matched:
        return None
    start = max(0, matched.start() - 15)
    return Finding(
        rule_code="DOC-LANG-002",
        category=QaCategory.LANGUAGE,
        severity=QaSeverity.P3,
        certainty=QaCertainty.CERTAIN,
        title="重复标点",
        message=f"第{page_num}页出现连续重复标点「{matched.group(0)}」",
        suggestion="删除多余标点",
        evidence=Evidence(
            kind="single",
            items=(EvidenceItem(excerpt=text[start:matched.end() + 15], page=page_num),),
        ),
        anchor=f"repeat_punct:{page_num}:{matched.start()}",
    )


def _mixed_punct_finding(page_num: int, text: str) -> Optional[Finding]:
    matches = list(_HALFWIDTH_BETWEEN_CJK_RE.finditer(text))
    if not matches:
        return None
    matched = matches[0]
    start = max(0, matched.start() - 8)
    window = text[start:matched.end() + 8]
    return Finding(
        rule_code="DOC-LANG-003",
        category=QaCategory.LANGUAGE,
        severity=QaSeverity.P3,
        certainty=QaCertainty.POSSIBLE,
        title="中英文标点混用",
        message=f"第{page_num}页中文语句中使用了半角标点「{matched.group(0)[1]}」，如「{window}」",
        suggestion="中文语句建议统一使用全角标点",
        evidence=Evidence(
            kind="single",
            items=(EvidenceItem(excerpt=window, page=page_num),),
        ),
        anchor=f"mixed_punct:{page_num}:{matched.start()}",
        closed_measurement=len(matches) >= 3,
    )


_Rules = tuple[tuple[str, _Detector], ...]

_PUNCT_RULES: _Rules = (
    ("DOC-LANG-002", _repeat_punct_finding),
    ("DOC-LANG-003", _mixed_punct_finding),
)


def _language_units(model: DocumentModel) -> list[tuple[int, str, _Rules]]:
    """标点按正文段和表格视觉行。括号另走紧邻括号段, 不在这里结算。"""
    units = [
        (span.page, span.text, _PUNCT_RULES)
        for span in prose_spans(model)
    ]
    for row in grid_rows(model):
        units.append((row.page, row.text, _PUNCT_RULES))
    units.sort(key=lambda unit: unit[0])
    return units


def excerpt_matches_language_rule(rule_code: str, text: str) -> bool:
    """摘录上重算语言规则。成立则复核不能把这条删掉。"""
    if rule_code == "DOC-LANG-001":
        leftover_open, extra_close = bracket_residual(text)
        return bool(leftover_open or extra_close)
    if rule_code == "DOC-LANG-002":
        return _REPEAT_PUNCT_RE.search(text) is not None
    if rule_code == "DOC-LANG-003":
        return _HALFWIDTH_BETWEEN_CJK_RE.search(text) is not None
    return False


def check_language(model: DocumentModel, rules: dict) -> CheckerOutcome:
    """字形规则走原字形通道: 版面页取区域文字行, 其余页取 visual_paragraphs。

    两者都缺时跳过, 禁止在 NFKC 文本上猜。
    """
    findings: list[Finding] = []
    if not model.visual_paragraphs and not model.regions:
        return CheckerOutcome(code=CODE, status=CheckerStatus.SKIPPED, findings=findings)

    hits: Counter[str] = Counter()
    for span in bracket_spans(model):
        if hits["DOC-LANG-001"] >= _MAX_PER_RULE:
            break
        finding = _bracket_span_finding(span)
        if finding is not None:
            findings.append(finding)
            hits["DOC-LANG-001"] += 1
    for page_num, text, unit_rules in _language_units(model):
        for rule_code, detect in unit_rules:
            if hits[rule_code] >= _MAX_PER_RULE:
                continue
            finding = detect(page_num, text)
            if finding is not None:
                findings.append(finding)
                hits[rule_code] += 1

    return CheckerOutcome(code=CODE, status=CheckerStatus.OK, findings=findings)
