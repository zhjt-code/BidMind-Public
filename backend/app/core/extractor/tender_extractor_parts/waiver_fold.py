"""
职责: 同一事项上，招标文件的具体豁免（「本项目不要求/不进行 X」）优先于引用型模板句
 （「须按招标文件要求提交 X」「按前附表规定…」），把后者折进前者，消除确认页里
 「不要求保证金」与「须提交保证金」并列矛盾的形态。
不做: 不处理禁止项（不得/不接受/禁止 是义务极性，不是豁免）、不改豁免项自身的否决/评审
 标记、不调用 LLM、不碰评分项、不做开放语义合并。

为何确定性而非交给合并 LLM: 两句字面差异大（一句否定一句义务），候选门 cosine 常
不达阈值，模型根本看不到它们同组；豁免 + 引用是可判定的结构关系，不需要模型裁量。
"""

from __future__ import annotations

import re

from app.core.extractor.tender_extractor_parts.dedup_normalize import numeric_fingerprint
from app.schemas.requirement import ExtractedRequirement

# 招标人豁免（对投标人无义务）。不含 不得/不接受/禁止 等禁止极性。
WAIVER_MARKERS: tuple[str, ...] = (
    "不要求", "不需要", "无需", "无须", "不收取", "免收", "不进行", "不组织", "不召开",
)
# 引用型模板句：义务内容委托给别处规定，本句不自含具体值。
DELEGATION_MARKERS: tuple[str, ...] = (
    "按招标文件要求", "按招标文件规定", "按招标文件", "按前附表", "见前附表",
    "按投标须知", "按规定", "按要求", "详见",
)
# 豁免对象 = 豁免标记之后、去掉动词与标点的名词段。
_OBJECT_VERB_RE = re.compile(r"^(?:提交|缴纳|提供|递交|开展|组织|进行|召开|收取|投标人|对)+")
_OBJECT_STOP_RE = re.compile(r"[，。；,;:：（(].*$")
_MIN_OBJECT_LEN = 3


def waiver_object(description: str) -> str:
    """取豁免句的对象名词段；非豁免句返回空串。"""
    text = description or ""
    for marker in WAIVER_MARKERS:
        idx = text.find(marker)
        if idx < 0:
            continue
        tail = _OBJECT_STOP_RE.sub("", text[idx + len(marker):]).strip()
        tail = _OBJECT_VERB_RE.sub("", tail).strip()
        if len(tail) >= _MIN_OBJECT_LEN:
            return tail
    return ""


def is_delegating_clause(description: str) -> bool:
    """引用型模板句：含委托标记、无数字事实、本身不是豁免。"""
    text = description or ""
    if not any(marker in text for marker in DELEGATION_MARKERS):
        return False
    if any(marker in text for marker in WAIVER_MARKERS):
        return False
    return not numeric_fingerprint(text)


def _object_appears_in(obj: str, description: str) -> bool:
    text = description or ""
    return obj in text or (len(obj) > _MIN_OBJECT_LEN and obj[-_MIN_OBJECT_LEN:] in text)


def fold_delegations_into_waivers(
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    """把与豁免项同对象、同类别的引用型条款并入豁免项（来源页/关键词并集）。"""
    waivers: list[tuple[int, str]] = [
        (idx, obj)
        for idx, req in enumerate(requirements)
        if (req.requirement_kind or "normal") == "normal" and (obj := waiver_object(req.description))
    ]
    if not waivers:
        return requirements

    removed: set[int] = set()
    absorbed: dict[int, list[ExtractedRequirement]] = {}
    for idx, req in enumerate(requirements):
        if (req.requirement_kind or "normal") != "normal" or not is_delegating_clause(req.description):
            continue
        for waiver_idx, obj in waivers:
            waiver = requirements[waiver_idx]
            if waiver_idx == idx or waiver.category != req.category:
                continue
            if not _object_appears_in(obj, req.description):
                continue
            removed.add(idx)
            absorbed.setdefault(waiver_idx, []).append(req)
            break

    if not removed:
        return requirements

    result: list[ExtractedRequirement] = []
    for idx, req in enumerate(requirements):
        if idx in removed:
            continue
        if idx in absorbed:
            req = _absorb(req, absorbed[idx])
        result.append(req)
    return result


def _absorb(waiver: ExtractedRequirement, extras: list[ExtractedRequirement]) -> ExtractedRequirement:
    pages = sorted({*(waiver.source_pages or []), *(p for e in extras for p in (e.source_pages or []))})
    keywords = list(dict.fromkeys([*(waiver.keywords or []), *(k for e in extras for k in (e.keywords or []))]))
    merged_count = (waiver.merged_count or 1) + sum((e.merged_count or 1) for e in extras)
    return waiver.model_copy(
        update={"source_pages": pages, "keywords": keywords, "merged_count": merged_count}
    )
