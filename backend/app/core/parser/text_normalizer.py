"""PDF / OCR 文本 Unicode 归一化（修复）。

PDF 字体映射异常会把常用汉字映射到 Kangxi Radicals (U+2F00-U+2FDF) 和
CJK Compatibility Ideographs (U+F900-U+FAFF)，导致下游 LLM 无法语义理解。

`normalize_cjk` 通过 NFKC 归一化将这类字符映射回 CJK Unified Ideographs。
`compat_ratio` 用于检测归一化后仍残留的异常字符占比。
"""

from __future__ import annotations

import unicodedata


def normalize_cjk(text: str) -> str:
    """NFKC 归一化：将 Kangxi Radicals / CJK Compat 映射回标准 CJK 区。

    对于 NFKC 仍处理不了的极少数字符，逐字符 NFKD 分解取首字符兜底。
    """
    if not text:
        return text

    result = unicodedata.normalize("NFKC", text)

    if not _has_compat_chars(result):
        return result

    chars: list[str] = []
    for ch in result:
        code = ord(ch)
        if _is_compat_range(code):
            decomposed = unicodedata.normalize("NFKD", ch)
            chars.append(decomposed[0] if decomposed else ch)
        else:
            chars.append(ch)
    return "".join(chars)


def compat_ratio(text: str) -> float:
    """计算文本中 CJK 兼容/康熙部首字符占比（0.0 ~ 1.0）。

    用于在 LLM 调用前检测输入质量：ratio > 2% 视为 PDF 解析异常。
    """
    if not text:
        return 0.0
    count = sum(1 for ch in text if _is_compat_range(ord(ch)))
    return count / len(text)


def _is_compat_range(code: int) -> bool:
    """判断 Unicode code point 是否在 CJK 兼容区段。"""
    return (0x2E80 <= code <= 0x2FFF) or (0xF900 <= code <= 0xFAFF)


def _has_compat_chars(text: str) -> bool:
    return any(_is_compat_range(ord(ch)) for ch in text)
