"""
职责: 为招标要求合并去重提供确定性比较键与数字指纹。
不做: 不修改展示原文、不判断语义重复、不调用外部模型。
"""

from __future__ import annotations

import re
import unicodedata

_LEADING_NUMBER_RE = re.compile(
    r"^(?:\(?\d+\)?|[①②③④⑤⑥⑦⑧⑨⑩]|[一二三四五六七八九十]+[、.．])"
)
_PUNCT_SPACE_RE = re.compile(r"[\s\W_]+", re.UNICODE)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def normalize_dedup_key(text: str) -> str:
    """生成仅用于比较的归一化键。"""
    normalized = unicodedata.normalize("NFKC", text or "").strip().lower()
    normalized = _LEADING_NUMBER_RE.sub("", normalized).strip()
    return _PUNCT_SPACE_RE.sub("", normalized)


def numeric_fingerprint(text: str) -> tuple[str, ...]:
    """提取文本中的数字序列，作为近重复自动合并的安全守卫。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    return tuple(_NUMBER_RE.findall(normalized))
