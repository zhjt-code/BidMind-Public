"""标书素材细标签分类学（同义归一 + 业务大类映射）。

职责: 提供素材细标签的同义分组判定与 DocumentBusinessType 大类映射。
不做: 不做匹配打分、不查询数据库、不调用 LLM、不推断章节归属。

背景:
- 体系认证家族（ISO认证 / 三体系认证 / 管理体系认证 等）在文件名、结构提示与内容识别
 三条来源中常以不同措辞出现，但语义等价；标签决议时应视为同组，避免被判为"冲突"。
- 出于安全，仅对语义确实等价的族做归并；身份证 vs 法人身份证等"主体不同"的标签
 绝不归并（保留个人/法人区分，防止 /证照串写同源问题）。
"""

from __future__ import annotations

import re
from typing import Optional

from app.models.writing_material import DocumentBusinessType

# 语义等价的细标签同义组。仅收录确实可互换的族，禁止把"主体不同"的标签放入同组。
_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "iso认证",
            "iso",
            "三体系认证",
            "三体系",
            "管理体系认证",
            "质量管理体系认证",
            "环境管理体系认证",
            "职业健康安全认证",
            "职业健康安全管理体系认证",
            "体系认证",
            "三体系确认证书",
        }
    ),
    frozenset(
        {
            "业绩材料",
            "项目业绩",
            "项目业绩综述",
            "业绩综述",
            "业绩证明",
            "经营业绩",
            "主要经营业绩",
            "业绩合同",
            "类似项目综述",
            "项目经验综述",
        }
    ),
)


def _normalize(label: Optional[str]) -> str:
    """归一化标签用于比较：去空白、去括号类符号、小写。"""
    if not label:
        return ""
    normalized = re.sub(r"[\s（()）【】\[\]]", "", label).lower()
    return normalized


def canonical_label(label: Optional[str]) -> str:
    """返回标签所属同义组的规范键；不属于任何组时返回归一化后的标签本身。"""
    normalized = _normalize(label)
    if not normalized:
        return ""
    for group in _SYNONYM_GROUPS:
        if normalized in group:
            # 以排序后的首元素作为稳定规范键
            return sorted(group)[0]
    return normalized


def same_label_group(a: Optional[str], b: Optional[str]) -> bool:
    """判断两个细标签是否语义等价（同一同义组，或归一化后相等）。"""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return canonical_label(a) == canonical_label(b)


def business_type_for_label(label: Optional[str]) -> Optional[DocumentBusinessType]:
    """将细标签映射到 DocumentBusinessType 大类。

    复用 filename_classifier 的关键词规则表作为唯一映射来源，避免重复维护词典。
    无法识别时返回 None。
    """
    if not label:
        return None
    from app.core.writer.filename_classifier import classify_by_text_signal

    biz_type, _detail = classify_by_text_signal(label)
    return biz_type


__all__ = [
    "canonical_label",
    "same_label_group",
    "business_type_for_label",
]
