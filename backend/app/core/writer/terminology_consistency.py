"""行业术语书写一致性

职责: 基于行业画像术语表检测正文中同一术语的书写形式是否混用。
不做: 不判断术语是否应当被使用、不做同义词归一、不读数据库、不调用 LLM、
      不修改正文。

设计取舍: 只报"同一术语出现多种书写形式"这类客观可判定的问题。
不报"画像术语未在正文出现"——画像术语表含大量仪器型号与标准编号，
单个章节乃至全文都不可能覆盖全部条目，该检查会产出大量无法处置的误报，
反而挤占"需复核"面板中真实高风险项的注意力。
"""

from __future__ import annotations

import re
from typing import Final, Sequence

# 与既有称谓一致性检查复用同一 issue type，避免前端出现未识别类型
ISSUE_TYPE_TERMINOLOGY: Final = "terminology_inconsistency"

_MIN_TERM_LEN: Final = 2
_FULL_TO_HALF_BRACKET: Final = {"（": "(", "）": ")"}


def find_term_variant_issues(
    full_text: str,
    terms: Sequence[str],
    *,
    max_issues: int = 5,
) -> list[dict]:
    """检测画像术语在正文中的书写变体混用。

    Args:
        full_text: 全部章节正文拼接后的文本
        terms: 行业画像术语表条目
        max_issues: 返回条数上限，防止刷屏挤占复核视野

    Returns:
        list[dict]: 与既有一致性检查同结构的问题列表
    """
    if not full_text or not terms or max_issues <= 0:
        return []

    issues: list[dict] = []
    for term in terms:
        if len(issues) >= max_issues:
            break
        cleaned = (term or "").strip()
        if len(cleaned) < _MIN_TERM_LEN:
            continue
        issue = _detect_written_variants(full_text, cleaned)
        if issue:
            issues.append(issue)
    return issues


def _detect_written_variants(full_text: str, term: str) -> dict | None:
    """检测同一术语在正文中的多种书写形式。

    归一化后再匹配，可一次覆盖英文大小写与全半角括号两类差异，
    并保证同一术语至多产出一条问题。
    """
    normalized_term = _normalize_for_match(term)
    normalized_text = _normalize_for_match(full_text)
    # 个别 Unicode 字符小写化会改变长度，此时无法按偏移回取原文写法
    if not normalized_term or len(normalized_text) != len(full_text):
        return None

    written_forms = {
        full_text[match.start():match.end()]
        for match in re.finditer(re.escape(normalized_term), normalized_text)
    }
    if len(written_forms) <= 1:
        return None

    listed = "、".join(sorted(written_forms))
    return _build_issue(
        message=f"专业术语「{term}」写法不统一: 全文同时出现 {listed}",
        detail="行业画像已给出该术语的标准写法，建议全文统一为画像中的形式",
    )


def _normalize_for_match(text: str) -> str:
    """把括号统一为半角并小写化，用于跨书写形式匹配。"""
    for source, target in _FULL_TO_HALF_BRACKET.items():
        text = text.replace(source, target)
    return text.lower()


def _build_issue(*, message: str, detail: str) -> dict:
    """构造与既有一致性检查同结构的低风险问题项。"""
    return {
        "type": ISSUE_TYPE_TERMINOLOGY,
        "severity": "low",
        "node_id": None,
        "message": message,
        "detail": detail,
    }
