"""明细评分表行级语义判定。

职责: 识别行内"卷分组合并单元格"(如"资信标（100分）")与无名子行的规则列取名。
不做: 不解析整表、不产评分项、不判定分值列(scoring_table_parser 职责)、不调用 LLM/DB。
"""

from __future__ import annotations

import re

from app.core.extractor.scoring.score_column_semantics import (
    find_embedded_scores,
    normalize_cell,
)
from app.core.extractor.scoring.weighted_volume_table import volume_from_scoring_label

# 卷分组合并单元格上限长度: "资信标（100分）"= 9 字符; 过长即数据正文, 非分组格。
_VOLUME_GROUP_CELL_MAX_LEN = 12
# 规则列前缀标签: "供货实施方案：..." → "供货实施方案"(不含顿号, 顿号意味着已进正文)。
_RULE_LEADING_LABEL_RE = re.compile(r"^([^：:。；;，,、]{2,24})[：:]")
_VOLUME_GROUP_NAME_RE = re.compile(
    r"^(?:技术|商务|资信|资格|价格|报价|综合|服务)"
    r"(?:标|部分|卷|评审)?(?:[（(].*[）)])?$"
)


def volume_group_marker(row: list[str], content_i: int | None) -> str | None:
    """识别行内"卷分组合并单元格"并返回卷别。

    只判定内容列左侧的短单元格是否为卷标签(含卷关键词, 且带"（N分）"内嵌总分
    或以 标/部分/卷 结尾), 命中返回标准 volume 键, 否则 None。
    """
    limit = content_i if content_i is not None and content_i > 0 else 0
    for idx in range(min(limit, len(row))):
        compact = normalize_cell(row[idx])
        if not compact or len(compact) > _VOLUME_GROUP_CELL_MAX_LEN:
            continue
        vol = volume_from_scoring_label(compact, default="")
        if not vol:
            continue
        embedded = find_embedded_scores(compact)
        label = embedded[0][0] if embedded else compact
        if embedded or label.endswith(("标", "部分", "卷")):
            return vol
    return None


def leading_rule_label(rule: str) -> str:
    """从评分规则前缀"XXX："提取子项名; 无标签返回空串。"""
    matched = _RULE_LEADING_LABEL_RE.match(normalize_cell(rule))
    return matched.group(1) if matched else ""


def rule_first_segment(rule: str) -> str:
    """无前缀标签时取规则首个语义段(顿号/逗号/句号前)作名, 限24字。"""
    compact = normalize_cell(rule)
    if not compact:
        return ""
    segment = re.split(r"[、，,。；;：:]", compact, maxsplit=1)[0]
    return segment[:24]


def is_volume_group_name(text: str) -> bool:
    """判断名称格是否为纯卷分组标签，而不是含卷关键词的具体评分项。"""
    compact = normalize_cell(text)
    return bool(compact and _VOLUME_GROUP_NAME_RE.fullmatch(compact))


__all__ = [
    "is_volume_group_name",
    "leading_rule_label",
    "rule_first_segment",
    "volume_group_marker",
]
