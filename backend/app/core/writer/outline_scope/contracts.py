"""
职责: 定义实质内容纲要标题的数据结构、粒度常量和清洗规则。
不做: 不选招标章节、不调用 LLM、不挂目录树、不写库。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.writer.numbering import strip_existing_number

MAX_TITLES_PER_PARENT = 8
MAX_TITLES_TOTAL = 20
TITLE_MAX_CHARS = 24
MIN_TITLE_CHARS = 4

REJECT_TOKENS = ("指标", "参数", "分值", "不得低于")
CATCHALL_EXTRACT_TOPIC = "招标项目内容、建设内容、研究内容与工作事项"


@dataclass(frozen=True)
class ScopeTitle:
    """招标需求章中的一条纲要级标题。"""

    title: str
    source_hint: str = ""


def sanitize_scope_title(raw: str) -> str | None:
    """清洗标题；过细、过短、过长或空结果返回 None。"""

    cleaned = strip_existing_number(str(raw or "")).strip()
    if len(cleaned) < MIN_TITLE_CHARS or len(cleaned) > TITLE_MAX_CHARS:
        return None
    if any(token in cleaned for token in REJECT_TOKENS):
        return None
    return cleaned
