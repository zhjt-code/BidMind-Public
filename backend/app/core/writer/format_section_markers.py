"""
职责: 提供写标投标文件格式章节的纯文本识别规则。
不做: 不选择页码范围、不访问数据库、不调用LLM。
"""

from __future__ import annotations

import re

_FORMAT_SECTION_RE = re.compile(
    r"(投标文件格式|商务.*部分格式|技术.*部分格式|价格.*部分格式|报价.*部分格式|资格.*部分格式)"
)

# 严格匹配"第N章/部分/篇/节 + 投标/响应文件格式"
_EXPLICIT_FORMAT_CHAPTER_RE = re.compile(
    r"第\s*[一二三四五六七八九十百零\d]+\s*(?:章|部分|篇|节)\s*"
    r"(?:投标|响应)文件(?:格式|格 式)?"
)

# 目录点线引导：同行内 3+ 个点号字符
_TOC_DOT_PATTERN = re.compile(r"[.．…·]{3,}")
_CHAPTER_HEADING_PREFIX_RE = re.compile(
    r"^第[一二三四五六七八九十百零\d]+(?:章|部分|篇|节)"
)
_TRAILING_PUNCT_RE = re.compile(r"[。．.、，,；;：:]+$")
# 去章号/条目号后的定位章名：含电子投标文件格式、投标文件目录，不含资格/商务部分格式
_LOCATOR_TITLE_RE = re.compile(
    r"^(?:电子)?(?:投标|响应)文件(?:格式|目录)$"
    r"|^(?:封面及)?目录格式$"
)
_LEADING_ITEM_NO_RE = re.compile(
    r"^(?:[一二三四五六七八九十百千]+、|\d+(?:[-.]\d+)*[、.]?|[（(]\d+[）)])"
)


def is_format_section_title(title: str) -> bool:
    """判断目录条目标题是否为投标文件格式相关章节。"""

    compact = re.sub(r"\s+", "", title or "")
    return bool(_FORMAT_SECTION_RE.search(compact))


def has_explicit_format_chapter_marker(text: str) -> bool:
    """判断正文是否含明确的投标/响应文件格式章节标题。

    注意：此函数不排除目录点线引导行，目录页条目同样返回 True。
    fixed_template 的 section_locator 仍依赖此行为，禁止修改语义。
    """

    return bool(_EXPLICIT_FORMAT_CHAPTER_RE.search(text or ""))


def is_format_chapter_locator_title(title: str) -> bool:
    """判断标题是否为招标文件「投标/响应文件格式」章定位名。

    只匹配章标题本身，不匹配其下真分卷名（如商务部分格式、资格证明文件格式）。
    """

    compact = re.sub(r"\s+", "", title or "")
    compact = _CHAPTER_HEADING_PREFIX_RE.sub("", compact)
    compact = _LEADING_ITEM_NO_RE.sub("", compact)
    compact = _TRAILING_PUNCT_RE.sub("", compact)
    return bool(_LOCATOR_TITLE_RE.fullmatch(compact))


def is_format_chapter_toc_page_title(title: str) -> bool:
    """判断标题是否为格式章内的印刷「目录」页，而非投标章节。"""

    compact = re.sub(r"\s+", "", title or "")
    compact = _LEADING_ITEM_NO_RE.sub("", compact)
    return compact == "目录"


def has_line_anchored_format_chapter_heading(text: str) -> bool:
    """判断文本中是否存在行首格式章标题（排除目录页点线引导行）。

    规则：
    - 对每一行 strip 后用 re.match（行首锚定）匹配格式章模式。
    - 若同一行在标题后出现 3 个及以上点号（`.`/`．`/`…`/`·`），
      判定为目录页条目，跳过该行。
    - 交叉引用不在行首（如"投标人按照第七章 投标文件格式编制"），不命中。
    - 正文标题行独占行或首行出现格式章标题时返回 True。
    """

    for line in (text or "").split("\n"):
        stripped = line.strip()
        m = _EXPLICIT_FORMAT_CHAPTER_RE.match(stripped)
        if not m:
            continue
        after = stripped[m.end():]
        if _TOC_DOT_PATTERN.search(after):
            # 目录页点线条目，排除
            continue
        return True
    return False
