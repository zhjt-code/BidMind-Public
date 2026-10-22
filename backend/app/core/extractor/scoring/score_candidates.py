"""分值来源冲突检测（阶段3, Scoring IR 显式化第一步）。

职责: 检测评分项"权威分值列取值"与"规则文本内显式满分声明"之间的矛盾, 产出
 score_conflict 诊断文案(提示不改值, 分值列权威保持—— 来源优先级)。
不做: 不改写分值、不解析表格、不做多候选仲裁(完整 ScoreCandidate 模型的后续步骤)。

判定从严(高精度优先, 两类高置信信号):
- "本项/该项满分N分"整项前缀声明与分值列不等(前缀锚定整项语义);
- 无前缀"满分N分"声明值 **大于** 分值列取值(子构成满分只会小于整项, 超过即矛盾——
 1546实测"满分4分"是6分项的子构成, 小于列值属合法, 不作冲突)。
分档区间(优6-8/良3-6)是同项内部档位, 不作冲突信号(误报源)。
"""

from __future__ import annotations

import re

_ITEM_SCOPED_MAX_RE = re.compile(r"(?:本项|该项)满分[为:：]?\s*[（(]?\s*(\d+(?:\.\d+)?)\s*分?\s*[）)]?")
_BARE_MAX_RE = re.compile(r"满分[为:：]?\s*[（(]?\s*(\d+(?:\.\d+)?)\s*分?\s*[）)]?")
# 治理阶段5 消歧: 原名 _TOLERANCE，与 group_closure 同名不同义（见该处注释）。
# 本处是候选分值与目标分值的**数值精度**容差。仅改名不改值。
_SCORE_MATCH_TOLERANCE = 0.01


def detect_score_rule_conflict(score: float, rule_text: str) -> str | None:
    """规则文本显式满分声明与分值列取值矛盾时返回诊断文案, 否则 None。"""
    compact = re.sub(r"\s+", "", str(rule_text or ""))
    if not compact:
        return None
    conflicting: set[float] = set()
    for match in _ITEM_SCOPED_MAX_RE.finditer(compact):
        value = _to_float(match.group(1))
        if value is not None and abs(value - float(score)) > _SCORE_MATCH_TOLERANCE:
            conflicting.add(value)
    for match in _BARE_MAX_RE.finditer(compact):
        value = _to_float(match.group(1))
        if value is not None and value - float(score) > _SCORE_MATCH_TOLERANCE:
            conflicting.add(value)
    if not conflicting:
        return None
    declared_text = "/".join(f"{value:g}分" for value in sorted(conflicting))
    return (
        f"分值列取值{score:g}分与规则文本内满分声明({declared_text})疑似矛盾，"
        f"已按分值列为准，请人工核对"
    )


def _to_float(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


__all__ = ["detect_score_rule_conflict"]
