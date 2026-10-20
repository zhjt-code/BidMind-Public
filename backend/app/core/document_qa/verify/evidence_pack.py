"""
职责: 把同页候选发现收成一次复核请求的文本证据包
不做: 不调用模型、不判定真伪、不渲染页面图像
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.core.document_qa.contracts import DocumentModel, Finding

_BATCH_SIZE = 6
_WINDOW_RADIUS = 280
_CONTEXT_CAP = 1800


@dataclass(frozen=True)
class VerifyCandidate:
    """一条待复核候选及其在原列表中的位置。"""

    candidate_id: str
    finding_index: int
    page: Optional[int]
    payload: dict[str, Any]


def confirmed_by_measurement(model: DocumentModel, finding: Finding) -> bool:
    """度量已经闭合的候选不再交给模型推翻。

    闭合标志由检查器在「证据本身可重算」时置上。
    另外: 无文本、无图片、无表格的页, 其完整性类候选(空白页等)成立。
    """
    from app.core.document_qa.contracts import QaCategory

    if finding.closed_measurement:
        return True
    page = primary_page(finding)
    if page is None or finding.category != QaCategory.INTEGRITY:
        return False
    qa_page = next((item for item in model.pages if item.page_num == page), None)
    if qa_page is None:
        return False
    return qa_page.char_count <= 5 and qa_page.image_count == 0 and qa_page.table_count == 0


def excerpt_supports_claim(finding: Finding) -> bool:
    """在证据摘录上重算语言类规则。重算仍成立时, 模型否定不能删除。

    括号未闭合、以及未标成闭合度量的半角标点, 只有证据覆盖完整对象时才挡住删除。
    摘录只是跨度前缀、或半角标点只有一两处时, 高置信否定可以删除。
    """
    from app.core.document_qa.checks.language_check import excerpt_matches_language_rule

    if finding.rule_code in {"DOC-LANG-001", "DOC-LANG-003"} and not finding.closed_measurement:
        return False
    text = "\n".join(item.excerpt or "" for item in finding.evidence.items)
    return excerpt_matches_language_rule(finding.rule_code, text)


def primary_page(finding: Finding) -> Optional[int]:
    """证据项里的第一页; 没有页码则无法按页合并。"""
    for item in finding.evidence.items:
        if item.page is not None:
            return int(item.page)
    return None


def _window(body: str, needle: str) -> str:
    if not body:
        return ""
    head = (needle or "").strip()[:40]
    index = body.find(head) if head else -1
    if index < 0:
        return body[:_WINDOW_RADIUS * 2]
    start = max(0, index - _WINDOW_RADIUS)
    end = min(len(body), index + _WINDOW_RADIUS)
    return body[start:end]


def page_context(model: DocumentModel, page: Optional[int], excerpts: list[str]) -> str:
    """摘录周围的页内原文, 多段窗口拼接后截断。"""
    if page is None:
        return ""
    body = model.body_texts.get(page, "")
    windows = [_window(body, excerpt) for excerpt in excerpts if excerpt]
    if not windows:
        windows = [body[:_CONTEXT_CAP]]
    joined = "\n---\n".join(dict.fromkeys(windows))
    return joined[:_CONTEXT_CAP]


def _candidate_payload(finding: Finding, candidate_id: str) -> dict[str, Any]:
    excerpts = [item.excerpt for item in finding.evidence.items if item.excerpt]
    return {
        "candidate_id": candidate_id,
        "rule_code": finding.rule_code,
        "title": finding.title,
        "message": finding.message,
        "excerpts": excerpts[:3],
        "expected": finding.evidence.expected,
        "actual": finding.evidence.actual,
    }


def build_batches(
    findings: list[Finding],
) -> list[tuple[Optional[int], list[VerifyCandidate]]]:
    """按页分组再按批大小切开。同页候选进入同一次调用。"""
    grouped: dict[Optional[int], list[tuple[int, Finding]]] = {}
    for index, finding in enumerate(findings):
        grouped.setdefault(primary_page(finding), []).append((index, finding))

    batches: list[tuple[Optional[int], list[VerifyCandidate]]] = []
    for page in sorted(grouped, key=lambda value: (value is None, value or 0)):
        rows = grouped[page]
        for offset in range(0, len(rows), _BATCH_SIZE):
            chunk = rows[offset:offset + _BATCH_SIZE]
            candidates = [
                VerifyCandidate(
                    candidate_id=f"p{page if page is not None else 'x'}-{index}",
                    finding_index=index,
                    page=page,
                    payload=_candidate_payload(finding, f"p{page if page is not None else 'x'}-{index}"),
                )
                for index, finding in chunk
            ]
            batches.append((page, candidates))
    return batches
