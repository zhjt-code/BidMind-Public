"""串标结构化字段归一化工具。"""

from __future__ import annotations

import re
from typing import Literal, Optional

from app.core.collusion.knowledge_base import get_entity_normalization_rules

# D1: 关联类型枚举（精确情形）
# 用于 is_credit_code_related / is_nearby_phone / is_same_address_building
# 等同类函数，让调用方能依据精确情形生成精确叙述（消除"或"字硬编码）。
RelationKind = Optional[Literal["exact", "prefix", "fuzzy"]]


def normalize_company_name(value: str | None) -> str:
    text = _base_normalize(value)
    if not text:
        return ""
    rules = get_entity_normalization_rules()
    for suffix in rules.get("company_suffixes", []):
        if suffix and text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


def normalize_address(value: str | None) -> str:
    text = _base_normalize(value)
    if not text:
        return ""
    rules = get_entity_normalization_rules()
    for marker in rules.get("address_room_markers", []):
        if marker:
            text = re.sub(rf"{re.escape(marker)}\d+[a-zA-Z\-]*", "", text)
    text = re.sub(r"\d{1,4}(室|房|层)$", "", text)
    return text.strip()


def normalize_phone(value: str | None) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-11:] if len(digits) >= 11 else digits


# 常见职位/称谓后缀，归一化时剥离
_PERSON_NAME_TITLES: tuple[str, ...] = (
    "先生", "女士", "同志", "经理", "总监", "主任", "工程师",
    "项目经理", "项目负责人", "技术负责人", "总工", "副总", "总经理",
    "董事长", "副总经理",
)


def normalize_person_name(value: str | None) -> str:
    """人名归一化：去空格/标点，剥离常见称谓后缀，转半角。

    保留中文姓名核心字符；非中文/英文姓名也兼容（去空格即可）。
    返回空串表示无效输入。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    text = _base_normalize(raw)
    if not text:
        return ""
    # 剥离前缀的"姓名:""姓名："等标签
    text = re.sub(r"^(?:姓名|name)[:：]\s*", "", text)
    for title in _PERSON_NAME_TITLES:
        if text.endswith(title.lower()):
            text = text[: -len(title)]
    # 去除括号内的注释（如"<PERSON_1>(项目经理)"）
    text = re.sub(r"\([^)]*\)", "", text)
    text = text.strip(" ,，、;.。")
    return text


_EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+$")


def normalize_email(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if _EMAIL_RE.match(text):
        return text
    return ""


def email_local_part(email: str | None) -> str:
    """提取邮箱用户名部分（@ 前部分），用于与 author 字段对比。"""
    text = normalize_email(email)
    if not text:
        return ""
    return text.split("@", 1)[0]


def normalize_credit_code(value: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def normalize_cert_code(value: str | None) -> str:
    text = str(value or "").upper()
    for old, new in (("（", "("), ("）", ")"), (" ", ""), ("-", ""), ("_", "")):
        text = text.replace(old, new)
    return re.sub(r"[^0-9A-Z()]", "", text)


def normalize_id_tail(value: str | None) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-4:] if len(digits) >= 4 else digits


def normalize_bank_account(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def is_nearby_phone(left: str | None, right: str | None) -> bool:
    """老接口：仅返回 bool（向后兼容）。新代码请用 nearby_phone_kind。"""
    return nearby_phone_kind(left, right) is not None


def nearby_phone_kind(left: str | None, right: str | None) -> RelationKind:
    """D1: 返回精确情形枚举。

    Returns:
        - "exact": 完全相同的归一化电话号码
        - "fuzzy": 前 10 位相同、末位差 <=2（号段相邻）
        - None:   无关
    """
    a = normalize_phone(left)
    b = normalize_phone(right)
    if not a or not b:
        return None
    if a == b:
        return "exact"
    if len(a) == 11 and len(b) == 11 and a[:10] == b[:10]:
        if abs(int(a[-1]) - int(b[-1])) <= 2:
            return "fuzzy"
    return None


def is_same_address_building(left: str | None, right: str | None) -> bool:
    """老接口：仅返回 bool（向后兼容）。新代码请用 address_building_kind。"""
    return address_building_kind(left, right) is not None


def address_building_kind(left: str | None, right: str | None) -> RelationKind:
    """D1: 返回地址匹配的精确情形。

    Returns:
        - "exact": 归一化后完全相同
        - "fuzzy": （预留）模糊匹配（如同楼栋不同房号），当前实现仅完全相同
        - None:   无关
    """
    a = normalize_address(left)
    b = normalize_address(right)
    if not a or not b:
        return None
    if a == b:
        return "exact"
    return None


def is_credit_code_related(left: str | None, right: str | None) -> bool:
    """老接口：仅返回 bool（向后兼容）。新代码请用 credit_code_kind。"""
    return credit_code_kind(left, right) is not None


def credit_code_kind(left: str | None, right: str | None) -> RelationKind:
    """D1: 返回统一社会信用代码关联的精确情形。

    Returns:
        - "exact":  归一化后两个信用代码完全相同
        - "prefix": 前 8 位（登记机关代码 + 主管部门代码）一致
        - None:     无关或长度不足
    """
    a = normalize_credit_code(left)
    b = normalize_credit_code(right)
    if not a or not b:
        return None
    if a == b:
        return "exact"
    if len(a) >= 8 and len(b) >= 8 and a[:8] == b[:8]:
        return "prefix"
    return None


def _base_normalize(value: str | None) -> str:
    text = str(value or "").strip().lower()
    for old, new in (
        ("（", "("),
        ("）", ")"),
        ("，", ","),
        ("。", ""),
        (" ", ""),
        ("\t", ""),
        ("\n", ""),
    ):
        text = text.replace(old, new)
    return text
