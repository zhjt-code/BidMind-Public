"""行业画像视图

职责: 把行业画像 Markdown 正文解析为按节可寻址的视图，并按消费场景提供选段。
不做: 不读数据库、不调用 LLM、不修改画像正文、不做语义改写、不做术语纠错。

背景: 画像正文由 ``industry_profiler`` 以六段式 Markdown 落库，是展示与人工编辑的
单一真源。下游各节点原先统一用 ``content[:N]`` 头部截断，导致排在后面的
"评标专家核心关注点 / 写作风格指导 / 差异化竞争建议"被系统性丢弃。本模块只在
读侧提供分段与按需选段，不引入第二份结构化真源。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# 六节语义键，与 prompts/industry_profile.py 的输出契约按序号一一对应
SECTION_INDUSTRY_TRAITS: Final = "industry_traits"
SECTION_TERMINOLOGY: Final = "terminology"
SECTION_TECHNICAL_STRUCTURE: Final = "technical_structure"
SECTION_REVIEWER_FOCUS: Final = "reviewer_focus"
SECTION_WRITING_STYLE: Final = "writing_style"
SECTION_DIFFERENTIATION: Final = "differentiation"

REQUIRED_PROFILE_SECTIONS: Final[tuple[str, ...]] = (
    SECTION_INDUSTRY_TRAITS,
    SECTION_TERMINOLOGY,
    SECTION_TECHNICAL_STRUCTURE,
    SECTION_REVIEWER_FOCUS,
    SECTION_WRITING_STYLE,
    SECTION_DIFFERENTIATION,
)

_ORDINAL_TO_SECTION: Final[dict[int, str]] = {
    1: SECTION_INDUSTRY_TRAITS,
    2: SECTION_TERMINOLOGY,
    3: SECTION_TECHNICAL_STRUCTURE,
    4: SECTION_REVIEWER_FOCUS,
    5: SECTION_WRITING_STYLE,
    6: SECTION_DIFFERENTIATION,
}

_CN_ORDINALS: Final[dict[str, int]] = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
}

_ORDINAL_PATTERN: Final = (
    r"(?:(?P<digit>[1-6])[ \t]*[.、．)）]|(?P<cn>[一二三四五六])[ \t]*[、.．])"
)

# 首选形态: Markdown 标题 "### 1. xxx"，生产落库画像 100% 为此形态。
# Markdown 井号是强结构信号，据此定位可按序号而非标题文字识别，
# 人工改写标题文字也不影响解析。
_MARKDOWN_HEADING_RE: Final = re.compile(
    r"^[ \t]*#{1,6}[ \t]*" + _ORDINAL_PATTERN + r"[ \t]*(?P<title>\S.*)$",
    re.MULTILINE,
)

# 降级形态: 人工编辑去掉井号后的 "1. xxx" / "一、xxx"。
# 此形态与术语表编号条目（"3. 腐蚀速率"）字面无法区分，
# 必须叠加标题关键词校验，否则术语表第 3~6 条会被误判为第 3~6 节标题并腰斩画像。
_PLAIN_HEADING_RE: Final = re.compile(
    r"^[ \t]*" + _ORDINAL_PATTERN + r"[ \t]*(?P<title>\S.*)$",
    re.MULTILINE,
)

_SECTION_TITLE_KEYWORDS: Final[dict[int, tuple[str, ...]]] = {
    1: ("行业", "特征"),
    2: ("术语",),
    3: ("技术方案", "结构"),
    4: ("评标", "关注"),
    5: ("风格", "文风"),
    6: ("差异", "竞争"),
}

# 术语条目: "1. 术语" / "- 术语" / "* 术语"，允许行首缩进
_TERM_ITEM_RE: Final = re.compile(
    r"^[ \t]*(?:\d{1,2}[.、．)）]|[-*+•])[ \t]*(?P<term>\S.*?)[ \t]*$",
    re.MULTILINE,
)

_TERM_MIN_LEN: Final = 2
_TERM_MAX_LEN: Final = 40

# 剩余预算低于此值时不再做尾节部分填充，避免塞入无信息量的碎片
_MIN_PARTIAL_TAIL_CHARS: Final = 200

# 各消费场景的段落优先级。预算不足时牺牲序列末尾的段落，
# 而非无条件丢弃正文中排在后面的段落。
PURPOSE_CHAPTER_PROFILE: Final = "chapter_profile"
PURPOSE_FREE_WRITING: Final = "free_writing"
PURPOSE_DATA_FILL: Final = "data_fill"
PURPOSE_SECTION_PLAN: Final = "section_plan"
PURPOSE_OUTLINE: Final = "outline"
PURPOSE_SUMMARY: Final = "summary"

_PURPOSE_PRIORITY: Final[dict[str, tuple[str, ...]]] = {
    # 章节画像判定"哪些约束属于哪个章节"，评标关注点与技术结构最有用，风格类无帮助。
    # 关注点排在结构之前: 实测技术结构节长 727~1338 字符而关注点仅 342~712，
    # 结构在前会在长结构项目上整段挤掉关注点。
    PURPOSE_CHAPTER_PROFILE: (
        SECTION_REVIEWER_FOCUS,
        SECTION_TECHNICAL_STRUCTURE,
        SECTION_INDUSTRY_TRAITS,
        SECTION_TERMINOLOGY,
    ),
    # 正文生成已有章节画像与约束，行业特征最可牺牲
    PURPOSE_FREE_WRITING: (
        SECTION_REVIEWER_FOCUS,
        SECTION_WRITING_STYLE,
        SECTION_DIFFERENTIATION,
        SECTION_TERMINOLOGY,
        SECTION_TECHNICAL_STRUCTURE,
        SECTION_INDUSTRY_TRAITS,
    ),
    # 资料填充产出表格与短段落，只需术语专业度与语言风格
    PURPOSE_DATA_FILL: (
        SECTION_TERMINOLOGY,
        SECTION_WRITING_STYLE,
    ),
    PURPOSE_SECTION_PLAN: (
        SECTION_REVIEWER_FOCUS,
        SECTION_DIFFERENTIATION,
        SECTION_TECHNICAL_STRUCTURE,
        SECTION_TERMINOLOGY,
        SECTION_INDUSTRY_TRAITS,
        SECTION_WRITING_STYLE,
    ),
    # 目录生成按正文原序，保持行业认知到结构建议的自然阅读顺序
    PURPOSE_OUTLINE: (
        SECTION_INDUSTRY_TRAITS,
        SECTION_TECHNICAL_STRUCTURE,
        SECTION_REVIEWER_FOCUS,
        SECTION_TERMINOLOGY,
        SECTION_DIFFERENTIATION,
        SECTION_WRITING_STYLE,
    ),
    # 摘要只做称谓与术语对齐，禁止引入建议性内容（否则画像建议会以"前文事实"身份传播）
    PURPOSE_SUMMARY: (
        SECTION_TERMINOLOGY,
    ),
}


@dataclass(frozen=True)
class IndustryProfileView:
    """行业画像的分节视图。

    Attributes:
        parsed: 是否识别出至少一个语义节；False 时调用方应退回原文截断
        sections: 语义键到该节完整文本（含标题行）的映射
        raw: 原始画像正文
    """

    parsed: bool
    sections: dict[str, str]
    raw: str

    def section(self, key: str) -> str:
        """取指定语义节文本，缺失返回空串。"""
        return self.sections.get(key, "")


def parse_industry_profile(content: str | None) -> IndustryProfileView:
    """把行业画像正文解析为分节视图。

    Args:
        content: 画像正文；None、空串或失败兜底文案均返回 parsed=False

    Returns:
        IndustryProfileView: 解析结果；parsed=False 时 sections 为空
    """
    raw = content or ""
    if not raw.strip():
        return IndustryProfileView(parsed=False, sections={}, raw=raw)

    boundaries = _locate_section_boundaries(raw)
    if not boundaries:
        return IndustryProfileView(parsed=False, sections={}, raw=raw)

    sections: dict[str, str] = {}
    for index, (ordinal, start) in enumerate(boundaries):
        end = boundaries[index + 1][1] if index + 1 < len(boundaries) else len(raw)
        body = raw[start:end].strip()
        if body:
            sections[_ORDINAL_TO_SECTION[ordinal]] = body

    return IndustryProfileView(parsed=bool(sections), sections=sections, raw=raw)


def _locate_section_boundaries(raw: str) -> list[tuple[int, int]]:
    """定位各语义节的起始偏移，返回按位置升序的 (序号, 起始位置)。

    Markdown 标题命中时不再回退到纯序号匹配，避免把术语表编号条目
    当成节标题。
    """
    markdown = _collect_boundaries(raw, _MARKDOWN_HEADING_RE, require_keyword=False)
    if markdown:
        return markdown
    return _collect_boundaries(raw, _PLAIN_HEADING_RE, require_keyword=True)


def _collect_boundaries(
    raw: str,
    pattern: re.Pattern[str],
    *,
    require_keyword: bool,
) -> list[tuple[int, int]]:
    """按给定形态收集节边界，同一序号重复出现时保留首次。"""
    boundaries: list[tuple[int, int]] = []
    seen: set[int] = set()
    for match in pattern.finditer(raw):
        ordinal = _resolve_ordinal(match)
        if ordinal is None or ordinal in seen:
            continue
        if require_keyword and not _title_matches_section(ordinal, match.group("title")):
            continue
        seen.add(ordinal)
        boundaries.append((ordinal, match.start()))
    boundaries.sort(key=lambda item: item[1])
    return boundaries


def _title_matches_section(ordinal: int, title: str) -> bool:
    """校验标题文字是否携带该节的语义特征词。"""
    keywords = _SECTION_TITLE_KEYWORDS.get(ordinal, ())
    return any(keyword in title for keyword in keywords)


def _resolve_ordinal(match: re.Match[str]) -> int | None:
    """把标题匹配结果归一为 1-6 的节序号。"""
    digit = match.group("digit")
    if digit:
        return int(digit)
    cn = match.group("cn")
    if cn:
        return _CN_ORDINALS.get(cn)
    return None


def select_profile_sections(
    view: IndustryProfileView,
    *,
    purpose: str,
    max_chars: int,
    allow_partial_tail: bool = True,
) -> str:
    """按消费场景的段落优先级拼装定长画像摘录。

    与头部截断的区别: 预算不足时丢弃的是该场景下优先级最低的段落，
    而不是正文中物理位置靠后的段落。

    Args:
        view: 已解析的画像视图
        purpose: 消费场景，取 PURPOSE_* 常量；未知值按 outline 顺序处理
        max_chars: 字符预算上限
        allow_partial_tail: 预算有余但整节放不下时，是否用该节前缀填满

    Returns:
        str: 拼装后的摘录；view 未解析或预算非正时返回空串
    """
    if not view.parsed or max_chars <= 0:
        return ""

    priority = _PURPOSE_PRIORITY.get(purpose, _PURPOSE_PRIORITY[PURPOSE_OUTLINE])
    parts: list[str] = []
    used = 0
    separator_cost = 2  # 段落之间的空行

    for key in priority:
        body = view.section(key)
        if not body:
            continue
        cost = len(body) + (separator_cost if parts else 0)
        if used + cost <= max_chars:
            parts.append(body)
            used += cost
            continue
        if not allow_partial_tail:
            continue
        remaining = max_chars - used - (separator_cost if parts else 0)
        if remaining >= _MIN_PARTIAL_TAIL_CHARS:
            parts.append(body[:remaining])
            used = max_chars
        break

    return "\n\n".join(parts)


def build_profile_excerpt(
    content: str | None,
    *,
    purpose: str,
    max_chars: int,
) -> str:
    """行业画像摘录主入口，解析失败时退回头部截断。

    供各消费点一行替换原 ``content[:max_chars]``，保证不引入新的失败路径。

    Args:
        content: 画像正文
        purpose: 消费场景，取 PURPOSE_* 常量
        max_chars: 字符预算上限

    Returns:
        str: 场景化摘录；无法解析时为原文头部截断
    """
    if not content or max_chars <= 0:
        return ""
    view = parse_industry_profile(content)
    excerpt = select_profile_sections(view, purpose=purpose, max_chars=max_chars)
    return excerpt or content[:max_chars]


def extract_profile_terminology(
    content: str | None,
    *,
    limit: int = 60,
) -> list[str]:
    """从画像术语表节提取专业术语列表。

    仅消费"专业术语表"节，避免把技术结构或差异化建议中的长句误当术语。

    Args:
        content: 画像正文
        limit: 返回条数上限

    Returns:
        list[str]: 去重后保持原序的术语列表；无术语表节时为空列表
    """
    view = parse_industry_profile(content)
    body = view.section(SECTION_TERMINOLOGY)
    if not body:
        return []

    terms: list[str] = []
    seen: set[str] = set()
    for match in _TERM_ITEM_RE.finditer(body):
        term = _normalize_term(match.group("term"))
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _normalize_term(raw_term: str) -> str:
    """清理术语条目的强调标记与尾随说明，长度越界返回空串。"""
    term = raw_term.strip().strip("*_`").strip()
    # "术语：说明" / "术语 - 说明" 只保留术语本体。
    # 全角冒号在中文行文中后不带空格；半角冒号要求跟空白，避免切断标准号等含冒号术语。
    term = re.split(r"：|:\s|\s[-—–]\s", term, maxsplit=1)[0].strip()
    if not (_TERM_MIN_LEN <= len(term) <= _TERM_MAX_LEN):
        return ""
    return term
