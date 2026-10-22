"""反向定性陈述与推测语境识别器

用途：
- 识别 LLM 输出 finding 中的"未发现串标线索"类反向陈述
- 识别 LLM 输出 finding 中的"假设/疑似"类推测语境（C2 阶段）

将命中项从证据列表中剔除并转入关注项 (attention_items.negative_finding /
attention_items.speculative_finding)。

设计原则：
- 仅做"反向/推测"识别，不做正向证据过滤
- 命中即降级，未命中则按原流程走
- 用于兜底 Prompt 红线未被 LLM 严格遵守的情况

常见反向陈述模式（修复时已收集）：
- "两份投标文件的技术方案核心内容不同"
- "已命中白名单不构成串标证据"
- "属于招标文件模板或行业惯例内容"
- "未发现雷同/未发现关联"
- "差异显著/互不相同/无实质性雷同"

常见推测语境模式（C2 新增，对应图2/图3）：
- "例如将'防腐'误写为'防府'（假设）"
- "可能存在格式雷同"
- "疑似采用同一软件生成"
- "估计来自同一作者"
- "或许存在关联"
- "尚不明确/有待核实"
"""

from __future__ import annotations

import re
from typing import Final, Optional

# 强语义反向陈述：明确表达"未发现/不构成串标"
_NEGATIVE_PATTERNS: Final[list[re.Pattern]] = [
    re.compile(r"不构成串[通标]"),
    re.compile(r"无实质性\s*[雷同重叠关联]"),
    re.compile(r"已命中白名单"),
    re.compile(r"属于(?:招标[文]?件)?\s*模板"),
    re.compile(r"属于(?:行业|国家|标准|法规)[规范惯例条文]"),
    re.compile(r"(?:不同|无关|互无).{0,12}(?:不构成|无串标|属正常)"),
    re.compile(r"未发现.{0,12}(?:雷同|重叠|关联|串标|可疑|线索)"),
    re.compile(r"(?:行业|国家)\s*惯例|行业标准[内容条文]?"),
    re.compile(r"招标[文]?件\s*模板|招标(?:统一)?要求(?:导致|造成|带来)"),
    re.compile(r"差异\s*显著.{0,8}(?:不|无)"),
    re.compile(r"互不相同.{0,8}(?:不|无)"),
    re.compile(r"已\s*豁免|应\s*豁免"),
    re.compile(r"白名单\s*覆盖|白名单\s*命中"),
]

# 硬命中模式：整句几乎完全是反向陈述，应直接降级
_DENY_HARD_PATTERNS: Final[list[re.Pattern]] = [
    re.compile(r"^.{0,40}核心内容\s*不同.{0,40}$"),
    re.compile(r"^.{0,40}技术方案.{0,16}不同.{0,40}$"),
    re.compile(r"^.{0,40}主要集中在.{0,30}通用.{0,30}白名单.{0,30}$"),
    re.compile(r"^.{0,40}属于.{0,12}模板.{0,40}$"),
]

# C2: 推测语境模式 — 表明 LLM 在猜测而非给出实证
# 这些词出现在 finding 内容中即视为推测，不应作为正式证据展示。
# "可能" 用 negative lookahead 排除"可能性较低"等正常表述。
_SPECULATIVE_PATTERNS: Final[list[re.Pattern]] = [
    re.compile(r"假设(?![已被])"),  # "假设" 但排除"假设已被"等
    re.compile(r"疑似"),
    re.compile(r"可能(?!性\s*较低|性\s*偏低)"),  # "可能" 排除"可能性较低"
    re.compile(r"或许"),
    re.compile(r"大概"),
    re.compile(r"估计"),
    re.compile(r"例如(?:将)?"),  # "例如将'防腐'误写为'防府'（假设）"
    re.compile(r"尚不[明清]"),
    re.compile(r"尚需\s*进一步"),
    re.compile(r"有待\s*核实"),
    re.compile(r"不排除"),
    re.compile(r"推测"),
    re.compile(r"猜测"),
]

# C2: 推测语境硬命中（整句基调即推测）
_SPECULATIVE_HARD_PATTERNS: Final[list[re.Pattern]] = [
    re.compile(r"\(假设\)|（假设）"),
    re.compile(r"^.{0,30}假设.{0,40}$"),
]


def is_negative_finding(text: Optional[str]) -> tuple[bool, Optional[str]]:
    """识别反向定性陈述。

    Args:
        text: finding 的 summary + detail 拼接文本

    Returns:
        (is_negative, pattern_label)
        is_negative: 是否为反向陈述
        pattern_label: 命中的模式标签，未命中时为 None
    """
    if not text:
        return False, None
    s = text.strip()
    if not s:
        return False, None

    for pat in _DENY_HARD_PATTERNS:
        if pat.search(s):
            return True, "explicit_no_collusion"

    for pat in _NEGATIVE_PATTERNS:
        if pat.search(s):
            return True, pat.pattern

    return False, None


def is_speculative_finding(text: Optional[str]) -> tuple[bool, Optional[str]]:
    """识别推测语境（C2 新增）。

    Args:
        text: finding 的 summary + detail 拼接文本

    Returns:
        (is_speculative, pattern_label)
    """
    if not text:
        return False, None
    s = text.strip()
    if not s:
        return False, None

    for pat in _SPECULATIVE_HARD_PATTERNS:
        if pat.search(s):
            return True, "explicit_speculation"

    for pat in _SPECULATIVE_PATTERNS:
        if pat.search(s):
            return True, pat.pattern

    return False, None


def assess_finding_polarity(finding: dict) -> tuple[bool, Optional[str]]:
    """评估 finding 是否为反向陈述。

    Args:
        finding: LLM 输出的 finding dict（含 summary/detail 等字段）

    Returns:
        (is_negative, pattern_label)
    """
    if not isinstance(finding, dict):
        return False, None

    summary = str(finding.get("summary") or "")
    detail = str(finding.get("detail") or "")
    combined = f"{summary} | {detail}".strip(" |")

    return is_negative_finding(combined)


def _has_evidence_anchors(finding: dict) -> bool:
    """判定 finding 是否具备四个实证锚点（quote_a + quote_b + page_a + page_b）。

    设计意图（P0-2）：
    - 若 finding 同时具备双方原文摘录与双方位置，说明 LLM 已给出可追溯的实证，
      即便文本中含有"可能/估计/例如"等中性推测词，也属于合规推断而非空想猜测，
      不应被 _SPECULATIVE_PATTERNS 软模式降级到 attention_items.speculative_finding。
    - 沿用 app.schemas.collusion.sanitize_quote 的语义空值口径（"无/null/N/A/-" 等
      视为空），与正式证据提取路径保持一致。
    - page 字段直接判断 trim 后是否非空字符串/非空 None；不引入复杂解析。
    """
    if not isinstance(finding, dict):
        return False
    from app.schemas.collusion import sanitize_quote

    quote_a = sanitize_quote(finding.get("quote_a"))
    quote_b = sanitize_quote(finding.get("quote_b"))
    if not quote_a or not quote_b:
        return False

    def _has_page(value: object) -> bool:
        if value is None:
            return False
        text = str(value).strip()
        return bool(text) and text.lower() not in {"null", "none", "n/a", "na", "-", "--"}

    if not _has_page(finding.get("page_a")):
        return False
    if not _has_page(finding.get("page_b")):
        return False
    return True


def assess_finding_speculative(finding: dict) -> tuple[bool, Optional[str]]:
    """评估 finding 是否为推测语境（C2 新增，P0-2 增加锚点豁免）。

 Args:
 finding: LLM 输出的 finding dict（含 summary/detail 等字段）

 Returns:
 (is_speculative, pattern_label)

 P0-2 行为变更：
 - 当 finding 具备完整四元实证锚点（quote_a + quote_b + page_a + page_b 全部非空）
 时，直接判定为非推测（返回 False, None），避免合规推断被误降级。
 - 锚点不全的 finding 仍走 is_speculative_finding 软/硬模式识别链路，保持
 修复的拦截能力。

 注：
 - is_speculative_finding 文本级判定保持不变（供独立单元测试覆盖纯模式分支）。
 - assess_finding_polarity（反向陈述识别）完全不变。
 """
    if not isinstance(finding, dict):
        return False, None

    if _has_evidence_anchors(finding):
        return False, None

    summary = str(finding.get("summary") or "")
    detail = str(finding.get("detail") or "")
    combined = f"{summary} | {detail}".strip(" |")

    return is_speculative_finding(combined)
