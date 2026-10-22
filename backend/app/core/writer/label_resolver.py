"""素材标签交叉校验与决议器

对比结构提示标签(structure_hint_label)、文件名预判标签(file_name_label)
与内容识别标签(content_label)，按"内容为准、结构/文件名为辅、冲突留痕"
原则产出最终决议。

决议优先级: content_label > structure_hint_label > file_name_label > 正文关键词推断
冲突处理: 不静默覆盖, 必须在 label_conflict 中记录状态
"""
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.core.writer.image_subtype_contract import (
    NEEDS_REVIEW_SUBTYPE,
    UNKNOWN_SUBTYPE,
    normalize_business_subtype,
)
from app.core.writer.material_matching.material_taxonomy import same_label_group
from app.models.writing_material_image import ImageType


@dataclass
class LabelResolution:
    """标签决议结果"""
    content_label: Optional[str] = None
    resolved_label: Optional[str] = None
    conflict_status: Optional[str] = None
    source: Optional[str] = None
    resolution_reason: Optional[str] = None


IMAGE_TYPE_TO_LABEL: dict[ImageType, str] = {
    ImageType.license: "营业执照",
    ImageType.personnel_cert: "人员证件",
    ImageType.qualification: "资质证书",
    ImageType.nameplate: "设备铭牌",
    ImageType.photo: "实物照片",
    ImageType.diagram: "图表",
}

BUSINESS_SUBTYPE_TO_LABEL: dict[str, str] = {
    "business_license": "营业执照",
    "tax_registration": "税务登记证",
    "org_code": "组织机构代码证",
    "bank_permit": "开户许可证",
    "id_card": "身份证",
    "degree_cert": "学位证书",
    "title_cert": "职称证书",
    "qualification_personnel": "资格证书",
    "qualification_cert": "资质证书",
    "iso_cert": "ISO认证",
    "safety_permit": "安全生产许可证",
    "equipment_nameplate": "设备铭牌",
    "equipment_photo": "设备照片",
    "site_photo": "现场照片",
    "organization_diagram": "组织架构图",
    "process_diagram": "流程图",
    "site_layout": "施工平面图",
    "section_drawing": "剖面示意图",
    "network_drawing": "进度网络图",
    "invoice_receipt": "发票",
    "tax_social_proof": "纳税证明",
    "honor_cert": "荣誉证书",
    "contract_notice": "项目合同",
    "authorization_letter": "授权委托书",
    "other_misc": "其他材料",
}

_HIGH_RISK_CONFLICT_PAIRS: set[frozenset[str]] = {
    frozenset({"发票", "税务登记证"}),
    frozenset({"营业执照", "身份证"}),
    frozenset({"营业执照", "法人身份证"}),
    frozenset({"学位证书", "身份证"}),
    frozenset({"学位证书", "法人身份证"}),
    frozenset({"荣誉证书", "资质证书"}),
}

_CERT_TYPE_REFINEMENT: dict[str, str] = {
    "身份证": "身份证",
    "居民身份证": "身份证",
    "法人身份证": "法人身份证",
    "法定代表人": "法人身份证",
    "营业执照": "营业执照",
    "开户许可": "开户许可证",
    "基本存款账户": "开户许可证",
    "税务登记": "税务登记证",
    "组织机构代码": "组织机构代码证",
    "安全生产许可": "安全生产许可证",
    "质量管理体系": "ISO认证",
    "环境管理体系": "ISO认证",
    "职业健康安全": "ISO认证",
    "管理体系认证": "ISO认证",
    "管理体系": "ISO认证",
    "体系认证": "ISO认证",
    "三体系": "ISO认证",
    "授权委托": "授权委托书",
    "社保": "社保证明",
    "纳税": "纳税证明",
    "完税": "完税证明",
    "发票": "发票",
    "审计报告": "审计报告",
    "合同": "项目合同",
    "中标通知": "中标通知书",
    "验收报告": "验收报告",
    "简历": "人员简历",
    "职称": "职称证书",
    "资格证": "资格证书",
}

TEXT_CONTENT_KEYWORDS: list[tuple[str, str]] = [
    ("公司简介", "公司简介"),
    ("企业简介", "企业简介"),
    ("企业概况", "企业概况"),
    ("项目业绩综述", "项目业绩综述"),
    ("业绩综述", "项目业绩综述"),
    ("类似项目", "项目业绩综述"),
    ("项目经验", "项目业绩综述"),
    ("服务承诺", "服务承诺"),
    ("售后服务", "售后服务承诺"),
    ("响应承诺", "响应承诺"),
    ("保障措施", "保障措施"),
    ("管理制度", "管理制度"),
    ("制度汇编", "制度汇编"),
    ("管理办法", "管理办法"),
    ("质量管理制度", "质量管理制度"),
    ("安全管理制度", "安全管理制度"),
    ("质量保证", "质量保证方案"),
    ("施工组织", "施工组织设计"),
    ("技术方案", "技术方案"),
    ("售后服务", "售后服务方案"),
]

_LABEL_SUPPORT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "公司简介": ("公司", "企业", "团队", "简介", "概况", "主营业务", "综合实力", "研发团队", "成立于"),
    "企业简介": ("公司", "企业", "团队", "简介", "概况", "主营业务", "综合实力", "研发团队", "成立于"),
    "企业概况": ("公司", "企业", "团队", "简介", "概况", "主营业务", "综合实力", "研发团队", "成立于"),
    "项目业绩综述": ("项目业绩", "业绩", "项目经验", "案例", "履约", "成果", "合同", "验收", "中标"),
    "服务承诺": ("服务承诺", "承诺", "响应", "售后", "保障", "时效"),
    "管理制度": ("管理制度", "制度", "管理办法", "流程", "质量管理", "安全管理", "QHSE"),
    "管理办法": ("管理办法", "制度", "办法", "流程", "管理"),
    "质量管理制度": ("质量管理", "质量保证", "质量体系", "制度"),
    "安全管理制度": ("安全管理", "安全生产", "制度", "风险防控"),
    "人员简历": ("简历", "姓名", "职务", "学历", "职称", "项目经理", "技术负责人", "工作经历"),
    "人员配置方案": ("人员配置", "汇总表", "任职", "岗位职责", "姓名", "负责人", "技术骨干"),
    "诉讼仲裁情况": ("诉讼", "仲裁", "纠纷", "法律纠纷", "案件"),
    "承诺书": ("承诺书", "承诺", "声明"),
    "无行贿犯罪承诺函": ("无行贿", "犯罪", "承诺函", "承诺"),
}


def _infer_content_label_from_images(
    images: list,
) -> tuple[Optional[str], str, str]:
    """从图片分类结果推断 content_label，并返回证据强度。"""
    if not images:
        return None, "none", "no_images"

    scores: dict[str, int] = defaultdict(int)
    priorities: dict[str, int] = defaultdict(int)
    sources: dict[str, set[str]] = defaultdict(set)
    uncertain_subtype_seen = False

    for img in images:
        img_type = getattr(img, "image_type", None)
        raw_business_subtype = getattr(img, "business_subtype", "") or ""
        business_subtype = (
            normalize_business_subtype(raw_business_subtype)
            if raw_business_subtype else ""
        )
        caption = getattr(img, "caption", "") or ""
        sf_raw = getattr(img, "structured_fields", None)

        if sf_raw and isinstance(sf_raw, str):
            try:
                sf = json.loads(sf_raw)
            except (json.JSONDecodeError, TypeError):
                sf = {}
        elif isinstance(sf_raw, dict):
            sf = sf_raw
        else:
            sf = {}

        # needs_review 是"经校验的不可信"判定（如税务/发票矛盾），必须整图跳过，
        # 不得用同图 caption/structured 回抬（保留既有安全护栏）。
        if raw_business_subtype and business_subtype == NEEDS_REVIEW_SUBTYPE:
            uncertain_subtype_seen = True
            continue
        # unknown 仅表示"子类未定"，只取消子类加分，同图结构化字段/caption/
        # 图片粗类等独立强证据仍应参与，避免身份证/ISO 等被整体丢弃。
        if raw_business_subtype and business_subtype == UNKNOWN_SUBTYPE:
            uncertain_subtype_seen = True
        elif business_subtype:
            subtype_label = BUSINESS_SUBTYPE_TO_LABEL.get(business_subtype)
            if subtype_label:
                scores[subtype_label] += 4
                priorities[subtype_label] = max(priorities[subtype_label], 4)
                sources[subtype_label].add("subtype")

        cert_type_value = sf.get("证件类型", "") or sf.get("资质名称", "") or ""
        if cert_type_value:
            for kw, label in _CERT_TYPE_REFINEMENT.items():
                if kw in cert_type_value:
                    scores[label] += 3
                    priorities[label] = max(priorities[label], 3)
                    sources[label].add("structured")
                    break

        if caption:
            for kw, label in _CERT_TYPE_REFINEMENT.items():
                if kw in caption:
                    scores[label] += 2
                    priorities[label] = max(priorities[label], 2)
                    sources[label].add("caption")
                    break

        if img_type and img_type in IMAGE_TYPE_TO_LABEL:
            if img_type != ImageType.other:
                img_label = IMAGE_TYPE_TO_LABEL[img_type]
                scores[img_label] += 1
                priorities[img_label] = max(priorities[img_label], 1)
                sources[img_label].add("image_type")

    if not scores:
        if uncertain_subtype_seen:
            return None, "needs_review", "subtype_uncertain"
        return None, "none", "no_image_signals"

    best_label = max(
        scores,
        key=lambda label: (scores[label], priorities[label], label),
    )
    best_sources = sources[best_label]
    if "subtype" in best_sources:
        return best_label, "validated", "subtype_validated"
    if "structured" in best_sources and "caption" in best_sources:
        return best_label, "validated", "structured_caption_agree"
    if "structured" in best_sources and "image_type" in best_sources:
        return best_label, "validated", "structured_type_agree"
    if "caption" in best_sources and "image_type" in best_sources:
        return best_label, "supported", "caption_type_agree"
    if uncertain_subtype_seen:
        return best_label, "needs_review", "subtype_uncertain"
    return best_label, "weak", "single_source_only"


def _infer_content_label_from_text(content: Optional[str]) -> Optional[str]:
    """从正文多个窗口关键词推断 content_label (非图片素材)"""
    if not content:
        return None
    compact = re.sub(r"\s+", " ", content)
    windows = [
        compact[:800],
        compact[len(compact) // 3: len(compact) // 3 + 800] if len(compact) > 1200 else "",
        compact[-800:] if len(compact) > 1600 else "",
    ]
    sampled_text = "\n".join(part for part in windows if part)
    for kw, label in TEXT_CONTENT_KEYWORDS:
        if kw in sampled_text:
            return label
    for kw, label in TEXT_CONTENT_KEYWORDS:
        if kw in compact:
            return label
    return None


def _normalize_label(label: Optional[str]) -> str:
    if not label:
        return ""
    normalized = re.sub(r"\s+", "", label)
    if normalized == "居民身份证":
        return "身份证"
    return normalized


def _estimate_label_support(
    content: Optional[str],
    label: Optional[str],
) -> int:
    """估算正文对某个标签的支持强度。"""
    normalized_label = _normalize_label(label)
    if not content or not normalized_label:
        return 0

    keywords = _LABEL_SUPPORT_KEYWORDS.get(normalized_label, ())
    if not keywords:
        return 0

    compact = re.sub(r"\s+", "", content)
    head = compact[:300]
    score = 0
    for keyword in keywords:
        if keyword in head:
            score += 2
        elif keyword in compact:
            score += 1
    return score


def _is_high_risk_conflict(
    file_name_label: Optional[str],
    content_label: Optional[str],
) -> bool:
    fn_label = _normalize_label(file_name_label)
    ct_label = _normalize_label(content_label)
    if not fn_label or not ct_label or fn_label == ct_label:
        return False
    return frozenset({fn_label, ct_label}) in _HIGH_RISK_CONFLICT_PAIRS


def _pick_name_hint(
    structure_hint_label: Optional[str],
    file_name_label: Optional[str],
) -> tuple[Optional[str], str]:
    if structure_hint_label:
        return structure_hint_label, "structure"
    if file_name_label:
        return file_name_label, "filename"
    return None, "none"


def resolve_material_label(
    file_name_label: Optional[str],
    material_content: Optional[str],
    images: Optional[list] = None,
    *,
    structure_hint_label: Optional[str] = None,
) -> LabelResolution:
    """交叉校验文件名标签与内容识别标签, 产出最终决议

    Args:
        file_name_label: 文件名规则预分类的细分标签(来自 filename_classifier)
        structure_hint_label: PDF 结构分析建议的细分标签
        material_content: 素材文本内容
        images: 素材关联的 WritingMaterialImage 列表

    Returns:
        LabelResolution: 包含 content_label / resolved_label / conflict_status / source
    """
    content_label, content_state, content_reason = _infer_content_label_from_images(images or [])
    content_source = "vl"
    name_hint_label, hint_source = _pick_name_hint(
        structure_hint_label,
        file_name_label,
    )
    if not content_label and content_state == "none":
        content_label = _infer_content_label_from_text(material_content)
        content_state = "validated" if content_label else "none"
        content_reason = "text_keyword" if content_label else "no_content_signal"
        content_source = "text"

    if not content_label and content_state == "needs_review":
        return LabelResolution(
            content_label=None,
            resolved_label=None,
            conflict_status="needs_review",
            source="vl",
            resolution_reason=content_reason,
        )

    if content_label and name_hint_label:
        fn_normalized = _normalize_label(name_hint_label)
        ct_normalized = _normalize_label(content_label)
        # 体系认证家族（ISO认证/三体系认证/管理体系认证）措辞不同但语义等价，
        # 视为一致而非冲突；主体不同的标签（如法人身份证 vs 身份证）不归并。
        if fn_normalized == ct_normalized or same_label_group(name_hint_label, content_label):
            return LabelResolution(
                content_label=content_label,
                resolved_label=content_label,
                conflict_status="matched",
                source=content_source if hint_source == "filename" else "structure",
                resolution_reason=f"{hint_source}_and_content_match:{content_reason}",
            )
        else:
            hint_support = _estimate_label_support(material_content, name_hint_label)
            content_support = _estimate_label_support(material_content, content_label)
            if hint_source == "structure" and content_source == "text":
                if hint_support >= 3 and hint_support >= content_support + 2:
                    return LabelResolution(
                        content_label=content_label,
                        resolved_label=name_hint_label,
                        conflict_status="matched",
                        source="structure",
                        resolution_reason=(
                            f"structure_stronger:{content_reason}:"
                            f"hint={hint_support},content={content_support}"
                        ),
                    )
                if hint_support >= 2 and content_support <= 1:
                    return LabelResolution(
                        content_label=content_label,
                        resolved_label=name_hint_label,
                        conflict_status="matched",
                        source="structure",
                        resolution_reason=(
                            f"structure_supported:{content_reason}:"
                            f"hint={hint_support},content={content_support}"
                        ),
                    )
            if _is_high_risk_conflict(name_hint_label, content_label) or content_state in {"needs_review", "weak"}:
                logger.warning(
                    f"[标签决议] 高风险/弱证据冲突降级: hint='{name_hint_label}', "
                    f"content='{content_label}', reason={content_reason}, 阻断 resolved_label 自动流转"
                )
                return LabelResolution(
                    content_label=content_label,
                    resolved_label=None,
                    conflict_status="needs_review",
                    source="conflict",
                    resolution_reason=f"conflict_or_weak:{content_reason}",
                )
            logger.info(
                f"[标签决议] 冲突检测: hint='{name_hint_label}', "
                f"content='{content_label}', 以内容识别为准"
            )
            return LabelResolution(
                content_label=content_label,
                resolved_label=content_label,
                conflict_status="conflicted",
                source=content_source,
                resolution_reason=f"content_wins:{content_reason}",
            )

    if content_label and not name_hint_label:
        if content_state not in {"validated", "supported"}:
            return LabelResolution(
                content_label=content_label,
                resolved_label=None,
                conflict_status="needs_review",
                source=content_source,
                resolution_reason=f"insufficient_support:{content_reason}",
            )
        return LabelResolution(
            content_label=content_label,
            resolved_label=content_label,
            conflict_status="matched",
            source=content_source,
            resolution_reason=f"content_only:{content_reason}",
        )

    if name_hint_label and not content_label:
        if hint_source == "structure":
            hint_support = _estimate_label_support(material_content, name_hint_label)
            if hint_support >= 2:
                return LabelResolution(
                    content_label=None,
                    resolved_label=name_hint_label,
                    conflict_status="matched",
                    source="structure",
                    resolution_reason=f"structure_only_supported:{hint_support}",
                )
        return LabelResolution(
            content_label=None,
            resolved_label=None,
            conflict_status="uncertain",
            source=hint_source,
            resolution_reason=f"{hint_source}_only",
        )

    return LabelResolution(
        content_label=None,
        resolved_label=None,
        conflict_status=None,
        source=None,
        resolution_reason="no_signal",
    )
