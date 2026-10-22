"""写标约束写作消费分级规则

职责: 用确定性词表识别明显不应进入正文匹配的程序性/告知类约束。
不做: 不调用 LLM、不写 metadata、不处理评分项/实质性条款铁律。
"""

from __future__ import annotations

from typing import Any

_PROCEDURAL_NOTICE_PATTERNS = (
    "开标时间", "开标地点", "递交时间", "递交地点", "截止时间",
    "联系方式", "联系人", "联系电话", "交易中心", "现场踏勘",
    "答疑时间", "澄清时间",
)

_CHECK_ONLY_DISQUALIFICATION_PATTERNS = (
    "签字", "盖章", "签章", "骑缝章", "逐页",
    "密封", "封套", "装订", "正副本",
)


def classify_reference_by_rules(clause: Any) -> str | None:
    """命中程序性告知词表时返回降级原因, 否则返回 None。"""
    text = f"{getattr(clause, 'content', '') or ''} {getattr(clause, 'raw_text', '') or ''}"
    if any(token in text for token in _PROCEDURAL_NOTICE_PATTERNS):
        return "程序性告知不影响标书正文编写"
    return None


def classify_check_only_by_rules(clause: Any) -> str | None:
    """内容可由末端一致性检查兜底的废标红线返回原因。"""
    if getattr(clause, "sub_type", "") != "disqualification":
        return None
    text = f"{getattr(clause, 'content', '') or ''} {getattr(clause, 'raw_text', '') or ''}"
    if any(token in text for token in _CHECK_ONLY_DISQUALIFICATION_PATTERNS):
        return "检查型红线由一致性检查兜底"
    return None


__all__ = ["classify_check_only_by_rules", "classify_reference_by_rules"]
