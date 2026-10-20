"""writer 图片子类契约

统一管理 business_subtype 的：
1. 白名单归一化
2. 高混淆对子类验真
3. unknown / needs_review 语义
"""

from __future__ import annotations

import json
import re
from typing import Any

UNKNOWN_SUBTYPE = "unknown"
NEEDS_REVIEW_SUBTYPE = "needs_review"

KNOWN_BUSINESS_SUBTYPES = {
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
    "equipment_nameplate",
    "equipment_photo",
    "site_photo",
    "organization_diagram",
    "process_diagram",
    "site_layout",
    "section_drawing",
    "network_drawing",
    "invoice_receipt",
    "tax_social_proof",
    "honor_cert",
    "contract_notice",
    "authorization_letter",
    "other_misc",
    UNKNOWN_SUBTYPE,
    NEEDS_REVIEW_SUBTYPE,
}

_SUBTYPE_ALIASES = {
    "businesslicense": "business_license",
    "taxregistration": "tax_registration",
    "tax_registration_certificate": "tax_registration",
    "orgcode": "org_code",
    "bankpermit": "bank_permit",
    "idcard": "id_card",
    "degreecertificate": "degree_cert",
    "titlecertificate": "title_cert",
    "qualificationpersonnel": "qualification_personnel",
    "qualificationcertificate": "qualification_cert",
    "isocert": "iso_cert",
    "safetypermit": "safety_permit",
    "equipmentnameplate": "equipment_nameplate",
    "equipmentphoto": "equipment_photo",
    "sitephoto": "site_photo",
    "organizationdiagram": "organization_diagram",
    "processdiagram": "process_diagram",
    "sitelayout": "site_layout",
    "sitelayoutdrawing": "site_layout",
    "sectiondrawing": "section_drawing",
    "networkdrawing": "network_drawing",
    "networkdiagram": "network_drawing",
    "invoicereceipt": "invoice_receipt",
    "taxsocialproof": "tax_social_proof",
    "honorcertificate": "honor_cert",
    "contractnotice": "contract_notice",
    "authorizationletter": "authorization_letter",
    "othermisc": "other_misc",
    "review": NEEDS_REVIEW_SUBTYPE,
    "needsreview": NEEDS_REVIEW_SUBTYPE,
    "uncertain": UNKNOWN_SUBTYPE,
}

_VALIDATION_RULES = {
    "invoice_receipt": {
        "positive": [
            "发票查验明细", "价税合计", "开票日期", "机器编号", "校验码",
            "发票代码", "发票号码", "购买方信息", "销售方信息",
        ],
        "negative": ["税务登记证", "税务登记号码", "纳税人识别号"],
    },
    "tax_registration": {
        "positive": ["税务登记证", "税务登记号码", "纳税人识别号", "税务登记"],
        "negative": ["发票查验明细", "价税合计", "开票日期", "发票代码", "发票号码", "校验码"],
    },
    "id_card": {
        "positive": ["公民身份号码", "居民身份证", "性别", "民族", "住址"],
        "negative": ["学位", "授予", "毕业", "学历"],
    },
    "degree_cert": {
        "positive": ["学位", "授予", "毕业", "学历", "博士", "硕士", "学士"],
        "negative": ["公民身份号码", "性别", "民族", "住址"],
    },
    "honor_cert": {
        "positive": ["荣誉", "奖状", "锦旗", "表彰", "获奖"],
        "negative": ["资质等级", "准入", "安全生产许可证", "认证范围"],
    },
    "qualification_cert": {
        "positive": ["资质证书", "资质等级", "准入", "发证机关", "认证范围"],
        "negative": ["荣誉", "奖状", "锦旗", "表彰", "获奖"],
    },
    "iso_cert": {
        "positive": [
            "iso", "管理体系", "质量管理体系", "环境管理体系", "职业健康安全",
            "体系认证", "三体系",
        ],
        "negative": ["身份证", "营业执照", "发票", "税务登记", "荣誉", "奖状"],
    },
    "site_layout": {
        "positive": ["平面布置", "总平面", "施工平面", "临设", "场地布置"],
        "negative": ["营业执照", "身份证", "组织架构"],
    },
    "section_drawing": {
        "positive": ["剖面", "断面", "支护", "脚手架", "基坑"],
        "negative": ["营业执照", "身份证", "组织架构"],
    },
    "network_drawing": {
        "positive": ["网络图", "横道图", "甘特", "关键线路", "pert", "cpm"],
        "negative": ["营业执照", "身份证"],
    },
}


def normalize_business_subtype(raw_value: Any) -> str:
    """将模型输出归一化到受控值域。"""
    if not raw_value or not isinstance(raw_value, str):
        return UNKNOWN_SUBTYPE
    normalized = raw_value.strip().lower()
    if normalized in KNOWN_BUSINESS_SUBTYPES:
        return normalized

    compact = re.sub(r"[^a-z_]+", "", normalized)
    if compact in _SUBTYPE_ALIASES:
        return _SUBTYPE_ALIASES[compact]

    compact = compact.replace("_", "")
    if compact in _SUBTYPE_ALIASES:
        return _SUBTYPE_ALIASES[compact]

    return UNKNOWN_SUBTYPE


def _build_evidence_text(
    vl_text: str,
    caption: str = "",
    structured_fields: Any = None,
) -> str:
    parts = [vl_text or "", caption or ""]
    if isinstance(structured_fields, dict):
        parts.extend(str(v) for v in structured_fields.values() if v)
    elif isinstance(structured_fields, str):
        try:
            data = json.loads(structured_fields)
            if isinstance(data, dict):
                parts.extend(str(v) for v in data.values() if v)
        except (TypeError, json.JSONDecodeError):
            parts.append(structured_fields)
    return "\n".join(part for part in parts if part)


def validate_business_subtype(
    business_subtype: str,
    *,
    vl_text: str,
    caption: str = "",
    structured_fields: Any = None,
) -> tuple[str, list[str]]:
    """反向校验高混淆对子类，返回最终子类与原因列表。"""
    normalized = normalize_business_subtype(business_subtype)
    if normalized in {UNKNOWN_SUBTYPE, NEEDS_REVIEW_SUBTYPE}:
        return normalized, ["model_uncertain"]

    rule = _VALIDATION_RULES.get(normalized)
    if not rule:
        return NEEDS_REVIEW_SUBTYPE, ["no_validation_rule"]

    # 大小写不敏感匹配：VL 输出的英文标识（如 "ISO"）与规则里的小写关键词对齐。
    evidence_text = _build_evidence_text(vl_text, caption, structured_fields).lower()
    positive_hits = [kw for kw in rule["positive"] if kw.lower() in evidence_text]
    negative_hits = [kw for kw in rule["negative"] if kw.lower() in evidence_text]

    if negative_hits and not positive_hits:
        return NEEDS_REVIEW_SUBTYPE, [f"negative_only:{','.join(negative_hits[:3])}"]
    if not positive_hits:
        return NEEDS_REVIEW_SUBTYPE, ["insufficient_evidence"]
    if negative_hits:
        return NEEDS_REVIEW_SUBTYPE, [f"contradictory_evidence:{','.join(negative_hits[:3])}"]
    return normalized, [f"validated:{','.join(positive_hits[:3])}"]
