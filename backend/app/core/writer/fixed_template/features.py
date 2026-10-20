"""固定模板文本特征提取。

职责: 从候选模板文本中识别字段槽、签章槽和非模板噪音。
不做: 不切分模板边界、不匹配章节、不访问数据库。
"""

from __future__ import annotations

import re


_FIELD_KEYWORDS = (
    "投标人", "法定代表人", "委托代理人", "授权代表", "项目名称",
    "项目编号", "招标人", "金额", "报价", "工期", "服务期",
    "质量", "地址", "电话", "传真", "电子邮箱", "职务", "姓名",
    "性别", "年龄", "身份证", "开户", "账号", "日期",
)

_SIGNATURE_KEYWORDS = ("签字", "盖章", "公章", "日期", "法定代表人", "委托代理人")

_GENERIC_HINTS = (
    "必须毫无例外地使用招标文件所提供的投标文件全部格式",
    "使用招标文件所提供的投标文件全部格式",
    "全部格式",
    "表格可以按同样格式扩展",
)


def extract_field_slots(text: str) -> tuple[str, ...]:
    """提取模板中的字段槽名称。"""
    found = {kw for kw in _FIELD_KEYWORDS if kw in (text or "")}
    for label in re.findall(r"([\u4e00-\u9fffA-Za-z0-9()]{2,18})\s*[:：]", text or ""):
        if any(kw in label for kw in _FIELD_KEYWORDS):
            found.add(label.strip())
    return tuple(sorted(found))


def extract_signature_slots(text: str) -> tuple[str, ...]:
    """提取签章/日期相关槽位。"""
    return tuple(sorted({kw for kw in _SIGNATURE_KEYWORDS if kw in (text or "")}))


def has_table_hint(text: str) -> bool:
    """粗判模板是否包含表格结构。"""
    compact = re.sub(r"\s+", " ", text or "")
    table_terms = ("项目名称", "报价", "备注", "姓名", "性别", "年龄", "序号")
    return sum(1 for term in table_terms if term in compact) >= 3


def is_generic_template_requirement(text: str) -> bool:
    """判断文本是否只是泛化格式要求，而非具体模板正文。"""
    compact = re.sub(r"\s+", "", text or "")
    if any(re.sub(r"\s+", "", hint) in compact for hint in _GENERIC_HINTS):
        return True
    slots = len(extract_field_slots(text)) + len(extract_signature_slots(text))
    return "使用招标文件" in compact and "格式" in compact and slots < 3


def is_directory_like(text: str) -> bool:
    """判断候选文本是否更像目录页。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    title_like = sum(1 for line in lines if re.match(r"^[一二三四五六七八九十\d]+[、.．]", line))
    has_slots = bool(extract_signature_slots(text) or extract_field_slots(text))
    return title_like >= 4 and not has_slots


def score_template_text(text: str) -> float:
    """给候选模板正文打分。"""
    if not text.strip() or is_directory_like(text) or is_generic_template_requirement(text):
        return 0.0
    score = 0.45
    score += min(len(extract_field_slots(text)) * 0.06, 0.24)
    score += min(len(extract_signature_slots(text)) * 0.06, 0.18)
    if has_table_hint(text):
        score += 0.08
    if len(text.strip()) >= 120:
        score += 0.08
    return round(min(score, 0.98), 3)
