"""
职责: 材料名词规范化（NFKC/剥泛主体与尾部噪声）与两级匹配判定
不做: 检索、分级、词表维护
"""

from __future__ import annotations

import unicodedata
from typing import Optional

from app.core.document_qa.normalizers import normalize_slot_value
from app.core.retriever.evidence_coverage import (
    _GENERIC_PREFIXES,
    _TAIL_NOISE,
    _normalize,
)
from app.core.retriever.query_decomposer import _split_on_particles

MATCH_STRONG = "strong"
MATCH_WEAK = "weak"
MATCH_NONE = "none"

# 全键包含升强匹配的最短键长; 2 字泛中心语(证书/证明)只作弱匹配
_CONTAINS_STRONG_MIN = 3


def canonical_material_key(raw: str) -> str:
    """NFKC + 去空白 + 剥泛主体前缀与尾部噪声, 得到稳定材料键。"""
    text = _normalize(unicodedata.normalize("NFKC", raw or ""))
    if not text:
        return ""
    prefixes = sorted(_GENERIC_PREFIXES, key=len, reverse=True)
    for prefix in prefixes:
        if text.startswith(prefix) and len(text) - len(prefix) >= 2:
            text = text[len(prefix) :]
            break
    stripped = _TAIL_NOISE.sub("", text)
    return stripped if len(stripped) >= 2 else text


def _match_canonical_left(
    left_key: str,
    right: str,
    *,
    left_identifier: Optional[str] = None,
    right_identifier: Optional[str] = None,
    left_subject: Optional[str] = None,
    right_subject: Optional[str] = None,
) -> str:
    """左键已规范化的两级匹配核心; 右侧仍走 canonical_material_key。

    供 match_material_keys（左键已 canonical 后委托）和 match_key_in_compact
    慢路径（窗口左键不重复 canonical）两处复用; 禁止再复制一份匹配逻辑。
    """
    right_key = canonical_material_key(right)
    if not left_key or not right_key:
        return MATCH_NONE

    if _values_conflict(left_identifier, right_identifier, kind="identifier"):
        return MATCH_NONE
    if _values_conflict(left_subject, right_subject, kind="org_name"):
        return MATCH_NONE

    if left_key == right_key:
        return MATCH_STRONG

    common = _longest_common_suffix(left_key, right_key)
    mods_left = _modifier_tokens(
        left_key[: len(left_key) - len(common)] if common else left_key
    )
    mods_right = _modifier_tokens(
        right_key[: len(right_key) - len(common)] if common else right_key
    )
    both_have_mods = bool(mods_left) and bool(mods_right)
    overlap = _modifier_overlap(mods_left, mods_right)

    if both_have_mods and overlap == 0:
        return MATCH_NONE

    shorter, longer = (
        (left_key, right_key)
        if len(left_key) <= len(right_key)
        else (right_key, left_key)
    )
    contained = shorter in longer
    if contained and len(shorter) >= _CONTAINS_STRONG_MIN:
        return MATCH_STRONG
    if common and overlap >= 1:
        return MATCH_STRONG
    if contained or (common and not both_have_mods):
        return MATCH_WEAK
    return MATCH_NONE


def match_material_keys(
    left: str,
    right: str,
    *,
    left_identifier: Optional[str] = None,
    right_identifier: Optional[str] = None,
    left_subject: Optional[str] = None,
    right_subject: Optional[str] = None,
) -> str:
    """两级匹配: strong 可硬定谳, weak 仅召回, none 拒绝。

    证号/主体只作佐证轴: 双方均有且冲突则不互认; 一致也不得把弱匹配
    或修饰语零重叠桥接成强匹配。
    """
    left_key = canonical_material_key(left)
    if not left_key:
        return MATCH_NONE
    return _match_canonical_left(
        left_key,
        right,
        left_identifier=left_identifier,
        right_identifier=right_identifier,
        left_subject=left_subject,
        right_subject=right_subject,
    )


def normalize_material_compact(text: str) -> str:
    """NFKC 并去掉空白。同一正文在热路径上只规范化一次。"""
    return _normalize(unicodedata.normalize("NFKC", text or ""))


def match_key_in_compact(key: str, compact: str) -> str:
    """在已规范化正文中定位已规范化材料键。

    长度不少于 2 的窗口针都以键尾两字结尾。尾两字不在正文中时，
    慢路径不可能命中，结果与逐窗扫描相同。
    """
    if not key or not compact:
        return MATCH_NONE
    if key in compact:
        return MATCH_STRONG if len(key) >= _CONTAINS_STRONG_MIN else MATCH_WEAK
    if len(key) >= 2 and key[-2:] not in compact:
        return MATCH_NONE
    best = MATCH_NONE
    for size in range(min(len(key), 8), 1, -1):
        needle = key[-size:]
        start = 0
        while True:
            pos = compact.find(needle, start)
            if pos < 0:
                break
            window_start = max(0, pos + size - max(len(key), 12))
            window = compact[window_start : pos + size]
            # key 已是 canonical; 右侧 window 在 _match_canonical_left 内走 canonical
            level = _match_canonical_left(key, window)
            if level == MATCH_STRONG:
                return MATCH_STRONG
            if level == MATCH_WEAK:
                best = MATCH_WEAK
            start = pos + 1
    return best


def match_material_in_text(noun: str, text: str) -> str:
    """在正文中定位材料键: 先全键包含, 再按中心语窗口做两级匹配。"""
    return match_key_in_compact(
        canonical_material_key(noun),
        normalize_material_compact(text),
    )


def _longest_common_suffix(left: str, right: str) -> str:
    limit = min(len(left), len(right))
    size = 0
    while size < limit and left[-(size + 1)] == right[-(size + 1)]:
        size += 1
    return left[-size:] if size >= 2 else ""


def _modifier_tokens(prefix: str) -> tuple[str, ...]:
    if not prefix:
        return ()
    tokens = tuple(token for token in _split_on_particles(prefix) if token)
    if tokens:
        return tokens
    return (prefix,) if len(prefix) >= 2 else ()


def _modifier_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    hits = 0
    for item in left:
        if any(_tokens_related(item, other) for other in right):
            hits += 1
    return hits


def _tokens_related(left: str, right: str) -> bool:
    """修饰语相关: 包含关系, 或较短一方的每个字都出现在较长一方(社保≈社会保险)。"""
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return all(char in longer for char in shorter)


def _values_conflict(
    left: Optional[str],
    right: Optional[str],
    *,
    kind: str,
) -> bool:
    if not left or not right:
        return False
    normalized_left = normalize_slot_value(kind, left)
    normalized_right = normalize_slot_value(kind, right)
    if not normalized_left or not normalized_right:
        return False
    return normalized_left != normalized_right
