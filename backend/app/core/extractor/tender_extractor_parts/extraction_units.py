"""
职责: 决定哪些分块作为招标要求的提取单位，并为跨块列表续项补回管辖上下文。
不做: 不调用 LLM、不改 parser 分块、不做去重合并、不判定评审意义。

为何需要: parser 为检索生成的 `table_row_with_header` / `scoring_table`
分块与同页正文块承载同一段文字。把它们都送提取器，会让同一表格既以「有表头角色
的整页文本」又以「无主体的单行」各被抽取一次——后者只能靠模型脑补主体与义务。
跨页列表续项（块首即「(11)…」）同样拿不到上一块的「有下列情形之一…」引导句。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from app.core.extractor.mandatory.signals import (
    CONSEQUENCE_LEADS,
    line_starts_enumeration,
)

# 与同页正文重复的派生分块：只服务检索/评分通道，不作为提取单位。
DERIVED_CHUNK_TYPES: frozenset[str] = frozenset({"table_row_with_header", "scoring_table"})

# 上文承接最多回看多少字符；引导句通常在上一块尾部，但前面可能已排了若干列表项。
_LEAD_LOOKBACK_CHARS = 600
_LEAD_TAIL_FALLBACK_CHARS = 120
# 引导句以冒号收尾；分号是列表项结尾，不是引导。
_LEAD_ENDINGS = ("：", ":")
# 「十一、定标」这类中文序号章节标题是新段落开头，不是上一列表的续项。
_CN_ORDINAL_HEADING_RE = re.compile(r"^\s*[一二三四五六七八九十]+、")


class _ChunkLike(Protocol):
    id: int
    content: str | None
    chunk_type: str | None


def select_extraction_chunks(chunks: Sequence[_ChunkLike]) -> list[_ChunkLike]:
    """过滑掉与正文重复的派生分块，保留正文/条款/图片证据等一次性载体。"""
    return [
        chunk
        for chunk in chunks
        if str(getattr(chunk, "chunk_type", "") or "") not in DERIVED_CHUNK_TYPES
    ]


def build_lead_context_map(chunks: Sequence[_ChunkLike]) -> dict[int, str]:
    """为块首是列表续项的分块，找出上一块中的引导句作为「上文承接」。

    返回 {chunk.id: lead_text}；无需承接的分块不出现在映射里。
    """
    lead_by_id: dict[int, str] = {}
    previous: _ChunkLike | None = None
    for chunk in chunks:
        content = str(getattr(chunk, "content", "") or "")
        if previous is not None and _continues_enumeration(content):
            lead = extract_lead_sentence(str(getattr(previous, "content", "") or ""))
            if lead:
                lead_by_id[int(chunk.id)] = lead
        previous = chunk
    return lead_by_id


def _continues_enumeration(content: str) -> bool:
    """块首为列表续项（(11) / 11. / ①），排除中文序号章节标题。"""
    if not line_starts_enumeration(content):
        return False
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    return not _CN_ORDINAL_HEADING_RE.match(first_line)


def extract_lead_sentence(previous_content: str) -> str:
    """从上一块尾部取引导句：优先后果引导/以冒号结尾的行，否则退化为尾部片段。"""
    tail = previous_content.strip()[-_LEAD_LOOKBACK_CHARS:]
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    for line in reversed(lines):
        if any(lead in line for lead in CONSEQUENCE_LEADS) or line.endswith(_LEAD_ENDINGS):
            return line
    return tail[-_LEAD_TAIL_FALLBACK_CHARS:].strip()


def prepend_lead_context(content: str, lead: str) -> str:
    """把引导句以显式标注拼到分块正文前，供 prompt 理解语境而不重复提取。"""
    if not lead:
        return content
    return f"[上文承接（上一分块结尾，仅供理解语境，不要重复提取）]\n{lead}\n\n{content}"
