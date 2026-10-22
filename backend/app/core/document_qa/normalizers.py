"""
职责: 实体槽位取值的类型化归一化(text/identifier/money/duration/date/quantity)
不做: 不定义槽位、不做冲突判定、不访问文档
"""

from __future__ import annotations

import re
from typing import Any, Optional

_FULLWIDTH_MAP = str.maketrans({
    "（": "(", "）": ")", "，": ",", "：": ":", "－": "-", "—": "-", "–": "-",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
})


def _normalize_text(value: str) -> Optional[str]:
    cleaned = "".join(value.split()).translate(_FULLWIDTH_MAP)
    return cleaned or None


def _normalize_identifier(value: str) -> Optional[str]:
    cleaned = "".join(value.split()).translate(_FULLWIDTH_MAP)
    return cleaned.upper() or None


def _normalize_money(value: str, unit: Optional[str]) -> Optional[str]:
    digits = value.translate(_FULLWIDTH_MAP).replace(",", "").replace("，", "").strip()
    try:
        amount = float(digits)
    except ValueError:
        return None
    if unit and "万" in unit:
        amount *= 10000
    return f"{amount:.2f}"


def _normalize_duration(value: str, unit: Optional[str]) -> Optional[str]:
    digits = value.translate(_FULLWIDTH_MAP).strip()
    try:
        count = int(digits)
    except ValueError:
        return None
    unit = (unit or "").strip()
    # 天/日/日历天 同义归并; 月不换算为天(换算有业务歧义, 保守按不同量纲处理)
    if unit in ("天", "日", "日历天", "个日历天", "个工作日", "工作日") or not unit:
        suffix = "工作日" if "工作" in unit else "天"
        return f"{count}{suffix}"
    return f"{count}{unit}"


def _normalize_date(value: str) -> Optional[str]:
    try:
        import dateparser

        parsed = dateparser.parse(value, languages=["zh"])
    except Exception:  # noqa: BLE001
        return None
    return parsed.date().isoformat() if parsed else None


_DEFAULT_ORG_DECORATIONS = (
    "公章", "盖章", "盖单位章", "签字或盖私章", "签名或盖私章", "签字或盖章",
)
_DEFAULT_ORG_SUFFIXES = (
    "股份有限公司", "有限责任公司", "集团有限公司", "有限公司",
    "集团公司", "研究院", "集团", "公司",
)
_ORG_DATE_DEBRIS_RE = re.compile(r"日$")
_ORG_PARENS_RE = re.compile(r"[()]+")


def _normalize_org_name(value: str, config: Optional[dict[str, Any]] = None) -> Optional[str]:
    """剥印章/日期残片, 按法律后缀截断邻栏, 再折叠后缀比词干。"""
    cfg = config or {}
    text = "".join(value.split()).translate(_FULLWIDTH_MAP)
    decorations = [str(item) for item in (cfg.get("decorations") or _DEFAULT_ORG_DECORATIONS)]
    for decoration in decorations:
        text = text.replace(decoration, "")
    text = _ORG_PARENS_RE.sub("", text)
    text = _ORG_DATE_DEBRIS_RE.sub("", text)
    suffixes = [str(item) for item in (cfg.get("legal_suffixes") or _DEFAULT_ORG_SUFFIXES)]
    cut_idx: Optional[int] = None
    cut_end: Optional[int] = None
    for suffix in suffixes:
        found = text.find(suffix)
        if found < 0:
            continue
        if cut_idx is None or found < cut_idx:
            cut_idx = found
            cut_end = found + len(suffix)
    if cut_end is not None:
        text = text[:cut_end]
    stem = text
    changed = True
    while changed and stem:
        changed = False
        for suffix in suffixes:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                changed = True
                break
    return stem or None


def _normalize_quantity(value: str, unit: Optional[str]) -> Optional[str]:
    """数量只按单位口径归一。同单位不同数值不构成冲突; 单位写不上则不参与。"""
    token = (unit or "").translate(_FULLWIDTH_MAP).replace("³", "3").replace(" ", "")
    token = token.replace("／", "/").lower()
    if not token or not re.search(r"\d", value or ""):
        return None
    if token.endswith("/月"):
        token = token[:-2] + "/month"
    return token or None


def normalize_slot_value(
    kind: str,
    value: str,
    unit: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """按槽位类型归一化取值; 无法解析返回 None(该次命中不参与冲突判定)。"""
    value = (value or "").strip()
    if not value:
        return None
    if kind == "identifier":
        return _normalize_identifier(value)
    if kind == "money":
        return _normalize_money(value, unit)
    if kind == "duration":
        return _normalize_duration(value, unit)
    if kind == "date":
        return _normalize_date(value)
    if kind == "org_name":
        return _normalize_org_name(value, config)
    if kind == "quantity":
        return _normalize_quantity(value, unit)
    return _normalize_text(value)
