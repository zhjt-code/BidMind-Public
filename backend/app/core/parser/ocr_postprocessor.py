"""OCR 输出后处理器

将 PaddleOCR 原始文本转化为与 visual_content 格式一致的
检索友好型自然语言描述，对齐 image_analyzer.VL_SYSTEM_PROMPT 的输出规范。

核心能力:
- 证照字段抽取（统一社会信用代码、证书编号、有效期等）
- 文档类型推断（基于关键词命中）
- 输出长度控制（<= 500字，与 VL 输出约束对齐）
"""

import re
from typing import Optional

from app.core.parser.ocr_engine import OcrResult

# 文档类型关键词映射
_DOC_TYPE_KEYWORDS: dict[str, list[str]] = {
    "营业执照": ["营业执照", "统一社会信用代码", "注册资本", "经营范围"],
    "资质证书": ["资质证书", "资质等级", "建筑业企业资质", "工程设计资质"],
    "安全生产许可证": ["安全生产许可证", "安全生产许可"],
    "身份证": ["公民身份号码", "身份证", "性别", "民族", "住址"],
    "建造师证书": ["建造师", "注册证书", "注册编号", "执业印章"],
    "社会保险": ["社会保险", "社保", "缴费证明", "参保证明", "养老保险"],
    "ISO认证证书": ["ISO", "管理体系认证", "认证证书"],
    "税务登记证": ["税务登记", "纳税人识别号"],
    "发票": [
        "发票号码", "发票代码", "价税合计", "增值税专用发票",
        "增值税普通发票", "电子发票", "结算发票",
    ],
    "检测报告": ["检测报告", "试验报告", "检验报告"],
    "合同": ["合同", "协议书", "甲方", "乙方", "合同金额"],
    "中标通知书": ["中标通知", "中标人", "中标金额"],
    "业绩证明": ["竣工验收", "完工证明", "业绩证明", "项目名称"],
    "授权委托书": ["授权委托", "委托人", "被委托人", "授权"],
    "投标函": ["投标函", "投标报价", "投标有效期"],
    "开户许可证": ["开户许可", "基本存款账户"],
}

# 结构化字段正则
_PATTERNS: dict[str, re.Pattern] = {
    "统一社会信用代码": re.compile(
        r"(?:统一社会信用代码|信用代码)[：:\s]*([0-9A-Z]{18})"
    ),
    "证书编号": re.compile(
        r"(?:证书编号|编号|证号|许可证编号|注册编号)[：:\s]*([\w\-/()]+)"
    ),
    "有效期": re.compile(
        r"(?:有效期|有效期至|至)[：:\s]*([\d年月日./-]+)"
    ),
    "单位名称": re.compile(
        r"(?:名\s*称|企业名称|单位名称|持证单位)[：:\s]*(.{2,40}?)(?:\n|$)"
    ),
    "法定代表人": re.compile(
        r"(?:法定代表人|负责人|法人)[：:\s]*(.{2,10})"
    ),
    "姓名": re.compile(
        r"(?:姓\s*名)[：:\s]*(.{2,10})"
    ),
    "等级": re.compile(
        r"(?:资质等级|等级)[：:\s]*(.{2,20})"
    ),
}


def infer_document_types(text: str, max_types: int = 3) -> list[str]:
    """推断文档命中的全部类型, 按命中关键词数降序(多标化)。

 一页扫描件常含多种材料或字段串用(如发票页同时含"纳税人识别号"),
 winner-take-all 单标会让真实材料名(发票)被另一类型(税务登记证)垄断,
 检索词面随之偏移。返回多标, 让每种材料名都进入 chunk 文本。

 Args:
 text: OCR 原始文本
 max_types: 首行最多并列的类型数

 Returns:
 命中类型列表(按命中数降序, 同分按定义顺序); 无命中返回空列表
 """
    if not text:
        return []

    scored: list[tuple[int, int, str]] = []
    for order, (doc_type, keywords) in enumerate(_DOC_TYPE_KEYWORDS.items()):
        hits = sum(1 for kw in keywords if kw in text)
        if hits > 0:
            scored.append((-hits, order, doc_type))
    scored.sort()
    return [doc_type for _, _, doc_type in scored[:max_types]]


def infer_document_type(text: str) -> Optional[str]:
    """推断最可能的单一文档类型(向后兼容: 取多标结果首位)。

    Args:
        text: OCR 原始文本

    Returns:
        命中数最高的文档类型名称，无法推断返回 None
    """
    types = infer_document_types(text)
    return types[0] if types else None


def is_document_type_image(ocr_text: str) -> bool:
    """判断 OCR 文本是否为证照类文档

    Args:
        ocr_text: OCR 原始文本

    Returns:
        True 表示为证照/文档类，适合 OCR 处理
    """
    return infer_document_type(ocr_text) is not None


def _extract_fields(text: str) -> dict[str, str]:
    """从 OCR 文本中抽取结构化字段"""
    fields: dict[str, str] = {}
    for field_name, pattern in _PATTERNS.items():
        m = pattern.search(text)
        if m:
            value = m.group(1).strip()
            if value:
                fields[field_name] = value
    return fields


def postprocess_ocr(
    ocr_result: OcrResult,
    spatial_context: str = "",
    size_category: str = "",
) -> str:
    """将 OCR 结果转化为检索友好的自然语言描述

    输出格式对齐 VL_SYSTEM_PROMPT 规范:
    - 第一行: 文件类型说明
    - 后续行: 关键信息列表
    - 控制在 500 字以内

    Args:
        ocr_result: OcrEngine.recognize() 的返回值
        spatial_context: 图片周围的文字上下文
        size_category: 图片尺寸分类

    Returns:
        自然语言描述文本，空串表示无法处理
    """
    if not ocr_result.raw_text or not ocr_result.raw_text.strip():
        return ""

    from app.core.parser.text_normalizer import normalize_cjk
    raw = normalize_cjk(ocr_result.raw_text)
    doc_types = infer_document_types(raw)
    fields = _extract_fields(raw)

    parts: list[str] = []

    # 多标首行——一页含多种材料时全部并列, 避免单标垄断检索词面。
    if len(doc_types) == 1:
        parts.append(f"这是一份{doc_types[0]}的扫描件。")
    elif len(doc_types) >= 2:
        parts.append(f"这是一份包含{'、'.join(doc_types)}等要素的扫描件。")
    else:
        parts.append("这是一份文档的扫描件。")

    if fields.get("单位名称"):
        parts.append(f"持证单位: {fields['单位名称']}")
    if fields.get("姓名"):
        parts.append(f"持证人: {fields['姓名']}")
    if fields.get("法定代表人"):
        parts.append(f"法定代表人: {fields['法定代表人']}")
    if fields.get("统一社会信用代码"):
        parts.append(f"统一社会信用代码: {fields['统一社会信用代码']}")
    if fields.get("证书编号"):
        parts.append(f"证书编号: {fields['证书编号']}")
    if fields.get("等级"):
        parts.append(f"资质等级: {fields['等级']}")
    if fields.get("有效期"):
        parts.append(f"有效期至: {fields['有效期']}")

    # 补充原始文本中的关键信息（避免遗漏）
    remaining_lines = []
    used_values = set(fields.values())
    for line in raw.split("\n"):
        line = line.strip()
        if not line or len(line) < 4:
            continue
        if any(v in line for v in used_values):
            continue
        remaining_lines.append(line)

    if remaining_lines:
        extra = "；".join(remaining_lines[:8])
        parts.append(f"其他信息: {extra}")

    result = "\n".join(parts)

    if len(result) > 500:
        result = result[:497] + "..."

    return result
