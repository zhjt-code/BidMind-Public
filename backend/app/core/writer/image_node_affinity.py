"""图片-章节亲和性规则

根据图片类型(ImageType)和章节标题的关键词匹配,
判定哪些图片与当前章节语义相关。
供 section_composer 和 doc_renderer 共用。
"""

from __future__ import annotations

import re
from typing import Optional

from app.models.writing_material_image import ImageType, WritingMaterialImage

IMAGE_TYPE_CHAPTER_KEYWORDS: dict[ImageType, list[str]] = {
    ImageType.license: [
        "营业执照", "税务登记", "组织机构", "三证合一", "企业证件",
        "开户许可", "银行开户",
    ],
    ImageType.personnel_cert: [
        "身份证", "人员", "项目经理", "负责人", "资历", "配置表",
        "团队", "项目总负责",
    ],
    ImageType.qualification: [
        "资质", "准入证", "认证", "许可证", "ISO", "QHSE", "体系",
        "安全生产", "环保",
    ],
    ImageType.nameplate: [
        "设备", "仪器", "铭牌", "装备",
    ],
    ImageType.photo: [
        "现场", "办公", "厂房", "仓库", "实景", "设备", "仪器",
    ],
    ImageType.diagram: [
        "组织", "架构", "流程", "方案图", "示意图",
    ],
}

BUSINESS_SUBTYPE_CHAPTER_KEYWORDS: dict[str, list[str]] = {
    "business_license": ["营业执照", "企业基本情况", "三证合一"],
    "tax_registration": ["税务登记", "税务登记证"],
    "org_code": ["组织机构代码", "组织机构代码证"],
    "bank_permit": ["开户许可", "银行开户", "基本账户"],
    "id_card": ["身份证", "法定代表人", "身份证明", "授权委托"],
    "degree_cert": ["学历", "学位", "毕业", "人员", "项目经理"],
    "title_cert": ["职称", "资格", "执业", "人员", "项目经理"],
    "qualification_personnel": ["建造师", "安全员", "项目经理", "人员"],
    "qualification_cert": ["资质", "准入", "许可"],
    "iso_cert": ["体系认证", "ISO", "质量管理", "环境管理", "职业健康", "HSE"],
    "safety_permit": ["安全生产", "许可证", "HSE"],
    "equipment_nameplate": ["设备", "仪器", "铭牌"],
    "equipment_photo": ["设备", "仪器", "现场"],
    "site_photo": ["现场", "办公", "厂房", "仓库"],
    "organization_diagram": ["组织", "架构"],
    "process_diagram": ["流程", "方案图", "示意图"],
    "site_layout": ["平面布置", "总平面", "临设", "场地", "施工平面"],
    "section_drawing": ["剖面", "断面", "支护", "脚手架", "基坑"],
    "network_drawing": ["进度", "工期", "网络图", "横道", "关键线路"],
    "invoice_receipt": ["发票", "收据", "票据", "回单"],
    "tax_social_proof": ["纳税", "完税", "社保", "缴费证明"],
    "honor_cert": ["荣誉", "奖项", "表彰"],
    "contract_notice": ["合同", "协议", "中标", "验收", "业绩"],
    "authorization_letter": ["授权委托", "法人代表", "法定代表人证明"],
}

_CJK_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+")
_UNCERTAIN_SUBTYPES = {"", "unknown", "needs_review"}
_CONTRACT_EVIDENCE_TITLE_KEYWORDS = ("合同", "协议", "业绩", "项目经验", "类似项目", "履约")
_CONTRACT_SCAN_CAPTION_KEYWORDS = ("合同", "协议", "中标", "验收", "业绩")
_SCAN_ATTACHMENT_KEYWORDS = ("扫描", "复印", "附件", "证明")

# 独占型图片业务子类: 同一张证照/票据类图片不应跨章节重复出现。
# 供 Word 渲染 (_insert_figure_images) 与 HTML 预览内联共用, 确保去重行为一致。
EXCLUSIVE_BUSINESS_SUBTYPES: frozenset[str] = frozenset({
    "business_license",
    "tax_registration",
    "org_code",
    "bank_permit",
    "id_card",
    "degree_cert",
    "title_cert",
    "qualification_personnel",
    "qualification_cert",
    "iso_cert",
    "safety_permit",
    "invoice_receipt",
    "tax_social_proof",
    "authorization_letter",
})

EXCLUSIVE_IMAGE_TYPES: frozenset[ImageType] = frozenset({
    ImageType.license,
    ImageType.personnel_cert,
    ImageType.qualification,
})


def is_exclusive_image(rec: WritingMaterialImage) -> bool:
    """判断图片是否为"独占型"(同一张证照类图片不应跨章节重复出现)。

    优先用 business_subtype 精确判定; 无 subtype 时回退到 image_type 粗类。
    Word 渲染与 HTML 预览共用此判定, 保证两侧去重行为一致。
    """
    business_subtype = getattr(rec, "business_subtype", "") or ""
    if business_subtype:
        return business_subtype in EXCLUSIVE_BUSINESS_SUBTYPES
    return getattr(rec, "image_type", None) in EXCLUSIVE_IMAGE_TYPES


def _extract_cjk_tokens(text: str) -> set[str]:
    """从文本中提取连续中文词组(2字及以上)"""
    return {m.group() for m in _CJK_TOKEN_RE.finditer(text) if len(m.group()) >= 2}


def _title_matches_image_type(
    title_lower: str, image_type: Optional[ImageType],
) -> bool:
    """检查章节标题是否与给定 image_type 的关键词匹配"""
    if image_type is None:
        return False
    keywords = IMAGE_TYPE_CHAPTER_KEYWORDS.get(image_type, [])
    return any(kw in title_lower for kw in keywords)


def _title_matches_business_subtype(
    title_lower: str,
    business_subtype: str,
) -> bool:
    if not business_subtype:
        return False
    keywords = BUSINESS_SUBTYPE_CHAPTER_KEYWORDS.get(business_subtype, [])
    return any(kw in title_lower for kw in keywords)


def _caption_matches_title(caption: str, title: str) -> float:
    """计算 caption 与章节标题的关键词重叠度 (0.0~1.0)

    先做双向子串包含(任一方向命中即高分),
    再做 CJK token 集合交集作为精细得分。
    """
    if not caption or not title:
        return 0.0

    cap_lower = caption.lower()
    title_lower = title.lower()

    title_tokens = _extract_cjk_tokens(title)
    if not title_tokens:
        return 0.0

    hit = 0
    for tok in title_tokens:
        if tok in cap_lower:
            hit += 1
    if hit > 0:
        return hit / len(title_tokens)

    cap_tokens = _extract_cjk_tokens(caption)
    for tok in cap_tokens:
        if tok in title_lower:
            return 0.5

    return 0.0


def _is_uncertain_contract_scan_for_title(
    img: WritingMaterialImage,
    title_lower: str,
) -> bool:
    """合同/业绩章节保留未细分的合同扫描附件。"""
    business_subtype = (getattr(img, "business_subtype", "") or "").strip()
    if business_subtype not in _UNCERTAIN_SUBTYPES:
        return False
    if getattr(img, "image_type", None) != ImageType.other:
        return False
    if not any(keyword in title_lower for keyword in _CONTRACT_EVIDENCE_TITLE_KEYWORDS):
        return False

    caption = (getattr(img, "caption", "") or "").lower()
    has_contract_signal = any(keyword in caption for keyword in _CONTRACT_SCAN_CAPTION_KEYWORDS)
    has_scan_signal = any(keyword in caption for keyword in _SCAN_ATTACHMENT_KEYWORDS)
    return has_contract_signal and has_scan_signal


def filter_images_for_node(
    images: list[WritingMaterialImage],
    node_title: str,
) -> list[WritingMaterialImage]:
    """过滤出与章节标题语义匹配的图片子集

    匹配策略(按优先级):
    1. image_type 的关键词与章节标题匹配 -> 保留
    2. caption 与章节标题的中文词重叠度 >= 0.3 -> 保留
    3. 都不匹配 -> 排除

    返回匹配的图片列表(保持原顺序)。
    """
    if not images or not node_title:
        return []

    title_lower = node_title.lower()
    matched: list[WritingMaterialImage] = []

    for img in images:
        business_subtype = getattr(img, "business_subtype", "") or ""
        if business_subtype and _title_matches_business_subtype(title_lower, business_subtype):
            matched.append(img)
            continue
        if _title_matches_image_type(title_lower, img.image_type):
            matched.append(img)
            continue
        if _is_uncertain_contract_scan_for_title(img, title_lower):
            matched.append(img)
            continue
        score = _caption_matches_title(img.caption or "", node_title)
        if score >= 0.3:
            matched.append(img)

    return matched


def find_best_image_for_placeholder(
    candidates: list[WritingMaterialImage],
    current_section_title: str,
) -> Optional[WritingMaterialImage]:
    """从候选图片中找到与当前章节最匹配的一张

    优先按 image_type 关键词匹配,其次按 caption 重叠度排序。
    无任何匹配时返回 None(不会强制塞入不相关图片)。
    """
    if not candidates or not current_section_title:
        return None

    title_lower = current_section_title.lower()

    subtype_matched: list[WritingMaterialImage] = []
    type_matched: list[WritingMaterialImage] = []
    caption_scored: list[tuple[float, WritingMaterialImage]] = []

    for img in candidates:
        business_subtype = getattr(img, "business_subtype", "") or ""
        if business_subtype and _title_matches_business_subtype(title_lower, business_subtype):
            subtype_matched.append(img)
            continue
        if _title_matches_image_type(title_lower, img.image_type):
            type_matched.append(img)
        else:
            score = _caption_matches_title(img.caption or "", current_section_title)
            if score > 0:
                caption_scored.append((score, img))

    if subtype_matched:
        return subtype_matched[0]

    if type_matched:
        return type_matched[0]

    if caption_scored:
        caption_scored.sort(key=lambda x: x[0], reverse=True)
        if caption_scored[0][0] >= 0.3:
            return caption_scored[0][1]

    return None
