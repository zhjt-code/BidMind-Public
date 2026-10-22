"""标书素材匹配 -- 类型硬门禁。

职责: 在召回与 LLM 裁决之前，按业务类型/否定信号裁决素材-章节配对是否允许，
 拦截明显的跨大类证件冲突与显式排除项。
不做: 不计算语义相似度、不调用 LLM、不做最终绑定决策、不查询数据库。

设计要点:
- 门禁只做"保守拦截"，不做精确匹配。同一业务大类内部(如多个资质章节)的精度区分
 交由语义召回与 LLM 句对裁决完成，门禁一律放行(allow/review)。
- 强拦截仅针对: 命中章节 rejects / 素材 inapplicable / 特定证件跨大类冲突
 (如身份证->营业执照、体系认证->开户许可证)，这些是 的原始误绑症状。
- 未验证图片素材(label_verified=False)降级为 review，禁止其在后续被语义单路直接放行。
"""

from __future__ import annotations

import re

from app.core.writer.material_matching.contracts import (
    ChapterDemandProfile,
    MaterialSemanticProfile,
    PairGateResult,
)
from app.core.writer.material_matching.material_taxonomy import business_type_for_label
from app.models.writing_material import DocumentBusinessType

# 特定证件大类: 一份文件强绑定特定章节，跨大类配对视为主体冲突。
# company_profile/personnel_info/performance_proof/equipment_info/other 为内容类，
# 可合理出现在多个章节，不参与证件冲突判定。
_SPECIFIC_BUSINESS_TYPE_VALUES: frozenset[str] = frozenset(
    {
        DocumentBusinessType.identity_document.value,
        DocumentBusinessType.business_license.value,
        DocumentBusinessType.bank_permit.value,
        DocumentBusinessType.qualification_cert.value,
        DocumentBusinessType.authorization.value,
        DocumentBusinessType.tax_social_proof.value,
        DocumentBusinessType.financial_document.value,
    }
)


def _chinese_terms(text: str) -> set[str]:
    """提取文本中的中文词块(连续>=2个汉字)。"""
    return set(re.findall(r"[\u4e00-\u9fff]{2,}", text or ""))


def _rejects_material(material: MaterialSemanticProfile, chapter: ChapterDemandProfile) -> bool:
    """素材类型是否命中章节 rejects 列表。"""
    if not chapter.rejects:
        return False
    reject_text = " ".join(chapter.rejects)
    for term in (material.resolved_label, material.business_type, material.domain):
        if term and term in reject_text:
            return True
    reject_terms = _chinese_terms(reject_text)
    material_terms = _chinese_terms(material.resolved_label or "") | _chinese_terms(material.title)
    return len(material_terms & reject_terms) >= 2


def _material_excludes_chapter(
    material: MaterialSemanticProfile, chapter: ChapterDemandProfile
) -> bool:
    """章节标题是否落在素材声明的 inapplicable_sections。"""
    if not material.inapplicable_sections:
        return False
    node_title = chapter.title.lower().strip()
    if not node_title:
        return False
    for section in material.inapplicable_sections:
        normalized = section.lower().strip()
        if normalized and (normalized in node_title or node_title in normalized):
            return True
    return False


def _chapter_business_type(chapter: ChapterDemandProfile) -> str | None:
    """从章节标题推断业务大类值(供证件冲突判定)。"""
    biz_type = business_type_for_label(chapter.title)
    return biz_type.value if biz_type is not None else None


def evaluate_pair_gate(
    material: MaterialSemanticProfile, chapter: ChapterDemandProfile
) -> PairGateResult:
    """裁决素材-章节配对的类型门禁结果。

    优先级(命中即返回):
    1. 未验证图片 -> review
    2. 命中章节 rejects -> block
    3. 命中素材 inapplicable -> block
    4. 特定证件跨大类冲突 -> block
    5. 其余 -> allow
    """
    if not material.label_verified:
        return PairGateResult(decision="review", reason="未验证图片素材，禁止语义单路放行")

    if _rejects_material(material, chapter):
        return PairGateResult(
            decision="block", reason=f"命中章节排除类型: {chapter.rejects}"
        )

    if _material_excludes_chapter(material, chapter):
        return PairGateResult(
            decision="block", reason="命中素材声明的不适用章节"
        )

    material_type = material.business_type
    chapter_type = _chapter_business_type(chapter)
    if (
        material_type in _SPECIFIC_BUSINESS_TYPE_VALUES
        and chapter_type in _SPECIFIC_BUSINESS_TYPE_VALUES
        and material_type != chapter_type
    ):
        return PairGateResult(
            decision="block",
            reason=f"证件主体跨大类冲突: {material_type} vs {chapter_type}",
        )

    return PairGateResult(decision="allow", reason="类型无明显冲突")


__all__ = ["evaluate_pair_gate"]
