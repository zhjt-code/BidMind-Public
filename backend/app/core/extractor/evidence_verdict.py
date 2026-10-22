"""投标响应落库判定 (合同 A)

职责: 在提取 found=False 时决定落库标记, 并提供占位符单源判定
不做: 数据库查询、LLM 调用、符合性打分、ORM 持久化

合同 A:
- 分面缺口或检索饥饿 -> [证据不足待复核], 不得定谳 [未找到响应]
- 证据包已有实质命中但 LLM 未确认 -> 短状态 partial, 原文走 chunks/raw_evidence
- 覆盖完整、无饥饿、包空/仅低信息 -> 仍允许 [未找到响应]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence

from app.core.extractor.extraction_failure import EXTRACTION_FAILED_CONTENT

NOT_FOUND_CONTENT = "[未找到响应]"
EVIDENCE_INSUFFICIENT_CONTENT = "[证据不足待复核]"
HIT_UNCONFIRMED_NOTE = "检索已命中待确认"
HIT_UNCONFIRMED_HEADER = "【检索已命中待确认】"
HIT_UNCONFIRMED_USER_LABEL = "相关材料待核对"
HIT_INVENTORY_NOTE = "已核实材料存在于投标文件，待核适用性"

PersistAction = Literal["not_found", "insufficient", "pack_as_partial"]

_PLACEHOLDER_CONTENTS = frozenset(
    {
        NOT_FOUND_CONTENT,
        EXTRACTION_FAILED_CONTENT,
        EVIDENCE_INSUFFICIENT_CONTENT,
    }
)


@dataclass(frozen=True)
class PersistVerdict:
    """提取未确认时的落库动作。"""

    action: PersistAction
    relevance_note: Optional[str] = None


def is_not_found_content(content: str | None) -> bool:
    return (content or "").strip() == NOT_FOUND_CONTENT


def is_evidence_insufficient_content(content: str | None) -> bool:
    return (content or "").strip() == EVIDENCE_INSUFFICIENT_CONTENT


def is_unconfirmed_response_content(content: str | None) -> bool:
    """占位响应: 未找到 / 提取失败 / 证据不足. matcher 与审查共用此单源。"""
    return (content or "").strip() in _PLACEHOLDER_CONTENTS


def pack_has_substantial_hits(contents: Sequence[str]) -> bool:
    """证据包是否含非低信息、达最低实质长度的片段。"""
    from app.core.retriever.evidence_ranker import is_low_information_content

    for raw in contents:
        text = (raw or "").strip()
        if len(text) < 5:
            continue
        if is_low_information_content(text):
            continue
        return True
    return False


def decide_bid_response_persist(
    *,
    facet_missing: bool,
    starvation: bool,
    pack_has_substantial: bool,
    inventory_material_hit: bool = False,
    inventory_pages: Sequence[int] | None = None,
) -> PersistVerdict:
    """found=False 时的落库判定(纯函数)。

    库存材料级强命中禁止 not_found。饥饿或分面缺口优先于「包内有命中」:
    未搜尽不得把命中当已确认响应, 也不得写成业务「未找到」。
    """
    if inventory_material_hit:
        pages = [int(page) for page in (inventory_pages or ()) if page]
        page_note = "、".join(f"第{page}页" for page in pages[:5])
        note = (
            HIT_INVENTORY_NOTE
            if not page_note
            else f"{HIT_INVENTORY_NOTE}（{page_note}）"
        )
        return PersistVerdict(action="pack_as_partial", relevance_note=note)
    if facet_missing or starvation:
        return PersistVerdict(
            action="insufficient",
            relevance_note=EVIDENCE_INSUFFICIENT_CONTENT,
        )
    if pack_has_substantial:
        return PersistVerdict(
            action="pack_as_partial",
            relevance_note=HIT_UNCONFIRMED_NOTE,
        )
    return PersistVerdict(action="not_found")


def build_pack_partial_content(
    snippets: Sequence[str],
    *,
    char_limit: int = 1800,
) -> str:
    """落库短状态, 供用户面识别未确认命中。

    原文不得写入 content；审查阅读走 bid_response_chunks / raw_evidence。
    char_limit 保留兼容调用方, 短状态远低于该上限。
    """
    del char_limit
    if not pack_has_substantial_hits(snippets):
        return EVIDENCE_INSUFFICIENT_CONTENT
    return HIT_UNCONFIRMED_HEADER


def is_hit_unconfirmed_content(content: str | None) -> bool:
    """检索包未确认状态(含历史 dump 前缀), 不是未找到/证据不足占位符。"""
    text = (content or "").lstrip()
    return text.startswith(HIT_UNCONFIRMED_HEADER) or text == HIT_UNCONFIRMED_NOTE


def user_facing_response_text(
    *,
    content: str | None = None,
    summary: str | None = None,
) -> str:
    """用户可见投标响应正文: 优先审查摘要, 禁止展示检索原文 dump。"""
    summary_text = (summary or "").strip()
    content_text = (content or "").strip()
    dump = is_hit_unconfirmed_content(content_text) or is_hit_unconfirmed_content(
        summary_text
    )
    if dump:
        if summary_text and not is_hit_unconfirmed_content(summary_text):
            return summary_text
        return HIT_UNCONFIRMED_USER_LABEL
    return summary_text or content_text
