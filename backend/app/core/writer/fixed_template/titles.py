"""固定模板标题归一与别名匹配。

职责: 将章节标题、模板标题归一为稳定 template_key。
不做: 不读取正文、不判断模板边界、不访问数据库。
"""

from __future__ import annotations

import re

from app.core.writer.fixed_template.models import TemplateMatchResult
from app.core.writer.numbering import strip_existing_number

KNOWN_TEMPLATE_KEYS: tuple[str, ...] = (
    "bid_letter",
    "bid_security_certificate",
    "legal_representative_certificate",
    "legal_representative_authorization",
    "bidder_basic_info",
    "bid_opening_table",
    "bid_quote",
)


_TEMPLATE_ALIASES: dict[str, tuple[str, ...]] = {
    "bid_letter": (
        "投标函",
        "投标书",
        "投标响应函",
    ),
    "bid_opening_table": (
        "开标一览表",
        "开标一览",
        "报价一览表",
    ),
    "bid_security_certificate": (
        "投标保证金缴纳证明",
        "投标保证金证明",
        "保证金缴纳证明",
    ),
    "legal_representative_certificate": (
        "法定代表人证明书",
        "法定代表人身份证明",
        "法定代表人身份证明书",
        "法定代表人证明",
        "法人代表证明书",
        "法人身份证明",
    ),
    "legal_representative_authorization": (
        "法定代表人授权委托书",
        "法定代表人授权书",
        "授权委托书",
        "法人授权委托书",
    ),
    "bid_quote": (
        "投标报价书",
        "投标报价",
        "报价书",
        "投 标 报 价 书",
    ),
    "bidder_basic_info": (
        "投标人基本情况表",
        "投标人基本情况及人员配置表及公司简介",
        "投标人基本情况",
    ),
}

def normalize_template_title(title: str) -> str:
    """归一模板标题，去编号、空白和常见后缀。"""
    text = strip_existing_number((title or "").strip())
    text = re.sub(r"\s+", "", text)
    text = text.replace("（盖单位章）", "").replace("(盖单位章)", "")
    text = text.replace("格式", "").replace("模板", "")
    text = text.replace("文件", "")
    return text


_KEY_BY_ALIAS = {
    normalize_template_title(alias): template_key
    for template_key, aliases in _TEMPLATE_ALIASES.items()
    for alias in aliases
}

# 词典外长尾模板的开放 key 前缀; custom key 仅由 LLM 识别产生,
# 按归一标题相等匹配, 不参与别名子串推断。
CUSTOM_TEMPLATE_KEY_PREFIX = "custom:"
_FRAGMENT_COVERAGE_MIN = 0.6


def custom_template_key(title: str) -> str | None:
    """把词典外模板标题归一为开放 custom key; 标题过短(噪音)返回 None。"""
    normalized = normalize_template_title(title)
    if len(normalized) < 4:
        return None
    return f"{CUSTOM_TEMPLATE_KEY_PREFIX}{normalized}"


def is_custom_template_key(template_key: str | None) -> bool:
    """判断是否为词典外开放 key。"""
    return bool(template_key) and str(template_key).startswith(CUSTOM_TEMPLATE_KEY_PREFIX)


def infer_fixed_template_key(title: str) -> str | None:
    """从标题推断固定模板 key。: "标题 ⊂ 别名"方向要求标题覆盖别名长度 >= 60%, 防止碎片标题
 (如"公司简介")反向命中复合别名("投标人基本情况及人员配置表及公司简介")
 导致同一模板被多个节点错误认领。
 """
    normalized = normalize_template_title(title)
    if not normalized:
        return None
    for norm_alias, template_key in _KEY_BY_ALIAS.items():
        if normalized == norm_alias or norm_alias in normalized:
            return template_key
        if (
            normalized in norm_alias
            and len(normalized) / len(norm_alias) >= _FRAGMENT_COVERAGE_MIN
        ):
            return template_key
    return None


def aliases_for_key(template_key: str) -> tuple[str, ...]:
    """返回 template_key 的标题别名。"""
    return _TEMPLATE_ALIASES.get(template_key, ())


def canonical_title(template_key: str, fallback: str = "") -> str:
    """返回 template_key 的标准标题。"""
    aliases = aliases_for_key(template_key)
    return aliases[0] if aliases else fallback


def match_template_title(node_title: str, template_title: str, template_key: str | None = None) -> TemplateMatchResult:
    """判断章节标题与模板标题是否指向同一固定模板。: 子串命中同样受碎片覆盖率约束(短侧 >= 长侧 60%),
 防止"公司简介"这类碎片标题经子串通道认领复合标题模板。
 """
    node_key = infer_fixed_template_key(node_title)
    source_key = template_key or infer_fixed_template_key(template_title)
    if node_key and source_key and node_key == source_key:
        return TemplateMatchResult(True, node_key, 0.98, "template_key_match")

    left = normalize_template_title(node_title)
    right = normalize_template_title(template_title)
    if left and right:
        short, long = sorted((left, right), key=len)
        if short == long or (
            short in long and len(short) / len(long) >= _FRAGMENT_COVERAGE_MIN
        ):
            return TemplateMatchResult(True, source_key or node_key, 0.88, "title_substring_match")

    return TemplateMatchResult(False, source_key or node_key, 0.0, "title_mismatch")
