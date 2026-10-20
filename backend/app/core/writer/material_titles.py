"""素材标题辅助函数。"""

from __future__ import annotations

import re
from typing import Any


def get_original_material_title(material: Any) -> str:
    """返回素材原始标题。"""
    return str(getattr(material, "title", "") or "").strip()


def get_user_material_title(material: Any) -> str:
    """返回用户修正标题。"""
    return str(getattr(material, "display_title", "") or "").strip()


def get_effective_material_title(material: Any) -> str:
    """返回供展示和下游消费的有效标题。"""
    return get_user_material_title(material) or get_original_material_title(material)


_ATTACHMENT_PAGE_TITLE_RE = re.compile(r"^附件材料\s*P\d+(?:\s*-\s*P?\d+)?$", re.IGNORECASE)
_PURE_INDEX_TITLE_RE = re.compile(r"^[\dA-Za-z._-]{1,12}$")
_FIGURE_PREFIX_RE = re.compile(r"^图\d+\s*[:：]\s*")


def is_poor_material_title(title: str) -> bool:
    """判断标题是否属于低质量兜底标题。"""
    normalized = str(title or "").strip()
    if not normalized:
        return True
    if _ATTACHMENT_PAGE_TITLE_RE.fullmatch(normalized):
        return True
    if _PURE_INDEX_TITLE_RE.fullmatch(normalized):
        return True
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    if len(chinese_chars) < 2:
        return True
    business_keywords = (
        "合同", "协议", "证", "证明", "承诺", "简介", "业绩", "授权", "简历",
        "资质", "体系", "许可", "发票", "函", "身份证", "社保", "纳税", "声明",
    )
    return not any(keyword in normalized for keyword in business_keywords)


def normalize_image_caption_title(caption: str) -> str:
    """将图片 caption 精简成可用作子素材标题的短语。"""
    normalized = str(caption or "").strip()
    if not normalized:
        return ""
    normalized = _FIGURE_PREFIX_RE.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ；;，,。.")
    parts = [part.strip(" ；;，,。.") for part in re.split(r"[，,。；;]", normalized) if part.strip(" ；;，,。.")]
    if parts:
        normalized = parts[0]
    normalized = re.sub(r"(扫描件|扫描图|图片|复印件)$", "扫描件", normalized)
    if len(normalized) > 24:
        normalized = normalized[:24].rstrip("，,。.;； ")
    return normalized


def is_strong_title_candidate(title: str) -> bool:
    """判断候选标题是否足够有业务语义。"""
    normalized = str(title or "").strip()
    if not normalized:
        return False
    if is_poor_material_title(normalized):
        return False
    return len(normalized) >= 3

