# -*- coding: utf-8 -*-
"""并列评分卷权重声明解析。

职责: 解析"按A、B、C分别编写, 满分均为N, 按A、B、C分别占a%、b%、c%的权重"这一类
 "并行评分卷 + 序位对应权重"声明, 产出 (原文标签, 卷内原始满分, 权重) 三元组。
不做: 不映射枚举卷(volume_parser 职责)、不产评分项、不做闭合校验、不调用 LLM/DB。

为何独立成模块: 该形态与既有 `分值构成: X20分; Y80分` 语义不同——后者的组分值
**即最终得分**, 本形态的组分值是**卷内原始满分**, 最终得分另由权重折算。两者混在
同一函数里会让"原始满分轴"与"折算得分轴"再次同轴相加(根因之一)。

序位对应是本形态唯一的配对依据, 故采纳门禁一律 fail-closed: 标签序列与权重序列
必须等长、卷数>=2、权重和≈100%、共享满分>0; 任一不成立整体拒收, 不做部分采纳。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "满分均为100" / "满分均为 100 分" / "满分为100分"——共享满分锚点(各卷同一原始满分)
_SHARED_MAX_SCORE_RE = re.compile(r"满分均?为\s*(\d+(?:\.\d+)?)\s*分?")
# "按技术标、资信标、商务标分别占30%、15%、55%的权重"
# 标签段与百分数段都允许内嵌空白: 原文常在百分数序列中间断行, 拼窗后带空格。
_SHARE_CLAUSE_RE = re.compile(
    r"按\s*([\u4e00-\u9fff、，,和及\s]{2,80}?)\s*分别占\s*"
    r"([\d.\s%％、，,和及]+?)\s*的?\s*权重"
)
# 同句中任意"按<卷列表>分别…"枚举(编写/编制/占…)。用于完备性核对: 声明列举了几卷,
# 权重就必须给几卷。刻意不绑定具体动词——动词是开放集合, 卷列表的结构位置不是。
_ENUMERATION_RE = re.compile(r"按\s*([\u4e00-\u9fff、，,和及\s]{2,80}?)\s*分别")
_LABEL_SPLIT_RE = re.compile(r"[、，,和及\s]+")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
# 治理阶段5 消歧: 原名 _WEIGHT_SUM_TOLERANCE 与 scoring_scope_validator 中同名常量
# 取值不同(1.0 vs 0.02)。本处校验的是**百分比份额之和**(以 100 为基准, 容差 1 个百分点),
# 与 scope_validator 校验的**权重小数和**(以 1.0 为基准, 容差 0.02)不同量纲。仅改名不改值。
_SHARE_PERCENT_SUM_TOLERANCE = 1.0
_MIN_PARALLEL_VOLUMES = 2
_MAX_LABEL_LEN = 20


@dataclass(frozen=True)
class WeightedVolumeShare:
    """单个并行评分卷: 原文标签 + 卷内原始满分 + 最终得分权重(0-1)。"""

    label: str
    max_score: float
    weight: float


def parse_weighted_share_composition(text: str) -> list[WeightedVolumeShare]:
    """解析并列卷权重声明; 任一门禁不成立返回空列表(整体拒收)。"""
    raw = str(text or "")
    if not raw:
        return []
    max_score = _shared_max_score(raw)
    if max_score is None:
        return []
    clause = _SHARE_CLAUSE_RE.search(raw)
    if clause is None:
        return []
    labels = _split_labels(clause.group(1))
    weights = [float(v) for v in _PERCENT_RE.findall(clause.group(2))]
    if len(labels) < _MIN_PARALLEL_VOLUMES or len(labels) != len(weights):
        return []
    if any(w <= 0 for w in weights):
        return []
    if abs(sum(weights) - 100.0) > _SHARE_PERCENT_SUM_TOLERANCE:
        return []
    if len(labels) < _max_enumerated_volume_count(raw):
        # 声明列举的卷比拿到权重的卷多 -> 有卷未被赋权, 声明覆盖不完整。
        return []
    return [
        WeightedVolumeShare(
            label=label, max_score=max_score, weight=round(weight / 100.0, 6)
        )
        for label, weight in zip(labels, weights)
    ]


def _shared_max_score(raw: str) -> float | None:
    """共享满分锚点; 缺失即拒收——不得按默认百分制替声明兜底。"""
    match = _SHARED_MAX_SCORE_RE.search(raw)
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _max_enumerated_volume_count(raw: str) -> int:
    """同句中"按<卷列表>分别…"枚举出的最大卷数, 作为权重覆盖完备性的对照基数。"""
    counts = [
        len(_split_labels(match.group(1))) for match in _ENUMERATION_RE.finditer(raw)
    ]
    return max(counts) if counts else 0


def _split_labels(segment: str) -> list[str]:
    """标签段 -> 有序标签列表; 超长/空标签视为解析失真, 整体作废。"""
    labels = [token for token in _LABEL_SPLIT_RE.split(segment.strip()) if token]
    if not labels or any(len(label) > _MAX_LABEL_LEN for label in labels):
        return []
    return labels


__all__ = ["WeightedVolumeShare", "parse_weighted_share_composition"]
