"""
职责: 区分招标义务句与废标失效情形句，供覆盖认领比对。
不做: 不改写展示原文、不调用 LLM、不落库、不把禁止项（不得/禁止）当成失效情形。
"""

from __future__ import annotations

from app.core.extractor.mandatory.signals import CONSEQUENCE_PHRASES
from app.core.extractor.tender_extractor_parts.dedup_normalize import normalize_dedup_key

# 废标列举常用「未做成 X」失效情形，不是「不得做 X」禁止项。
_CIRCUMSTANCE_MARKERS: tuple[str, ...] = (
    "未按要求",
    "未按规定",
    "未将",
    "未提交",
    "未提供",
    "未递交",
    "未携带",
    "未带到",
    "未附送",
    "未出具",
    "未附",
)

_PROHIBITION_MARKERS: tuple[str, ...] = (
    "不得",
    "禁止",
    "严禁",
    "不允许",
    "不可",
)

# 仅比对时去掉义务情态，避免「需将 X」与「未将 X」对不上。
_OBLIGATION_MODALS: tuple[str, ...] = (
    "必须",
    "应当",
    "应该",
    "须",
    "应",
    "需",
    "将",
)


def is_prohibition_voice(text: str | None) -> bool:
    """原文带禁止极性时，不得按失效情形去否定词比对。"""
    raw = str(text or "")
    return any(marker in raw for marker in _PROHIBITION_MARKERS)


def is_failure_circumstance_voice(text: str | None) -> bool:
    """「未提交/未将/未提供」等失效情形句；禁止项一律除外。"""
    raw = str(text or "")
    if not raw or is_prohibition_voice(raw):
        return False
    return any(marker in raw for marker in _CIRCUMSTANCE_MARKERS)


def strip_circumstance_markers(text: str | None) -> str:
    """仅供比对：去掉失效情形标记。不用于展示或落库。"""
    raw = str(text or "")
    for marker in _CIRCUMSTANCE_MARKERS:
        raw = raw.replace(marker, "")
    return raw


def _condition_match_payload(text: str | None) -> str:
    """仅供比对：去掉失效标记、后果尾与义务情态后的条件核。"""
    raw = strip_circumstance_markers(text)
    for marker in CONSEQUENCE_PHRASES:
        raw = raw.replace(marker, "")
    for marker in _OBLIGATION_MODALS:
        raw = raw.replace(marker, "")
    return raw.replace("的，", "").replace("的,", "")


COVER_RATIO_THRESHOLD = 0.55


def bigram_hit_ratio(haystack: str, needle: str) -> float:
    """needle 的归一化双字在 haystack 中的命中率（0-1）；包含关系记 1.0。"""
    hay = normalize_dedup_key(haystack)
    ned = normalize_dedup_key(needle)
    if not hay or not ned:
        return 0.0
    if ned in hay or hay in ned:
        return 1.0
    grams = {ned[i : i + 2] for i in range(len(ned) - 1)}
    if not grams:
        return 0.0
    return sum(1 for gram in grams if gram in hay) / len(grams)


def normalized_covers(haystack: str, needle: str) -> bool:
    """归一化后的包含或双字重叠，用于认领原子条件/信号。"""
    ratio = bigram_hit_ratio(haystack, needle)
    if ratio >= 1.0:
        return True
    if len(normalize_dedup_key(needle)) < 8:
        return False
    return ratio >= COVER_RATIO_THRESHOLD


def _payload_covers(haystack: str, needle: str) -> bool:
    """条件核比对；剥离后短核用四字窗，避免「营业执照副本」被 8 字门槛丢掉。"""
    if normalized_covers(haystack, needle):
        return True
    hay = normalize_dedup_key(haystack)
    ned = normalize_dedup_key(needle)
    if not hay or len(ned) < 4 or len(ned) >= 8:
        return False
    return any(ned[i : i + 4] in hay for i in range(len(ned) - 3))


def covers_same_condition(existing: str, candidate: str) -> bool:
    """义务句与对应「未 X」情形句视为同一条件；禁止项不参与去否定比对。"""
    if normalized_covers(existing, candidate):
        return True
    if is_prohibition_voice(existing) or is_prohibition_voice(candidate):
        return False
    if not (
        is_failure_circumstance_voice(existing)
        or is_failure_circumstance_voice(candidate)
    ):
        return False
    existing_payload = _condition_match_payload(existing)
    candidate_payload = _condition_match_payload(candidate)
    return (
        _payload_covers(existing, candidate_payload)
        or _payload_covers(candidate, existing_payload)
        or _payload_covers(existing_payload, candidate_payload)
        or _payload_covers(candidate_payload, existing_payload)
    )
