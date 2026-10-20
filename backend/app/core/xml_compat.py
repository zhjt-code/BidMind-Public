"""XML 1.0 文本兼容清洗。

职责: 去掉 lxml / python-docx 拒绝的 XML 1.0 非法字符，使任意外部/LLM 文本可写入 OOXML。
不做: 不处理花括号占位符、不解析 HTML 结构、不改可见标点与合法空白（TAB/LF/CR）。
"""

from __future__ import annotations

import re

# XML 1.0 Char: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
_ILLEGAL_XML_1_0 = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]"
)


def sanitize_xml_compatible_text(text: str | None) -> str:
    """剥离 XML 1.0 非法字符，保留 TAB/LF/CR 与全部合法 Unicode。"""
    if not text:
        return ""
    return _ILLEGAL_XML_1_0.sub("", text)
